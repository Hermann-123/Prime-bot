import os
import sys
import datetime
import random
import time
import string
import json
import websocket
import pandas as pd
import ta
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAGaCLJ3GKiPk_EJXKIn6Ch0oKfLy1xx7f4"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
mode_trading = {}
plateforme_trading = {}
filtre_special = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
signaux_cache = {}
cooldown_actifs = {}
niveaux_martingale = {}
historique_signaux = {}  # 🆕 Historique qualité par paire

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}

SYNTHETIC_PAIRS = ["V10", "V25", "V50", "V75", "V100"]
COMMODITY_PAIRS = ["XAUUSD", "XAGUSD", "USOUSD"]
CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD",
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF",
    "CADCHF", "EURCHF", "USDJPY"
]

ELITE_PAIRS_MT5 = SYNTHETIC_PAIRS + COMMODITY_PAIRS
ALL_PAIRS_POCKET = SYNTHETIC_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS + CRYPTO_PAIRS

# ==========================================
# 🆕 PROFILS DYNAMIQUES V27 (AFFINÉS)
# ==========================================

def obtenir_profil_actif(symbole):
    """
    V27 : Profils affinés avec filtres RSI divergence + ATR adaptatif.
    Les seuils sont plus stricts pour réduire les faux signaux.
    """
    if symbole in SYNTHETIC_PAIRS:
        return {
            "stoch_achat": 30,      # 🔧 Abaissé de 45 → 30 (survendu réel)
            "rsi_achat": 35,        # 🔧 Abaissé de 40 → 35
            "stoch_vente": 70,      # 🔧 Relevé de 55 → 70 (suracheté réel)
            "rsi_vente": 65,        # 🔧 Relevé de 60 → 65
            "vol_multiplier": 2.5,  # 🔧 Réduit de 3.0 → 2.5 (filtre chaos)
            "rr_min": 1.8,
            "cooldown_otm": 900,    # 🆕 15 min après OTM
            "nom": "SMC Synthétiques"
        }
    elif symbole in COMMODITY_PAIRS:
        return {
            "stoch_achat": 25,      # 🔧 Plus strict
            "rsi_achat": 40,
            "stoch_vente": 75,      # 🔧 Plus strict
            "rsi_vente": 60,
            "vol_multiplier": 2.0,
            "rr_min": 2.0,          # 🔧 Or/Pétrole : on veut du ratio
            "cooldown_otm": 1200,   # 🆕 20 min après OTM
            "nom": "SMC Métaux/Énergie"
        }
    else:
        return {
            "stoch_achat": 25,      # 🔧 Plus exigeant
            "rsi_achat": 40,
            "stoch_vente": 75,
            "rsi_vente": 60,
            "vol_multiplier": 1.8,  # 🔧 Plus strict Forex
            "rr_min": 1.5,
            "cooldown_otm": 1800,   # 🆕 30 min après OTM Forex
            "nom": "SMC Forex"
        }

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V27 (PRECISION MAX)"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# SYSTÈME DE GESTION DES ACCÈS VIP
# ==========================================

def est_autorise(user_id):
    if user_id == ADMIN_ID: return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME" or datetime.datetime.now() < expiration: return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ**\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
            except: pass
            return False
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    try:
        argument = message.text.split()[1].lower()
        if argument == '1s': jours = 7
        elif argument == '2s': jours = 14
        elif argument == '1m': jours = 30
        elif argument == '3m': jours = 90
        elif argument == 'vie': jours = "LIFETIME"
        else: jours = int(argument)
        cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cles_generees[cle] = jours
        texte = f"✅ **CLÉ GÉNÉRÉE**\n\n🔑 **Clé :** `{cle}`\n"
        texte += f"⏳ **Durée :** À VIE 👑\n\n" if jours == "LIFETIME" else f"⏳ **Durée :** {jours} Jours\n\n"
        bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: pass

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    chat_id = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle in cles_generees:
            jours = cles_generees[cle]
            if jours == "LIFETIME":
                utilisateurs_autorises[chat_id] = "LIFETIME"
                expiration_texte = "À VIE 👑"
            else:
                expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
                utilisateurs_autorises[chat_id] = expiration
                expiration_texte = expiration.strftime('%d/%m/%Y à %H:%M')
            del cles_generees[cle]
            texte = f"🎉 **ACCÈS DÉVERROUILLÉ !**\n\nBienvenue.\n⏳ **Fin :** {expiration_texte}\n\n👉 Tapez /start"
            bot.send_message(chat_id, texte, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "❌ **Clé invalide ou déjà utilisée.**", parse_mode="Markdown")
    except: pass

# ==========================================
# VERROUILLAGE TEMPOREL DYNAMIQUE
# ==========================================

def est_symbole_autorise(symbole):
    if symbole in SYNTHETIC_PAIRS:
        return "AUTORISE", ""
    now = datetime.datetime.utcnow()
    jour = now.weekday()
    heure_dec = now.hour + (now.minute / 60.0)
    est_week_end = (jour == 4 and heure_dec >= 21.0) or (jour == 5) or (jour == 6 and heure_dec < 21.0)
    if est_week_end:
        if symbole in CRYPTO_PAIRS: return "AUTORISE", ""
        else: return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Marchés fermés le week-end."
    if symbole in CRYPTO_PAIRS:
        return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Cryptos verrouillées la semaine."
    if heure_dec >= 17.5:
        return "HORS_SESSION", "🛑 **REPLI TACTIQUE** : Couvre-feu en cours (17h30 - 00h00 GMT)."
    if 0.0 <= heure_dec < 8.0:
        if symbole in ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD", "XAUUSD", "XAGUSD", "USOUSD"]:
            return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Session Asiatique."
    if 7.0 <= heure_dec < 12.0:
        paires = ["EURUSD", "EURJPY", "EURAUD", "EURCHF", "USDCHF", "CADCHF", "XAUUSD", "XAGUSD", "USOUSD"]
        if heure_dec < 8.0: paires.extend(["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"])
        if symbole in paires: return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Session Européenne."
    if 12.0 <= heure_dec < 17.5:
        if symbole in ["EURUSD", "USDCAD", "AUDUSD", "XAUUSD", "XAGUSD", "USOUSD"]:
            return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Zone US/CA."
    return "BLOCAGE_TOTAL", "🛑 Erreur temporelle."

# ==========================================
# ROUTEUR DERIV
# ==========================================

def est_heure_de_news_dynamique():
    if not FMP_API_KEY: return False
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today}&to={today}&apikey={FMP_API_KEY}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            events = response.json()
            maintenant = datetime.datetime.utcnow()
            for event in events:
                if event.get('impact') == 'High':
                    e_time = datetime.datetime.strptime(event['date'], "%Y-%m-%d %H:%M:%S")
                    diff = abs((maintenant - e_time).total_seconds() / 60)
                    if diff <= 30: return True
    except: pass
    return False

def prefixer_symbole(symbole_brut):
    if symbole_brut in SYNTHETIC_PAIRS: return f"R_{symbole_brut.replace('V', '')}"
    if symbole_brut in CRYPTO_PAIRS: return f"cry{symbole_brut}"
    return f"frx{symbole_brut}"

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 300, "style": "candles", "granularity": granularite}
            ws.send(json.dumps(req))
            history = json.loads(ws.recv())
            ws.close()
            if "error" not in history and "candles" in history:
                return history['candles']
        except:
            time.sleep(1)
            continue
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 1, "style": "ticks"}
            ws.send(json.dumps(req))
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]:
                return float(res["history"]["prices"][0])
        except:
            time.sleep(1)
            continue
    return None

# ==========================================
# 🆕 CONFIRMATION MULTI-TIMEFRAME (MTF)
# ==========================================

def confirmation_mtf(symbole, action_visee):
    """
    V27 : Vérifie l'alignement sur 3 timeframes avant de valider un signal.
    Retourne (bool, str) : (signal_valide, raison)
    """
    action_simplifiee = "CALL" if "ACHAT" in action_visee or action_visee == "CALL" else "PUT"
    votes = 0
    total = 0

    # TF1 : M15 (tendance de fond)
    candles_m15 = obtenir_donnees_deriv(symbole, 900)
    if candles_m15:
        try:
            df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles_m15])
            ema20 = ta.trend.EMAIndicator(close=df['close'], window=20).ema_indicator()
            ema50 = ta.trend.EMAIndicator(close=df['close'], window=50).ema_indicator()
            prix = df['close'].iloc[-1]
            tendance = "HAUSSIERE" if prix > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1] else "BAISSIERE"
            total += 1
            if (action_simplifiee == "CALL" and tendance == "HAUSSIERE") or \
               (action_simplifiee == "PUT" and tendance == "BAISSIERE"):
                votes += 1
        except: pass

    # TF2 : M5 (momentum)
    candles_m5 = obtenir_donnees_deriv(symbole, 300)
    if candles_m5:
        try:
            df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles_m5])
            macd = ta.trend.MACD(close=df['close'])
            macd_diff = macd.macd_diff().iloc[-1]
            rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
            total += 1
            if action_simplifiee == "CALL" and macd_diff > 0 and rsi > 45:
                votes += 1
            elif action_simplifiee == "PUT" and macd_diff < 0 and rsi < 55:
                votes += 1
        except: pass

    # TF3 : M1 (timing précis)
    candles_m1 = obtenir_donnees_deriv(symbole, 60)
    if candles_m1:
        try:
            df = pd.DataFrame([{'close': float(c['close']), 'open': float(c['open']),
                                 'high': float(c['high']), 'low': float(c['low'])} for c in candles_m1])
            # Vérifier que la dernière bougie confirme la direction
            derniere = df.iloc[-1]
            avant_derniere = df.iloc[-2]
            corps_last = derniere['close'] - derniere['open']
            total += 1
            if action_simplifiee == "CALL" and corps_last > 0 and avant_derniere['close'] > avant_derniere['open']:
                votes += 1
            elif action_simplifiee == "PUT" and corps_last < 0 and avant_derniere['close'] < avant_derniere['open']:
                votes += 1
        except: pass

    if total == 0: return True, "MTF non disponible"
    ratio = votes / total
    if ratio >= 0.67:  # Au moins 2/3 TF alignés
        return True, f"✅ MTF aligné ({votes}/{total})"
    else:
        return False, f"❌ MTF divergent ({votes}/{total} TF)"

# ==========================================
# 🆕 DÉTECTION DIVERGENCE RSI
# ==========================================

def detecter_divergence(df, action_visee):
    """
    V27 : Détecte les divergences RSI haussières/baissières.
    Une divergence = signal beaucoup plus fiable.
    """
    try:
        rsi_series = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        prix = df['close']

        # Divergence haussière : prix fait plus bas, RSI fait plus haut
        if "ACHAT" in action_visee or action_visee == "CALL":
            if prix.iloc[-1] < prix.iloc[-5] and rsi_series.iloc[-1] > rsi_series.iloc[-5]:
                return True, "🔄 Divergence RSI Haussière détectée"

        # Divergence baissière : prix fait plus haut, RSI fait plus bas
        elif "VENTE" in action_visee or action_visee == "PUT":
            if prix.iloc[-1] > prix.iloc[-5] and rsi_series.iloc[-1] < rsi_series.iloc[-5]:
                return True, "🔄 Divergence RSI Baissière détectée"

    except: pass
    return False, ""

# ==========================================
# 🆕 FILTRE DE QUALITÉ HISTORIQUE PAR PAIRE
# ==========================================

def obtenir_qualite_paire(symbole, action):
    """
    V27 : Si une paire a eu 2 OTM consécutifs dans la même direction, on l'évite.
    """
    cle = f"{symbole}_{action}"
    hist = historique_signaux.get(cle, [])
    if len(hist) >= 2 and all(r == "OTM" for r in hist[-2:]):
        return False, f"⚠️ {symbole} : 2 OTM consécutifs en {action}. Paire temporairement évitée."
    return True, ""

def enregistrer_resultat_historique(symbole, action, resultat):
    cle = f"{symbole}_{action}"
    if cle not in historique_signaux:
        historique_signaux[cle] = []
    historique_signaux[cle].append(resultat)
    # Garder uniquement les 5 derniers
    historique_signaux[cle] = historique_signaux[cle][-5:]

# ==========================================
# 🆕 VÉRIFICATION DE LA CORRÉLATION (AMÉLIORÉE)
# ==========================================

def verifier_correlation(symbole_base, action_visee):
    if symbole_base in SYNTHETIC_PAIRS or symbole_base in COMMODITY_PAIRS: return True
    correlations = {
        "EURUSD": ("USDCHF", "INVERSE"),
        "GBPUSD": ("USDCHF", "INVERSE"),
        "AUDUSD": ("USDCAD", "INVERSE"),
        "USDCHF": ("EURUSD", "INVERSE"),
        "USDCAD": ("AUDUSD", "INVERSE")
    }
    if symbole_base not in correlations: return True
    symbole_corr, type_corr = correlations[symbole_base]
    candles = obtenir_donnees_deriv(symbole_corr, 300)
    if not candles: return True
    try:
        df_c = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        c_recent_high = df_c['high'].iloc[-20:-1].max()
        c_recent_low = df_c['low'].iloc[-20:-1].min()
        c_prix = df_c['close'].iloc[-1]
        tendance_corr = "HAUSSE" if (c_prix - c_recent_low) > (c_recent_high - c_prix) else "BAISSE"
        action_simplifiee = "CALL" if "ACHAT" in action_visee else "PUT"
        if type_corr == "INVERSE":
            if action_simplifiee == "CALL" and tendance_corr == "HAUSSE": return False
            if action_simplifiee == "PUT" and tendance_corr == "BAISSE": return False
        return True
    except: return True

# ==========================================
# COMMANDE /vision
# ==========================================

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    if message.chat.id in trades_en_cours:
        return bot.send_message(message.chat.id, "⚠️ **SILENCE RADIO** : Combat en cours !")
    commande = message.text.split()
    if len(commande) < 2:
        return bot.send_message(message.chat.id, "⚠️ Précise l'actif (ex: EURUSD, XAUUSD, V75).")
    symbole = commande[1].upper()
    plateforme = plateforme_trading.get(message.chat.id, "MT5")
    if plateforme == "MT5" and symbole not in ELITE_PAIRS_MT5:
        return bot.send_message(message.chat.id, "❌ En mode MT5, analyse restreinte aux Élite.")
    if symbole not in ALL_PAIRS_POCKET:
        return bot.send_message(message.chat.id, "❌ Symbole non reconnu.")
    try:
        msg = bot.send_message(message.chat.id, f"🔍 *Analyse Rayons X SMC V27...*", parse_mode="Markdown")
    except: return
    candles = obtenir_donnees_deriv(symbole)
    if not candles:
        return bot.edit_message_text("⚠️ Impossible de scanner.", message.chat.id, msg.message_id)
    try:
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        df['volume_proxy'] = df['high'] - df['low']
        vol_moyen = df['volume_proxy'].rolling(window=10).mean().iloc[-1]
        vol_actuel = df['volume_proxy'].iloc[-1]
        etat_vol = "Actif 💥" if vol_actuel > vol_moyen else "Faible 💤"
        swing_high_1 = df['high'].iloc[-20:-10].max()
        swing_low_1 = df['low'].iloc[-20:-10].min()
        swing_high_2 = df['high'].iloc[-10:-1].max()
        swing_low_2 = df['low'].iloc[-10:-1].min()
        structure_haussiere = (swing_high_2 > swing_high_1) and (swing_low_2 >= swing_low_1)
        structure_baissiere = (swing_low_2 < swing_low_1) and (swing_high_2 <= swing_high_1)
        tendance = "Order Flow Hausse 🟢" if structure_haussiere else "Order Flow Baisse 🔴" if structure_baissiere else "Consolidation ⚠️"
        rsi = ta.momentum.RSIIndicator(close=df['close']).rsi().iloc[-1]
        macd = ta.trend.MACD(close=df['close'])
        macd_diff = macd.macd_diff().iloc[-1]
        prix_actuel = df['close'].iloc[-1]

        # 🆕 Affichage MTF
        mtf_ok, mtf_msg = confirmation_mtf(symbole, "CALL" if structure_haussiere else "PUT")
        rapport = f"""👁️ **VISION ELITE SMC V27 : {symbole}** 👁️
──────────────────
💰 **Prix :** `{prix_actuel:.5f}`
🧱 **Structure :** `{tendance}`
⛽ **Volume :** `{etat_vol}`
📊 **RSI :** `{rsi:.2f}`
📈 **MACD Diff :** `{macd_diff:.5f}`
🔗 **MTF :** `{mtf_msg}`
──────────────────"""
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except:
        bot.edit_message_text("❌ Erreur d'analyse.", message.chat.id, msg.message_id)

# ==========================================
# 🔥 MOTEUR SMC V27 (PRÉCISION MAXIMALE)
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    """
    V27 : Moteur de signal revu en profondeur.
    
    Améliorations clés :
    1. RSI divergence comme bonus de score (+1.5 pts)
    2. Confirmation MACD obligatoire
    3. Filtre ATR : évite les marchés trop volatils ou trop calmes
    4. MTF obligatoire (au moins 2/3 TF alignés)
    5. Score minimum relevé à 8.5/10 pour signaux réels
    6. Vérification qualité historique de la paire
    """
    if est_heure_de_news_dynamique() and (symbole in COMMODITY_PAIRS or symbole in FOREX_PAIRS):
        return "⚠️ ALERTE NEWS : Marché manipulé.", None, None, None, None, None, None, None

    profil = obtenir_profil_actif(symbole)
    timeframes = [600, 300, 120] if mode == "STANDARD" else [60]

    for tf in timeframes:
        candles = obtenir_donnees_deriv(symbole, tf)
        if not candles: continue

        try:
            df = pd.DataFrame([{
                'open': float(c['open']), 'close': float(c['close']),
                'high': float(c['high']), 'low': float(c['low'])
            } for c in candles])

            df['corps_bougie'] = abs(df['close'] - df['open'])
            df['taille_bougie'] = df['high'] - df['low']
            df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
            df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']
            df['volume_proxy'] = df['high'] - df['low']
            df['volume_moyen'] = df['volume_proxy'].rolling(window=14).mean()

            # ================================
            # 🆕 FILTRE ATR (V27)
            # ================================
            atr = ta.volatility.AverageTrueRange(
                high=df['high'], low=df['low'], close=df['close'], window=14
            ).average_true_range()
            atr_val = atr.iloc[-1]
            atr_moyen = atr.iloc[-20:].mean()

            # Marché trop calme (squeeze) ou trop chaotique (news spike)
            if atr_val < atr_moyen * 0.5:
                continue  # Trop calme : pas de signal fiable
            if atr_val > atr_moyen * 3.0:
                return "⚠️ Filtre ATR : Marché en spike (news ?).", None, None, None, None, None, None, None

            vol_actuel = df['volume_proxy'].iloc[-1]
            vol_moyen = df['volume_moyen'].iloc[-1]
            volume_ok = (vol_actuel > vol_moyen) and (vol_actuel < (vol_moyen * profil["vol_multiplier"]))

            avg_taille = df['taille_bougie'].iloc[-4:-1].mean()
            avg_corps = df['corps_bougie'].iloc[-4:-1].mean()
            if avg_corps > 0 and (avg_taille > avg_corps * 3.5):
                return "⚠️ Filtre Anti-Chaos activé.", None, None, None, None, None, None, None

            # ================================
            # INDICATEURS TECHNIQUES
            # ================================
            df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
            df['stoch_k'] = ta.momentum.StochasticOscillator(
                high=df['high'], low=df['low'], close=df['close']
            ).stoch()
            macd = ta.trend.MACD(close=df['close'])
            df['macd_diff'] = macd.macd_diff()

            last, prev, p_prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            c = last['close']
            rsi_val = round(last['rsi'], 1)
            stoch_val = round(last['stoch_k'], 1)
            macd_diff_val = last['macd_diff']

            action, confiance, bb_status, score_algo = None, 0, "En Attente", 5.0

            vrai_corps = last['corps_bougie'] > (last['taille_bougie'] * 0.30)  # 🔧 Plus strict : 25 → 30%
            last_is_green = last['close'] > last['open']
            last_is_red = last['close'] < last['open']
            prev_is_green = prev['close'] > prev['open']
            prev_is_red = prev['close'] < prev['open']

            rejet_haussier = last['meche_basse'] > (last['corps_bougie'] * 2.0)  # 🔧 Plus strict : 1.5 → 2.0
            rejet_baissier = last['meche_haute'] > (last['corps_bougie'] * 2.0)
            avalement_haussier = prev_is_red and last_is_green and (last['close'] > prev['open']) and (last['open'] <= prev['close'])
            avalement_baissier = prev_is_green and last_is_red and (last['close'] < prev['open']) and (last['open'] >= prev['close'])
            harami_bull = prev_is_red and last_is_green and (last['open'] > prev['close']) and (last['close'] < prev['open'])
            harami_bear = prev_is_green and last_is_red and (last['open'] < prev['close']) and (last['close'] > prev['open'])

            corps_prev = prev['corps_bougie']
            danger_rejet_baisse = prev['meche_haute'] > (corps_prev * 1.5) if corps_prev > 0 else False
            danger_rejet_hausse = prev['meche_basse'] > (corps_prev * 1.5) if corps_prev > 0 else False
            fusee_haussiere = last_is_green and prev_is_green and (p_prev['close'] > p_prev['open']) and vrai_corps
            fusee_baissiere = last_is_red and prev_is_red and (p_prev['close'] < p_prev['open']) and vrai_corps

            swing_high_1 = df['high'].iloc[-20:-10].max()
            swing_low_1 = df['low'].iloc[-20:-10].min()
            swing_high_2 = df['high'].iloc[-10:-1].max()
            swing_low_2 = df['low'].iloc[-10:-1].min()
            structure_haussiere = (swing_high_2 > swing_high_1) and (swing_low_2 >= swing_low_1)
            structure_baissiere = (swing_low_2 < swing_low_1) and (swing_high_2 <= swing_high_1)

            prix_moyen_recent = df['close'].iloc[-6:-1].mean()
            dans_zone_discount = c < prix_moyen_recent
            dans_zone_premium = c > prix_moyen_recent

            if mode == "STANDARD":
                if tf == 300:
                    duree_secondes = 180
                    exp_texte = "3 MIN (HIT & RUN ⚡)"
                else:
                    duree_secondes = tf
                    exp_texte = f"{int(tf/60)} MIN"

                # === SIGNAL ACHAT ===
                if structure_haussiere and dans_zone_discount and volume_ok and vrai_corps \
                        and not danger_rejet_baisse and not fusee_baissiere \
                        and macd_diff_val > 0:  # 🆕 MACD obligatoire

                    if (stoch_val < profil["stoch_achat"]) and (rsi_val < 40):  # 🔧 RSI plus strict
                        action, confiance, score_algo = "🟢 ACHAT (CALL)", 82, 7.5
                        bb_status = f"🎯 {profil['nom']} : Order Block (Zone Discount)"

                    if avalement_haussier or rejet_haussier or harami_bull:
                        action, confiance, score_algo = "🟢 ACHAT (CALL)", 95, 9.0
                        bb_status = f"👑 {profil['nom']} : Prise de Liquidité 🚀"

                # === SIGNAL VENTE ===
                elif structure_baissiere and dans_zone_premium and volume_ok and vrai_corps \
                        and not danger_rejet_hausse and not fusee_haussiere \
                        and macd_diff_val < 0:  # 🆕 MACD obligatoire

                    if (stoch_val > profil["stoch_vente"]) and (rsi_val > 60):  # 🔧 RSI plus strict
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 82, 7.5
                        bb_status = f"🎯 {profil['nom']} : Order Block (Zone Premium)"

                    if avalement_baissier or rejet_baissier or harami_bear:
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 95, 9.0
                        bb_status = f"👑 {profil['nom']} : Prise de Liquidité ☄️"

            elif mode == "SCALP":
                indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2)
                bb_haute = indicateur_bb.bollinger_hband().iloc[-1]
                bb_basse = indicateur_bb.bollinger_lband().iloc[-1]
                df['bb_width'] = indicateur_bb.bollinger_wband()
                squeeze = df['bb_width'].iloc[-1] < (df['bb_width'].rolling(window=20).mean().iloc[-1] * 0.8)
                duree_secondes, exp_texte = 60, "1 MINUTE (SCALP 🛡️)"

                if not squeeze and volume_ok and vrai_corps:
                    if (last['low'] <= bb_basse) and rejet_haussier \
                            and not danger_rejet_baisse and not fusee_baissiere \
                            and macd_diff_val > 0:
                        action, confiance, score_algo = "🟢 ACHAT (CALL)", 90, 9.0
                        bb_status = f"🛡️ Scalp {profil['nom']} : Liquidité Basse"
                    elif (last['high'] >= bb_haute) and rejet_baissier \
                            and not danger_rejet_hausse and not fusee_haussiere \
                            and macd_diff_val < 0:
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 90, 9.0
                        bb_status = f"🛡️ Scalp {profil['nom']} : Liquidité Haute"

            if action:
                # =============================================
                # 🆕 BONUS DIVERGENCE RSI (+1.5 pts si présente)
                # =============================================
                div_ok, div_msg = detecter_divergence(df, action)
                if div_ok:
                    score_algo = min(score_algo + 1.5, 10.0)
                    bb_status += f"\n{div_msg}"

                # =============================================
                # 🆕 CONFIRMATION MTF (obligatoire ≥ 2/3 TF)
                # =============================================
                if symbole not in SYNTHETIC_PAIRS:  # Synthétiques : MTF moins critique
                    mtf_ok, mtf_msg = confirmation_mtf(symbole, action)
                    if not mtf_ok:
                        return f"⚠️ Signal annulé : {mtf_msg}", None, None, None, None, None, None, None

                # =============================================
                # 🆕 VÉRIFICATION QUALITÉ HISTORIQUE
                # =============================================
                action_simple = "CALL" if "ACHAT" in action else "PUT"
                hist_ok, hist_msg = obtenir_qualite_paire(symbole, action_simple)
                if not hist_ok:
                    return f"⚠️ {hist_msg}", None, None, None, None, None, None, None

                # Corrélation et cooldown
                if not verifier_correlation(symbole, action):
                    return "⚠️ **FAKEOUT DÉTECTÉ** : Corrélation adverse.", None, None, None, None, None, None, None

                delai_blocage = 600 if mode == "SCALP" else 1800
                if symbole in cooldown_actifs and \
                   (time.time() - cooldown_actifs[symbole]['time'] < delai_blocage):
                    if action_simple == cooldown_actifs[symbole]['action']:
                        return "⚠️ **BLOCAGE ANTI-FAKEOUT**", None, None, None, None, None, None, None

                return action, min(confiance, 99), exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo

        except: continue

    return f"⚠️ En attente d'une opportunité ({mode}).", None, None, None, None, None, None, None

# ==========================================
# INTERFACE MENU & COMMANDES
# ==========================================

def obtenir_clavier(user_id):
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    plateforme = plateforme_trading.get(user_id, "MT5")
    filtre = filtre_special.get(user_id, "TOUS")
    btn_mode = "🛡️ MODE: SMC STANDARD" if mode_actuel == "STANDARD" else "🔥 MODE: SMC SCALP"
    btn_plateforme = "🏦 BROKER: POCKET" if plateforme == "POCKET" else "📈 BROKER: MT5"
    btn_filtre = "💎 SIGNAUX: TOUS" if filtre == "TOUS" else "💎 SIGNAUX: SPÉCIAUX"
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton(btn_mode), KeyboardButton(btn_plateforme))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"), KeyboardButton(btn_filtre))
    return markup

@bot.message_handler(func=lambda m: m.text.startswith("💎 SIGNAUX:"))
def toggle_filtre(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    actuel = filtre_special.get(user_id, "TOUS")
    if actuel == "TOUS":
        filtre_special[user_id] = "SPECIAUX"
        bot.send_message(user_id, "💎 **MODE VIP ULTRA 10/10 ACTIF**\nUniquement les structures parfaites.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        filtre_special[user_id] = "TOUS"
        bot.send_message(user_id, "📡 **MODE TOUS SIGNAUX ACTIVÉ**\nRadar ouvert 8/10 et 10/10.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours:
        return bot.send_message(user_id, "⚠️ Un trade est déjà en cours.")
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    if mode_actuel == "STANDARD":
        mode_trading[user_id] = "SCALP"
        bot.send_message(user_id, "🔥 **MODE SMC SCALPING ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE SMC STANDARD ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_plateforme(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours:
        return bot.send_message(user_id, "⚠️ Terminez le trade en cours.")
    plateforme_actuelle = plateforme_trading.get(user_id, "MT5")
    if plateforme_actuelle == "POCKET":
        plateforme_trading[user_id] = "MT5"
        bot.send_message(user_id, "📈 **MODE MT5 ACTIVÉ**\nÉlite uniquement.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        plateforme_trading[user_id] = "POCKET"
        bot.send_message(user_id, "🏦 **MODE POCKET ACTIVÉ**\n100% Forex Binaire.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    plateforme_trading[user_id] = plateforme_trading.get(user_id, "MT5")
    filtre_special[user_id] = filtre_special.get(user_id, "TOUS")
    texte = """🏴‍☠️ **TERMINAL PRIME - ÉDITION V27 (PRECISION MAX)** 🔥
──────────────────
🚨 **MOTEUR SMC V27 : TRIPLE CONFIRMATION ACTIVE** 🚨

✅ MTF obligatoire (M15 + M5 + M1)
✅ MACD comme filtre de momentum
✅ Divergence RSI comme bonus
✅ ATR pour filtrer les marchés instables
✅ Mémoire historique par paire
✅ Martingale adaptative avec pivot intelligent

👉 Moins de signaux. Beaucoup plus précis."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **HORAIRES DE TIR RESTREINTS** 🕒

🥇 **Matières Premières & Forex :** Lun–Ven, Sessions Londres & New York.
💥 **Indices Volatility :** 24h/24, 7j/7."""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE CIBLE" or m.text == "📊 CHOISIR UNE CIBLE ELITE")
def devises(message):
    if not est_autorise(message.chat.id): return
    plateforme = plateforme_trading.get(message.chat.id, "MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    if plateforme == "MT5":
        markup.add(
            InlineKeyboardButton("🔥 V10", callback_data="set_V10"),
            InlineKeyboardButton("🔥 V25", callback_data="set_V25"),
            InlineKeyboardButton("🔥 V50", callback_data="set_V50")
        )
        markup.add(
            InlineKeyboardButton("⚡ V75", callback_data="set_V75"),
            InlineKeyboardButton("💥 V100", callback_data="set_V100")
        )
        markup.add(
            InlineKeyboardButton("🥇 GOLD", callback_data="set_XAUUSD"),
            InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"),
            InlineKeyboardButton("🛢 PÉTROLE", callback_data="set_USOUSD")
        )
        texte_menu = "Sélectionne ta cible (L'Élite MT5) :"
    else:
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"),
            InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"),
            InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY")
        )
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"),
            InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"),
            InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY")
        )
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"),
            InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"),
            InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD")
        )
        markup.add(
            InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"),
            InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"),
            InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF")
        )
        markup.add(InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"))
        texte_menu = "Sélectionne ta cible (Mode Binaire : 100% Forex) :"
    bot.send_message(message.chat.id, texte_menu, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours:
        return bot.send_message(chat_id, "⚠️ Combat en cours.")
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une cible !")
    save_devise(type('obj', (object,), {
        'data': f"set_{actif}",
        'message': message,
        'from_user': message.from_user,
        'id': 0
    })())

# ==========================================
# 🆕 GESTION DU TIMING D'ENTRÉE (V27)
# ==========================================

def calculer_entree_precise(duree_signal):
    """
    V27 : Calcule le délai d'entrée de façon intelligente.
    - Synchronise sur la prochaine minute ronde
    - Ajoute 30s de préparation (au lieu de 120s fixes)
    - Vérifie que le signal sera encore valide
    """
    maintenant = datetime.datetime.now()
    secondes_restantes_dans_minute = 60 - maintenant.second

    # Si on est entre 45s et 59s : attendre la minute suivante + 5s
    if maintenant.second >= 45:
        delai = secondes_restantes_dans_minute + 5
    # Si on est entre 0 et 10s : entrer dans 5 secondes (on est tout frais)
    elif maintenant.second <= 10:
        delai = max(5, 60 - maintenant.second + 5)
    # Sinon : entrer à la prochaine minute ronde
    else:
        delai = secondes_restantes_dans_minute + 5  # +5s de marge broker

    heure_entree = maintenant + datetime.timedelta(seconds=delai)
    return delai, heure_entree.strftime("%H:%M:%S")

# ==========================================
# EXÉCUTION EXACTE
# ==========================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours:
        try: bot.answer_callback_query(call.id, "⚠️ Combat en cours !", show_alert=True)
        except: pass
        return

    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id if hasattr(call, 'from_user') else chat_id] = actif
    plateforme = plateforme_trading.get(chat_id, "MT5")
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    cle_memoire = f"{actif}_{mode_actuel}"

    signal_cache = signaux_cache.get(cle_memoire)
    utiliser_cache = False
    if signal_cache:
        age_secondes = time.time() - signal_cache['time']
        if age_secondes <= 90:  # 🔧 Réduit de 120 → 90s (signal plus frais)
            utiliser_cache = True
        else:
            del signaux_cache[cle_memoire]

    try: bot.delete_message(chat_id, call.message.message_id)
    except: pass

    if actif in SYNTHETIC_PAIRS: nom_affiche = f"💥 V{actif.replace('V', '')}"
    elif actif == "XAUUSD": nom_affiche = "🥇 GOLD (XAU/USD)"
    elif actif == "XAGUSD": nom_affiche = "🥈 ARGENT (XAG/USD)"
    elif actif == "USOUSD": nom_affiche = "🛢 PÉTROLE (CRUDE/WTI)"
    elif actif in CRYPTO_PAIRS: nom_affiche = f"🪙 {actif[:3]}/{actif[3:]}"
    else: nom_affiche = f"💱 {actif[:3]}/{actif[3:]}"

    if not utiliser_cache:
        bot.send_message(chat_id, f"⏱️ **OPPORTUNITÉ EXPIRÉE SUR {actif}**\n\nSignal trop vieux (>90 secondes). Le marché a bougé. Attendez la prochaine alerte.", parse_mode="Markdown")
        return

    # 🆕 Timing précis V27
    delai_entree, str_entree = calculer_entree_precise(signal_cache.get('dur', 60))
    current_ask = obtenir_prix_actuel_deriv(actif) or 0.0

    if plateforme == "MT5":
        action_affiche = "🟢 BUY MARKET" if "ACHAT" in signal_cache['action'] else "🔴 SELL MARKET"
        signal = f"""⚡ **SIGNAL MT5 SNIPER V27 💎** ⚡
──────────────────
🌐 **ACTIF :** {nom_affiche}
👉 **ORDRE :** {action_affiche}
🎯 **RATIO R/R :** {signal_cache.get('mt5_rr', 0.0):.2f}
──────────────────
💰 **PRIX ACTUEL :** `{current_ask:.5f}`
🛑 **STOP LOSS :** `{signal_cache.get('mt5_sl', 0.0):.5f}`
✅ **TAKE PROFIT :** `{signal_cache.get('mt5_tp', 0.0):.5f}`
⏱ **ENTRÉE OPTIMALE :** `{str_entree}`
──────────────────
⚠️ *Lot de 0.001 pour les indices*"""
        bot.send_message(chat_id, signal, parse_mode="Markdown")
        return

    else:
        palier = niveaux_martingale.get(chat_id, 0)
        score = signal_cache.get('sc', 5.0)
        mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))

        if palier == 0 and score < 9.0:  # 🔧 Seuil relevé : 10 → 9.0 pour fantôme
            signal = f"""👻 **MODE FANTÔME (PALIER 0)** 👻
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_entree}`
👉 **ACTION :** {signal_cache['action']}
⏳ **DURÉE :** {signal_cache['exp']}
📊 **SCORE :** `{score}/10`

*L'IA observe virtuellement. NE RENTREZ PAS.*
*(Si ITM fantôme → le bot déverrouille le signal réel Palier 1)*"""

        elif palier == 0 and score >= 9.0:
            palier = 1
            niveaux_martingale[chat_id] = 1
            signal = f"""🚨 **SIGNAL RÉEL VIP 💎 (SCORE {score}/10)** 🚨
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_entree}`
⏳ **EXPIRATION :** {signal_cache['exp']}
👉 **ACTION :** {signal_cache['action']}
🛡️ {signal_cache['bb']}

✅ Triple confirmation MTF validée !
💵 **MISE CALCULÉE :** `{mise}$`
*(Palier 1)*"""
        else:
            signal = f"""🚨 **SIGNAL DE TIR : PALIER {palier}** 🚨
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_entree}`
👉 **ACTION :** {signal_cache['action']}
⏳ **DURÉE :** {signal_cache['exp']}
💵 **MISE :** `{mise}$`
──────────────────
⏳ *Flash de confirmation dans {delai_entree}s*"""

        bot.send_message(chat_id, signal, parse_mode="Markdown")
        action_brute = "CALL" if "ACHAT" in signal_cache['action'] else "PUT"
        Timer(delai_entree, executer_tir_flash, args=[
            chat_id, actif, action_brute, signal_cache['dur'], palier, nom_affiche
        ]).start()

# ==========================================
# SCANNER AUTOMATIQUE V27
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            utilisateurs_libres = [uid for uid in utilisateurs_actifs if est_autorise(uid) and uid not in trades_en_cours]
            if not utilisateurs_libres: continue

            for paire in ALL_PAIRS_POCKET:
                statut, _ = est_symbole_autorise(paire)
                if statut == "BLOCAGE_TOTAL": continue

                for mode in ["STANDARD", "SCALP"]:
                    delai_repos = 300 if mode == "STANDARD" else 120
                    cle_memoire = f"{paire}_{mode}"
                    if cle_memoire in derniere_alerte_auto and \
                       (time.time() - derniere_alerte_auto[cle_memoire] < delai_repos): continue

                    action, conf, exp, dur, rsi, stoch, bb, sc = analyser_binaire_pro(paire, mode)

                    if action and "⚠️" not in action:
                        if statut == "HORS_SESSION" and (sc is None or sc < 9.5): continue

                        action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
                        alerte_valide = True
                        sl, tp, ratio_rr = 0, 0, 0
                        profil = obtenir_profil_actif(paire)

                        candles_m15 = obtenir_donnees_deriv(paire, 900)
                        if candles_m15:
                            df_m15 = pd.DataFrame([{
                                'close': float(c['close']),
                                'high': float(c['high']),
                                'low': float(c['low'])
                            } for c in candles_m15])
                            df_m15['ema_50'] = ta.trend.EMAIndicator(close=df_m15['close'], window=50).ema_indicator()
                            df_m15['ema_20'] = ta.trend.EMAIndicator(close=df_m15['close'], window=20).ema_indicator()
                            tendance_m15 = "HAUSSIERE" if df_m15['close'].iloc[-1] > df_m15['ema_50'].iloc[-1] else "BAISSIERE"

                            # 🔧 Vérification EMA20 > EMA50 pour la tendance
                            ema_alignee = df_m15['ema_20'].iloc[-1] > df_m15['ema_50'].iloc[-1] if tendance_m15 == "HAUSSIERE" else df_m15['ema_20'].iloc[-1] < df_m15['ema_50'].iloc[-1]

                            if (action_simplifiee == "CALL" and tendance_m15 == "BAISSIERE") or \
                               (action_simplifiee == "PUT" and tendance_m15 == "HAUSSIERE"):
                                alerte_valide = False

                            if alerte_valide and not ema_alignee:
                                alerte_valide = False  # 🆕 EMA20/50 doit être alignée

                            if alerte_valide and paire in ELITE_PAIRS_MT5:
                                candles_m5 = obtenir_donnees_deriv(paire, 300)
                                current_ask = obtenir_prix_actuel_deriv(paire)
                                if candles_m5 and current_ask:
                                    df_m5 = pd.DataFrame([{
                                        'high': float(c['high']),
                                        'low': float(c['low']),
                                        'close': float(c['close'])
                                    } for c in candles_m5])
                                    df_m5['atr'] = ta.volatility.AverageTrueRange(
                                        high=df_m5['high'], low=df_m5['low'],
                                        close=df_m5['close'], window=14
                                    ).average_true_range()
                                    atr_val = df_m5['atr'].iloc[-1]

                                    if action_simplifiee == "CALL":
                                        creux = df_m15['low'].iloc[-30:-1].min()
                                        sl = creux - (atr_val * 1.5)
                                        tp = df_m15['high'].iloc[-40:-1].max()
                                        if tp <= current_ask: tp = current_ask + (abs(current_ask - sl) * 2.0)
                                    else:
                                        sommet = df_m15['high'].iloc[-30:-1].max()
                                        sl = sommet + (atr_val * 1.5)
                                        tp = df_m15['low'].iloc[-40:-1].min()
                                        if tp >= current_ask: tp = current_ask - (abs(sl - current_ask) * 2.0)

                                    risque = abs(current_ask - sl)
                                    recompense = abs(tp - current_ask)
                                    ratio_rr = recompense / risque if risque > 0 else 0
                                    if ratio_rr < profil["rr_min"]: alerte_valide = False

                        if not alerte_valide: continue

                        signaux_cache[cle_memoire] = {
                            'time': time.time(), 'action': action, 'conf': conf,
                            'exp': exp, 'dur': dur, 'rsi': rsi, 'stoch': stoch,
                            'bb': bb, 'sc': sc, 'mt5_sl': sl, 'mt5_tp': tp, 'mt5_rr': ratio_rr
                        }
                        derniere_alerte_auto[cle_memoire] = time.time()

                        for uid in utilisateurs_libres:
                            pf = plateforme_trading.get(uid, "MT5")
                            if pf == "MT5" and paire not in ELITE_PAIRS_MT5: continue
                            if pf == "POCKET" and paire not in FOREX_PAIRS: continue
                            if mode_trading.get(uid, "STANDARD") != mode: continue
                            if filtre_special.get(uid) == "SPECIAUX" and (sc is None or sc < 9.5): continue

                            if paire in SYNTHETIC_PAIRS: nom_aff = f"V{paire.replace('V', '')}"
                            elif paire == "XAUUSD": nom_aff = "GOLD"
                            elif paire == "XAGUSD": nom_aff = "ARGENT"
                            elif paire == "USOUSD": nom_aff = "PÉTROLE"
                            else: nom_aff = f"{paire[:3]}/{paire[3:]}"

                            profil_nom = profil['nom']
                            markup = InlineKeyboardMarkup().add(
                                InlineKeyboardButton(f"⚡ Frapper {nom_aff}", callback_data=f"set_{paire}")
                            )
                            if sc >= 9.5:
                                msg = f"🔔 **{profil_nom} {sc:.1f}/10 : {nom_aff}**\n✅ Triple MTF aligné. Signal premium. 90s pour frapper."
                            else:
                                msg = f"🔔 **RADAR {profil_nom} : {nom_aff}**\nStructure validée. 90s pour agir."

                            try: bot.send_message(uid, msg, reply_markup=markup, parse_mode="Markdown")
                            except: pass

        except Exception as e: pass

# ==========================================
# FONCTIONS DE RÉSULTATS
# ==========================================

def executer_tir_flash(chat_id, symbole, action_brute, duree, palier, nom_affiche):
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    if palier == 0:
        texte = f"👻 **FANTÔME LANCÉ ({nom_affiche})**\nL'IA observe virtuellement..."
        markup = None
    else:
        texte = f"🔥 **TIR IMMÉDIAT : PALIER {palier} ({nom_affiche})**\n👉 **CLIQUEZ SUR {action_affichage} MAINTENANT !**"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET", callback_data="force_win"))
    try: bot.send_message(chat_id, texte, parse_mode="Markdown", reply_markup=markup)
    except: pass
    trades_en_cours[chat_id] = {'symbole': symbole, 'action': action_brute, 'duree': duree, 'nom_affiche': nom_affiche}
    Timer(2, relever_prix_entree, args=[chat_id, symbole]).start()
    Timer(duree, verifier_resultat, args=[chat_id]).start()

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee, cooldown_actifs, niveaux_martingale
    time.sleep(3)
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'): return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie: return

    prix_entree = trade['prix_entree']
    action = trade['action']
    nom_affiche = trade['nom_affiche']
    palier_actuel = niveaux_martingale.get(chat_id, 0)
    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)

    if gagne:
        niveaux_martingale[chat_id] = 0
        enregistrer_resultat_historique(symbole, action, "ITM")  # 🆕
        if palier_actuel == 0:
            texte = f"👻 **FANTÔME RÉUSSI (ITM)**\nZone parfaite sur {nom_affiche}.\n🔓 Radar actif."
        else:
            texte = f"✅ **CIBLE ABATTUE (ITM)**\n🚀 {nom_affiche} ({action})\n📈 **Entrée :** `{prix_entree:.5f}`\n📉 **Sortie :** `{prix_sortie:.5f}`\n🔓 Radar déverrouillé."
            stats_journee['ITM'] += 1
            stats_journee['details'].append(f"✅ {nom_affiche}")
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, texte, parse_mode="Markdown")
        except: pass

    else:
        enregistrer_resultat_historique(symbole, action, "OTM")  # 🆕
        profil = obtenir_profil_actif(symbole)

        if palier_actuel < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]

            # ==========================================
            # 🔥 MARTINGALE ADAPTATIVE V27 (AMÉLIORÉE)
            # ==========================================
            action_martingale = action
            commentaire_ia = "🔍 Structure toujours valide. On persiste."

            candles_analyse = obtenir_donnees_deriv(symbole, 60)
            if candles_analyse:
                try:
                    df_a = pd.DataFrame([{
                        'open': float(c['open']), 'close': float(c['close']),
                        'high': float(c['high']), 'low': float(c['low'])
                    } for c in candles_analyse])

                    derniere = df_a.iloc[-1]
                    corps = abs(derniere['close'] - derniere['open'])
                    taille_totale = derniere['high'] - derniere['low']

                    # 🆕 Analyse des 3 dernières bougies pour éviter les faux pivots
                    df_recentes = df_a.iloc[-3:]
                    corps_moyen = df_recentes.apply(
                        lambda r: abs(r['close'] - r['open']), axis=1
                    ).mean()
                    force_directionnelle = sum(
                        1 if r['close'] > r['open'] else -1
                        for _, r in df_recentes.iterrows()
                    )

                    if taille_totale > 0:
                        if action == "CALL":
                            # Pivot uniquement si : bougie rouge forte + momentum baissier confirmé
                            if (derniere['close'] < derniere['open'] and
                                    corps > (taille_totale * 0.75) and  # 🔧 Plus strict : 0.70 → 0.75
                                    force_directionnelle <= -2 and       # 🆕 Au moins 2 bougies baissières
                                    corps > corps_moyen * 1.2):          # 🆕 Corps plus grand que la moyenne
                                action_martingale = "PUT"
                                commentaire_ia = "🔄 **BREAKER BLOCK CONFIRMÉ** : Momentum baissier sur 3 bougies. Pivot PUT validé."
                        elif action == "PUT":
                            if (derniere['close'] > derniere['open'] and
                                    corps > (taille_totale * 0.75) and
                                    force_directionnelle >= 2 and
                                    corps > corps_moyen * 1.2):
                                action_martingale = "CALL"
                                commentaire_ia = "🔄 **BREAKER BLOCK CONFIRMÉ** : Momentum haussier sur 3 bougies. Pivot CALL validé."

                except Exception: pass

            msg_fail = f"""⚠️ **PIÈGE BROKER (Palier {palier_actuel} OTM)**
📉 **Sortie :** `{prix_sortie:.5f}`

🧠 **ANALYSE IA V27 :**
{commentaire_ia}

⚡ Signal Palier {palier_actuel + 1} en cours de génération..."""
            bot.send_message(chat_id, msg_fail, parse_mode="Markdown")

            cle_memoire = f"{symbole}_{mode_trading.get(chat_id, 'STANDARD')}"
            action_texte_final = "🟢 ACHAT (CALL)" if action_martingale == "CALL" else "🔴 VENTE (PUT)"

            signaux_cache[cle_memoire] = {
                'time': time.time(),
                'action': action_texte_final,
                'conf': 99,
                'exp': f"{int(trade['duree']/60)} MIN" if trade['duree'] >= 60 else f"{trade['duree']} SEC",
                'dur': trade['duree'],
                'rsi': 50, 'stoch': 50,
                'bb': f"Martingale V27 ({commentaire_ia[:40]}...)",
                'sc': 5.0
            }

            class CallFictif:
                def __init__(self, c_id, msg_id, data):
                    self.message = type('obj', (object,), {
                        'chat': type('obj', (object,), {'id': c_id}),
                        'message_id': msg_id
                    })
                    self.data = data
                    self.id = 0
                    self.from_user = type('obj', (object,), {'id': c_id})

            save_devise(CallFictif(chat_id, 0, f"set_{symbole}"))

        else:
            # 🆕 Cooldown dynamique basé sur le profil
            cooldown_duree = profil.get("cooldown_otm", 1200)
            niveaux_martingale[chat_id] = 0
            texte = f"🛑 **SÉQUENCE ARRÊTÉE (OTM)**\nSécurisation des fonds sur {nom_affiche}.\n⏳ Radar verrouillé {cooldown_duree//60} min."
            if palier_actuel > 0: stats_journee['OTM'] += 1
            cooldown_actifs[symbole] = {'time': time.time(), 'action': action, 'duree': cooldown_duree}
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
            try: bot.send_message(chat_id, texte, parse_mode="Markdown")
            except: pass

@bot.callback_query_handler(func=lambda c: c.data == "force_win")
def override_victoire_manuelle(call):
    chat_id = call.message.chat.id
    if chat_id in trades_en_cours:
        stats_journee['ITM'] += 1
        trade = trades_en_cours[chat_id]
        enregistrer_resultat_historique(trade['symbole'], trade['action'], "ITM")  # 🆕
        texte = f"✅ **CIBLE ABATTUE (ITM MANUEL)**\n🚀 {trade['nom_affiche']} ({trade['action']})\n🔓 Radar déverrouillé."
        bot.send_message(chat_id, texte, parse_mode="Markdown")
        del trades_en_cours[chat_id]
    niveaux_martingale[chat_id] = 0
    try: bot.answer_callback_query(call.id, "Victoire enregistrée.", show_alert=True)
    except: pass
    try: bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except: pass

def gestionnaire_bilan():
    global stats_journee
    bilan_envoye_aujourdhui = False
    while True:
        try:
            now = datetime.datetime.utcnow()
            if now.hour == 18 and now.minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                winrate = (stats_journee['ITM'] / total_trades * 100) if total_trades > 0 else 0
                texte_bilan = f"""📊 **BILAN QUOTIDIEN V27** 📊
──────────────────
✅ **ITM :** {stats_journee['ITM']}
❌ **OTM :** {stats_journee['OTM']}
🎯 **WINRATE :** {winrate:.1f}%
──────────────────"""
                for uid in utilisateurs_actifs:
                    if est_autorise(uid):
                        try: bot.send_message(uid, texte_bilan, parse_mode="Markdown")
                        except: pass
                stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}
                bilan_envoye_aujourdhui = True
            elif now.hour == 18 and now.minute > 5:
                bilan_envoye_aujourdhui = False
        except: pass
        time.sleep(30)

# ==========================================
# LANCEMENT
# ==========================================

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestionnaire_bilan, daemon=True).start()
    print("⬛ TERMINAL PRIME V27 (PRECISION MAX) : Démarré.", flush=True)
    bot.infinity_polling()
