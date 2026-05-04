import os
import sys
import datetime
import random
import time
import string
import json
import websocket
import threading
import pandas as pd
import ta
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAG0LpBkmnWvix3XhQJ7h-JRaCOlkM8HDqo" # Remets ton token si différent
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# 🔴 CONFIGURATION MARTINGALE SÉCURISÉE
COEF_MARTINGALE = 2.5  # Multiplicateur pour couvrir les pertes (Option Binaire)
MAX_MARTINGALE = 2     # Nombre maximum de rattrapages autorisés (Base -> M1 -> M2)

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
cooldown_actifs = {} 
prix_en_direct = {} 
niveaux_martingale = {} # 🔥 NOUVEAU : Suivi des paliers par utilisateur

utilisateurs_autorises = {
    ADMIN_ID: "LIFETIME"
}
cles_generees = {}

stats_journee = {
    'ITM': 0, 
    'OTM': 0, 
    'details': []
}

CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD", 
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF", 
    "CADCHF", "EURCHF", "USDJPY"
]

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : V10 (MTF + Divergence + Martingale)"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    threading.Thread(target=run, daemon=True).start()

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

def generer_cle():
    return f"PRIME-{''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))}"

def generer_jauge(pourcentage):
    if pourcentage >= 99: return "[██████████] 👑 MAX"
    pleins = int(pourcentage / 10)
    return f"[{'█' * pleins}{'░' * (10 - pleins)}] {pourcentage}%"

# ==========================================
# MOTEUR RÉSEAU DERIV (ZERO LATENCE)
# ==========================================

def prefixer_symbole(symbole_brut):
    return f"cry{symbole_brut}" if symbole_brut in CRYPTO_PAIRS else f"frx{symbole_brut}"

def on_ws_message(ws, message):
    data = json.loads(message)
    if 'tick' in data:
        symbole = data['tick']['symbol']
        prix = float(data['tick']['quote'])
        prix_en_direct[symbole] = prix 

def on_ws_open(ws):
    symboles = [prefixer_symbole(p) for p in CRYPTO_PAIRS + FOREX_PAIRS]
    for s in symboles:
        ws.send(json.dumps({"ticks": s, "subscribe": 1}))

def demarrer_flux_deriv():
    while True:
        try:
            ws = websocket.WebSocketApp("wss://ws.derivws.com/websockets/v3?app_id=1089", 
                                        on_open=on_ws_open, on_message=on_ws_message)
            ws.run_forever()
        except:
            time.sleep(5)

def obtenir_prix_actuel_deriv(symbole_brut):
    return prix_en_direct.get(prefixer_symbole(symbole_brut))

def obtenir_donnees_deriv(symbole_brut, granularity=300):
    symbole = prefixer_symbole(symbole_brut)
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
        req = {"ticks_history": symbole, "end": "latest", "count": 250, "style": "candles", "granularity": granularity}
        ws.send(json.dumps(req))
        history = json.loads(ws.recv())
        ws.close()
        return history.get('candles')
    except: return None

# ==========================================
# NOUVELLES FONCTIONS PRO (NEWS & MTF)
# ==========================================

def est_heure_de_news_dynamique():
    if not FMP_API_KEY: return False
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today}&to={today}&apikey={FMP_API_KEY}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            maintenant = datetime.datetime.utcnow()
            for event in res.json():
                if event.get('impact') == 'High':
                    e_time = datetime.datetime.strptime(event['date'], "%Y-%m-%d %H:%M:%S")
                    if abs((maintenant - e_time).total_seconds() / 60) <= 30: return True
    except: pass
    return False

def obtenir_tendance_M15(symbole_brut):
    candles = obtenir_donnees_deriv(symbole_brut, granularity=900)
    if not candles: return "NEUTRE"
    try:
        df_m15 = pd.DataFrame([{'close': float(c['close'])} for c in candles])
        ema_m15 = ta.trend.EMAIndicator(close=df_m15['close'], window=20).ema_indicator()
        if df_m15['close'].iloc[-1] > ema_m15.iloc[-1]: return "UP"
        elif df_m15['close'].iloc[-1] < ema_m15.iloc[-1]: return "DOWN"
    except: pass
    return "NEUTRE"

# ==========================================
# GESTIONNAIRE CENTRALISÉ DES TRADES & MARTINGALE
# ==========================================

def gestionnaire_trades():
    global stats_journee, cooldown_actifs, niveaux_martingale
    while True:
        maintenant = time.time()
        a_supprimer = []
        
        for chat_id, trade in trades_en_cours.items():
            symbole = trade['symbole']
            prix_actuel = obtenir_prix_actuel_deriv(symbole)
            if not prix_actuel: continue

            if trade['prix_entree'] is None and maintenant >= trade['heure_entree'] and maintenant < trade['heure_sortie']:
                trades_en_cours[chat_id]['prix_entree'] = prix_actuel

            if maintenant >= trade['heure_sortie']:
                prix_entree = trade.get('prix_entree')
                if prix_entree:
                    action = trade['action']
                    gagne = (action == "CALL" and prix_actuel > prix_entree) or (action == "PUT" and prix_actuel < prix_entree)
                    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
                    
                    if gagne:
                        niveaux_martingale[chat_id] = 0 # 🔥 RESET MARTINGALE
                        texte = f"✅ **VICTOIRE (ITM)**\n🚀 Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_actuel}`\n👤 Client : `{chat_id}`\n🔄 **Palier Réinitialisé (Base)**"
                        stats_journee['ITM'] += 1
                        stats_journee['details'].append(f"✅ {nom_paire} ({action})")
                        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
                    else:
                        # 🔥 GESTION MARTINGALE
                        palier_actuel = niveaux_martingale.get(chat_id, 0)
                        if palier_actuel < MAX_MARTINGALE:
                            niveaux_martingale[chat_id] = palier_actuel + 1
                            statut_mg = f"⚠️ **MARTINGALE ACTIVÉE (Palier {palier_actuel + 1})**"
                        else:
                            niveaux_martingale[chat_id] = 0 # Arrêt d'urgence
                            statut_mg = f"🛑 **MARTINGALE STOP (Retour à la base)**"
                            
                        texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_actuel}`\n👤 Client : `{chat_id}`\n{statut_mg}"
                        stats_journee['OTM'] += 1
                        stats_journee['details'].append(f"❌ {nom_paire} ({action})")
                        cooldown_actifs[symbole] = time.time()
                    
                    try: bot.send_message(chat_id, texte, parse_mode="Markdown") # Informe aussi l'utilisateur du résultat
                    except: pass
                    try: bot.send_message(ADMIN_ID, texte, parse_mode="Markdown") # Informe l'admin
                    except: pass
                
                a_supprimer.append(chat_id)
        
        for chat_id in a_supprimer:
            del trades_en_cours[chat_id]
            
        time.sleep(1)

# ==========================================
# MOTEUR D'ANALYSE ( V10 )
# ==========================================

def analyser_binaire_pro(symbole):
    if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole] < 3600):
        return f"⚠️ **SILENCIEUX ACTIF**", None, None, None, None, None, None, None

    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE NEWS", None, None, None, None, None, None, None

    candles = obtenir_donnees_deriv(symbole)
    if not candles: return "⚠️ Erreur connexion", None, None, None, None, None, None, None
    
    try:
        df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        
        tendance_m15 = obtenir_tendance_M15(symbole)

        df['taille_bougie'] = abs(df['open'] - df['close'])
        df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']
        
        df['adx'] = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14).adx()
        marche_en_mouvement = df['adx'].iloc[-1] > 20 

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        
        atr_actuel = df['atr'].iloc[-1]
        atr_moyen = df['atr'].rolling(window=20).mean().iloc[-1]
        
        last = df.iloc[-1]
        c = last['close']
        rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
        bb_h, bb_b = last['bb_haute'], last['bb_basse']
        
        rejet_haussier = last['meche_basse'] > (last['taille_bougie'] * 1.5)
        rejet_baissier = last['meche_haute'] > (last['taille_bougie'] * 1.5)

        tendance_haussiere = c > df['ema_200'].iloc[-1]
        tendance_baissiere = c < df['ema_200'].iloc[-1]

        prix_precedent_bas = df['low'].iloc[-15:-3].min()
        prix_precedent_haut = df['high'].iloc[-15:-3].max()
        rsi_precedent_bas = df['rsi'].iloc[-15:-3].min()
        rsi_precedent_haut = df['rsi'].iloc[-15:-3].max()

        divergence_haussiere = (last['low'] < prix_precedent_bas) and (last['rsi'] > rsi_precedent_bas)
        divergence_baissiere = (last['high'] > prix_precedent_haut) and (last['rsi'] < rsi_precedent_haut)

        action, confiance, bb_status, score_algo = None, 0, "Recherche setup...", 5
        
        if atr_actuel > (atr_moyen * 1.5): duree_secondes, expiration_texte = 120, "2 MINUTES (Vitesse Élevée ⚡)"
        elif atr_actuel < (atr_moyen * 0.8): duree_secondes, expiration_texte = 600, "10 MINUTES (Marché Lent 🐢)"
        else: duree_secondes, expiration_texte = 300, "5 MINUTES (Standard 💎)"

        if c <= bb_b and tendance_haussiere and tendance_m15 in ["UP", "NEUTRE"] and marche_en_mouvement and rejet_haussier and divergence_haussiere:
            action, confiance = "🟢 ACHAT (CALL)", 99
            score_algo, bb_status = 10, "Rejet Bas + DIVERGENCE + MTF M15 🚀"

        elif c >= bb_h and tendance_baissiere and tendance_m15 in ["DOWN", "NEUTRE"] and marche_en_mouvement and rejet_baissier and divergence_baissiere:
            action, confiance = "🔴 VENTE (PUT)", 99
            score_algo, bb_status = 10, "Rejet Haut + DIVERGENCE + MTF M15 ☄️"

        if action and score_algo >= 9: return action, confiance, expiration_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo
        else: return f"⚠️ Recherche de Divergence Parfaite...", None, None, None, None, None, None, None
            
    except: return None, None, None, None, None, None, None, None

# ==========================================
# COMMANDES TÉLÉGRAM ET MENUS VIP
# ==========================================

def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("✅ Accepter", callback_data=f"admin_accepter_{user_id}"), InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}"))
        try: bot.send_message(ADMIN_ID, f"🚨 **NOUVEAU CLIENT POTENTIEL** 🚨\n\n🆔 `{user_id}`", reply_markup=markup, parse_mode="Markdown")
        except: pass
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT** 🔒\n\n📲 **Contact: [@hermann1123](https://t.me/hermann1123)**", parse_mode="Markdown")

    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0) # Init martingale
    bot.send_message(message.chat.id, "🏴‍☠️ **TERMINAL PRIME - V10 (MARTINGALE)** 💸\n\nRadar institutionnel et système de recouvrement de pertes activés.", reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    bot.send_message(message.chat.id, "🕒 **GUIDE DES HORAIRES (GMT)** 🕒\n\n✅ 08h-11h : EUR/USD, GBP/USD\n🔥 13h30-16h30 : EUR/USD, AUD/USD\n🌉 20h-08h : AUD/JPY, USD/JPY, EUR/JPY", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    if datetime.datetime.now().weekday() >= 5:
        markup.add(InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD"))
        msg = "Mode Week-End 🪙 : Crypto :"
    else:
        markup.add(InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"), InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"))
        msg = "Mode Semaine 💱 : Sélectionne ta cible :"
    bot.send_message(message.chat.id, msg, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id] = actif
    nom_affiche = f"{actif[:3]}/{actif[3:]}"
    
    msg = bot.send_message(chat_id, "⏳ *Initialisation de l'analyse Élite...*", parse_mode="Markdown")
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action: return bot.edit_message_text(f"{action}", chat_id, msg.message_id)
    elif not action: return bot.edit_message_text("❌ Attente du setup parfait. Les filtres RSI/MTF bloquent l'entrée.", chat_id, msg.message_id)

    maintenant = time.time()
    secondes_restantes = (60 - datetime.datetime.now().second) + 60
    if (60 - datetime.datetime.now().second) < 15: secondes_restantes += 60
    heure_entree_dt = datetime.datetime.now() + datetime.timedelta(seconds=secondes_restantes)
    
    trades_en_cours[chat_id] = {
        'symbole': actif,
        'action': "CALL" if "ACHAT" in action else "PUT",
        'prix_entree': None, 
        'heure_entree': maintenant + secondes_restantes,
        'heure_sortie': maintenant + secondes_restantes + duree_secondes
    }

    # 🔥 CALCUL DE LA MISE AVEC MARTINGALE
    palier_actuel = niveaux_martingale.get(chat_id, 0)
    mise_base = CAPITAL_ACTUEL * 0.02
    mise_calculee = mise_base * (COEF_MARTINGALE ** palier_actuel)
    
    info_palier = "Base" if palier_actuel == 0 else f"Martingale {palier_actuel}"
    alerte_mg = "⚠️ **ATTENTION : TRADE DE RATTRAPAGE** ⚠️\n" if palier_actuel > 0 else ""

    signal = f"""💎 SIGNAL ÉLITE VALIDÉ V10 💎
──────────────────
🛰 **ACTIF :** {nom_affiche}
🎯 **ACTION :** {action}
⏳ **EXPIRATION :** {exp_texte}
──────────────────
🛡️ **CONFIRMATION :** {bb_status} ✅
──────────────────
📍 **ORDRE À : {heure_entree_dt.strftime("%H:%M:00")}** 👈
{alerte_mg}💵 **MISE :** {int(mise_calculee)}$ (Palier : {info_palier})"""

    bot.delete_message(chat_id, msg.message_id)
    bot.send_message(chat_id, signal, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

if __name__ == "__main__":
    keep_alive()
    threading.Thread(target=demarrer_flux_deriv, daemon=True).start()
    threading.Thread(target=gestionnaire_trades, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition V10 (Martingale) Démarrée.", flush=True)
    bot.infinity_polling()
