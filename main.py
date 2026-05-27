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

TELEGRAM_TOKEN = "8658287331:AAEAh_3ofl--K1neCuRWsFYnzCpQQPfJx_o"
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
signaux_cache = {} # 🧠 MÉMOIRE PHOTO (2 MINUTES MAX)
cooldown_actifs = {} 
niveaux_martingale = {} 

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}

# 🧠 LES DEUX CERVEAUX DE L'IA 
SYNTHETIC_PAIRS = ["V10", "V25", "V50", "V75", "V100"]
COMMODITY_PAIRS = ["XAUUSD", "XAGUSD", "USOUSD"]
CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD", 
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF", 
    "CADCHF", "EURCHF", "USDJPY"
]

# Cibles selon le broker
ELITE_PAIRS_MT5 = SYNTHETIC_PAIRS + COMMODITY_PAIRS
ALL_PAIRS_POCKET = SYNTHETIC_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS + CRYPTO_PAIRS

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)
@app.route('/')
def home(): return "Terminal Prime VIP : Édition Parfaite V25"

def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

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
        else: return "BLOCAGE_TOTAL", f"🔒 **ACCÈS REFUSÉ** : Les marchés Forex/Matières Premières sont fermés le week-end."

    if symbole in CRYPTO_PAIRS:
        return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Les Cryptomonnaies sont verrouillées la semaine."

    if heure_dec >= 17.5: return "HORS_SESSION", f"🛑 **REPLI TACTIQUE** : Couvre-feu en cours (17h30 - 00h00 GMT)."
    
    if heure_dec >= 0.0 and heure_dec < 8.0:
        if symbole in ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD", "XAUUSD", "XAGUSD", "USOUSD"]: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Session Asiatique."

    if heure_dec >= 7.0 and heure_dec < 12.0:
        paires = ["EURUSD", "EURJPY", "EURAUD", "EURCHF", "USDCHF", "CADCHF", "XAUUSD", "XAGUSD", "USOUSD"]
        if heure_dec < 8.0: paires.extend(["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"])
        if symbole in paires: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Session Européenne."

    if heure_dec >= 12.0 and heure_dec < 17.5:
        if symbole in ["EURUSD", "USDCAD", "AUDUSD", "XAUUSD", "XAGUSD", "USOUSD"]: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Zone de Guerre US/CA."

    return "BLOCAGE_TOTAL", "🛑 Erreur temporelle."

# ==========================================
# ROUTEUR DERIV (MT5 & POCKET)
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
    if symbole_base in SYNTHETIC_PAIRS or symbole_base in COMMODITY_PAIRS: return True 
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
    if len(commande) < 2: return bot.send_message(message.chat.id, "⚠️ Précise l'actif (ex: EURUSD, XAUUSD, V75).")
    symbole = commande[1].upper()
    
    plateforme = plateforme_trading.get(message.chat.id, "MT5")
    if plateforme == "MT5" and symbole not in ELITE_PAIRS_MT5:
        return bot.send_message(message.chat.id, "❌ En mode MT5, l'analyse est restreinte aux matières premières et synthétiques.")
    if symbole not in ALL_PAIRS_POCKET:
        return bot.send_message(message.chat.id, "❌ Symbole non reconnu.")
        
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
        
        swing_high_1 = df['high'].iloc[-20:-10].max()
        swing_low_1 = df['low'].iloc[-20:-10].min()
        swing_high_2 = df['high'].iloc[-10:-1].max()
        swing_low_2 = df['low'].iloc[-10:-1].min()
        
        structure_haussiere = (swing_high_2 > swing_high_1) and (swing_low_2 >= swing_low_1)
        structure_baissiere = (swing_low_2 < swing_low_1) and (swing_high_2 <= swing_high_1)
        tendance = "Order Flow Hausse 🟢" if structure_haussiere else "Order Flow Baisse 🔴" if structure_baissiere else "Consolidation (Liquidity Build) ⚠️"

        rsi = ta.momentum.RSIIndicator(close=df['close']).rsi().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        rapport = f"👁️ **VISION ELITE SMC : {symbole}** 👁️\n──────────────────\n💰 **Prix :** `{prix_actuel:.5f}`\n🧱 **Structure :** `{tendance}`\n⛽ **Volume :** `{etat_vol}`\n📊 **RSI :** `{rsi:.2f}`\n──────────────────"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except: bot.edit_message_text("❌ Erreur d'analyse.", message.chat.id, msg.message_id)

# ==========================================
# MOTEUR SMC VIP (CALCUL DU SCORE 10/10)
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    if est_heure_de_news_dynamique() and (symbole in COMMODITY_PAIRS or symbole in FOREX_PAIRS):
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
                        bb_status = f"👑 SMC ULTIME : Prise de Liquidité Perfect 🚀"
                        
                elif structure_baissiere and dans_zone_premium and volume_ok and vrai_corps and not danger_rejet_hausse and not fusee_haussiere:
                    if (stoch_val > 60) and (rsi_val < 60):
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 85, 8.0
                        bb_status = f"🎯 SMC : Order Block (Zone Premium)"
                    if avalement_baissier or rejet_baissier or harami_bear:
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 99, 10.0
                        bb_status = f"👑 SMC ULTIME : Prise de Liquidité Perfect ☄️"

            elif mode == "SCALP":
                indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2)
                bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
                df['bb_width'] = indicateur_bb.bollinger_wband()
                squeeze = df['bb_width'].iloc[-1] < (df['bb_width'].rolling(window=20).mean().iloc[-1] * 0.8)

                duree_secondes, exp_texte = 60, "1 MINUTE (SCALP 🛡️)"
                
                if not squeeze and volume_ok and vrai_corps:
                    if (last['low'] <= bb_basse) and rejet_haussier and not danger_rejet_baisse and not fusee_baissiere:
                        action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 95, 9.5, "🛡️ SMC Scalp : Liquidité Basse"
                    elif (last['high'] >= bb_haute) and rejet_baissier and not danger_rejet_hausse and not fusee_haussiere:
                        action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 95, 9.5, "🛡️ SMC Scalp : Liquidité Haute"

            if action:
                if not verifier_correlation(symbole, action):
                    return f"⚠️ **FAKEOUT DÉTECTÉ**", None, None, None, None, None, None, None

                delai_blocage = 600 if mode == "SCALP" else 1800
                action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
                if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole]['time'] < delai_blocage):
                    if action_simplifiee == cooldown_actifs[symbole]['action']:
                        return f"⚠️ **BLOCAGE ANTI-FAKEOUT**", None, None, None, None, None, None, None
                
                return action, min(confiance, 99), exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo
                
        except: continue

    return f"⚠️ En attente d'une opportunité ({mode}).", None, None, None, None, None, None, None

# ==========================================
# INTERFACE MENU & COMMANDE
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
        bot.send_message(user_id, "💎 **MODE VIP ULTRA 10/10 ACTIF**\nLe bot filtrera uniquement les structures parfaites absolues.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        filtre_special[user_id] = "TOUS"
        bot.send_message(user_id, "📡 **MODE TOUS SIGNAUX ACTIVÉ**\nLe radar est ouvert aux configurations classiques (8/10) et VIP (10/10).", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Un trade est déjà en cours.")
        
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    if mode_actuel == "STANDARD":
        mode_trading[user_id] = "SCALP"
        bot.send_message(user_id, "🔥 **MODE SMC SCALPING ULTRA RAPIDE ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE SMC STANDARD INTRADAY ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_plateforme(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Terminez le trade en cours.")
        
    plateforme_actuelle = plateforme_trading.get(user_id, "MT5")
    if plateforme_actuelle == "POCKET":
        plateforme_trading[user_id] = "MT5"
        bot.send_message(user_id, "📈 **MODE MT5 EXCLUSIF ACTIVÉ**\nTrades ciblés sur ~30-45 minutes. L'IA se concentre uniquement sur l'Élite (Matières premières et Synthétiques).", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        plateforme_trading[user_id] = "POCKET"
        bot.send_message(user_id, "🏦 **MODE POCKET BROKER ACTIVÉ**\nL'IA passe en mode Binaire : Le radar et le menu se concentrent à 100% sur le Forex classique.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    plateforme_trading[user_id] = plateforme_trading.get(user_id, "MT5")
    filtre_special[user_id] = filtre_special.get(user_id, "TOUS")
    
    texte = """🏴‍☠️ **TERMINAL PRIME - ÉDITION PARFAITE (V25)** 🔥
──────────────────
🚨 **SYSTÈME À DOUBLE CERVEAU PURIFIÉ** 🚨
Format original, chirurgical, précis à la seconde `00`, avec les vrais tickets de trading et le système Zéro Latence Intégré !

📈 **Sur MT5 :** Snipe chirurgical uniquement sur l'Élite.
🏦 **Sur Pocket Broker :** Isolation totale du 💱 FOREX. Menu épuré. Le radar filtre la tendance M15 avant de parler pour bloquer les Fakeouts."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **HORAIRES DE TIR RESTREINTS** 🕒
    
🥇 **Matières Premières & Forex :** Lundi au Vendredi, actif dès l'ouverture des bourses de Londres et New York. Verrouillage total le week-end.

💥 **Indices Volatility :**
Ouverts 24h/24, 7j/7 sans aucune interruption temporelle."""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE CIBLE" or m.text == "📊 CHOISIR UNE CIBLE ELITE")
def devises(message):
    if not est_autorise(message.chat.id): return
    plateforme = plateforme_trading.get(message.chat.id, "MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    
    if plateforme == "MT5":
        # Menu exclusif MT5 (Élite uniquement)
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
        texte_menu = "Sélectionne ta cible (L'Élite réservée à MT5) :"
    
    else:
        # Menu exclusif Pocket Broker (Forex uniquement)
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY")
        )
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY")
        )
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD")
        )
        markup.add(
            InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF")
        )
        markup.add(
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
        )
        texte_menu = "Sélectionne ta cible (Mode Binaire : 100% Forex) :"

    bot.send_message(message.chat.id, texte_menu, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours: return bot.send_message(chat_id, f"⚠️ Combat en cours.", parse_mode="Markdown")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une cible !")
    
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

# ==========================================
# EXÉCUTION EXACTE (RESTAURATION AFFICHAGE)
# ==========================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours:
        bot.answer_callback_query(call.id, f"⚠️ Combat en cours !", show_alert=True)
        return
    
    actif = call.data.replace("set_", "")
    plateforme = plateforme_trading.get(chat_id, "MT5")
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    cle_memoire = f"{actif}_{mode_actuel}"
    
    # 🧠 LECTURE DE LA MÉMOIRE PHOTO (La règle des 2 Minutes)
    signal_cache = signaux_cache.get(cle_memoire)
    utiliser_cache = False
    
    if signal_cache:
        age_secondes = time.time() - signal_cache['time']
        if age_secondes <= 120:  # 120 secondes = 2 minutes de grâce
            utiliser_cache = True
        else:
            del signaux_cache[cle_memoire] 
            
    try: bot.delete_message(chat_id, call.message.message_id)
    except: pass

    # SI CLIC DANS LES 2 MINUTES : ON DONNE LE SIGNAL SANS RÉFLÉCHIR
    if utiliser_cache:
        current_ask = obtenir_prix_actuel_deriv(actif)
        if not current_ask: current_ask = 0.0

        if actif in SYNTHETIC_PAIRS: nom_affiche = f"💥 V{actif.replace('V', '')}"
        elif actif == "XAUUSD": nom_affiche = "🥇 GOLD (XAU/USD)"
        elif actif == "XAGUSD": nom_affiche = "🥈 ARGENT (XAG/USD)"
        elif actif == "USOUSD": nom_affiche = "🛢 PÉTROLE (CRUDE/WTI)"
        elif actif in CRYPTO_PAIRS: nom_affiche = f"🪙 {actif[:3]}/{actif[3:]}"
        else: nom_affiche = f"💱 {actif[:3]}/{actif[3:]}"
        
        if plateforme == "MT5":
            action_affiche = "🟢 BUY MARKET" if "ACHAT" in signal_cache['action'] else "🔴 SELL MARKET"
            
            signal = f"""⚡ **SIGNAL MT5 SNIPER 💎** ⚡
──────────────────
🌐 **ACTIF :** {nom_affiche}
👉 **ORDRE :** {action_affiche}
🎯 **RATIO INITIAL :** {signal_cache.get('mt5_rr', 0.0):.2f}
──────────────────
💰 **PRIX ACTUEL :** `{current_ask:.5f}`
🛑 **STOP LOSS (SL) :** `{signal_cache.get('mt5_sl', 0.0):.5f}`
✅ **TAKE PROFIT (TP) :** `{signal_cache.get('mt5_tp', 0.0):.5f}`
──────────────────
⚠️ *Lot de 0.001 pour les indices*"""
            bot.send_message(chat_id, signal, parse_mode="Markdown")
            return
            
        else:
            # POCKET BROKER EXÉCUTION À LA SECONDE 00 (ÉDITION PARFAITE)
            palier = niveaux_martingale.get(chat_id, 0)
            score = signal_cache.get('sc', 5.0)
            
            maintenant = datetime.datetime.now()
            sec_rest = 60 - maintenant.second
            if sec_rest < 10: sec_rest += 60 # Laisse le temps de se préparer sur le broker
            
            heure_entree = maintenant + datetime.timedelta(seconds=sec_rest)
            str_p0 = heure_entree.strftime("%H:%M:00")
            mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))

            if palier == 0 and score < 10.0:
                signal = f"""👻 **MODE FANTÔME (PALIER 0)** 👻
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_p0}`
👉 **ACTION :** {signal_cache['action']}
⏳ **DURÉE :** {signal_cache['exp']}

*Le bot prend ce trade virtuellement (Fantôme SMC). NE RENTREZ PAS.*

*(Si échec, le bot générera instantanément le signal Palier 1)*"""
            
            elif palier == 0 and score == 10.0:
                palier = 1
                niveaux_martingale[chat_id] = 1
                signal = f"""🚨 **ALERTE DE TIR RÉEL VIP 💎** 🚨
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_p0}`
⏳ **EXPIRATION :** {signal_cache['exp']}
👉 **ACTION :** {signal_cache['action']}
🛡️ {signal_cache['bb']}

👑 **EXCEPTION 10/10 PERFECT !**
Prise de liquidité parfaite, on attaque en réel direct !
💵 **MISE CALCULÉE :** `{mise}$`
*(Statut : Palier 1)*"""
            else:
                signal = f"""🚨 **SIGNAL DE TIR : PALIER {palier}** 🚨
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_p0}`
👉 **ACTION :** {signal_cache['action']}
⏳ **DURÉE :** {signal_cache['exp']}
💵 **MISE :** `{mise}$`
──────────────────
⏳ *Préparez le broker. L'IA enverra un flash pour valider le tir à la seconde 00.*"""

            bot.send_message(chat_id, signal, parse_mode="Markdown")
            
            # Le Timer exact à la seconde près
            action_brute = "CALL" if "ACHAT" in signal_cache['action'] else "PUT"
            Timer(sec_rest, executer_tir_flash, args=[chat_id, actif, action_brute, signal_cache['dur'], palier, nom_affiche]).start()

    # SI CLIC APRÈS 2 MINUTES : ANNULATION POUR SÉCURITÉ
    else:
        bot.send_message(chat_id, f"⏱️ **OPPORTUNITÉ EXPIRÉE SUR {actif}**\n\nVous avez mis plus de 2 minutes à cliquer sur le bouton. Le signal a été détruit par sécurité car le marché a bougé. Veuillez attendre la prochaine cible.", parse_mode="Markdown")
        return

# ==========================================
# SCANNER AUTOMATIQUE DYNAMIQUE ET INTELLIGENT
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
                        if statut == "HORS_SESSION" and (sc is None or sc < 10.0): continue

                        action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
                        alerte_valide = True
                        sl, tp, ratio_rr = 0, 0, 0

                        # LE SCANNER VÉRIFIE LA TENDANCE LOURDE
                        candles_m15 = obtenir_donnees_deriv(paire, 900)
                        if candles_m15:
                            df_m15 = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles_m15])
                            df_m15['ema_50'] = ta.trend.EMAIndicator(close=df_m15['close'], window=50).ema_indicator()
                            tendance_m15 = "HAUSSIERE" if df_m15['close'].iloc[-1] > df_m15['ema_50'].iloc[-1] else "BAISSIERE"

                            if (action_simplifiee == "CALL" and tendance_m15 == "BAISSIERE") or (action_simplifiee == "PUT" and tendance_m15 == "HAUSSIERE"):
                                alerte_valide = False
                            
                            # LE SCANNER CALCULE LE RATIO AVANT DE PARLER POUR MT5
                            if alerte_valide and paire in ELITE_PAIRS_MT5:
                                candles_m5 = obtenir_donnees_deriv(paire, 300)
                                current_ask = obtenir_prix_actuel_deriv(paire)
                                if candles_m5 and current_ask:
                                    df_m5 = pd.DataFrame([{'high': float(c['high']), 'low': float(c['low']), 'close': float(c['close'])} for c in candles_m5])
                                    df_m5['atr'] = ta.volatility.AverageTrueRange(high=df_m5['high'], low=df_m5['low'], close=df_m5['close'], window=14).average_true_range()
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
                                    
                                    if ratio_rr < 1.5: alerte_valide = False 

                        if not alerte_valide:
                            continue 

                        # 📸 PRISE DE LA PHOTO EN MÉMOIRE
                        signaux_cache[cle_memoire] = {
                            'time': time.time(), 'action': action, 'conf': conf, 'exp': exp, 'dur': dur,
                            'rsi': rsi, 'stoch': stoch, 'bb': bb, 'sc': sc, 'mt5_sl': sl, 'mt5_tp': tp, 'mt5_rr': ratio_rr
                        }
                        derniere_alerte_auto[cle_memoire] = time.time()
                        
                        for uid in utilisateurs_libres:
                            pf = plateforme_trading.get(uid, "MT5")
                            if pf == "MT5" and paire not in ELITE_PAIRS_MT5: continue
                            if pf == "POCKET" and paire not in FOREX_PAIRS: continue

                            if mode_trading.get(uid, "STANDARD") == mode:
                                if filtre_special.get(uid) == "SPECIAUX" and (sc is None or sc < 10.0):
                                    continue 

                                if paire in SYNTHETIC_PAIRS: nom_aff = f"V{paire.replace('V', '')}"
                                elif paire == "XAUUSD": nom_aff = "GOLD"
                                elif paire == "XAGUSD": nom_aff = "ARGENT"
                                elif paire == "USOUSD": nom_aff = "PÉTROLE"
                                else: nom_aff = f"{paire[:3]}/{paire[3:]}"
                                
                                markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {nom_aff}", callback_data=f"set_{paire}"))
                                msg = f"🔔 **SMC OB 10/10 PERFECT : {nom_aff}**\nLa structure est figée. Vous avez 2 min pour frapper." if sc == 10.0 else f"🔔 **RADAR : {nom_aff}**\nLa structure est figée. Vous avez 2 min pour frapper."
                                
                                try: bot.send_message(uid, msg, reply_markup=markup, parse_mode="Markdown")
                                except: pass
        except Exception as e: pass

# === FONCTIONS DE RÉSULTATS BINAIRES (RESTAURÉES) ===
def executer_tir_flash(chat_id, symbole, action_brute, duree, palier, nom_affiche):
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    
    if palier == 0:
        texte = f"👻 **LE FANTÔME EST LANCÉ ({nom_affiche})** 👻\nL'IA observe le marché virtuellement..."
        markup = None
    else:
        texte = f"🔥 **TIR IMMÉDIAT : PALIER {palier} ({nom_affiche})** 🔥\n👉 **CLIQUEZ SUR {action_affichage} MAINTENANT !**"
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
        if palier_actuel == 0: 
            texte = f"👻 **FANTÔME RÉUSSI (ITM)**\nLa zone a parfaitement réagi sur {nom_affiche}.\n🔓 Radar disponible."
        else:
            texte = f"✅ **CIBLE ABATTUE (ITM)**\n🚀 {nom_affiche} ({action})\n📈 **Entrée :** `{prix_entree:.5f}`\n📉 **Sortie :** `{prix_sortie:.5f}`\n🔓 Radar déverrouillé."
            stats_journee['ITM'] += 1
            stats_journee['details'].append(f"✅ {nom_affiche}")
            
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, texte, parse_mode="Markdown")
        except: pass
    else:
        if palier_actuel < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id] 
            
            msg_fail = f"⚠️ **PIÈGE BROKER DÉTECTÉ (Échec)**\n📉 **Sortie :** `{prix_sortie:.5f}`\n\n⚡ Génération instantanée du signal Palier {palier_actuel + 1}..."
            bot.send_message(chat_id, msg_fail, parse_mode="Markdown")
            
            # On simule un clic automatique pour relancer le palier suivant avec le calcul de l'heure exacte
            class CallFictif:
                def __init__(self, c_id, msg_id, data):
                    self.message = type('obj', (object,), {'chat': type('obj', (object,), {'id': c_id}), 'message_id': msg_id})
                    self.data = data
            
            # Réinitialiser la mémoire avec les mêmes données de base pour permettre la relance du tir
            cle_memoire = f"{symbole}_{mode_trading.get(chat_id, 'STANDARD')}"
            if cle_memoire not in signaux_cache:
                signaux_cache[cle_memoire] = {
                    'time': time.time(), 'action': f"🟢 ACHAT (CALL)" if action == "CALL" else f"🔴 VENTE (PUT)", 'conf': 99, 
                    'exp': f"{int(trade['duree']/60)} MIN" if trade['duree'] >= 60 else f"{trade['duree']} SEC", 
                    'dur': trade['duree'], 'rsi': 50, 'stoch': 50, 'bb': "SMC Validé", 'sc': 5.0
                }

            save_devise(CallFictif(chat_id, 0, f"set_{symbole}"))
        else:
            niveaux_martingale[chat_id] = 0
            texte = f"🛑 **SÉQUENCE ARRÊTÉE (OTM)**\nSécurisation des fonds sur {nom_affiche}."
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
        trade = trades_en_cours[chat_id]
        texte = f"✅ **CIBLE ABATTUE (ITM MANUEL)**\n🚀 {trade['nom_affiche']} ({trade['action']})\n🔓 Radar déverrouillé."
        bot.send_message(chat_id, texte, parse_mode="Markdown")
        del trades_en_cours[chat_id]
    niveaux_martingale[chat_id] = 0
    bot.answer_callback_query(call.id, "Victoire forcée enregistrée.", show_alert=True)
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
                
                texte_bilan = f"📊 **BILAN QUOTIDIEN DUAL BRAIN** 📊\n──────────────────\n✅ **ITM :** {stats_journee['ITM']}\n❌ **OTM :** {stats_journee['OTM']}\n🎯 **WINRATE :** {winrate:.1f}%\n──────────────────"
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

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestionnaire_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition Parfaite V25 Démarrée.", flush=True)
    bot.infinity_polling()
