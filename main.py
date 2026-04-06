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

TELEGRAM_TOKEN = "8658287331:AAGR9EqtnnowYiedmjavRXg9FT2zbTS4wak"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
cooldown_actifs = {} 

utilisateurs_autorises = {
    ADMIN_ID: "LIFETIME"
}
cles_generees = {}

stats_journee = {
    'ITM': 0, 
    'OTM': 0, 
    'details': []
}

bilan_envoye_aujourdhui = False
transition_nuit_envoyee = False
transition_jour_envoyee = False

# 🧹 NETTOYAGE DES DEVISES (ÉLITE UNIQUEMENT, SANS GBP)
CRYPTO_PAIRS = ["BTCUSD", "ETHUSD"]
FOREX_PAIRS = ["EURUSD", "AUDUSD", "USDJPY", "EURJPY", "USDCAD"]

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : FRACTION CHIRURGICALE (Allégée & Épurée)"

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

def generer_cle():
    caracteres = string.ascii_uppercase + string.digits
    return f"PRIME-{''.join(random.choice(caracteres) for _ in range(8))}"

def generer_jauge(pourcentage):
    if pourcentage >= 99: return "[██████████] 👑 MAX"
    pleins = int(pourcentage / 10)
    return f"[{'█' * pleins}{'░' * (10 - pleins)}] {pourcentage}%"

# ==========================================
# FONCTIONS PRO (NEWS & H1)
# ==========================================

def est_heure_de_news_dynamique():
    if not FMP_API_KEY: return False
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today}&to={today}&apikey={FMP_API_KEY}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            maintenant = datetime.datetime.utcnow()
            for event in response.json():
                if event.get('impact') == 'High':
                    e_time = datetime.datetime.strptime(event['date'], "%Y-%m-%d %H:%M:%S")
                    if abs((maintenant - e_time).total_seconds() / 60) <= 30: return True
    except: pass
    return False

def obtenir_tendance_H1(symbole_brut):
    symbole = f"cry{symbole_brut}" if symbole_brut in CRYPTO_PAIRS else f"frx{symbole_brut}"
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
        ws.send(json.dumps({"ticks_history": symbole, "end": "latest", "count": 50, "style": "candles", "granularity": 3600}))
        res = json.loads(ws.recv())
        ws.close()
        if "candles" in res and len(res["candles"]) > 20:
            df = pd.DataFrame(res['candles'])
            ema20 = ta.trend.EMAIndicator(close=df['close'].astype(float), window=20).ema_indicator()
            return "UP" if float(df['close'].iloc[-1]) > ema20.iloc[-1] else "DOWN"
    except: pass
    return "NEUTRE"

# ==========================================
# ROUTEUR API DERIV 
# ==========================================

def obtenir_donnees_deriv(symbole_brut):
    symbole = f"cry{symbole_brut}" if symbole_brut in CRYPTO_PAIRS else f"frx{symbole_brut}"
    for _ in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            ws.send(json.dumps({"ticks_history": symbole, "end": "latest", "count": 250, "style": "candles", "granularity": 300}))
            history = json.loads(ws.recv())
            ws.close()
            if "candles" in history: return history['candles']
        except: time.sleep(1)
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = f"cry{symbole_brut}" if symbole_brut in CRYPTO_PAIRS else f"frx{symbole_brut}"
    for _ in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            ws.send(json.dumps({"ticks_history": symbole, "end": "latest", "count": 1, "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res: return float(res["history"]["prices"][0])
        except: time.sleep(1)
    return None

# ==========================================
# SYSTÈME DE VÉRIFICATION ITM/OTM
# ==========================================

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee, cooldown_actifs
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'): return

    symbole, prix_entree, action = trade['symbole'], trade['prix_entree'], trade['action']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie: return

    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    type_emoji = "🪙" if symbole in CRYPTO_PAIRS else "💱"
    
    if gagne:
        texte = f"✅ **VICTOIRE CHIRURGICALE (ITM)**\n🚀 Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
        stats_journee['ITM'] += 1
        stats_journee['details'].append(f"✅ {type_emoji} {nom_paire} ({action})")
        cooldown_actifs.pop(symbole, None)
    else:
        texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {type_emoji} {nom_paire} ({action})")
        cooldown_actifs[symbole] = time.time()
    
    try: bot.send_message(ADMIN_ID, texte, parse_mode="Markdown")
    except: pass
    trades_en_cours.pop(chat_id, None)

# ==========================================
# MOTEUR CHIRURGICAL (GOD MODE V7.1 ALLÉGÉ)
# ==========================================

def analyser_binaire_pro(symbole):
    if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole] < 3600):
        return f"⚠️ **SILENCIEUX ACTIF** : Protection du capital suite à OTM.", None, None, None, None, None, None, None

    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE NEWS : Marché manipulé.", None, None, None, None, None, None, None

    tendance_h1 = obtenir_tendance_H1(symbole)
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return "⚠️ Erreur connexion marché", None, None, None, None, None, None, None
    
    try:
        df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        
        # Anatomie de la bougie pour le Price Action
        df['corps'] = abs(df['close'] - df['open'])
        df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']

        # Indicateurs Standard
        ind_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2) 
        df['bb_h'], df['bb_b'] = ind_bb.bollinger_hband(), ind_bb.bollinger_lband()
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        
        # Volatilité & Force (ATR + ADX)
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        atr_act, atr_moy = df['atr'].iloc[-1], df['atr'].rolling(20).mean().iloc[-1]
        
        ind_adx = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['adx'], df['di_p'], df['di_m'] = ind_adx.adx(), ind_adx.adx_pos(), ind_adx.adx_neg()

        last = df.iloc[-1]
        c, o = last['close'], last['open']
        rsi, stoch, adx = round(last['rsi'], 1), round(last['stoch'], 1), round(last['adx'], 1)
        bb_h, bb_b = last['bb_h'], last['bb_b']
        
        # Filtres de Tendance
        ema_act, ema_anc = df['ema_200'].iloc[-1], df['ema_200'].iloc[-5] 
        trend_up = c > ema_act and tendance_h1 in ["UP", "NEUTRE"]
        trend_down = c < ema_act and tendance_h1 in ["DOWN", "NEUTRE"]

        # DÉTECTION CHIRURGICALE (PRICE ACTION)
        rejet_haussier = last['meche_basse'] > (last['corps'] * 1.5)
        rejet_baissier = last['meche_haute'] > (last['corps'] * 1.5)

        # Sélection Expiration
        if atr_act > (atr_moy * 1.5): dur, exp_txt = 120, "2 MINUTES ⚡"
        elif atr_act < (atr_moy * 0.8): dur, exp_txt = 600, "10 MINUTES 🐢"
        else: dur, exp_txt = 300, "5 MINUTES 💎"

        # VERROUS STRICTS
        if adx < 20: return f"⚠️ Scan: ADX trop faible ({adx}). Pas de force.", None, None, None, None, None, None, None

        action, conf, bb_status, score = None, 0, "", 0

        # ACHAT (CALL) CHIRURGICAL - ALLÉGÉ
        if c <= bb_b and rsi <= 25 and stoch <= 20 and trend_up:
            if (adx > 35 and last['di_m'] > last['di_p']): return "⚠️ ADX: Chute mortelle. Achat annulé.", None, None, None, None, None, None, None
            if rejet_haussier:
                action, conf, score = "🟢 ACHAT (CALL)", 99, 10
                bb_status = f"Rejet Haussier + Surchauffe RSI/Stoch (ADX:{adx})"

        # VENTE (PUT) CHIRURGICALE - ALLÉGÉ
        elif c >= bb_h and rsi >= 75 and stoch >= 80 and trend_down:
            if (adx > 35 and last['di_p'] > last['di_m']): return "⚠️ ADX: Hausse mortelle. Vente annulée.", None, None, None, None, None, None, None
            if rejet_baissier:
                action, conf, score = "🔴 VENTE (PUT)", 99, 10
                bb_status = f"Rejet Baissier + Surchauffe RSI/Stoch (ADX:{adx})"

        if action and score >= 10:
            return action, conf, exp_txt, dur, rsi, stoch, bb_status, score
        return f"⚠️ Scan en cours... (RSI actuel: {rsi} / Stoch: {stoch})", None, None, None, None, None, None, None
            
    except: return None, None, None, None, None, None, None, None

# ==========================================
# LE SCANNER AUTOMATIQUE DE L'OMBRE
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(60)
            users = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not users: continue
                
            devises = CRYPTO_PAIRS if datetime.datetime.now().weekday() >= 5 else FOREX_PAIRS
            
            for actif in devises:
                action, conf, exp, dur, rsi, stoch, bb_status, score = analyser_binaire_pro(actif)
                if action and "⚠️" not in action and conf:
                    t = time.time()
                    if actif in derniere_alerte_auto and (t - derniere_alerte_auto[actif] < 3600): continue
                    derniere_alerte_auto[actif] = t
                    nom = f"{actif[:3]}/{actif[3:]}"
                    
                    markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"🎯 Frapper {nom}", callback_data=f"set_{actif}"))
                    msg = f"🥷 **FRAPPE CHIRURGICALE DÉTECTÉE** 🥷\n\n**CONFIANCE :** [██████████] 👑 MAX\nCible : **{nom}**\nConfiguration : {bb_status}\n\n👇 *Autorisation de tir accordée.*"
                        
                    for chat_id in users:
                        try: bot.send_message(chat_id, msg, reply_markup=markup, parse_mode="Markdown")
                        except: pass
        except: pass

# ==========================================
# GESTIONNAIRE D'HORAIRES ET DE BILAN 
# ==========================================

def gestion_horaires_et_bilan():
    global stats_journee, bilan_envoye_aujourdhui
    while True:
        try:
            maintenant = datetime.datetime.now()
            if maintenant.hour == 22 and maintenant.minute == 0 and not bilan_envoye_aujourdhui:
                tot = stats_journee['ITM'] + stats_journee['OTM']
                if tot > 0:
                    winrate = round((stats_journee['ITM'] / tot) * 100)
                    txt = f"📊 **BILAN CHIRURGICAL** 📊\n🎯 **Signaux :** {tot}\n✅ **ITM :** {stats_journee['ITM']}\n❌ **OTM :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n"
                    for d in stats_journee['details']: txt += f"{d}\n"
                    try: bot.send_message(ADMIN_ID, txt, parse_mode="Markdown")
                    except: pass
                stats_journee, bilan_envoye_aujourdhui = {'ITM': 0, 'OTM': 0, 'details': []}, True
            elif maintenant.hour == 23: bilan_envoye_aujourdhui = False
            time.sleep(30)
        except: time.sleep(60)

# ==========================================
# COMMANDES TÉLÉGRAM
# ==========================================

@bot.message_handler(commands=['capital', 'panel'])
def voir_infos(message):
    if message.chat.id == ADMIN_ID:
        bot.send_message(ADMIN_ID, f"💰 **COMPTE PRO** 💰\nMontant : `{CAPITAL_ACTUEL}$`", parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    if not est_autorise(message.chat.id):
        return bot.send_message(message.chat.id, "🔒 **ACCÈS RESTREINT** 🔒\nContacter l'admin.", parse_mode="Markdown")
    utilisateurs_actifs.add(message.chat.id)
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    bot.send_message(message.chat.id, "🏴‍☠️ **TERMINAL PRIME - MODE CHIRURGICAL ACTIF**\n\nLe bot ne réagira désormais qu'aux configurations parfaites (Confluence + Price Action).", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    jour_semaine = datetime.datetime.now().weekday()
    
    if jour_semaine >= 5:
        markup.add(
            InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"),
            InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD")
        )
        message_texte = "Mode Week-End 🪙 : Sélectionne la Crypto :"
    else:
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), 
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD")
        )
        markup.add(
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"), 
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), 
            InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD")
        )
        message_texte = "Mode Semaine 💱 : Cibles hautement liquides. Sélectionne ta devise :"
        
    bot.send_message(message.chat.id, message_texte, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id] = actif
    
    msg = bot.send_message(chat_id, "⏳ *Alignement des paramètres chirurgicaux...*", parse_mode="Markdown")
    action, conf, exp_txt, dur, rsi, stoch, bb_status, score = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        return bot.edit_message_text(f"{action}", chat_id, msg.message_id)
    elif not action:
        return bot.edit_message_text("❌ Échec de la connexion Deriv.", chat_id, msg.message_id)

    dt = datetime.datetime.now() + datetime.timedelta(seconds=(120 - datetime.datetime.now().second if datetime.datetime.now().second > 45 else 60 - datetime.datetime.now().second))
    
    signal = f"""🔥 **FRAPPE VALIDÉE** 🔥
──────────────────
🛰 **ACTIF :** {actif[:3]}/{actif[3:]}
🎯 **ACTION :** {action}
⏳ **EXPIRATION :** {exp_txt}
──────────────────
🧠 **SETUP :** {bb_status}
📍 **ORDRE À : {dt.strftime("%H:%M:00")}** 👈
💵 **MISE MAX :** 2% du capital
──────────────────"""
    bot.delete_message(chat_id, msg.message_id)
    bot.send_message(chat_id, signal, parse_mode="Markdown")
    trades_en_cours[chat_id] = {'symbole': actif, 'action': "CALL" if "ACHAT" in action else "PUT"}
    Timer((dt - datetime.datetime.now()).total_seconds(), relever_prix_entree, args=[chat_id, actif]).start()
    Timer((dt - datetime.datetime.now()).total_seconds() + dur, verifier_resultat, args=[chat_id]).start()

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    actif = user_prefs.get(message.from_user.id)
    if actif: save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    try:
        symbole = message.text.split()[1].upper()
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in obtenir_donnees_deriv(symbole)])
        adx = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14).adx().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        bot.send_message(message.chat.id, f"👁️ **VISION {symbole}**\nPrix: `{df['close'].iloc[-1]:.5f}`\nRSI: `{rsi:.1f}`\nADX: `{adx:.1f}`", parse_mode="Markdown")
    except: bot.send_message(message.chat.id, "⚠️ Usage: `/vision EURUSD`", parse_mode="Markdown")

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestion_horaires_et_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : ÉDITION CHIRURGICALE EN LIGNE (ALLÉGÉE ET ÉPURÉE).", flush=True)
    bot.infinity_polling()
