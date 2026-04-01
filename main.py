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

TELEGRAM_TOKEN = "8658287331:AAHRobSFxOzGoNUoy8-C9h_opnWS7igEtaQ"
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
    return "Terminal Prime VIP : Édition GOD MODE (V5 - Quotex M5)"

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
    if user_id == ADMIN_ID:
        return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME":
            return True
        if datetime.datetime.now() < expiration:
            return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé. Veuillez contacter le fondateur [@hermann1123](https://t.me/hermann1123).", parse_mode="Markdown")
            except: pass
            return False
    return False

def generer_cle():
    caracteres = string.ascii_uppercase + string.digits
    aleatoire = ''.join(random.choice(caracteres) for _ in range(8))
    return f"PRIME-{aleatoire}"

def generer_jauge(pourcentage):
    if pourcentage >= 99:
        return "[██████████] 👑 MAX"
    pleins = int(pourcentage / 10)
    vides = 10 - pleins
    return f"[{'█' * pleins}{'░' * vides}] {pourcentage}%"

# ==========================================
# NOUVELLES FONCTIONS PRO (NEWS & H1)
# ==========================================

def est_heure_de_news_dynamique():
    """Scan le calendrier mondial pour les news à High Impact"""
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
                    if diff <= 30: # Bloque 30 min avant et après
                        return True
    except: pass
    return False

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
            ema20 = ta.trend.EMAIndicator(close=df['close'].astype(float), window=20).ema_indicator()
            return "UP" if float(df['close'].iloc[-1]) > ema20.iloc[-1] else "DOWN"
    except: pass
    return "NEUTRE"

# ==========================================
# ROUTEUR API DERIV (FOREX VS CRYPTO)
# ==========================================

def prefixer_symbole(symbole_brut):
    if symbole_brut in CRYPTO_PAIRS:
        return f"cry{symbole_brut}"
    return f"frx{symbole_brut}"

def obtenir_donnees_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            # PASSAGE EN M5 (BOUGIES DE 5 MINUTES : granularity 300)
            req = {"ticks_history": symbole, "end": "latest", "count": 250, "style": "candles", "granularity": 300}
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
# SYSTÈME DE VÉRIFICATION ITM/OTM
# ==========================================

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee
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
    else:
        texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {type_emoji} {nom_paire} ({action})")
    
    try: bot.send_message(ADMIN_ID, texte, parse_mode="Markdown")
    except: pass
        
    if chat_id in trades_en_cours: del trades_en_cours[chat_id]

# ==========================================
# MOTEUR D'ANALYSE ( GOD MODE INSTITUTIONNEL )
# ==========================================

def analyser_binaire_pro(symbole):
    # 1. Filtre News Dynamique
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE NEWS : Marché manipulé, radar coupé.", None, None, None, None, None, None, None

    # 2. Tendance de Fond
    tendance_h1 = obtenir_tendance_H1(symbole)
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return "⚠️ Impossible de se connecter au marché", None, None, None, None, None, None, None
    
    try:
        df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        
        # 3. CORRECTION VSA : Utilisation du Momentum (corps de la bougie) au lieu du count Deriv
        df['corps_bougie'] = abs(df['close'] - df['open'])

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()

        # Calcul VSA & SMC
        corps_moyen = df['corps_bougie'].rolling(window=10).mean().iloc[-1]
        vsa_valide = df['corps_bougie'].iloc[-1] > corps_moyen 
        
        fvg_haussier = df['low'].iloc[-1] > df['high'].iloc[-3]
        fvg_baissier = df['high'].iloc[-1] < df['low'].iloc[-3]

        last = df.iloc[-1]
        c = last['close']
        rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
        bb_h, bb_b, ema_200 = last['bb_haute'], last['bb_basse'], last['ema_200']

        tendance_haussiere = c > ema_200 and tendance_h1 in ["UP", "NEUTRE"]
        tendance_baissiere = c < ema_200 and tendance_h1 in ["DOWN", "NEUTRE"]

        action, confiance, bb_status, score_algo = None, 0, "Au Milieu", 5
        duree_secondes = 300
        expiration_texte = "5 MINUTES ⏱"

        if c <= bb_b and tendance_haussiere:
            action, confiance = "🟢 ACHAT (CALL)", 99
            score_algo = 7
            bb_status = "Bande Basse"
            if vsa_valide: score_algo += 1; bb_status += " + Vol VSA"
            if fvg_haussier: score_algo += 2; bb_status += " + SMC"

        elif c >= bb_h and tendance_baissiere:
            action, confiance = "🔴 VENTE (PUT)", 99
            score_algo = 7
            bb_status = "Bande Haute"
            if vsa_valide: score_algo += 1; bb_status += " + Vol VSA"
            if fvg_baissier: score_algo += 2; bb_status += " + SMC"

        if action and score_algo >= 8:
            return action, confiance, expiration_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo
        else:
            return f"⚠️ Marché instable ou manipulé. Filtrage actif.", None, None, None, None, None, None, None
            
    except Exception as e: 
        return None, None, None, None, None, None, None, None

# ==========================================
# LE SCANNER AUTOMATIQUE DE L'OMBRE
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: continue
                
            maintenant = datetime.datetime.now()
            jour_semaine = maintenant.weekday() 
            devises_a_surveiller = CRYPTO_PAIRS if jour_semaine >= 5 else FOREX_PAIRS
            
            for actif in devises_a_surveiller:
                action, confiance, exp, duree, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif)
                if action and "⚠️" not in action and confiance:
                    temps_actuel = time.time()
                    # LE SILENCIEUX : Blocage de 1 Heure (3600 secondes) par devise
                    if actif in derniere_alerte_auto and (temps_actuel - derniere_alerte_auto[actif] < 3600): continue
                    derniere_alerte_auto[actif] = temps_actuel
                    nom_affiche = f"{actif[:3]}/{actif[3:]}"
                    
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"📊 Analyser {nom_affiche}", callback_data=f"set_{actif}"))
                    
                    if score >= 9: alerte_msg = f"🔥 **ALERTE GOD MODE (M5)** 🔥\n\nConfiguration mathématique lourde (Score {score}/10) sur **{nom_affiche}**.\n\n👇 *Clique sur le bouton pour déclencher la frappe !*"
                    else: alerte_msg = f"🚨 **OPPORTUNITÉ VIP FILTRÉE (M5)** 🚨\n\nLe radar a esquivé les pièges. Signal propre sur **{nom_affiche}** (Score {score}/10).\n\n👇 *Clique sur le bouton pour l'analyse !*"
                        
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
                    texte_nuit = "🌉 **TRANSITION DE SESSION : MODE ASIATIQUE ACTIVÉ** 🌉\n\nLes volumes s'effondrent sur l'Europe. Le Terminal Prime bascule ses radars exclusivement sur l'Asie.\n\n*La chasse continue de nuit. Restez concentrés.* 🥷"
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, texte_nuit, parse_mode="Markdown")
                        except: pass
                    transition_nuit_envoyee, transition_jour_envoyee = True, False

                elif heure == 8 and minute == 0 and not transition_jour_envoyee:
                    texte_jour = "☀️ **TRANSITION DE SESSION : MODE EUROPE/US ACTIVÉ** ☀️\n\nOuverture des marchés majeurs. La volatilité est de retour.\n\n*Bonne journée de trading à tous les VIP !* 🚀"
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, texte_jour, parse_mode="Markdown")
                        except: pass
                    transition_jour_envoyee, transition_nuit_envoyee = True, False

            if heure == 22 and minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                if total_trades > 0:
                    winrate = round((stats_journee['ITM'] / total_trades) * 100)
                    texte_bilan_admin = f"📊 **BILAN VIP DE LA JOURNÉE (GOD MODE M5)** 📊\n──────────────────\n🎯 **Total Signaux :** {total_trades}\n✅ **Victoires (ITM) :** {stats_journee['ITM']}\n❌ **Pertes (OTM) :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n──────────────────\n"
                    for detail in stats_journee['details']: texte_bilan_admin += f"{detail}\n"
                    try: bot.send_message(ADMIN_ID, texte_bilan_admin, parse_mode="Markdown")
                    except: pass
                stats_journee, bilan_envoye_aujourdhui = {'ITM': 0, 'OTM': 0, 'details': []}, True
            elif heure == 23: bilan_envoye_aujourdhui = False
            time.sleep(30)
        except: time.sleep(60)

# ==========================================
# COMMANDES ADMIN ET GÉNÉRATION DE CLÉS
# ==========================================

@bot.message_handler(commands=['panel'])
def admin_panel(message):
    if message.chat.id != ADMIN_ID: return
    bot.send_message(ADMIN_ID, f"Admin Panel 🔥\nCapital actuel : {CAPITAL_ACTUEL}$")

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
        bot.send_message(message.chat.id, f"✅ **CLÉ ACCEPTÉE !** 🎉\n\nVotre abonnement est activé {duree_texte}.\n\nTapez /start pour lancer le Terminal Prime.", parse_mode="Markdown")
    else: bot.send_message(message.chat.id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")

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
            InlineKeyboardButton("2 Mois 💎", callback_data=f"gen_60_{user_id}"),
            InlineKeyboardButton("3 Mois ✨", callback_data=f"gen_90_{user_id}"),
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
    duree_texte = {7:"1 Semaine", 14:"2 Semaines", 30:"1 Mois", 60:"2 Mois", 90:"3 Mois", 999:"À VIE"}.get(jours, f"{jours} Jours")
    msg = f"🔑 **CLÉ GÉNÉRÉE** 🔑\n\n⏳ Durée : {duree_texte}\n👤 ID : `{user_id}`\n\nCopie ce message à ton client :\n\n`{cle}`"
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

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
        try: bot.send_message(ADMIN_ID, f"🚨 **NOUVEAU CLIENT POTENTIEL** 🚨\n\n🆔 `{user_id}`\n\nGénérer un abonnement ?", reply_markup=markup, parse_mode="Markdown")
        except: pass
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT - TERMINAL PRIVÉ** 🔒\n\nCe système est une intelligence artificielle de trading haute précision sous licence payante.\n\n📲 **Pour obtenir votre clé d'accès (Abonnement), veuillez contacter le fondateur : [@hermann1123](https://t.me/hermann1123)**", parse_mode="Markdown", disable_web_page_preview=True)

    utilisateurs_actifs.add(user_id)
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - ÉDITION GOD MODE (V3 - M5)** 🔥
    
Bienvenue dans le radar institutionnel. Ce système est doté d'un filtre Anti-Manipulation et analyse désormais les bougies de **5 MINUTES (M5)**.

📖 **MODE D'EMPLOI :**
1️⃣ **SÉLECTION :** Clique sur "📊 CHOISIR UNE DEVISE" pour verrouiller un actif.
2️⃣ **RADAR :** Clique sur "🚀 LANCER L'ANALYSE" pour déclencher le scan Sniper.
3️⃣ **DISCIPLINE :** N'oublie pas : 2% de mise maximum et stop total après 3 pertes dans une session.

⚠️ **ATTENTION : RÉGLEZ L'HORLOGE DE VOTRE COURTIER SUR 5 MINUTES !** ⏱️"""
    bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **GUIDE DES HORAIRES DE TRADING (Heure GMT)** 🕒

✅ **SESSION SEMAINE 1 (08h00 - 11h00) :** EUR/USD, GBP/USD
🔥 **SESSION SEMAINE 2 (13h30 - 16h30) :** EUR/USD, AUD/USD
🌉 **SESSION SEMAINE 3 (20h00 - 08h00) :** AUD/JPY, USD/JPY, EUR/JPY
🪙 **SESSION WEEK-END (Samedi/Dimanche) :** CRYPTOMONNAIES UNIQUEMENT

*Rappel de Discipline : Fixe-toi tes 2% de mise max et arrête-toi après 3 pertes !*"""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    jour_semaine = datetime.datetime.now().weekday()
    
    if jour_semaine >= 5:
        markup.add(
            InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"),
            InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"),
            InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD")
        )
        message_texte = "Mode Week-End 🪙 : Les banques sont fermées. Sélectionne la Crypto :"
    else:
        # Clavier mis à jour pour correspondre à Quotex/Pocket Broker
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
            InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
        )
        message_texte = "Mode Semaine 💱 : Arsenal Quotex synchronisé. Sélectionne ta cible :"
    bot.send_message(message.chat.id, message_texte, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id] = actif
    nom_affiche = f"{actif[:3]}/{actif[3:]}"
    
    try:
        msg = bot.send_message(chat_id, "⏳ *Initialisation du scan GOD MODE (M5)...*", parse_mode="Markdown")
        time.sleep(1)
        bot.edit_message_text("⚙️ *Lecture de l'Order Flow sur 5 Minutes et détection de pièges...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(1)
    except: return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Échec de la récupération des données Deriv. Relance l'analyse.", chat_id, msg.message_id)
        except: pass
        return

    # ⚡ INJECTION DU MOTEUR CHRONOMÉTRIQUE : 2 MINUTES DE PRÉPARATION
    maintenant = datetime.datetime.now()
    secondes_restantes = (60 - maintenant.second) + 60
    if (60 - maintenant.second) < 15: secondes_restantes += 60
    heure_entree_dt = maintenant + datetime.timedelta(seconds=secondes_restantes)
    
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)
    titre_signal = "🔥 SIGNAL VALIDÉ GOD MODE (M5) 🔥" if score >= 8 else "⚡ SIGNAL VIP SÉCURISÉ (M5) ⚡"

    signal = f"""{titre_signal}
──────────────────
🛰 **ACTIF :** {nom_affiche}
🎯 **ACTION :** {action}
⏳ **EXPIRATION :** {exp_texte}
──────────────────
🧠 **SCORE ALGO :** {score}/10
🛡️ **FILTRE ANTI-PIÈGE :** VALIDÉ ✅

📊 **VALIDATION MULTI-CERVEAU :**
➤ **RSI :** 🟢 Validé ({rsi_val})
➤ **Stochastique :** 🟢 Validé ({stoch_val})
➤ **Volume/Trend :** 🟢 {bb_status}
──────────────────
📍 **ORDRE À : {heure_entree_dt.strftime("%H:%M:00")}** 👈
💵 **MISE RECOMMANDÉE :** {mise_recommandee}$ (2%)
──────────────────
⚠️ *Trade validé en M5. Vous avez 2 minutes pour préparer l'ordre de 5 MINUTES sur Quotex.*"""

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

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    commande = message.text.split()
    if len(commande) < 2: return bot.send_message(message.chat.id, "⚠️ Précise la devise. Exemple : `/vision EURUSD`", parse_mode="Markdown")
    symbole = commande[1].upper()
    try: msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X de {symbole} (M5)...*", parse_mode="Markdown")
    except: return
    
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return bot.edit_message_text("⚠️ Impossible de scanner (manque de données).", message.chat.id, msg.message_id)
        
    try:
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        ema_200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        position_bb = "🔴 Au Plafond (Touche la bande haute)" if prix_actuel >= bb_haute else "🟢 Au Plancher (Touche la bande basse)" if prix_actuel <= bb_basse else "⚪ Au Milieu (Zone neutre)"
        nom_affiche = f"{symbole[:3]}/{symbole[3:]}"
        
        rapport = f"""👁️ **VISION RAYONS X (M5) : {nom_affiche}** 👁️
──────────────────
💰 **Prix actuel :** `{prix_actuel:.5f}`
🛡️ **EMA 200 (Tendance) :** `{ema_200:.5f}`
📏 **Position Bollinger :** {position_bb}

📊 **Niveau RSI :** `{rsi:.2f}` *(Rappel: >60 = Surchauffe, <40 = Essoufflé)*
📉 **Niveau Stochastique :** `{stoch_k:.2f}` *(Rappel: >80 = Surachat, <20 = Survente)*
──────────────────"""
        rapport += "\n⚠️ *Le prix teste les limites sur 5 min, tiens-toi prêt !*" if position_bb != "⚪ Au Milieu (Zone neutre)" else "\n💤 *Le marché respire tranquillement.*"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except Exception as e: bot.edit_message_text(f"❌ Erreur : {e}", message.chat.id, msg.message_id)

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestion_horaires_et_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition GOD MODE (V3 - M5) Démarrée.", flush=True)
    bot.infinity_polling()
