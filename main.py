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

TELEGRAM_TOKEN = "8658287331:AAGDhgMK1Tj7ZnzgEDZiSs5RoyHENVmFOuE"
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
def home(): return "Terminal Prime VIP : Édition V22.1 DUAL BRAIN (MT5 Elite + Pocket Forex)"

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
        bot.send_message(user_id, "🏦 **MODE POCKET BROKER ACTIVÉ**\nL'IA réactive l'analyse intégrale (Forex + Crypto) pour cibler les meilleurs pourcentages de paiement en Binaire.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    plateforme_trading[user_id] = plateforme_trading.get(user_id, "MT5")
    filtre_special[user_id] = filtre_special.get(user_id, "TOUS")
    
    texte = """🏴‍☠️ **TERMINAL PRIME - V22.1 DUAL BRAIN** 🔥
──────────────────
🚨 **SYSTÈME À DOUBLE CERVEAU ACTIVÉ** 🚨
L'IA adapte son univers d'analyse selon ton Broker :

📈 **Sur MT5 :** Snipe chirurgical uniquement sur 🥇 GOLD, 🥈 ARGENT, 🛢 PÉTROLE et 💥 VOLATILITY.
🏦 **Sur Pocket Broker :** Réactivation automatique du 💱 FOREX et des 🪙 CRYPTOS pour maximiser les rendements binaires."""
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
    
    # Sécurité anti-forex sur MT5
    if plateforme == "MT5" and actif not in ELITE_PAIRS_MT5:
        bot.send_message(chat_id, "❌ Le Forex et les Cryptos sont bloqués en mode MT5. Passe en mode Pocket Broker pour les trader.", parse_mode="Markdown")
        return

    statut, msg_erreur = est_symbole_autorise(actif)
    
    if statut == "BLOCAGE_TOTAL":
        bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
        return
        
    user_prefs[call.from_user.id] = actif
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    
    if actif in SYNTHETIC_PAIRS: nom_affiche = f"💥 V{actif.replace('V', '')}"
    elif actif == "XAUUSD": nom_affiche = "🥇 GOLD (XAU/USD)"
    elif actif == "XAGUSD": nom_affiche = "🥈 ARGENT (XAG/USD)"
    elif actif == "USOUSD": nom_affiche = "🛢 PÉTROLE (CRUDE/WTI)"
    elif actif in CRYPTO_PAIRS: nom_affiche = f"🪙 {actif[:3]}/{actif[3:]}"
    else: nom_affiche = f"💱 {actif[:3]}/{actif[3:]}"
    
    try: msg = bot.send_message(chat_id, f"⏳ *Analyse Algorithmie VIP sur {nom_affiche}...*", parse_mode="Markdown")
    except: return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif, mode_actuel)
    
    # Filtrage VIP 10/10 strict
    if filtre_special.get(chat_id) == "SPECIAUX" and (score is None or score < 10.0):
        try: bot.edit_message_text(f"⏳ **MODE VIP STRICT**\nLa configuration sur {nom_affiche} n'est pas un 10/10 parfait. Tir annulé.", chat_id, msg.message_id, parse_mode="Markdown")
        except: pass
        return

    if statut == "HORS_SESSION" and (score is None or score < 10.0):
        try: bot.edit_message_text(f"{msg_erreur}\n*(Annulé car inférieur à 10/10)*", chat_id, msg.message_id, parse_mode="Markdown")
        except: pass
        return
    
    if not action or "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return

    # ==========================================
    # BRANCHE MT5 EXCLUSIVE (LOGIQUE SCALP/INTRADAY)
    # ==========================================
    if plateforme == "MT5":
        try: bot.edit_message_text(f"⏳ *Calcul des structures ATR M15/M5 pour {nom_affiche}...*", chat_id, msg.message_id, parse_mode="Markdown")
        except: pass
        
        candles_m15 = obtenir_donnees_deriv(actif, 900)
        candles_m5 = obtenir_donnees_deriv(actif, 300)
        current_ask = obtenir_prix_actuel_deriv(actif)

        if candles_m15 and candles_m5 and current_ask:
            df_m15 = pd.DataFrame([{'close': float(c['close'])} for c in candles_m15])
            df_m5 = pd.DataFrame([{'high': float(c['high']), 'low': float(c['low']), 'close': float(c['close'])} for c in candles_m5])
            
            df_m15['ema_50'] = ta.trend.EMAIndicator(close=df_m15['close'], window=50).ema_indicator()
            tendance_m15 = "HAUSSIERE" if df_m15['close'].iloc[-1] > df_m15['ema_50'].iloc[-1] else "BAISSIERE"
            action_mt4 = "Achat" if "CALL" in action else "Vente"

            avertissement_tendance = ""
            if (action_mt4 == "Achat" and tendance_m15 == "BAISSIERE") or (action_mt4 == "Vente" and tendance_m15 == "HAUSSIERE"):
                avertissement_tendance = "\n⚠️ *Note IA : Tir à contre-tendance M15 (Pullback).* "

            df_m5['atr'] = ta.volatility.AverageTrueRange(high=df_m5['high'], low=df_m5['low'], close=df_m5['close'], window=14).average_true_range()
            atr_val = df_m5['atr'].iloc[-1]
            
            mult_sl = 0.3 if actif == "XAGUSD" else 0.6 if actif in ["XAUUSD", "USOUSD"] else 0.5

            if action_mt4 == "Achat":
                creux_structurel = df_m5['low'].iloc[-15:-1].min()
                sl_secu = creux_structurel - (atr_val * mult_sl)
                tp_secu = current_ask + ((current_ask - sl_secu) * 2.5)
            else:
                sommet_structurel = df_m5['high'].iloc[-15:-1].max()
                sl_secu = sommet_structurel + (atr_val * mult_sl)
                tp_secu = current_ask - ((sl_secu - current_ask) * 2.5)

            avertissement_lot = "\n⚠️ **ATTENTION SYNTHÉTIQUES : LOT STRICT DE 0.001 !**" if actif in SYNTHETIC_PAIRS else ""

            action_affiche = "🟢 BUY MARKET (ACHAT)" if action_mt4 == "Achat" else "🔴 SELL MARKET (VENTE)"
            signal = f"""⚡ **SIGNAL MT5 SCALP ELITE 💎** ⚡
──────────────────
🌐 **ACTIF :** {nom_affiche}
👉 **ORDRE :** {action_affiche}
🧠 **CONTEXTE :** Tendance M15 = {tendance_m15} {avertissement_tendance}
🛡️ **STRUCTURE :** Zone d'intervention validée (SMC)
──────────────────
💰 **PRIX D'ENTRÉE :** `{current_ask:.5f}`
🛑 **STOP LOSS (SL) :** `{sl_secu:.5f}`
✅ **TAKE PROFIT (TP) :** `{tp_secu:.5f}`
──────────────────
*(Temps de clôture estimé : ~30 à 45 minutes. Ratio 1:2.5).* {avertissement_lot}"""
            try:
                bot.delete_message(chat_id, msg.message_id)
                bot.send_message(chat_id, signal, parse_mode="Markdown")
            except: pass
            return
        else:
            try: bot.edit_message_text("❌ Erreur de flux de données Deriv.", chat_id, msg.message_id)
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
        fantome_texte = "🧠 **FANTÔME DESACTIVÉ PAR L'IA VIP (10/10 PERFECT)**\n*Configuration royale, entrée immédiate en réel.*"
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
*(Si échec, génération automatique du Palier 1)*"""
    else:
        signal = f"""🚨 **ALERTE DE TIR RÉEL POCKET 💎** 🚨
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

# === FONCTIONS DE RÉSULTATS BINAIRES ===
def executer_tir_flash(chat_id, symbole, action_brute, duree, palier):
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    nom_paire = f"{symbole[:3]}/{symbole[3:]}" if len(symbole) == 6 and symbole not in SYNTHETIC_PAIRS else symbole
    
    if palier == 0:
        texte = f"👻 **FANTÔME ACTIF ({nom_paire})**\nL'IA étudie la réaction de la zone..."
        markup = None
    else:
        texte = f"🔥 **TIR EN COURS : PALIER {palier} ({nom_paire})** 🔥\n👉 **EXÉCUTEZ L'ORDRE {action_affichage} SUR LE BROKER !**"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ MANUEL WIN", callback_data="force_win"))
        
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
    texte = f"🚨 **SIGNAL AUTOMATIQUE : PALIER {palier}** 🚨\n🌐 **ACTIF :** {nom_paire}\n⏱ **ENTRÉE :** `{heure_entree.strftime('%H:%M:00')}`\n👉 **ACTION :** {action_affichage}\n⏳ **DURÉE :** {exp_texte}\n💵 **MISE :** `{mise}$`"
    
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

    if gagne:
        niveaux_martingale[chat_id] = 0 
        if palier_actuel == 0: texte = f"👻 **FANTÔME RÉUSSI (ITM)**\nLa zone a parfaitement réagi sur {nom_paire}.\n🔓 Radar disponible."
        else:
            texte = f"✅ **TIR EMBARQUÉ (ITM)**\n🔥 {nom_paire} : Cible éliminée !\n🔓 Radar disponible."
            stats_journee['ITM'] += 1
            stats_journee['details'].append(f"✅ {nom_paire}")
            
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, texte, parse_mode="Markdown")
        except: pass
    else:
        if palier_actuel < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id] 
            
            msg_fail = f"⚠️ **ZONAGE EN ÉCHEC (Palier {palier_actuel})**\nRe-calcul immédiat de la structure..."
            bot.send_message(chat_id, msg_fail, parse_mode="Markdown")
            preparer_nouveau_palier(chat_id, symbole, action, trade['duree'], palier_actuel + 1)
        else:
            niveaux_martingale[chat_id] = 0
            texte = f"🛑 **SEQUENCE ARRETÉE (OTM)**\nSécurisation des fonds. Pause tactique sur {nom_paire}."
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
    bot.answer_callback_query(call.id, "Victoire forcée enregistrée.", show_alert=True)
    try: bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except: pass

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **HORAIRES DE TIR RESTREINTS (V22.1 DUAL BRAIN)** 🕒
    
🥇 **Matières Premières & Forex :** 
Lundi au Vendredi, actif dès l'ouverture des bourses de Londres et New York. Verrouillage total le week-end.

💥 **Indices Volatility & Cryptos :**
Ouverts 24h/24, 7j/7 sans aucune interruption temporelle."""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE CIBLE" or m.text == "📊 CHOISIR UNE CIBLE ELITE")
def devises(message):
    if not est_autorise(message.chat.id): return
    plateforme = plateforme_trading.get(message.chat.id, "MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    
    # Boutons d'actifs d'élite (Toujours visibles)
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

    # NOUVEAU : Ajout conditionnel du Forex et Crypto si Pocket Broker
    if plateforme == "POCKET":
        markup.add(
            InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD")
        )
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
            InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
        )
        texte_menu = "Sélectionne ta cible (Forex et Cryptos réactivés pour Pocket Broker) :"
    else:
        texte_menu = "Sélectionne ta cible (Le Forex est bloqué en mode MT5) :"

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
# SCANNER AUTOMATIQUE DYNAMIQUE
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
                        derniere_alerte_auto[cle_memoire] = time.time()
                        
                        for uid in utilisateurs_libres:
                            pf = plateforme_trading.get(uid, "MT5")
                            
                            # Filtre intelligent : Si MT5, on saute les signaux Forex/Crypto
                            if pf == "MT5" and paire not in ELITE_PAIRS_MT5:
                                continue

                            if mode_trading.get(uid, "STANDARD") == mode:
                                if filtre_special.get(uid) == "SPECIAUX" and (sc is None or sc < 10.0):
                                    continue 

                                type_alerte = "📊 Tir Pocket" if pf == "POCKET" else "📈 Ordre Scalp MT5"
                                
                                if paire in SYNTHETIC_PAIRS: nom_aff = f"V{paire.replace('V', '')}"
                                elif paire == "XAUUSD": nom_aff = "GOLD"
                                elif paire == "XAGUSD": nom_aff = "ARGENT"
                                elif paire == "USOUSD": nom_aff = "PÉTROLE"
                                else: nom_aff = f"{paire[:3]}/{paire[3:]}"
                                
                                markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {nom_aff}", callback_data=f"set_{paire}"))
                                msg = f"🔔 **SMC OB 10/10 PERFECT : {nom_aff}**\nStructure prête pour le tir." if sc == 10.0 else f"🔔 **RADAR : {nom_aff}**"
                                
                                try: bot.send_message(uid, msg, reply_markup=markup)
                                except: pass
        except Exception as e: pass

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
    print("⬛ BOÎTE NOIRE : Édition V22.1 DUAL BRAIN Démarrée.", flush=True)
    bot.infinity_polling()
