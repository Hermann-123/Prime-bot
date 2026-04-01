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

TELEGRAM_TOKEN = "8658287331:AAETKECfCtVZWhtRlVyVWXhp2BtjEHSenIs"
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_DEFAUT = 1000 

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}

capital_users = {} 
pertes_consecutives = {} 

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
    return "Terminal Prime VIP : Édition GOD MODE INSTITUTIONNEL (V5)"

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
        if expiration == "LIFETIME": return True
        if datetime.datetime.now() < expiration: return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVeuillez contacter l'administrateur.", parse_mode="Markdown")
            except: pass
            return False
    return False

def generer_cle():
    return f"PRIME-{''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))}"

# ==========================================
# MODULE 1 : FILTRE DE NEWS DYNAMIQUE (FMP)
# ==========================================

def est_heure_de_news_dynamique():
    """Scan le calendrier mondial pour esquiver les News à Impact Élevé"""
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
                    if diff <= 30: return True # Bouclier activé 30 min autour de la news
    except: pass
    return False

# ==========================================
# MODULE 2 : ANALYSE MULTI-TIMEFRAME (H1)
# ==========================================

def prefixer_symbole(symbole_brut):
    return f"cry{symbole_brut}" if symbole_brut in CRYPTO_PAIRS else f"frx{symbole_brut}"

def obtenir_tendance_H1(symbole_brut):
    """Analyse la tendance majeure sur 1 Heure"""
    symbole = prefixer_symbole(symbole_brut)
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
        req = {"ticks_history": symbole, "end": "latest", "count": 50, "style": "candles", "granularity": 3600}
        ws.send(json.dumps(req))
        res = json.loads(ws.recv())
        ws.close()
        if "candles" in res and len(res["candles"]) > 20:
            df = pd.DataFrame(res['candles'])
            df['close'] = df['close'].astype(float)
            ema20 = ta.trend.EMAIndicator(close=df['close'], window=20).ema_indicator()
            return "UP" if df['close'].iloc[-1] > ema20.iloc[-1] else "DOWN"
    except: pass
    return "NEUTRE"

def obtenir_donnees_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for _ in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 250, "style": "candles", "granularity": 300} # M5
            ws.send(json.dumps(req))
            history = json.loads(ws.recv())
            ws.close()
            if "error" not in history and "candles" in history: return history['candles']
        except: time.sleep(1)
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for _ in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 1, "style": "ticks"}
            ws.send(json.dumps(req))
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]: return float(res["history"]["prices"][0])
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
    global stats_journee, pertes_consecutives
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'): return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie: return

    prix_entree = trade['prix_entree']
    action = trade['action']

    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    type_emoji = "🪙" if symbole in CRYPTO_PAIRS else "💱"
    
    if gagne:
        texte = f"✅ **VICTOIRE (ITM)**\n🚀 Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['ITM'] += 1
        stats_journee['details'].append(f"✅ {type_emoji} {nom_paire} ({action})")
        pertes_consecutives[chat_id] = 0
    else:
        texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {type_emoji} {nom_paire} ({action})")
        pertes_consecutives[chat_id] = pertes_consecutives.get(chat_id, 0) + 1
    
    try: 
        bot.send_message(chat_id, texte, parse_mode="Markdown")
        bot.send_message(ADMIN_ID, texte, parse_mode="Markdown")
    except: pass
    if chat_id in trades_en_cours: del trades_en_cours[chat_id]

# ==========================================
# MOTEUR D'ANALYSE : IA INSTITUTIONNELLE
# ==========================================

def analyser_binaire_pro(symbole):
    # 1. Protection Anti-News
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE : Calendrier économique rouge (Impact Élevé). Radar coupé.", None, None, None, None, None, None, None

    # 2. Confluence Multi-Timeframe
    tendance_h1 = obtenir_tendance_H1(symbole)

    candles = obtenir_donnees_deriv(symbole)
    if not candles or len(candles) < 200: 
        return "⚠️ Impossible d'établir la connexion data.", None, None, None, None, None, None, None
    
    try:
        df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        
        # 3. Volume Spread Analysis (VSA Proxy par Momentum de corps)
        df['body'] = abs(df['close'] - df['open'])
        avg_body = df['body'].rolling(10).mean().iloc[-1]
        vsa_valide = df['body'].iloc[-1] > avg_body # Validation par la force du mouvement institutionnel

        # 4. Smart Money Concepts (Fair Value Gaps)
        fvg_haussier = df['low'].iloc[-1] > df['high'].iloc[-3]
        fvg_baissier = df['high'].iloc[-1] < df['low'].iloc[-3]

        last = df.iloc[-1]
        c = last['close']
        rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
        bb_h, bb_b, ema_200 = last['bb_haute'], last['bb_basse'], last['ema_200']

        tendance_haussiere = c > ema_200 and tendance_h1 in ["UP", "NEUTRE"]
        tendance_baissiere = c < ema_200 and tendance_h1 in ["DOWN", "NEUTRE"]

        action, score_algo, bb_status = None, 5, "Zone Neutre"
        
        # 🟢 LOGIQUE ACHAT CONFLUENCE
        if c <= bb_b and tendance_haussiere:
            score_algo = 7
            bb_status = "Rejet Bande Basse"
            if vsa_valide: score_algo += 1
            if fvg_haussier: 
                score_algo += 2
                bb_status += " + SMC (FVG)"
            if score_algo >= 8: action = "🟢 ACHAT (CALL)"

        # 🔴 LOGIQUE VENTE CONFLUENCE
        elif c >= bb_h and tendance_baissiere:
            score_algo = 7
            bb_status = "Rejet Bande Haute"
            if vsa_valide: score_algo += 1
            if fvg_baissier:
                score_algo += 2
                bb_status += " + SMC (FVG)"
            if score_algo >= 8: action = "🔴 VENTE (PUT)"

        if action: 
            return action, 99, "5 MINUTES ⏱", 300, rsi_val, stoch_val, bb_status, score_algo
        else: 
            return f"⚠️ En attente d'alignement institutionnel (H1/M5).", None, None, None, None, None, None, None
            
    except: return None, None, None, None, None, None, None, None

# ==========================================
# LE SCANNER AUTOMATIQUE DE L'OMBRE
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: continue
                
            devises_a_surveiller = CRYPTO_PAIRS if datetime.datetime.now().weekday() >= 5 else FOREX_PAIRS
            
            for actif in devises_a_surveiller:
                action, confiance, exp, duree, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif)
                if action and "⚠️" not in action:
                    temps_actuel = time.time()
                    if actif in derniere_alerte_auto and (temps_actuel - derniere_alerte_auto[actif] < 3600): continue
                    derniere_alerte_auto[actif] = temps_actuel
                    nom_affiche = f"{actif[:3]}/{actif[3:]}"
                    
                    markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"📊 Analyser {nom_affiche}", callback_data=f"set_{actif}"))
                    alerte_msg = f"🔥 **ALERTE GOD MODE (M5)** 🔥\n\nConfiguration lourde détectée sur **{nom_affiche}**.\n\n👇 *Clique pour déclencher la frappe !*"
                        
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except: pass
        except: pass

# ==========================================
# GESTIONNAIRE D'HORAIRES ET DE BILAN (22H00)
# ==========================================

def gestion_horaires_et_bilan():
    global stats_journee, bilan_envoye_aujourdhui, transition_nuit_envoyee, transition_jour_envoyee
    while True:
        try:
            maintenant = datetime.datetime.now()
            heure, minute, jour_semaine = maintenant.hour, maintenant.minute, maintenant.weekday()
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]

            if jour_semaine < 5: 
                if heure == 20 and minute == 0 and not transition_nuit_envoyee:
                    texte_nuit = "🌉 **MODE ASIATIQUE ACTIVÉ** 🌉\n\nLes volumes s'effondrent sur l'Europe. Chasse de nuit activée."
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, texte_nuit, parse_mode="Markdown")
                        except: pass
                    transition_nuit_envoyee, transition_jour_envoyee = True, False

                elif heure == 8 and minute == 0 and not transition_jour_envoyee:
                    texte_jour = "☀️ **MODE EUROPE/US ACTIVÉ** ☀️\n\nRetour de la volatilité majeure."
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, texte_jour, parse_mode="Markdown")
                        except: pass
                    transition_jour_envoyee, transition_nuit_envoyee = True, False

            if heure == 22 and minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                if total_trades > 0:
                    winrate = round((stats_journee['ITM'] / total_trades) * 100)
                    texte_bilan_admin = f"📊 **BILAN VIP DE LA JOURNÉE (V5)** 📊\n──────────────────\n🎯 **Total :** {total_trades}\n✅ **ITM :** {stats_journee['ITM']}\n❌ **OTM :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n──────────────────\n"
                    for detail in stats_journee['details']: texte_bilan_admin += f"{detail}\n"
                    try: bot.send_message(ADMIN_ID, texte_bilan_admin, parse_mode="Markdown")
                    except: pass
                stats_journee, bilan_envoye_aujourdhui = {'ITM': 0, 'OTM': 0, 'details': []}, True
            elif heure == 23: bilan_envoye_aujourdhui = False
            time.sleep(30)
        except: time.sleep(60)

# ==========================================
# COMMANDES ADMIN ET CONFIGURATION VIP
# ==========================================

@bot.message_handler(commands=['capital'])
def configurer_capital(message):
    if not est_autorise(message.chat.id): return
    try:
        montant = float(message.text.split()[1])
        capital_users[message.chat.id] = montant
        bot.send_message(message.chat.id, f"🏦 **Capital configuré à {montant}$**\nLe bot calculera automatiquement vos mises à 2% ({int(montant * 0.02)}$).", parse_mode="Markdown")
    except:
        bot.send_message(message.chat.id, "⚠️ **Erreur de format.** \nUtilisation : `/capital 500`", parse_mode="Markdown")

@bot.message_handler(commands=['reset'])
def reinitialiser_pertes(message):
    if not est_autorise(message.chat.id): return
    pertes_consecutives[message.chat.id] = 0
    bot.send_message(message.chat.id, "🔄 **Coupe-circuit réinitialisé.**\nReprise du radar.", parse_mode="Markdown")

@bot.message_handler(commands=['panel'])
def admin_panel(message):
    if message.chat.id != ADMIN_ID: return
    bot.send_message(ADMIN_ID, "Admin Panel 🔥\nTerminal Actif.")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("PRIME-"))
def activer_cle(message):
    cle = message.text.strip()
    if cle in cles_generees:
        infos_cle = cles_generees[cle]
        if infos_cle["user_id"] != message.chat.id:
            bot.send_message(message.chat.id, "❌ **ACCÈS REFUSÉ**", parse_mode="Markdown")
            return
        jours = infos_cle["jours"]
        if jours == 999:
            utilisateurs_autorises[message.chat.id] = "LIFETIME"
            duree_texte = "À VIE 👑"
        else:
            expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
            utilisateurs_autorises[message.chat.id] = expiration
            duree_texte = f"jusqu'au {expiration.strftime('%d/%m/%Y à %H:%M')}"
        del cles_generees[cle] 
        bot.send_message(message.chat.id, f"✅ **CLÉ ACCEPTÉE !** 🎉\nAbonnement activé {duree_texte}.\nTapez /start pour lancer.", parse_mode="Markdown")
    else: bot.send_message(message.chat.id, "❌ **Clé invalide.**", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    if call.from_user.id != ADMIN_ID: return
    action, user_id = call.data.split("_")[1], int(call.data.split("_")[2])
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"),
            InlineKeyboardButton("2 Semaines 🔥", callback_data=f"gen_14_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"),
            InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Utilisateur `{user_id}` accepté.\nChoisis la durée :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    elif action == "refuser": bot.edit_message_text("❌ Demande refusée.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    if call.from_user.id != ADMIN_ID: return
    jours, user_id = int(call.data.split("_")[1]), int(call.data.split("_")[2])
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    duree_texte = {7:"1 Sem", 14:"2 Sem", 30:"1 Mois", 999:"À VIE"}.get(jours, f"{jours} Jours")
    bot.edit_message_text(f"🔑 **CLÉ :** `{cle}`\n⏳ Durée : {duree_texte}\n👤 ID : `{user_id}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

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
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Accepter", callback_data=f"admin_accepter_{user_id}"))
        try: bot.send_message(ADMIN_ID, f"🚨 **NOUVEAU CLIENT** 🚨\n🆔 `{user_id}`", reply_markup=markup, parse_mode="Markdown")
        except: pass
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**\nContactez l'administrateur.", parse_mode="Markdown")

    utilisateurs_actifs.add(user_id)
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - V5 ÉLITE (M5)** 🔥
    
Le radar est désormais couplé à une intelligence institutionnelle (SMC, VSA, Multi-Timeframe) et à l'API Financial Modeling Prep.

📖 **MOTEUR SÉCURISÉ :**
➤ `/capital [montant]` : Ajuste automatiquement vos mises.
➤ **Coupe-circuit :** Arrêt auto après 3 pertes.
➤ **RÉGLEZ VOTRE COURTIER SUR 5 MINUTES !** ⏱️"""
    bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    bot.send_message(message.chat.id, "🕒 **MOTEUR DYNAMIQUE**\nLe bot gère désormais les News automatiquement via API. Vous êtes protégé des manipulations en temps réel.", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    if datetime.datetime.now().weekday() >= 5:
        markup.add(InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD"))
        msg = "Mode Week-End 🪙 (Crypto) :"
    else:
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
            InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
        )
        msg = "Mode Semaine 💱 (Forex) :"
    bot.send_message(message.chat.id, msg, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return

    if pertes_consecutives.get(chat_id, 0) >= 3:
        bot.send_message(chat_id, "🚫 **STOP LOSS ATTEINT** 🚫\n3 pertes consécutives. Terminal verrouillé pour protéger votre capital.\nTapez `/reset` pour forcer.", parse_mode="Markdown")
        return

    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id] = actif
    nom_affiche = f"{actif[:3]}/{actif[3:]}"
    
    try:
        msg = bot.send_message(chat_id, "⏳ *Analyse Multi-Timeframe & SMC...*", parse_mode="Markdown")
        time.sleep(1)
        bot.edit_message_text("⚙️ *Requête API de Calendrier Économique en cours...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(1)
    except: return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Échec Data. Relancez.", chat_id, msg.message_id)
        except: pass
        return

    maintenant = datetime.datetime.now()
    secondes_restantes = (60 - maintenant.second) + 60
    if (60 - maintenant.second) < 15: secondes_restantes += 60
    heure_entree_dt = maintenant + datetime.timedelta(seconds=secondes_restantes)
    
    capital_client = capital_users.get(chat_id, CAPITAL_DEFAUT)
    mise_recommandee = max(1, int(capital_client * 0.02))

    signal = f"""🔥 **SIGNAL PRO ÉLITE (M5)** 🔥
──────────────────
🛰 **ACTIF :** `{nom_affiche}`
🎯 **ACTION :** `{action}`
⏳ **EXPIRATION :** `{exp_texte}`
──────────────────
🧠 **CONFLUENCE H1/M5 :** `{score}/10`
🛡️ **STATUT SMC/VSA :** `{bb_status}`

📊 **VALIDATION TECHNIQUE :**
➤ **RSI :** 🟢 Validé ({rsi_val})
➤ **Stochastique :** 🟢 Validé ({stoch_val})
──────────────────
📍 **ORDRE À : {heure_entree_dt.strftime("%H:%M:00")}** 👈
💵 **MISE RECOMMANDÉE :** `{mise_recommandee}$`
──────────────────
⚠️ *Préparez-vous.*"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except: pass

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[chat_id] = {'symbole': actif, 'action': action_simplifiee}
    Timer(secondes_restantes, relever_prix_entree, args=[chat_id, actif]).start()
    Timer(secondes_restantes + duree_secondes, verifier_resultat, args=[chat_id]).start()

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestion_horaires_et_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition GOD MODE INSTITUTIONNEL (V5) Démarrée.", flush=True)
    bot.infinity_polling()
