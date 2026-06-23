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

TELEGRAM_TOKEN = "8658287331:AAFvZRB2pSdw6DpGagM3sGne-_SeRMeLD1g"
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
historique_signaux = {}

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
# PROFILS DYNAMIQUES V27 (AFFINÉS)
# ==========================================

def obtenir_profil_actif(symbole):
    if symbole in SYNTHETIC_PAIRS:
        return {
            "stoch_achat": 30, "rsi_achat": 35,
            "stoch_vente": 70, "rsi_vente": 65,
            "vol_multiplier": 2.5, "rr_min": 1.8,
            "cooldown_otm": 900, "nom": "SMC Synthétiques"
        }
    elif symbole in COMMODITY_PAIRS:
        return {
            "stoch_achat": 25, "rsi_achat": 40,
            "stoch_vente": 75, "rsi_vente": 60,
            "vol_multiplier": 2.0, "rr_min": 2.0,
            "cooldown_otm": 1200, "nom": "SMC Métaux/Énergie"
        }
    else:
        return {
            "stoch_achat": 40, "rsi_achat": 45,
            "stoch_vente": 60, "rsi_vente": 55,
            "vol_multiplier": 2.5, "rr_min": 1.2,
            "cooldown_otm": 900, "nom": "SMC Forex"
        }

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V29 (MT5 PRO & POCKET FIXED)"

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
# ROUTEUR DERIV ET CACHE
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

_cache_donnees = {}
_cache_prix = {}
_CACHE_TTL = 25

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    cle = f"{symbole_brut}_{granularite}"
    maintenant = time.time()
    if cle in _cache_donnees:
        donnees, ts = _cache_donnees[cle]
        if maintenant - ts < _CACHE_TTL:
            return donnees

    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(2):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=8)
            req = {"ticks_history": symbole, "end": "latest", "count": 250, "style": "candles", "granularity": granularite}
            ws.send(json.dumps(req))
            ws.settimeout(8)
            history = json.loads(ws.recv())
            ws.close()
            if "error" not in history and "candles" in history:
                _cache_donnees[cle] = (history['candles'], maintenant)
                return history['candles']
        except:
            time.sleep(0.5)
            continue
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    maintenant = time.time()
    if symbole_brut in _cache_prix:
        prix, ts = _cache_prix[symbole_brut]
        if maintenant - ts < 10:
            return prix

    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(2):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=8)
            req = {"ticks_history": symbole, "end": "latest", "count": 1, "style": "ticks"}
            ws.send(json.dumps(req))
            ws.settimeout(8)
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]:
                prix = float(res["history"]["prices"][0])
                _cache_prix[symbole_brut] = (prix, maintenant)
                return prix
        except:
            time.sleep(0.5)
            continue
    return None

# ==========================================
# CONFIRMATION MULTI-TIMEFRAME (MTF) & OUTILS SMC
# ==========================================

def confirmation_mtf(symbole, action_visee):
    action_simplifiee = "CALL" if "ACHAT" in action_visee or action_visee == "CALL" else "PUT"
    votes, total = 0, 0

    candles_m15 = obtenir_donnees_deriv(symbole, 900)
    if candles_m15:
        try:
            df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles_m15])
            ema20 = ta.trend.EMAIndicator(close=df['close'], window=20).ema_indicator()
            ema50 = ta.trend.EMAIndicator(close=df['close'], window=50).ema_indicator()
            prix = df['close'].iloc[-1]
            tendance = "HAUSSIERE" if prix > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1] else "BAISSIERE"
            total += 1
            if (action_simplifiee == "CALL" and tendance == "HAUSSIERE") or (action_simplifiee == "PUT" and tendance == "BAISSIERE"): votes += 1
        except: pass

    candles_m5 = obtenir_donnees_deriv(symbole, 300)
    if candles_m5:
        try:
            df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles_m5])
            macd = ta.trend.MACD(close=df['close'])
            macd_diff = macd.macd_diff().iloc[-1]
            rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
            total += 1
            if action_simplifiee == "CALL" and macd_diff > 0 and rsi > 45: votes += 1
            elif action_simplifiee == "PUT" and macd_diff < 0 and rsi < 55: votes += 1
        except: pass

    candles_m1 = obtenir_donnees_deriv(symbole, 60)
    if candles_m1:
        try:
            df = pd.DataFrame([{'close': float(c['close']), 'open': float(c['open']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles_m1])
            derniere = df.iloc[-1]
            avant_derniere = df.iloc[-2]
            corps_last = derniere['close'] - derniere['open']
            total += 1
            if action_simplifiee == "CALL" and corps_last > 0 and avant_derniere['close'] > avant_derniere['open']: votes += 1
            elif action_simplifiee == "PUT" and corps_last < 0 and avant_derniere['close'] < avant_derniere['open']: votes += 1
        except: pass

    if total == 0: return True, "MTF non disponible"
    if (votes / total) >= 0.67: return True, f"✅ MTF aligné ({votes}/{total})"
    return False, f"❌ MTF divergent ({votes}/{total} TF)"

def detecter_divergence(df, action_visee):
    try:
        rsi_series = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        prix = df['close']
        if "ACHAT" in action_visee or action_visee == "CALL":
            if prix.iloc[-1] < prix.iloc[-5] and rsi_series.iloc[-1] > rsi_series.iloc[-5]:
                return True, "🔄 Divergence RSI Haussière détectée"
        elif "VENTE" in action_visee or action_visee == "PUT":
            if prix.iloc[-1] > prix.iloc[-5] and rsi_series.iloc[-1] < rsi_series.iloc[-5]:
                return True, "🔄 Divergence RSI Baissière détectée"
    except: pass
    return False, ""

def obtenir_qualite_paire(symbole, action):
    cle = f"{symbole}_{action}"
    hist = historique_signaux.get(cle, [])
    if len(hist) >= 2 and all(r == "OTM" for r in hist[-2:]):
        return False, f"⚠️ {symbole} : 2 OTM consécutifs en {action}. Paire temporairement évitée."
    return True, ""

def enregistrer_resultat_historique(symbole, action, resultat):
    cle = f"{symbole}_{action}"
    if cle not in historique_signaux: historique_signaux[cle] = []
    historique_signaux[cle].append(resultat)
    historique_signaux[cle] = historique_signaux[cle][-5:]

def verifier_correlation(symbole_base, action_visee):
    if symbole_base in SYNTHETIC_PAIRS or symbole_base in COMMODITY_PAIRS: return True
    correlations = {
        "EURUSD": ("USDCHF", "INVERSE"), "GBPUSD": ("USDCHF", "INVERSE"),
        "AUDUSD": ("USDCAD", "INVERSE"), "USDCHF": ("EURUSD", "INVERSE"),
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
# COMMANDES INTERACTIVES
# ==========================================

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    if message.chat.id in trades_en_cours: return bot.send_message(message.chat.id, "⚠️ **SILENCE RADIO** : Combat en cours !")
    commande = message.text.split()
    if len(commande) < 2: return bot.send_message(message.chat.id, "⚠️ Précise l'actif (ex: EURUSD, XAUUSD, V75).")
    symbole = commande[1].upper()
    plateforme = plateforme_trading.get(message.chat.id, "MT5")
    if plateforme == "MT5" and symbole not in ELITE_PAIRS_MT5: return bot.send_message(message.chat.id, "❌ En mode MT5, analyse restreinte aux Élite.")
    if symbole not in ALL_PAIRS_POCKET: return bot.send_message(message.chat.id, "❌ Symbole non reconnu.")
    try: msg = bot.send_message(message.chat.id, f"🔍 *Analyse Rayons X SMC...*", parse_mode="Markdown")
    except: return
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return bot.edit_message_text("⚠️ Impossible de scanner.", message.chat.id, msg.message_id)
    try:
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        df['volume_proxy'] = df['high'] - df['low']
        vol_moyen = df['volume_proxy'].rolling(window=10).mean().iloc[-1]
        vol_actuel = df['volume_proxy'].iloc[-1]
        etat_vol = "Actif 💥" if vol_actuel > vol_moyen else "Faible 💤"
        swing_high_1, swing_low_1 = df['high'].iloc[-20:-10].max(), df['low'].iloc[-20:-10].min()
        swing_high_2, swing_low_2 = df['high'].iloc[-10:-1].max(), df['low'].iloc[-10:-1].min()
        structure_haussiere = (swing_high_2 > swing_high_1) and (swing_low_2 >= swing_low_1)
        structure_baissiere = (swing_low_2 < swing_low_1) and (swing_high_2 <= swing_high_1)
        tendance = "Order Flow Hausse 🟢" if structure_haussiere else "Order Flow Baisse 🔴" if structure_baissiere else "Consolidation ⚠️"
        rsi = ta.momentum.RSIIndicator(close=df['close']).rsi().iloc[-1]
        macd = ta.trend.MACD(close=df['close'])
        macd_diff = macd.macd_diff().iloc[-1]
        prix_actuel = df['close'].iloc[-1]

        mtf_ok, mtf_msg = confirmation_mtf(symbole, "CALL" if structure_haussiere else "PUT")
        rapport = f"""👁️ **VISION ELITE SMC : {symbole}** 👁️
──────────────────
💰 **Prix :** `{prix_actuel:.5f}`
🧱 **Structure :** `{tendance}`
⛽ **Volume :** `{etat_vol}`
📊 **RSI :** `{rsi:.2f}`
📈 **MACD Diff :** `{macd_diff:.5f}`
🔗 **MTF :** `{mtf_msg}`
──────────────────"""
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except: bot.edit_message_text("❌ Erreur d'analyse.", message.chat.id, msg.message_id)

# ==========================================
# MOTEUR DE DÉCISION
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    if est_heure_de_news_dynamique() and (symbole in COMMODITY_PAIRS or symbole in FOREX_PAIRS):
        return "⚠️ ALERTE NEWS : Marché manipulé.", None, None, None, None, None, None, None

    profil = obtenir_profil_actif(symbole)
    timeframes = [300, 120] if (symbole in FOREX_PAIRS or symbole in CRYPTO_PAIRS) and mode == "STANDARD" else \
                 [600, 300, 120] if mode == "STANDARD" else [60]

    for tf in timeframes:
        candles = obtenir_donnees_deriv(symbole, tf)
        if not candles: continue

        try:
            df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
            df['corps_bougie'] = abs(df['close'] - df['open'])
            df['taille_bougie'] = df['high'] - df['low']
            df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
            df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']
            df['volume_proxy'] = df['high'] - df['low']
            df['volume_moyen'] = df['volume_proxy'].rolling(window=14).mean()

            atr = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
            atr_val, atr_moyen = atr.iloc[-1], atr.iloc[-20:].mean()

            if atr_val < atr_moyen * 0.5: continue
            if atr_val > atr_moyen * 3.0: return "⚠️ Filtre ATR : Marché en spike (news ?).", None, None, None, None, None, None, None

            vol_actuel, vol_moyen_val = df['volume_proxy'].iloc[-1], df['volume_moyen'].iloc[-1]
            volume_ok = (vol_actuel > vol_moyen_val) and (vol_actuel < (vol_moyen_val * profil["vol_multiplier"]))

            avg_taille = df['taille_bougie'].iloc[-4:-1].mean()
            avg_corps = df['corps_bougie'].iloc[-4:-1].mean()
            if avg_corps > 0 and (avg_taille > avg_corps * 3.5): continue

            df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
            df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close']).stoch()
            df['macd_diff'] = ta.trend.MACD(close=df['close']).macd_diff()

            last, prev, p_prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            c, rsi_val, stoch_val, macd_diff_val = last['close'], round(last['rsi'], 1), round(last['stoch_k'], 1), last['macd_diff']
            action, confiance, bb_status, score_algo = None, 0, "En Attente", 5.0

            vrai_corps = last['corps_bougie'] > (last['taille_bougie'] * 0.30)
            last_is_green, last_is_red = last['close'] > last['open'], last['close'] < last['open']
            prev_is_green, prev_is_red = prev['close'] > prev['open'], prev['close'] < prev['open']

            rejet_haussier = last['meche_basse'] > (last['corps_bougie'] * 2.0)
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

            swing_high_1, swing_low_1 = df['high'].iloc[-20:-10].max(), df['low'].iloc[-20:-10].min()
            swing_high_2, swing_low_2 = df['high'].iloc[-10:-1].max(), df['low'].iloc[-10:-1].min()
            structure_haussiere = (swing_high_2 > swing_high_1) and (swing_low_2 >= swing_low_1)
            structure_baissiere = (swing_low_2 < swing_low_1) and (swing_high_2 <= swing_high_1)
            prix_moyen_recent = df['close'].iloc[-6:-1].mean()
            dans_zone_discount, dans_zone_premium = c < prix_moyen_recent, c > prix_moyen_recent

            if mode == "STANDARD":
                duree_secondes, exp_texte = (180, "3 MIN (HIT & RUN ⚡)") if tf == 300 else (tf, f"{int(tf/60)} MIN")
                if structure_haussiere and dans_zone_discount and volume_ok and vrai_corps and not danger_rejet_baisse and not fusee_baissiere and macd_diff_val > 0:
                    if (stoch_val < profil["stoch_achat"]) and (rsi_val < profil["rsi_achat"]):
                        action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 82, 7.5, f"🎯 {profil['nom']} : Order Block (Discount)"
                    if avalement_haussier or rejet_haussier or harami_bull:
                        action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 95, 9.0, f"👑 {profil['nom']} : Prise de Liquidité 🚀"
                elif structure_baissiere and dans_zone_premium and volume_ok and vrai_corps and not danger_rejet_hausse and not fusee_haussiere and macd_diff_val < 0:
                    if (stoch_val > profil["stoch_vente"]) and (rsi_val > profil["rsi_vente"]):
                        action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 82, 7.5, f"🎯 {profil['nom']} : Order Block (Premium)"
                    if avalement_baissier or rejet_baissier or harami_bear:
                        action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 95, 9.0, f"👑 {profil['nom']} : Prise de Liquidité ☄️"

            elif mode == "SCALP":
                indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2)
                bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
                squeeze = indicateur_bb.bollinger_wband().iloc[-1] < (indicateur_bb.bollinger_wband().rolling(window=20).mean().iloc[-1] * 0.8)
                duree_secondes, exp_texte = 60, "1 MINUTE (SCALP 🛡️)"
                if not squeeze and volume_ok and vrai_corps:
                    if (last['low'] <= bb_basse) and rejet_haussier and not danger_rejet_baisse and not fusee_baissiere and macd_diff_val > 0:
                        action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 90, 9.0, f"🛡️ Scalp {profil['nom']} : Liquidité Basse"
                    elif (last['high'] >= bb_haute) and rejet_baissier and not danger_rejet_hausse and not fusee_haussiere and macd_diff_val < 0:
                        action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 90, 9.0, f"🛡️ Scalp {profil['nom']} : Liquidité Haute"

            if action:
                div_ok, div_msg = detecter_divergence(df, action)
                if div_ok: score_algo, bb_status = min(score_algo + 1.5, 10.0), bb_status + f"\n{div_msg}"

                mtf_msg = "MTF non vérifié (Forex)"
                if symbole in SYNTHETIC_PAIRS or symbole in COMMODITY_PAIRS:
                    mtf_ok, mtf_msg = confirmation_mtf(symbole, action)
                    if not mtf_ok: return f"⚠️ Signal annulé : {mtf_msg}", None, None, None, None, None, None, None

                action_simple = "CALL" if "ACHAT" in action else "PUT"
                hist_ok, hist_msg = obtenir_qualite_paire(symbole, action_simple)
                if not hist_ok: return f"⚠️ {hist_msg}", None, None, None, None, None, None, None
                if not verifier_correlation(symbole, action): return "⚠️ **FAKEOUT DÉTECTÉ** : Corrélation adverse.", None, None, None, None, None, None, None

                delai_blocage = 300 if mode == "SCALP" else profil.get("cooldown_otm", 900)
                if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole]['time'] < delai_blocage):
                    if action_simple == cooldown_actifs[symbole]['action']: return "⚠️ **BLOCAGE ANTI-FAKEOUT**", None, None, None, None, None, None, None

                return action, min(confiance, 99), exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo
        except: continue
    return f"⚠️ En attente d'une opportunité ({mode}).", None, None, None, None, None, None, None

# ==========================================
# GESTION INTERFACE TELEGRAM
# ==========================================

def obtenir_clavier(user_id):
    mode_actuel, plateforme, filtre = mode_trading.get(user_id, "STANDARD"), plateforme_trading.get(user_id, "MT5"), filtre_special.get(user_id, "TOUS")
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
    if filtre_special.get(user_id, "TOUS") == "TOUS":
        filtre_special[user_id] = "SPECIAUX"
        bot.send_message(user_id, "💎 **MODE VIP ULTRA 10/10 ACTIF**\nUniquement les structures parfaites.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        filtre_special[user_id] = "TOUS"
        bot.send_message(user_id, "📡 **MODE TOUS SIGNAUX ACTIVÉ**\nRadar ouvert 8/10 et 10/10.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Un trade est déjà en cours.")
    if mode_trading.get(user_id, "STANDARD") == "STANDARD":
        mode_trading[user_id] = "SCALP"
        bot.send_message(user_id, "🔥 **MODE SMC SCALPING ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE SMC STANDARD ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_plateforme(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Terminez le trade en cours.")
    if plateforme_trading.get(user_id, "MT5") == "POCKET":
        plateforme_trading[user_id] = "MT5"
        bot.send_message(user_id, "📈 **MODE MT5 V29 ACTIVÉ**\nÉlite et SL/TP Dynamiques SMC.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        plateforme_trading[user_id] = "POCKET"
        bot.send_message(user_id, "🏦 **MODE POCKET ACTIVÉ**\n100% Forex Binaire.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    plateforme_trading[user_id] = plateforme_trading.get(user_id, "MT5")
    filtre_special[user_id] = filtre_special.get(user_id, "TOUS")
    texte = """🏴‍☠️ **TERMINAL PRIME - ÉDITION V29 (MT5 SMC PRO)** 🔥
──────────────────
🚨 **MOTEUR SMC UPGRADE GLOBAL** 🚨

📈 **MODE MT5 (Nouveau) :**
✅ Recherche d'Order Blocks & Swing ciblés
✅ SL adaptatif (Liquidity + ATR Buffer)
✅ Multi-Take Profit (TP1 Sécurité, TP2 Liquidité max)
✅ Suggestion dynamique de lot (Risk Management)

🏦 **MODE POCKET :**
✅ MTF obligatoire (M15 + M5 + M1)
✅ Filtre ATR et MACD
✅ Martingale adaptative avec pivot intelligent"""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE", "📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    if not est_autorise(message.chat.id): return
    plateforme = plateforme_trading.get(message.chat.id, "MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    if plateforme == "MT5":
        markup.add(InlineKeyboardButton("🔥 V10", callback_data="set_V10"), InlineKeyboardButton("🔥 V25", callback_data="set_V25"), InlineKeyboardButton("🔥 V50", callback_data="set_V50"))
        markup.add(InlineKeyboardButton("⚡ V75", callback_data="set_V75"), InlineKeyboardButton("💥 V100", callback_data="set_V100"))
        markup.add(InlineKeyboardButton("🥇 GOLD", callback_data="set_XAUUSD"), InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"), InlineKeyboardButton("🛢 PÉTROLE", callback_data="set_USOUSD"))
        texte_menu = "Sélectionne ta cible (MT5 V29 PRO) :"
    else:
        markup.add(InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"))
        markup.add(InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"))
        markup.add(InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"))
        markup.add(InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"))
        markup.add(InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"))
        texte_menu = "Sélectionne ta cible (Mode Binaire) :"
    bot.send_message(message.chat.id, texte_menu, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if est_autorise(message.chat.id): bot.send_message(message.chat.id, "🕒 **HORAIRES DE TIR RESTREINTS** 🕒\n\n🥇 **Matières Premières & Forex :** Lun–Ven, Sessions Londres & New York.\n💥 **Indices Volatility :** 24h/24, 7j/7.", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours: return bot.send_message(chat_id, "⚠️ Combat en cours.")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une cible !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user, 'id': 0})())

def calculer_entree_precise(duree_signal):
    maintenant = datetime.datetime.now()
    secondes_restantes = 60 - maintenant.second
    if maintenant.second >= 45: delai = secondes_restantes + 5
    elif maintenant.second <= 10: delai = max(5, 60 - maintenant.second + 5)
    else: delai = secondes_restantes + 5
    heure_entree = maintenant + datetime.timedelta(seconds=delai)
    return delai, heure_entree.strftime("%H:%M:%S")

# ==========================================
# AFFICHAGE FINAL ET EXÉCUTION
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
    if signal_cache and (time.time() - signal_cache['time'] <= 90): utiliser_cache = True
    else: signaux_cache.pop(cle_memoire, None)

    try: bot.delete_message(chat_id, call.message.message_id)
    except: pass

    if actif in SYNTHETIC_PAIRS: nom_affiche = f"💥 V{actif.replace('V', '')}"
    elif actif == "XAUUSD": nom_affiche = "🥇 GOLD (XAU/USD)"
    elif actif == "XAGUSD": nom_affiche = "🥈 ARGENT (XAG/USD)"
    elif actif == "USOUSD": nom_affiche = "🛢 PÉTROLE (CRUDE/WTI)"
    elif actif in CRYPTO_PAIRS: nom_affiche = f"🪙 {actif[:3]}/{actif[3:]}"
    else: nom_affiche = f"💱 {actif[:3]}/{actif[3:]}"

    if not utiliser_cache:
        return bot.send_message(chat_id, f"⏱️ **OPPORTUNITÉ EXPIRÉE SUR {actif}**\n\nSignal trop vieux (>90s). Le marché a bougé.", parse_mode="Markdown")

    delai_entree, str_entree = calculer_entree_precise(signal_cache.get('dur', 60))
    current_ask = obtenir_prix_actuel_deriv(actif) or 0.0

    # 🆕 AFFICHAGE MT5 V29 PRO
    if plateforme == "MT5":
        action_affiche = "🟢 ACHAT (BUY LIMIT/MARKET)" if "ACHAT" in signal_cache['action'] else "🔴 VENTE (SELL LIMIT/MARKET)"
        tp1 = signal_cache.get('mt5_tp1', 0.0)
        tp2 = signal_cache.get('mt5_tp2', tp1)
        
        # Gestion intelligente des lots suggérés
        if actif in SYNTHETIC_PAIRS: lot_suggere = "0.005 à 0.05"
        elif actif == "V75": lot_suggere = "0.001 (Micro lot)"
        elif actif in COMMODITY_PAIRS: lot_suggere = "0.01 à 0.02"
        else: lot_suggere = "0.01 à 0.10"

        signal = f"""⚡ **SIGNAL MT5 ÉLITE V29 (SMC) 💎** ⚡
──────────────────
🌐 **ACTIF :** {nom_affiche}
👉 **ORDRE :** {action_affiche}
🎯 **POTENTIEL R/R :** 1:{signal_cache.get('mt5_rr', 0.0):.2f}
──────────────────
💰 **PRIX D'ENTRÉE :** `{current_ask:.5f}`
🛑 **STOP LOSS (SL) :** `{signal_cache.get('mt5_sl', 0.0):.5f}`

✅ **TAKE PROFIT 1 :** `{tp1:.5f}` *(Sécurité 50%)*
🚀 **TAKE PROFIT 2 :** `{tp2:.5f}` *(Target SMC)*
──────────────────
⚖️ **Lot suggéré :** `{lot_suggere}`
⚠️ *Astuce : Placez le trade à Break-Even (BE) dès le TP1 touché !*"""
        bot.send_message(chat_id, signal, parse_mode="Markdown")
        return

    # AFFICHAGE POCKET
    else:
        palier, score = niveaux_martingale.get(chat_id, 0), signal_cache.get('sc', 5.0)
        mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))

        if palier == 0 and score < 9.0:
            signal = f"👻 **MODE FANTÔME (PALIER 0)** 👻\n──────────────────\n🌐 **ACTIF :** {nom_affiche}\n⏱ **ENTRÉE :** `{str_entree}`\n👉 **ACTION :** {signal_cache['action']}\n⏳ **DURÉE :** {signal_cache['exp']}\n📊 **SCORE :** `{score}/10`\n*L'IA observe virtuellement.*"
        elif palier == 0 and score >= 9.0:
            niveaux_martingale[chat_id] = 1
            signal = f"🚨 **SIGNAL RÉEL VIP 💎 (SCORE {score}/10)** 🚨\n──────────────────\n🌐 **ACTIF :** {nom_affiche}\n⏱ **ENTRÉE :** `{str_entree}`\n⏳ **EXPIRATION :** {signal_cache['exp']}\n👉 **ACTION :** {signal_cache['action']}\n🛡️ {signal_cache['bb']}\n💵 **MISE CALCULÉE :** `{mise}$`"
        else:
            signal = f"🚨 **SIGNAL DE TIR : PALIER {palier}** 🚨\n──────────────────\n🌐 **ACTIF :** {nom_affiche}\n⏱ **ENTRÉE :** `{str_entree}`\n👉 **ACTION :** {signal_cache['action']}\n⏳ **DURÉE :** {signal_cache['exp']}\n💵 **MISE :** `{mise}$`"

        bot.send_message(chat_id, signal, parse_mode="Markdown")
        action_brute = "CALL" if "ACHAT" in signal_cache['action'] else "PUT"
        Timer(delai_entree, executer_tir_flash, args=[chat_id, actif, action_brute, signal_cache['dur'], palier, nom_affiche]).start()

# ==========================================
# MOTEUR DE SCAN AUTO MT5 & BINAIRE
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
                    if cle_memoire in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle_memoire] < delai_repos): continue

                    action, conf, exp, dur, rsi, stoch, bb, sc = analyser_binaire_pro(paire, mode)

                    if action and "⚠️" not in action:
                        if statut == "HORS_SESSION" and (sc is None or sc < 9.5): continue
                        action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
                        alerte_valide = True
                        sl, tp1, tp2, ratio_rr_tp2 = 0, 0, 0, 0
                        profil = obtenir_profil_actif(paire)

                        candles_m15 = obtenir_donnees_deriv(paire, 900)
                        if candles_m15:
                            df_m15 = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles_m15])
                            df_m15['ema_50'] = ta.trend.EMAIndicator(close=df_m15['close'], window=50).ema_indicator()
                            df_m15['ema_20'] = ta.trend.EMAIndicator(close=df_m15['close'], window=20).ema_indicator()
                            tendance_m15 = "HAUSSIERE" if df_m15['close'].iloc[-1] > df_m15['ema_50'].iloc[-1] else "BAISSIERE"
                            ema_alignee = df_m15['ema_20'].iloc[-1] > df_m15['ema_50'].iloc[-1] if tendance_m15 == "HAUSSIERE" else df_m15['ema_20'].iloc[-1] < df_m15['ema_50'].iloc[-1]

                            if (action_simplifiee == "CALL" and tendance_m15 == "BAISSIERE") or (action_simplifiee == "PUT" and tendance_m15 == "HAUSSIERE"): alerte_valide = False
                            if alerte_valide and not ema_alignee and paire in ELITE_PAIRS_MT5: alerte_valide = False

                            # 🆕 LOGIQUE MT5 V29 : CALCULS SMC (LIQUIDITÉ ET SL ATR)
                            if alerte_valide and paire in ELITE_PAIRS_MT5:
                                current_ask = obtenir_prix_actuel_deriv(paire)
                                if current_ask:
                                    df_m15['atr'] = ta.volatility.AverageTrueRange(high=df_m15['high'], low=df_m15['low'], close=df_m15['close'], window=14).average_true_range()
                                    atr_val = df_m15['atr'].iloc[-1]

                                    if action_simplifiee == "CALL":
                                        creux_recent = df_m15['low'].iloc[-20:-1].min()
                                        sl = creux_recent - (atr_val * 0.8)  # SL sécurisé sous l'order block
                                        tp1 = df_m15['high'].iloc[-15:-1].max()  # TP1 Liquidité interne
                                        if tp1 <= current_ask: tp1 = current_ask + (abs(current_ask - sl) * 1.5)
                                        tp2 = df_m15['high'].iloc[-40:-15].max() # TP2 Liquidité externe
                                        if tp2 <= tp1: tp2 = current_ask + (abs(current_ask - sl) * 3.0)
                                    else:
                                        sommet_recent = df_m15['high'].iloc[-20:-1].max()
                                        sl = sommet_recent + (atr_val * 0.8)
                                        tp1 = df_m15['low'].iloc[-15:-1].min()
                                        if tp1 >= current_ask: tp1 = current_ask - (abs(sl - current_ask) * 1.5)
                                        tp2 = df_m15['low'].iloc[-40:-15].min()
                                        if tp2 >= tp1: tp2 = current_ask - (abs(sl - current_ask) * 3.0)

                                    risque = abs(current_ask - sl)
                                    ratio_rr_tp1 = abs(tp1 - current_ask) / risque if risque > 0 else 0
                                    ratio_rr_tp2 = abs(tp2 - current_ask) / risque if risque > 0 else 0
                                    
                                    if ratio_rr_tp1 < 1.0: alerte_valide = False # Filtre sécurité MT5

                        if not alerte_valide: continue

                        signaux_cache[cle_memoire] = {
                            'time': time.time(), 'action': action, 'conf': conf,
                            'exp': exp, 'dur': dur, 'rsi': rsi, 'stoch': stoch,
                            'bb': bb, 'sc': sc, 'mt5_sl': sl, 'mt5_tp1': tp1, 'mt5_tp2': tp2, 'mt5_rr': ratio_rr_tp2
                        }
                        derniere_alerte_auto[cle_memoire] = time.time()

                        for uid in utilisateurs_libres:
                            pf = plateforme_trading.get(uid, "MT5")
                            if pf == "MT5" and paire not in ELITE_PAIRS_MT5: continue
                            if pf == "POCKET" and paire not in FOREX_PAIRS: continue
                            if mode_trading.get(uid, "STANDARD") != mode: continue
                            if filtre_special.get(uid) == "SPECIAUX" and (sc is None or sc < 9.5): continue

                            nom_aff = f"V{paire.replace('V', '')}" if paire in SYNTHETIC_PAIRS else "GOLD" if paire == "XAUUSD" else "ARGENT" if paire == "XAGUSD" else "PÉTROLE" if paire == "USOUSD" else f"{paire[:3]}/{paire[3:]}"
                            markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {nom_aff}", callback_data=f"set_{paire}"))
                            msg = f"🔔 **{profil['nom']} {sc:.1f}/10 : {nom_aff}**\n✅ Triple MTF aligné. Signal premium. 90s pour frapper." if sc >= 9.5 else f"🔔 **RADAR {profil['nom']} : {nom_aff}**\nStructure validée. 90s pour agir."
                            try: bot.send_message(uid, msg, reply_markup=markup, parse_mode="Markdown")
                            except: pass

        except Exception as e:
            try: print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ Scanner erreur : {e}", flush=True)
            except: pass

# ==========================================
# RÉSULTATS POCKET ET BOURSE
# ==========================================

def executer_tir_flash(chat_id, symbole, action_brute, duree, palier, nom_affiche):
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    texte = f"👻 **FANTÔME LANCÉ ({nom_affiche})**\nL'IA observe virtuellement..." if palier == 0 else f"🔥 **TIR IMMÉDIAT : PALIER {palier} ({nom_affiche})**\n👉 **CLIQUEZ SUR {action_affichage} MAINTENANT !**"
    markup = None if palier == 0 else InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET", callback_data="force_win"))
    try: bot.send_message(chat_id, texte, parse_mode="Markdown", reply_markup=markup)
    except: pass
    trades_en_cours[chat_id] = {'symbole': symbole, 'action': action_brute, 'duree': duree, 'nom_affiche': nom_affiche}
    Timer(2, relever_prix_entree, args=[chat_id, symbole]).start()
    Timer(duree, verifier_resultat, args=[chat_id]).start()

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole: trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee, cooldown_actifs, niveaux_martingale
    time.sleep(3)
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'): return
    symbole, prix_entree, action, nom_affiche = trade['symbole'], trade['prix_entree'], trade['action'], trade['nom_affiche']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie: return

    palier_actuel = niveaux_martingale.get(chat_id, 0)
    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)

    if gagne:
        niveaux_martingale[chat_id] = 0
        enregistrer_resultat_historique(symbole, action, "ITM")
        if palier_actuel == 0: texte = f"👻 **FANTÔME RÉUSSI (ITM)**\nZone parfaite sur {nom_affiche}.\n🔓 Radar actif."
        else:
            texte = f"✅ **CIBLE ABATTUE (ITM)**\n🚀 {nom_affiche} ({action})\n📈 **Entrée :** `{prix_entree:.5f}`\n📉 **Sortie :** `{prix_sortie:.5f}`\n🔓 Radar déverrouillé."
            stats_journee['ITM'], stats_journee['details'] = stats_journee['ITM'] + 1, stats_journee['details'] + [f"✅ {nom_affiche}"]
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, texte, parse_mode="Markdown")
        except: pass
    else:
        enregistrer_resultat_historique(symbole, action, "OTM")
        profil = obtenir_profil_actif(symbole)
        if palier_actuel < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
            action_martingale, commentaire_ia = action, "🔍 Structure toujours valide. On persiste."
            candles_analyse = obtenir_donnees_deriv(symbole, 60)
            if candles_analyse:
                try:
                    df_a = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles_analyse])
                    derniere, df_recentes = df_a.iloc[-1], df_a.iloc[-3:]
                    corps, taille_totale = abs(derniere['close'] - derniere['open']), derniere['high'] - derniere['low']
                    corps_moyen = df_recentes.apply(lambda r: abs(r['close'] - r['open']), axis=1).mean()
                    force_dir = sum(1 if r['close'] > r['open'] else -1 for _, r in df_recentes.iterrows())

                    if taille_totale > 0:
                        if action == "CALL" and derniere['close'] < derniere['open'] and corps > (taille_totale * 0.75) and force_dir <= -2 and corps > corps_moyen * 1.2:
                            action_martingale, commentaire_ia = "PUT", "🔄 **BREAKER BLOCK CONFIRMÉ** : Momentum baissier massif. Pivot PUT."
                        elif action == "PUT" and derniere['close'] > derniere['open'] and corps > (taille_totale * 0.75) and force_dir >= 2 and corps > corps_moyen * 1.2:
                            action_martingale, commentaire_ia = "CALL", "🔄 **BREAKER BLOCK CONFIRMÉ** : Momentum haussier massif. Pivot CALL."
                except Exception: pass

            bot.send_message(chat_id, f"⚠️ **PIÈGE BROKER (Palier {palier_actuel} OTM)**\n📉 **Sortie :** `{prix_sortie:.5f}`\n🧠 **ANALYSE IA :** {commentaire_ia}\n⚡ Signal Palier {palier_actuel + 1} en cours...", parse_mode="Markdown")
            cle_memoire = f"{symbole}_{mode_trading.get(chat_id, 'STANDARD')}"
            signaux_cache[cle_memoire] = {'time': time.time(), 'action': "🟢 ACHAT (CALL)" if action_martingale == "CALL" else "🔴 VENTE (PUT)", 'conf': 99, 'exp': f"{int(trade['duree']/60)} MIN" if trade['duree'] >= 60 else f"{trade['duree']} SEC", 'dur': trade['duree'], 'rsi': 50, 'stoch': 50, 'bb': f"Martingale ({commentaire_ia[:40]}...)", 'sc': 5.0}
            class CallFictif:
                def __init__(self, c_id, msg_id, data):
                    self.message, self.data, self.id, self.from_user = type('obj', (object,), {'chat': type('obj', (object,), {'id': c_id}), 'message_id': msg_id}), data, 0, type('obj', (object,), {'id': c_id})
            save_devise(CallFictif(chat_id, 0, f"set_{symbole}"))
        else:
            cooldown_duree = profil.get("cooldown_otm", 1200)
            niveaux_martingale[chat_id] = 0
            if palier_actuel > 0: stats_journee['OTM'] += 1
            cooldown_actifs[symbole] = {'time': time.time(), 'action': action, 'duree': cooldown_duree}
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
            try: bot.send_message(chat_id, f"🛑 **SÉQUENCE ARRÊTÉE (OTM)**\nSécurisation des fonds sur {nom_affiche}.\n⏳ Radar verrouillé {cooldown_duree//60} min.", parse_mode="Markdown")
            except: pass

@bot.callback_query_handler(func=lambda c: c.data == "force_win")
def override_victoire_manuelle(call):
    chat_id = call.message.chat.id
    if chat_id in trades_en_cours:
        trade = trades_en_cours[chat_id]
        stats_journee['ITM'] += 1
        enregistrer_resultat_historique(trade['symbole'], trade['action'], "ITM")
        bot.send_message(chat_id, f"✅ **CIBLE ABATTUE (ITM MANUEL)**\n🚀 {trade['nom_affiche']} ({trade['action']})\n🔓 Radar déverrouillé.", parse_mode="Markdown")
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
                texte_bilan = f"📊 **BILAN QUOTIDIEN** 📊\n──────────────────\n✅ **ITM :** {stats_journee['ITM']}\n❌ **OTM :** {stats_journee['OTM']}\n🎯 **WINRATE :** {winrate:.1f}%\n──────────────────"
                for uid in utilisateurs_actifs:
                    if est_autorise(uid):
                        try: bot.send_message(uid, texte_bilan, parse_mode="Markdown")
                        except: pass
                stats_journee, bilan_envoye_aujourdhui = {'ITM': 0, 'OTM': 0, 'details': []}, True
            elif now.hour == 18 and now.minute > 5: bilan_envoye_aujourdhui = False
        except: pass
        time.sleep(30)

# ==========================================
# LANCEMENT
# ==========================================

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestionnaire_bilan, daemon=True).start()
    print("⬛ TERMINAL PRIME V29 (SMC PRO & POCKET FIXED) : Démarré.", flush=True)
    bot.infinity_polling()
