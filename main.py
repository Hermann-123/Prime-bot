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

# ⚠️ TON TOKEN TELEGRAM ACTUEL
TELEGRAM_TOKEN = "8658287331:AAFYN-L9J5kCXFGMOp2xaB_U5aig3-qalUE"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# 🔴 CONFIGURATION MARTINGALE SÉCURISÉE (Uniquement pour Pocket Broker)
COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3  

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
mode_trading = {} 
plateforme_trading = {} 
filtre_special = {} # 💎 NOUVEAU: Stocke le choix du filtre (TOUS ou SPECIAUX)
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
cooldown_actifs = {} 
niveaux_martingale = {} 

utilisateurs_autorises = {
    ADMIN_ID: "LIFETIME"
}
cles_generees = {}

stats_journee = {
    'ITM': 0, 
    'OTM': 0, 
    'details': []
}

# 🚀 NOUVEAU : LISTE DES INDICES SYNTHÉTIQUES (24/7)
SYNTHETIC_PAIRS = ["V10", "V25", "V50", "V75", "V100"]

CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD", 
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF", 
    "CADCHF", "EURCHF", "USDJPY"
]
COMMODITY_PAIRS = ["XAUUSD", "XAGUSD"] 

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V20.0 ULTIME (MT5 + Synthétiques 24/7)"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

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
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
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
        
        texte = f"✅ **CLÉ GÉNÉRÉE AVEC SUCCÈS**\n\n🔑 **Clé :** `{cle}`\n"
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
            texte = f"🎉 **ACCÈS TERMINAL PRIME DÉVERROUILLÉ !** 🎉\n\nBienvenue dans l'équipe.\n⏳ **Fin de l'abonnement :** {expiration_texte}\n\n👉 Tapez /start pour initialiser votre tableau de bord."
            bot.send_message(chat_id, texte, parse_mode="Markdown")
        else: bot.send_message(chat_id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")
    except: pass

# ==========================================
# VERROUILLAGE TEMPOREL
# ==========================================

def est_symbole_autorise(symbole):
    # 🚀 DÉBLOCAGE 24/7 POUR LES INDICES SYNTHÉTIQUES
    if symbole in SYNTHETIC_PAIRS:
        return "AUTORISE", ""

    now = datetime.datetime.utcnow()
    jour = now.weekday()
    heure = now.hour
    minute = now.minute
    heure_dec = heure + (minute / 60.0)

    est_week_end = False
    if jour == 4 and heure_dec >= 21.0: est_week_end = True
    elif jour == 5: est_week_end = True
    elif jour == 6 and heure_dec < 21.0: est_week_end = True

    if est_week_end:
        if symbole in CRYPTO_PAIRS: return "AUTORISE", ""
        else: return "BLOCAGE_TOTAL", f"🔒 **ACCÈS REFUSÉ** : Les marchés Forex et Matières Premières sont fermés le week-end."

    if symbole in CRYPTO_PAIRS:
        return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Les Cryptomonnaies sont verrouillées la semaine."

    if heure_dec >= 17.5: return "HORS_SESSION", f"🛑 **REPLI TACTIQUE** : Couvre-feu en cours (17h30 - 00h00 GMT)."
    
    if heure_dec >= 0.0 and heure_dec < 8.0:
        if symbole in ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD", "XAUUSD", "XAGUSD"]: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Session Asiatique."

    if heure_dec >= 7.0 and heure_dec < 12.0:
        paires = ["EURUSD", "EURJPY", "EURAUD", "EURCHF", "USDCHF", "CADCHF", "XAUUSD", "XAGUSD"]
        if heure_dec < 8.0: paires.extend(["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"])
        if symbole in paires: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Session Européenne."

    if heure_dec >= 12.0 and heure_dec < 17.5:
        if symbole in ["EURUSD", "USDCAD", "AUDUSD", "XAUUSD", "XAGUSD"]: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Zone de Guerre US/CA."

    return "BLOCAGE_TOTAL", "🛑 Erreur temporelle."

# ==========================================
# FONCTIONS PRO & ROUTEUR DERIV (MT5)
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
    if symbole_brut in SYNTHETIC_PAIRS:
        return f"R_{symbole_brut.replace('V', '')}" # Traduit V75 en R_75 pour l'API
    if symbole_brut in CRYPTO_PAIRS: return f"cry{symbole_brut}"
    if symbole_brut in COMMODITY_PAIRS: return f"frx{symbole_brut}" 
    return f"frx{symbole_brut}"

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 250, "style": "candles", "granularity": granularite}
            ws.send(json.dumps(req))
            history = json.loads(ws.recv())
            ws.close()
            if "error" not in history and "candles" in history: return history['candles']
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
            if "history" in res and "prices" in res["history"]: return float(res["history"]["prices"][0])
        except:
            time.sleep(1)
            continue
    return None

def verifier_correlation(symbole_base, action_visee):
    if symbole_base in SYNTHETIC_PAIRS: return True # Pas de corrélation pour les indices
    correlations = {"EURUSD": ("USDCHF", "INVERSE"), "GBPUSD": ("USDCHF", "INVERSE"), "AUDUSD": ("USDCAD", "INVERSE"), "USDCHF": ("EURUSD", "INVERSE"), "USDCAD": ("AUDUSD", "INVERSE")}
    if symbole_base not in correlations: return True 
    symbole_corr, type_corr = correlations[symbole_base]
    candles = obtenir_donnees_deriv(symbole_corr, 300)
    if not candles: return True 
    try:
        df_c = pd.DataFrame([{'close': float(c['close'])} for c in candles])
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

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    if message.chat.id in trades_en_cours: return bot.send_message(message.chat.id, "⚠️ **SILENCE RADIO** : Combat en cours !")
    commande = message.text.split()
    if len(commande) < 2: return bot.send_message(message.chat.id, "⚠️ Précise la devise ou l'indice.")
    symbole = commande[1].upper()
    try: msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X (SMC + Killswitch)...*", parse_mode="Markdown")
    except: return
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return bot.edit_message_text("⚠️ Impossible de scanner.", message.chat.id, msg.message_id)
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
        tendance = "Order Flow Hausse 🟢" if structure_haussiere else "Order Flow Baisse 🔴" if structure_baissiere else "Consolidation (Liquidity Build) ⚠️"

        rsi = ta.momentum.RSIIndicator(close=df['close']).rsi().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        rapport = f"👁️ **VISION RAYONS X SMC : {symbole}** 👁️\n──────────────────\n💰 **Prix :** `{prix_actuel:.5f}`\n🧱 **Structure (SMC) :** `{tendance}`\n⛽ **Volume/Tick :** `{etat_vol}`\n📊 **RSI :** `{rsi:.2f}`\n──────────────────"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except: bot.edit_message_text("❌ Erreur d'analyse.", message.chat.id, msg.message_id)

# ==========================================
# MOTEUR SMC DE BASE (POUR POCKET BROKER / DECLENCHEUR)
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS and symbole not in SYNTHETIC_PAIRS:
        return "⚠️ ALERTE NEWS : Marché manipulé.", None, None, None, None, None, None, None

    timeframes = [600, 300, 120] if mode == "STANDARD" else [60]
    
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
            df['volume_moyen'] = df['volume_proxy'].rolling(window=10).mean()
            
            vol_actuel = df['volume_proxy'].iloc[-1]
            vol_moyen = df['volume_moyen'].iloc[-1]
            volume_ok = (vol_actuel > vol_moyen) and (vol_actuel < (vol_moyen * 2.5))

            avg_taille = df['taille_bougie'].iloc[-4:-1].mean()
            avg_corps = df['corps_bougie'].iloc[-4:-1].mean()
            if avg_corps > 0 and (avg_taille > avg_corps * 3.5):
                return "⚠️ Filtre Anti-Chaos activé.", None, None, None, None, None, None, None

            df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
            df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close']).stoch()
            
            last, prev, p_prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            c = last['close']
            rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
            action, confiance, bb_status, score_algo = None, 0, "En Attente", 5
            
            vrai_corps = last['corps_bougie'] > (last['taille_bougie'] * 0.25)
            last_is_green = last['close'] > last['open']
            last_is_red = last['close'] < last['open']
            prev_is_green = prev['close'] > prev['open']
            prev_is_red = prev['close'] < prev['open']
            
            rejet_haussier = last['meche_basse'] > (last['corps_bougie'] * 1.5)
            rejet_baissier = last['meche_haute'] > (last['corps_bougie'] * 1.5)
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
                
                if structure_haussiere and dans_zone_discount and volume_ok and vrai_corps and not danger_rejet_baisse and not fusee_baissiere:
                    if (stoch_val < 40) and (rsi_val > 40): 
                        action, confiance, score_algo = "🟢 ACHAT (CALL)", 85, 8.0
                        bb_status = f"🎯 SMC : Order Block (Zone Discount)"
                    if avalement_haussier or rejet_haussier or harami_bull:
                        action, confiance, score_algo = "🟢 ACHAT (CALL)", 99, 10.0
                        bb_status = f"👑 SMC ULTIME : Prise de Liquidité 🚀"
                        
                elif structure_baissiere and dans_zone_premium and volume_ok and vrai_corps and not danger_rejet_hausse and not fusee_haussiere:
                    if (stoch_val > 60) and (rsi_val < 60):
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 85, 8.0
                        bb_status = f"🎯 SMC : Order Block (Zone Premium)"
                    if avalement_baissier or rejet_baissier or harami_bear:
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 99, 10.0
                        bb_status = f"👑 SMC ULTIME : Prise de Liquidité ☄️"

            elif mode == "SCALP":
                indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2)
                bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
                df['bb_width'] = indicateur_bb.bollinger_wband()
                squeeze = df['bb_width'].iloc[-1] < (df['bb_width'].rolling(window=20).mean().iloc[-1] * 0.8)

                duree_secondes, exp_texte = 60, "1 MINUTE (SCALP 🛡️)"
                
                if not squeeze and volume_ok and vrai_corps:
                    if (last['low'] <= bb_basse) and rejet_haussier and not danger_rejet_baisse and not fusee_baissiere:
                        action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 95, 9.5, "🛡️ SMC Scalp : Chasse aux Stops Bas"
                    elif (last['high'] >= bb_haute) and rejet_baissier and not danger_rejet_hausse and not fusee_haussiere:
                        action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 95, 9.5, "🛡️ SMC Scalp : Chasse aux Stops Haut"

            if action:
                if not verifier_correlation(symbole, action):
                    return f"⚠️ **FAKEOUT DÉTECTÉ**", None, None, None, None, None, None, None

                action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
                delai_blocage = 600 if mode == "SCALP" else 1800
                if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole]['time'] < delai_blocage):
                    if action_simplifiee == cooldown_actifs[symbole]['action']:
                        return f"⚠️ **BLOCAGE ANTI-FAKEOUT**", None, None, None, None, None, None, None
                
                return action, min(confiance, 99), exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo
                
        except: continue

    return f"⚠️ En attente d'une opportunité ({mode}).", None, None, None, None, None, None, None

# ==========================================
# GESTION DES SIGNAUX & DESIGN PREMIUM
# ==========================================

def obtenir_clavier(user_id):
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    plateforme = plateforme_trading.get(user_id, "MT5") # MT5 par défaut
    filtre = filtre_special.get(user_id, "TOUS")
    
    btn_mode = "🛡️ MODE: SMC STANDARD" if mode_actuel == "STANDARD" else "🔥 MODE: SMC SCALP"
    btn_plateforme = "🏦 BROKER: POCKET" if plateforme == "POCKET" else "📈 BROKER: MT5" # MAJ MT5
    btn_filtre = "💎 SIGNAUX: TOUS" if filtre == "TOUS" else "💎 SIGNAUX: SPÉCIAUX"
    
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
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
        bot.send_message(user_id, "💎 **MODE SPÉCIAL ACTIVÉ**\nSilence radio. Vous ne recevrez **que** les signaux 10/10 absolus (Prise de Liquidité Ultime).", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        filtre_special[user_id] = "TOUS"
        bot.send_message(user_id, "📡 **MODE TOUS SIGNAUX ACTIVÉ**\nLe radar est grand ouvert. Vous recevrez les signaux classiques (8/10) et les spéciaux.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Silence Radio actif.")
        
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    if mode_actuel == "STANDARD":
        mode_trading[user_id] = "SCALP"
        bot.send_message(user_id, "🔥 **MODE SMC SCALPING (1 MIN) ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE SMC STANDARD ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_plateforme(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Terminez votre trade en cours avant de changer de plateforme.")
        
    plateforme_actuelle = plateforme_trading.get(user_id, "MT5")
    if plateforme_actuelle == "POCKET":
        plateforme_trading[user_id] = "MT5"
        bot.send_message(user_id, "📈 **MODE META TRADER (MT5 PRO) ACTIVÉ**\nLe bot analyse maintenant la structure H4, H1 et calcule l'ATR pour des Stop Loss professionnels.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        plateforme_trading[user_id] = "POCKET"
        bot.send_message(user_id, "🏦 **MODE POCKET BROKER ACTIVÉ**\nLe bot générera des signaux binaires à expiration.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    plateforme_trading[user_id] = plateforme_trading.get(user_id, "MT5")
    filtre_special[user_id] = filtre_special.get(user_id, "TOUS")
    texte = """🏴‍☠️ **TERMINAL PRIME - V20.0 ULTIME 🔀** 🔥
    
Mise à jour activée : 🔀 **CERVEAU MT5 + INDICES SYNTHÉTIQUES (24/7)**. 
Utilisez le nouveau bouton 💎 pour filtrer uniquement les signaux parfaits (10/10)."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours:
        bot.answer_callback_query(call.id, f"⚠️ Focus activé !", show_alert=True)
        return
    
    actif = call.data.replace("set_", "")
    plateforme = plateforme_trading.get(chat_id, "MT5")
    statut, msg_erreur = est_symbole_autorise(actif)
    
    if statut == "BLOCAGE_TOTAL":
        bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
        return
        
    user_prefs[call.from_user.id] = actif
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    
    if actif in SYNTHETIC_PAIRS: nom_affiche = f"💥 Volatility {actif.replace('V', '')}"
    elif actif in COMMODITY_PAIRS: nom_affiche = f"{actif[:3]} (Gold)" if actif == "XAUUSD" else f"{actif[:3]} (Silver)"
    else: nom_affiche = f"{actif[:3]}/{actif[3:]}"
    
    try: msg = bot.send_message(chat_id, f"⏳ *Initialisation Scanner SMC ({plateforme})...*", parse_mode="Markdown")
    except: return
        
    # PREMIER SCAN RAPIDE
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif, mode_actuel)
    
    # GESTION DU FILTRE SPÉCIAL VIP
    if filtre_special.get(chat_id) == "SPECIAUX" and (score is None or score < 10.0):
        try: bot.edit_message_text(f"⏳ **MODE SPÉCIAL ACTIF**\nLe setup sur {nom_affiche} n'est pas un 10/10 parfait. L'IA ignore ce tir pour protéger votre capital.", chat_id, msg.message_id, parse_mode="Markdown")
        except: pass
        return

    if statut == "HORS_SESSION":
        if score is None or score < 10.0:
            try: bot.edit_message_text(f"{msg_erreur}\n\n*(Le setup n'est pas un 10/10 parfait pour forcer l'entrée)*", chat_id, msg.message_id, parse_mode="Markdown")
            except: pass
            return
    
    if not action or "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return

    # ==========================
    # BRANCHE 1 : META TRADER 5 (LOGIQUE INSTITUTIONNELLE MTF)
    # ==========================
    if plateforme == "MT5":
        try: bot.edit_message_text(f"⏳ *Radar Pro : Analyse Top-Down (H4 + H1) et calcul ATR sur {nom_affiche}...*", chat_id, msg.message_id, parse_mode="Markdown")
        except: pass
        
        # Le bot demande les bougies H4 (14400 sec) et H1 (3600 sec) à Deriv
        candles_h4 = obtenir_donnees_deriv(actif, 14400) # H4
        if not candles_h4: candles_h4 = obtenir_donnees_deriv(actif, 7200) # Fallback sur H2 si API Deriv bloque le H4 pur
        candles_h1 = obtenir_donnees_deriv(actif, 3600)  # H1
        current_ask = obtenir_prix_actuel_deriv(actif)

        if candles_h4 and candles_h1 and current_ask:
            df_h4 = pd.DataFrame([{'close': float(c['close'])} for c in candles_h4])
            df_h1 = pd.DataFrame([{'high': float(c['high']), 'low': float(c['low']), 'close': float(c['close'])} for c in candles_h1])
            
            # TENDANCE LOURDE (Filtre institutionnel H4)
            df_h4['ema_50'] = ta.trend.EMAIndicator(close=df_h4['close'], window=50).ema_indicator()
            tendance_h4 = "HAUSSIERE" if df_h4['close'].iloc[-1] > df_h4['ema_50'].iloc[-1] else "BAISSIERE"

            action_mt4 = "Achat" if "CALL" in action else "Vente"

            # VÉRIFICATION DE LA TENDANCE (Anti-Fakeout)
            if (action_mt4 == "Achat" and tendance_h4 == "BAISSIERE") or (action_mt4 == "Vente" and tendance_h4 == "HAUSSIERE"):
                try: bot.edit_message_text(f"🛑 **TRADE REJETÉ PAR L'IA (Filtre Pro)**\nLe signal est à {action_mt4.lower()}, mais la tendance lourde (H4) est {tendance_h4.lower()}. Risque de Fakeout.", chat_id, msg.message_id, parse_mode="Markdown")
                except: pass
                return

            # CALCUL DE L'ATR (Volatilité réelle pour la marge du SL)
            df_h1['atr'] = ta.volatility.AverageTrueRange(high=df_h1['high'], low=df_h1['low'], close=df_h1['close'], window=14).average_true_range()
            atr_val = df_h1['atr'].iloc[-1]
            
            # SL CHIRURGICAL BÂTI SUR LA STRUCTURE H1
            if action_mt4 == "Achat":
                creux_majeur_h1 = df_h1['low'].iloc[-15:-1].min() # Le vrai dernier creux H1
                sl_secu = creux_majeur_h1 - (atr_val * 0.2) # Marge de protection sous le creux (ATR)
                tp_secu = current_ask + ((current_ask - sl_secu) * 2) 
            else:
                sommet_majeur_h1 = df_h1['high'].iloc[-15:-1].max() # Le vrai dernier sommet H1
                sl_secu = sommet_majeur_h1 + (atr_val * 0.2) # Marge de protection au-dessus du sommet (ATR)
                tp_secu = current_ask - ((sl_secu - current_ask) * 2)

            avertissement_lot = "\n⚠️ **ATTENTION V75/V100 : LOT MAXIMUM DE 0.001 !**" if actif in SYNTHETIC_PAIRS else ""

            action_affiche = "🟢 BUY MARKET (ACHAT)" if action_mt4 == "Achat" else "🔴 SELL MARKET (VENTE)"
            signal = f"""📈 **SIGNAL INSTITUTIONNEL (MT5) 💎** 📈
──────────────────
🌐 **ACTIF :** {nom_affiche}
👉 **ORDRE :** {action_affiche}
🧠 **CONTEXTE :** Tendance Lourde (H4) Validée
🛡️ **STRUCTURE :** Stop Loss placé via ATR + Swing H1
──────────────────
💰 **PRIX D'ENTRÉE :** `{current_ask:.5f}`
🛑 **STOP LOSS (SL) :** `{sl_secu:.5f}`
✅ **TAKE PROFIT (TP) :** `{tp_secu:.5f}`
──────────────────
*(Ratio R/R : 1:2. Protection Anti-Fakeout active).* {avertissement_lot}"""
            try:
                bot.delete_message(chat_id, msg.message_id)
                bot.send_message(chat_id, signal, parse_mode="Markdown")
            except: pass
            return
        else:
            try: bot.edit_message_text("❌ Échec de la récupération des données H4/H1. Relancez l'analyse.", chat_id, msg.message_id)
            except: pass
            return

    # ==========================
    # BRANCHE 2 : POCKET BROKER (BINAIRE)
    # ==========================
    maintenant = datetime.datetime.now()
    sec_rest = (60 - maintenant.second)
    if mode_actuel == "SCALP" and sec_rest < 45: sec_rest += 60 
    elif mode_actuel == "STANDARD" and sec_rest < 15: sec_rest += 60

    palier = niveaux_martingale.get(chat_id, 0)
    
    if palier == 0 and score is not None and score >= 10.0:
        palier = 1 
        niveaux_martingale[chat_id] = 1 
        sec_rest += 60 
        if statut == "HORS_SESSION": 
            fantome_texte = "👑 **EXCEPTION 10/10 HORS SESSION !**\n*Prise de liquidité parfaite, on attaque en réel direct !*"
        else: 
            fantome_texte = "🧠 **FANTÔME DÉSACTIVÉ PAR L'IA SMC (10/10)**\n*Prise de liquidité parfaite, on attaque en réel direct !*"
    elif palier == 0:
        fantome_texte = "*Le bot prend ce trade virtuellement (Fantôme SMC). NE RENTREZ PAS.*"
    else:
        fantome_texte = ""

    heure_entree_p0 = maintenant + datetime.timedelta(seconds=sec_rest)
    str_p0 = heure_entree_p0.strftime("%H:%M:00")
    mise_calculee = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** (palier - 1 if palier > 0 else 0)))

    if palier == 0:
        signal = f"""👻 **MODE FANTÔME (PALIER 0)** 👻
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_p0}`
👉 **ACTION :** {action}
⏳ **DURÉE :** {exp_texte}

{fantome_texte}
──────────────────
*(Si échec, le bot générera instantanément le signal Palier 1)*"""
    else:
        signal = f"""🚨 **ALERTE DE TIR RÉEL VIP 💎** 🚨
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_p0}`
⏳ **EXPIRATION :** {exp_texte}
👉 **ACTION :** {action}
🛡️ {bb_status}

{fantome_texte if fantome_texte else ''}
💵 **MISE CALCULÉE :** `{mise_calculee}$`
*(Statut : Palier {palier})*"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except: pass

    action_brute = "CALL" if "ACHAT" in action else "PUT"
    Timer(sec_rest, executer_tir_flash, args=[chat_id, actif, action_brute, duree_secondes, palier]).start()

# === LES FONCTIONS DE RÉSULTATS BINAIRES SUIVENT ===
def executer_tir_flash(chat_id, symbole, action_brute, duree, palier):
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    nom_paire = f"{symbole[:3]}/{symbole[3:]}" if len(symbole) == 6 and symbole not in SYNTHETIC_PAIRS else symbole
    
    if palier == 0:
        texte = f"👻 **LE FANTÔME EST LANCÉ ({nom_paire})** 👻\nL'IA observe le marché virtuellement..."
        markup = None
    else:
        texte = f"🔥 **TIR IMMÉDIAT : PALIER {palier} ({nom_paire})** 🔥\n👉 **CLIQUEZ SUR {action_affichage} MAINTENANT !**"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET", callback_data="force_win"))
        
    try: bot.send_message(chat_id, texte, parse_mode="Markdown", reply_markup=markup)
    except: pass
    
    trades_en_cours[chat_id] = {'symbole': symbole, 'action': action_brute, 'duree': duree}
    Timer(2, relever_prix_entree, args=[chat_id, symbole]).start()
    Timer(duree, verifier_resultat, args=[chat_id]).start()

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole:
        trades_en_cours[chat_id]['prix_entree'] = prix

def preparer_nouveau_palier(chat_id, symbole, action_brute, duree, palier):
    nom_paire = f"{symbole[:3]}/{symbole[3:]}" if len(symbole) == 6 and symbole not in SYNTHETIC_PAIRS else symbole
    mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))
    exp_texte = f"{int(duree/60)} MIN" if duree >= 60 else f"{duree} SEC"
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    
    maintenant = datetime.datetime.now()
    sec_rest = 60 - maintenant.second
    if sec_rest < 15: sec_rest += 60 
    
    heure_entree = maintenant + datetime.timedelta(seconds=sec_rest)
    heure_texte = heure_entree.strftime("%H:%M:00")
    
    texte = f"🚨 **SIGNAL DE TIR : PALIER {palier}** 🚨\n"
    texte += f"──────────────────\n"
    texte += f"🌐 **ACTIF :** {nom_paire}\n"
    texte += f"⏱ **ENTRÉE EXACTE :** `{heure_texte}`\n"
    texte += f"👉 **ACTION :** {action_affichage}\n"
    texte += f"⏳ **DURÉE :** {exp_texte}\n"
    texte += f"💵 **MISE :** `{mise}$`\n"
    texte += f"──────────────────\n"
    texte += f"⏳ *Préparez le broker. L'IA enverra un flash pour valider le tir à la seconde 00.*"
    
    try: bot.send_message(chat_id, texte, parse_mode="Markdown")
    except: pass
    
    Timer(sec_rest, executer_tir_flash, args=[chat_id, symbole, action_brute, duree, palier]).start()

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
    palier_actuel = niveaux_martingale.get(chat_id, 0)
    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = f"{symbole[:3]}/{symbole[3:]}" if len(symbole) == 6 and symbole not in SYNTHETIC_PAIRS else symbole
    type_emoji = "🪙" if symbole in CRYPTO_PAIRS else "💥" if symbole in SYNTHETIC_PAIRS else "💱"

    if gagne:
        niveaux_martingale[chat_id] = 0 
        if palier_actuel == 0: texte = f"👻 **FANTÔME RÉUSSI (ITM)**\nLe trade virtuel sur {nom_paire} est passé sans nous.\n🔓 *Radar déverrouillé.*"
        else:
            texte = f"✅ **CIBLE ABATTUE (ITM)**\n🚀 {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n🔓 *Radar déverrouillé.*"
            stats_journee['ITM'] += 1
            stats_journee['details'].append(f"✅ {type_emoji} {nom_paire} ({action})")
            
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, texte, parse_mode="Markdown")
        except: pass
    else:
        if palier_actuel < MAX_MARTINGALE:
            candles_urgence = obtenir_donnees_deriv(symbole, trade['duree'])
            if candles_urgence:
                df_urg = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close'])} for c in candles_urgence])
                last_3 = df_urg.iloc[-3:]
                fusee_haussiere = all(row['close'] > row['open'] for _, row in last_3.iterrows())
                fusee_baissiere = all(row['close'] < row['open'] for _, row in last_3.iterrows())
                
                if (action == "PUT" and fusee_haussiere) or (action == "CALL" and fusee_baissiere):
                    niveaux_martingale[chat_id] = 0
                    msg_urgence = f"🛑 **KILLSWITCH ACTIVÉ (ANTI-FUSÉE)** 🛑\nLe marché a explosé contre nous de manière anormale sur {nom_paire}.\nMartingale annulée pour protéger le capital. Repli tactique immédiat."
                    stats_journee['OTM'] += 1
                    cooldown_actifs[symbole] = {'time': time.time(), 'action': action}
                    if chat_id in trades_en_cours: del trades_en_cours[chat_id]
                    try: bot.send_message(chat_id, msg_urgence, parse_mode="Markdown")
                    except: pass
                    return

            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id] 
            
            if palier_actuel == 0: 
                msg_fail = f"⚠️ **PIÈGE BROKER DÉTECTÉ (Fantôme Échoué)**\n📉 Sortie : `{prix_sortie}`\n\n⚡ *Génération instantanée du signal Palier 1...*"
            else: 
                msg_fail = f"⚠️ **TIR RATÉ (Palier {palier_actuel} Échoué)**\n📉 Sortie : `{prix_sortie}`\n\n⚡ *Génération instantanée du palier suivant...*"
                
            bot.send_message(chat_id, msg_fail, parse_mode="Markdown")
            preparer_nouveau_palier(chat_id, symbole, action, trade['duree'], palier_actuel + 1)
            
        else:
            niveaux_martingale[chat_id] = 0
            texte = f"🛑 **FIN DE SÉQUENCE ATTEINTE (OTM)**\n⚠️ {nom_paire} ({action})\n📉 Sortie : `{prix_sortie}`\nRepli tactique."
            if palier_actuel > 0: stats_journee['OTM'] += 1
            cooldown_actifs[symbole] = {'time': time.time(), 'action': action}
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
            try: bot.send_message(chat_id, texte, parse_mode="Markdown")
            except: pass

@bot.callback_query_handler(func=lambda c: c.data == "force_win")
def override_victoire_manuelle(call):
    chat_id = call.message.chat.id
    if chat_id in trades_en_cours:
        stats_journee['ITM'] += 1
        del trades_en_cours[chat_id]
    niveaux_martingale[chat_id] = 0
    bot.answer_callback_query(call.id, "✅ Victoire validée ! Le radar est libéré.", show_alert=True)
    try: bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except: pass
    bot.send_message(chat_id, "🔄 **CORRECTION MANUELLE APPLIQUÉE**", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **GUIDE DES HORAIRES (Verrouillage IA Actif)** 🕒
    
✅ **Session Asiatique (00h00 - 08h00) :** JPY, AUD, CAD, CHF, GOLD
🇪🇺 **Session Europe (07h00 - 12h00) :** EUR, USD, CHF, GOLD
🔥 **Zone de Guerre (12h00 - 17h30) :** EUR/USD, AUD/USD, USD/CAD, GOLD
🛑 **Repli Tactique (17h30 - 00h00) :** Le Forex est bloqué.
💥 **INDICES SYNTHÉTIQUES (V10, V75...) :** Ouverts 24h/24 & 7j/7."""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    
    # 💥 AJOUT DES INDICES SYNTHÉTIQUES ICI
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
        InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD"),
        InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
        InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
        InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
        InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"),
        InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
    )
    markup.add(
        InlineKeyboardButton("🥇 OR (XAU/USD)", callback_data="set_XAUUSD"), 
        InlineKeyboardButton("🥈 ARGENT (XAG/USD)", callback_data="set_XAGUSD")
    )
    bot.send_message(message.chat.id, "Sélectionne ta cible (Les Indices Synthétiques fonctionnent 24h/24) :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours: return bot.send_message(chat_id, f"⚠️ Combat en cours sur **{trades_en_cours[chat_id]['symbole']}**.", parse_mode="Markdown")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    
    statut, msg_erreur = est_symbole_autorise(actif)
    if statut == "BLOCAGE_TOTAL": return bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
        
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            utilisateurs_libres = [uid for uid in utilisateurs_actifs if est_autorise(uid) and uid not in trades_en_cours]
            if not utilisateurs_libres: continue
                
            for paire in SYNTHETIC_PAIRS + CRYPTO_PAIRS + FOREX_PAIRS + COMMODITY_PAIRS:
                statut, _ = est_symbole_autorise(paire)
                if statut == "BLOCAGE_TOTAL": continue
                    
                for mode in ["STANDARD", "SCALP"]:
                    delai_repos = 300 if mode == "STANDARD" else 120
                    cle_memoire = f"{paire}_{mode}"
                    if cle_memoire in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle_memoire] < delai_repos): continue
                        
                    action, conf, exp, dur, rsi, stoch, bb, sc = analyser_binaire_pro(paire, mode)
                    
                    if action and "⚠️" not in action:
                        if statut == "HORS_SESSION" and (sc is None or sc < 10.0): continue
                            
                        derniere_alerte_auto[cle_memoire] = time.time()
                        
                        for uid in utilisateurs_libres:
                            if mode_trading.get(uid, "STANDARD") == mode:
                                # GESTION DU FILTRE SPÉCIAL POUR LE SCANNER AUTO
                                if filtre_special.get(uid) == "SPECIAUX" and (sc is None or sc < 10.0):
                                    continue 

                                pf = plateforme_trading.get(uid, "MT5")
                                type_alerte = "📊 Verrouiller SMC" if pf == "POCKET" else "📈 Ordre MT5"
                                
                                if paire in SYNTHETIC_PAIRS: nom_paire_affiche = f"V{paire.replace('V', '')}"
                                elif paire in COMMODITY_PAIRS: nom_paire_affiche = f"{paire[:3]}"
                                else: nom_paire_affiche = f"{paire[:3]}/{paire[3:]}"
                                
                                markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {nom_paire_affiche}" if mode == "SCALP" else f"{type_alerte} {nom_paire_affiche}", callback_data=f"set_{paire}"))
                                
                                prefixe = "👑 **EXCEPTION SMC HORS SESSION** 👑\n" if statut == "HORS_SESSION" else ""
                                msg = f"{prefixe}🔔 **CHASSE AUX STOPS : {nom_paire_affiche}**\n👉 Dégaine !" if mode == "SCALP" else f"{prefixe}🔔 **ORDER BLOCK {exp} : {nom_paire_affiche}**"
                                try: bot.send_message(uid, msg, reply_markup=markup)
                                except: pass
        except Exception as e: pass

def gestionnaire_bilan():
    global stats_journee
    bilan_envoye_aujourdhui = False
    
    while True:
        try:
            now = datetime.datetime.utcnow()
            if now.hour == 18 and now.minute == 0:
                if not bilan_envoye_aujourdhui:
                    total_trades = stats_journee['ITM'] + stats_journee['OTM']
                    winrate = (stats_journee['ITM'] / total_trades * 100) if total_trades > 0 else 0
                    
                    texte_bilan = f"📊 **BILAN JOURNALIER SMC (18h00 GMT)** 📊\n"
                    texte_bilan += f"──────────────────\n"
                    texte_bilan += f"✅ **CIBLES ABATTUES (ITM) :** {stats_journee['ITM']}\n"
                    texte_bilan += f"❌ **TIRS RATÉS (OTM) :** {stats_journee['OTM']}\n"
                    texte_bilan += f"🎯 **TAUX DE RÉUSSITE :** {winrate:.1f}%\n"
                    texte_bilan += f"──────────────────\n"
                    texte_bilan += f"*Nettoyage des serveurs. L'analyse des Synthétiques continue 24/7.*"
                    
                    for uid in utilisateurs_actifs:
                        if est_autorise(uid):
                            try: bot.send_message(uid, texte_bilan, parse_mode="Markdown")
                            except: pass
                    
                    stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}
                    bilan_envoye_aujourdhui = True
            
            elif now.hour == 18 and now.minute > 5:
                bilan_envoye_aujourdhui = False
                
        except Exception as e: pass
        time.sleep(30)

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestionnaire_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition V20.0 ULTIME Démarrée.", flush=True)
    bot.infinity_polling()
