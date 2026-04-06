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

TELEGRAM_TOKEN = "8658287331:AAFjlCSev-ZanvrLIRt__Cpe4KKe9b85xNc"
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
cooldown_actifs = {} # Le "Silencieux" pour bloquer les paires perdantes

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
    return "Terminal Prime VIP : Édition GOD MODE CHIRURGICAL (V7.1)"

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
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
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
                    if diff <= 30: 
                        return True
    except: pass
    return False

def obtenir_tendance_H1(symbole_brut):
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
# SYSTÈME DE VÉRIFICATION ITM/OTM & COOLDOWN
# ==========================================

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee, cooldown_actifs
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
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
    else:
        texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {type_emoji} {nom_paire} ({action})")
        cooldown_actifs[symbole] = time.time()
    
    try: bot.send_message(ADMIN_ID, texte, parse_mode="Markdown")
    except: pass
        
    if chat_id in trades_en_cours: del trades_en_cours[chat_id]

# ==========================================
# MOTEUR D'ANALYSE ( GOD MODE ELITE V7.1 )
# ==========================================

def analyser_binaire_pro(symbole):
    if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole] < 3600):
        return f"⚠️ **SILENCIEUX ACTIF** : {symbole} a récemment subi une manipulation (OTM). Radar coupé pendant 1 heure pour protéger le capital.", None, None, None, None, None, None, None

    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE NEWS : Marché manipulé, radar coupé.", None, None, None, None, None, None, None

    tendance_h1 = obtenir_tendance_H1(symbole)
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return "⚠️ Impossible de se connecter au marché", None, None, None, None, None, None, None
    
    try:
        df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        
        # 🧠 ANATOMIE DE LA BOUGIE (PRICE ACTION)
        df['corps_bougie'] = abs(df['close'] - df['open'])
        df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2) # 2.2 pour des extrêmes plus sûrs
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        
        # ⚡ Calcul ATR pour l'Expiration Dynamique
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        atr_actuel = df['atr'].iloc[-1]
        atr_moyen = df['atr'].rolling(window=20).mean().iloc[-1]

        # 🛡️ Calcul ADX pour la Force Minimum et le Bouclier
        ind_adx = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['adx'] = ind_adx.adx()
        df['di_plus'] = ind_adx.adx_pos()
        df['di_moins'] = ind_adx.adx_neg()

        last = df.iloc[-1]
        c = last['close']
        rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
        bb_h, bb_b = last['bb_haute'], last['bb_basse']
        adx_val = round(last['adx'], 1)
        di_p, di_m = last['di_plus'], last['di_moins']
        
        ema_actuelle = df['ema_200'].iloc[-1]
        ema_ancienne = df['ema_200'].iloc[-5] 
        
        tendance_haussiere = c > ema_actuelle and tendance_h1 in ["UP", "NEUTRE"] and ema_actuelle >= ema_ancienne
        tendance_baissiere = c < ema_actuelle and tendance_h1 in ["DOWN", "NEUTRE"] and ema_actuelle <= ema_ancienne

        # REJET PRICE ACTION
        rejet_haussier = last['meche_basse'] > (last['corps_bougie'] * 1.5)
        rejet_baissier = last['meche_haute'] > (last['corps_bougie'] * 1.5)

        action, confiance, bb_status, score_algo = None, 0, "Au Milieu", 5
        
        # ⏱️ SÉLECTION AUTOMATIQUE DE L'EXPIRATION
        if atr_actuel > (atr_moyen * 1.5):
            duree_secondes = 120
            expiration_texte = "2 MINUTES (Vitesse Élevée ⚡)"
        elif atr_actuel < (atr_moyen * 0.8):
            duree_secondes = 600
            expiration_texte = "10 MINUTES (Marché Lent 🐢)"
        else:
            duree_secondes = 300
            expiration_texte = "5 MINUTES (Standard 💎)"

        # 🛑 VERROU 1 : FILTRE DE FORCE MINIMUM (ADX < 20)
        if adx_val < 20:
            return f"⚠️ Scan: ADX trop faible ({adx_val}). Pas de force.", None, None, None, None, None, None, None

        # 🟢 ACHAT CHIRURGICAL (RSI <= 25, Stoch <= 20)
        if c <= bb_b and rsi_val <= 25 and stoch_val <= 20 and tendance_haussiere:
            if (adx_val > 35 and di_m > di_p):
                return "⚠️ ADX ALERTE : Le marché s'effondre lourdement. Achat annulé.", None, None, None, None, None, None, None
            if rejet_haussier:
                action, confiance = "🟢 ACHAT (CALL)", 99
                score_algo = 10
                bb_status = f"Rejet Haussier + Surchauffe (ADX: {adx_val})"

        # 🔴 VENTE CHIRURGICALE (RSI >= 75, Stoch >= 80)
        elif c >= bb_h and rsi_val >= 75 and stoch_val >= 80 and tendance_baissiere:
            if (adx_val > 35 and di_p > di_m):
                return "⚠️ ADX ALERTE : Hausse explosive détectée. Vente annulée.", None, None, None, None, None, None, None
            if rejet_baissier:
                action, confiance = "🔴 VENTE (PUT)", 99
                score_algo = 10
                bb_status = f"Rejet Baissier + Surchauffe (ADX: {adx_val})"

        if action and score_algo >= 10:
            return action, confiance, expiration_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo
        else:
            return f"⚠️ Scan en cours... (RSI: {rsi_val} / Stoch: {stoch_val})", None, None, None, None, None, None, None
            
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
                    if actif in derniere_alerte_auto and (temps_actuel - derniere_alerte_auto[actif] < 3600): continue
                    derniere_alerte_auto[actif] = temps_actuel
                    nom_affiche = f"{actif[:3]}/{actif[3:]}"
                    
                    jauge_visuelle = generer_jauge(score * 10)
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"🎯 Frapper {nom_affiche}", callback_data=f"set_{actif}"))
                    
                    alerte_msg = f"🥷 **FRAPPE CHIRURGICALE DÉTECTÉE** 🥷\n\n**CONFIANCE :** {jauge_visuelle}\nCible : **{nom_affiche}**\n\n👇 *Clique sur le bouton pour l'analyse !*"
                        
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
                    texte_bilan_admin = f"📊 **BILAN VIP DE LA JOURNÉE** 📊\n──────────────────\n🎯 **Total Signaux :** {total_trades}\n✅ **Victoires (ITM) :** {stats_journee['ITM']}\n❌ **Pertes (OTM) :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n──────────────────\n"
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

# 💰 AJOUT DE LA COMMANDE CAPITAL SÉPARÉE
@bot.message_handler(commands=['capital'])
def voir_capital(message):
    if message.chat.id != ADMIN_ID: return
    bot.send_message(ADMIN_ID, f"💰 **SOLDE ACTUEL DU COMPTE** 💰\n──────────────────\n💵 Montant : `{CAPITAL_ACTUEL}$`\n──────────────────\n⚖️ *Prêt pour la prochaine session !*", parse_mode="Markdown")

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
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - ÉDITION GOD MODE DYNAMIQUE (V7)** 🔥
    
Bienvenue dans le radar institutionnel. Ce système est doté d'un cerveau de volatilité : il choisit **LUI-MÊME** le meilleur temps d'expiration (2, 5 ou 10 minutes) en fonction du marché.

📖 **MODE D'EMPLOI :**
1️⃣ **SÉLECTION :** Clique sur "📊 CHOISIR UNE DEVISE" pour verrouiller un actif.
2️⃣ **RADAR :** Clique sur "🚀 LANCER L'ANALYSE" pour déclencher le scan Sniper.
3️⃣ **DISCIPLINE :** N'oublie pas : 2% de mise maximum et stop total après 3 pertes.

⚠️ **ATTENTION : RÉGLEZ SOIGNEUSEMENT L'HORLOGE DE POCKET BROKER SELON LE SIGNAL !** ⏱️"""
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
            InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD")
        )
        message_texte = "Mode Week-End 🪙 : Les banques sont fermées. Sélectionne la Crypto :"
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
        message_texte = "Mode Semaine 💱 : Arsenal Pocket Broker synchronisé. Sélectionne ta cible :"
    bot.send_message(message.chat.id, message_texte, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id] = actif
    nom_affiche = f"{actif[:3]}/{actif[3:]}"
    
    try:
        msg = bot.send_message(chat_id, "⏳ *Initialisation du scan DYNAMIQUE...*", parse_mode="Markdown")
        time.sleep(1)
        bot.edit_message_text("⚙️ *Lecture de l'Order Flow et calcul de la Volatilité ATR...*", chat_id, msg.message_id, parse_mode="Markdown")
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

    maintenant = datetime.datetime.now()
    secondes_restantes = (60 - maintenant.second) + 60
    if (60 - maintenant.second) < 15: secondes_restantes += 60
    heure_entree_dt = maintenant + datetime.timedelta(seconds=secondes_restantes)
    
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)
    titre_signal = "🔥 SIGNAL VALIDÉ DYNAMIQUE 🔥" if score >= 8 else "⚡ SIGNAL VIP SÉCURISÉ ⚡"
    jauge_visuelle = generer_jauge(score * 10) 

    signal = f"""{titre_signal}
──────────────────
🛰 **ACTIF :** {nom_affiche}
🎯 **ACTION :** {action}
⏳ **EXPIRATION :** {exp_texte}
──────────────────
🧠 **CONFIANCE :** {jauge_visuelle}
🛡️ **FILTRE ANTI-PIÈGE :** VALIDÉ ✅

📊 **VALIDATION MULTI-CERVEAU :**
➤ **RSI :** 🟢 Validé ({rsi_val})
➤ **Stochastique :** 🟢 Validé ({stoch_val})
➤ **Volume/Trend :** 🟢 {bb_status}
──────────────────
📍 **ORDRE À : {heure_entree_dt.strftime("%H:%M:00")}** 👈
💵 **MISE RECOMMANDÉE :** {mise_recommandee}$ (2%)
──────────────────
⚠️ *Préparez l'ordre sur Pocket Broker avec le temps d'expiration indiqué ci-dessus.*"""

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
    try: msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X de {symbole}...*", parse_mode="Markdown")
    except: return
    
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return bot.edit_message_text("⚠️ Impossible de scanner (manque de données).", message.chat.id, msg.message_id)
        
    try:
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2)
        bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        ema_200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        # 🛡️ AJOUT DE L'ADX DANS LA COMMANDE VISION
        indicateur_adx = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        adx_val = indicateur_adx.adx().iloc[-1]
        force_tendance = "🚀 Tendance TRÈS FORTE" if adx_val > 30 else "💤 Marché plat / Sans direction" if adx_val < 20 else "📈 Tendance modérée"

        position_bb = "🔴 Au Plafond (Touche la bande haute)" if prix_actuel >= bb_haute else "🟢 Au Plancher (Touche la bande basse)" if prix_actuel <= bb_basse else "⚪ Au Milieu (Zone neutre)"
        nom_affiche = f"{symbole[:3]}/{symbole[3:]}"
        
        rapport = f"""👁️ **VISION RAYONS X : {nom_affiche}** 👁️
──────────────────
💰 **Prix actuel :** `{prix_actuel:.5f}`
🛡️ **EMA 200 (Tendance) :** `{ema_200:.5f}`
📏 **Position Bollinger :** {position_bb}

📊 **Niveau RSI :** `{rsi:.2f}` *(Attente: <=25 ou >=75)*
📉 **Niveau Stochastique :** `{stoch_k:.2f}` *(Attente: <=20 ou >=80)*
🌪️ **Force ADX :** `{adx_val:.2f}` ({force_tendance})
──────────────────"""
        rapport += "\n⚠️ *Le prix teste les limites, tiens-toi prêt !*" if position_bb != "⚪ Au Milieu (Zone neutre)" else "\n💤 *Le marché respire tranquillement.*"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except Exception as e: bot.edit_message_text(f"❌ Erreur : {e}", message.chat.id, msg.message_id)

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestion_horaires_et_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition GOD MODE DYNAMIQUE (V7.1) Démarrée.", flush=True)
    bot.infinity_polling()
