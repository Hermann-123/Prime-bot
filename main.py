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

TELEGRAM_TOKEN = "8658287331:AAEmwLMtrA9GSt8WcbOFXyPFCdld3SrDNx0"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# 🔴 CONFIGURATION MARTINGALE SÉCURISÉE
COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 2

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
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
    return "Terminal Prime VIP : Édition GOD MODE PULLBACK + PRICE ACTION"

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
        if expiration == "LIFETIME" or datetime.datetime.now() < expiration:
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
    if symbole_brut in CRYPTO_PAIRS: return f"cry{symbole_brut}"
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
    global stats_journee, cooldown_actifs, niveaux_martingale
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'): return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie: return

    prix_entree = trade['prix_entree']
    action = trade['action']
    palier_actuel = niveaux_martingale.get(chat_id, 0)

    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    type_emoji = "🪙" if symbole in CRYPTO_PAIRS else "💱"
    
    if gagne:
        niveaux_martingale[chat_id] = 0 # RESET
        if palier_actuel == 0:
            texte = f"👻 **FANTÔME ITM** : Le trade virtuel sur {nom_paire} est passé. Le bot se remet en chasse."
        else:
            texte = f"✅ **VICTOIRE VIP (ITM)**\n🚀 Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n🔄 **Palier Réinitialisé (Base)**"
            stats_journee['ITM'] += 1
            stats_journee['details'].append(f"✅ {type_emoji} {nom_paire} ({action})")
        
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
    else:
        # GESTION MARTINGALE ET COOLDOWN ANTI-FAKEOUT
        if palier_actuel < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier_actuel + 1
            if palier_actuel == 0:
                texte = f"⚠️ **PIÈGE DÉTECTÉ (Mode Fantôme)**\nLe trade virtuel a échoué ! La voie est libre.\n🔥 **PRÉPAREZ-VOUS POUR LE PALIER 1** 🔥"
            else:
                texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n⚠️ **MARTINGALE ACTIVÉE (Palier {palier_actuel + 1})**"
        else:
            niveaux_martingale[chat_id] = 0
            texte = f"🛑 **STOP MARTINGALE ATTEINT**\nRetour au mode Fantôme de base."
            
        if palier_actuel > 0:
            stats_journee['OTM'] += 1
            stats_journee['details'].append(f"❌ {type_emoji} {nom_paire} ({action})")
            # ACTIVATION DU BOUCLIER UNIQUEMENT SI LE VRAI CAPITAL EST PERDU
            cooldown_actifs[symbole] = {'time': time.time(), 'action': action}
    
    try: 
        bot.send_message(chat_id, texte, parse_mode="Markdown")
        if palier_actuel > 0 or gagne: bot.send_message(ADMIN_ID, texte, parse_mode="Markdown")
    except: pass
        
    if chat_id in trades_en_cours: del trades_en_cours[chat_id]

# ==========================================
# MOTEUR D'ANALYSE ( PULLBACK + PRICE ACTION VIP )
# ==========================================

def analyser_binaire_pro(symbole):
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE NEWS : Marché manipulé, radar coupé.", None, None, None, None, None, None, None

    candles = obtenir_donnees_deriv(symbole)
    if not candles: return "⚠️ Impossible de se connecter au marché", None, None, None, None, None, None, None
    
    try:
        df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        
        # Indicateurs Techniques
        df['corps_bougie'] = abs(df['close'] - df['open'])
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        df['bb_milieu'] = indicateur_bb.bollinger_mavg()
        
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        df['adx'] = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14).adx()
        
        df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']

        atr_actuel = df['atr'].iloc[-1]
        atr_moyen = df['atr'].rolling(window=20).mean().iloc[-1]
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        c = last['close']
        rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
        
        # --- DÉTECTION DES BOUGIES VIP (PRICE ACTION) ---
        last_is_green = last['close'] > last['open']
        last_is_red = last['close'] < last['open']
        prev_is_green = prev['close'] > prev['open']
        prev_is_red = prev['close'] < prev['open']
        
        # Avalement (Engulfing)
        avalement_haussier = prev_is_red and last_is_green and (last['close'] > prev['open']) and (last['open'] <= prev['close'])
        avalement_baissier = prev_is_green and last_is_red and (last['close'] < prev['open']) and (last['open'] >= prev['close'])
        
        # Pinbar (Marteau / Étoile Filante) avec rejet violent
        rejet_haussier = last['meche_basse'] > (last['corps_bougie'] * 2.0)
        rejet_baissier = last['meche_haute'] > (last['corps_bougie'] * 2.0)

        # --- LOGIQUE V13 : PULLBACK (Suivi de Tendance Actif) ---
        tendance_haussiere = c > df['ema_200'].iloc[-1]
        tendance_baissiere = c < df['ema_200'].iloc[-1]
        marche_en_mouvement = last['adx'] > 18 
        
        # Conditions de "Respiration" (Pullback)
        condition_achat = tendance_haussiere and marche_en_mouvement and (stoch_val < 35) and (rsi_val > 45)
        condition_vente = tendance_baissiere and marche_en_mouvement and (stoch_val > 65) and (rsi_val < 55)

        action, confiance, bb_status, score_algo = None, 0, "En Attente", 5
        
        if atr_actuel > (atr_moyen * 1.5): duree_secondes, expiration_texte = 120, "2 MINUTES (Vitesse Élevée ⚡)"
        elif atr_actuel < (atr_moyen * 0.8): duree_secondes, expiration_texte = 600, "10 MINUTES (Marché Lent 🐢)"
        else: duree_secondes, expiration_texte = 300, "5 MINUTES (Standard 💎)"

        # ANALYSE ET APPLICATION DES BONUS PRICE ACTION
        if condition_achat:
            action, confiance = "🟢 ACHAT (CALL)", 85
            score_algo = 9.0
            bb_status = "🎯 Pullback Haussier (Stochastique Bas)"
            
            if avalement_haussier or rejet_haussier:
                score_algo = 10.0
                confiance = 99
                bb_status = "👑 PULLBACK + PRICE ACTION (Avalement/Marteau) 🚀"
            
        elif condition_vente:
            action, confiance = "🔴 VENTE (PUT)", 85
            score_algo = 9.0
            bb_status = "🎯 Pullback Baissier (Stochastique Haut)"
            
            if avalement_baissier or rejet_baissier:
                score_algo = 10.0
                confiance = 99
                bb_status = "👑 PULLBACK + PRICE ACTION (Avalement/Étoile) ☄️"

        if action:
            # BOUCLIER ANTI-FAKEOUT DIRECTIONNEL
            action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
            if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole]['time'] < 3600):
                if action_simplifiee == cooldown_actifs[symbole]['action']:
                    return f"⚠️ **BLOCAGE ANTI-FAKEOUT** : Cassure détectée ici. Radar coupé.", None, None, None, None, None, None, None

            return action, min(confiance, 99), expiration_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo

        return f"⚠️ En attente d'un retrait (Pullback) dans la tendance.", None, None, None, None, None, None, None
            
    except Exception as e: 
        return None, None, None, None, None, None, None, None

# ==========================================
# LA GESTION DES SIGNAUX & DESIGN PREMIUM
# ==========================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id] = actif
    nom_affiche = f"{actif[:3]}/{actif[3:]}"
    
    try:
        msg = bot.send_message(chat_id, "⏳ *Initialisation du scan PULLBACK...*", parse_mode="Markdown")
        time.sleep(1)
    except: return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Échec des filtres. Le marché ne donne rien.", chat_id, msg.message_id)
        except: pass
        return

    maintenant = datetime.datetime.now()
    secondes_restantes = (60 - maintenant.second) + 60
    if (60 - maintenant.second) < 15: secondes_restantes += 60
    heure_entree_dt = maintenant + datetime.timedelta(seconds=secondes_restantes)
    heure_format = heure_entree_dt.strftime("%H:%M:00")
    
    palier = niveaux_martingale.get(chat_id, 0)
    mise_de_base = CAPITAL_ACTUEL * 0.02
    mise_calculee = mise_de_base * (COEF_MARTINGALE ** palier)
    jauge_visuelle = generer_jauge(score * 10) 

    # 👻 SI PALIER 0 -> LE BOT TIRE DANS LE VIDE
    if palier == 0:
        signal = f"""👻 **MODE FANTÔME (TEST MARCHÉ)** 👻
──────────────────
🌐 **ACTIF :** {nom_affiche}
🎯 **ACTION :** {action}

*Le bot prend ce trade virtuellement pour encaisser les faux signaux. NE RENTREZ PAS. S'il échoue, l'alerte VIP (Palier 1) se déclenchera pour vous faire entrer avec puissance.*"""

    # 💎 SI PALIER 1 ou 2 -> ALERTE VIP PREMIUM
    else:
        # MISE À JOUR DU TITRE SELON LE SCORE (PRICE ACTION)
        if score >= 10:
            titre_signal = "👑 CONFIGURATION SUPRÊME (PRICE ACTION) 👑"
        elif score >= 9:
            titre_signal = "🚨 ALERTE PULLBACK INSTITUTIONNELLE 🚨"
        else:
            titre_signal = "⚡ FRAPPE DE RATTRAPAGE VIP ⚡"
            
        signal = f"""{titre_signal}

🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE :** {heure_format} (SOYEZ PRÉCIS)
⏳ **EXPIRATION :** {duree_secondes // 60} MINUTES

1️⃣ **INSTRUCTION DE FRAPPE :**
👉 {heure_format} = {action}

2️⃣ **SÉCURITÉ ET FILTRES :**
🛡️ {bb_status}
🧠 Confiance : {jauge_visuelle}

💵 **MISE CALCULÉE :** {int(mise_calculee)}$
*(Statut : MARTINGALE Palier {palier})*

📌 **ASTUCE PRO :** Utilisez un "Ordre en différé" (By Time) sur votre broker à l'heure exacte, ou attendez un léger rebond de 2 secondes avant de cliquer !"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except: pass

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[chat_id] = {'symbole': actif, 'action': action_simplifiee}
    Timer(secondes_restantes, relever_prix_entree, args=[chat_id, actif]).start()
    Timer(secondes_restantes + duree_secondes, verifier_resultat, args=[chat_id]).start()

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
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT - TERMINAL PRIVÉ** 🔒", parse_mode="Markdown")

    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - V13 (PULLBACK + PRICE ACTION)** 🔥
    
Bienvenue dans le radar institutionnel. Ce système traque les tendances et utilise le **Mode Fantôme**.

📖 **COMMENT ÇA MARCHE ?**
1️⃣ Le bot trade le Palier 0 de manière INVISIBLE (Pullback).
2️⃣ **Tu ne trades pas ce signal ! Laisse-le purger le marché.**
3️⃣ S'il perd son trade virtuel, il déclenche l'Alerte VIP. C'est LÀ que tu entres avec la Martingale 1 !

⚠️ **ATTENTION : RÉGLEZ SOIGNEUSEMENT L'HORLOGE DE POCKET BROKER !** ⏱️"""
    bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    bot.send_message(message.chat.id, "🕒 **GUIDE DES HORAIRES DE TRADING (Heure GMT)** 🕒\n\n✅ **SESSION SEMAINE 1 (08h00 - 11h00) :** EUR/USD, GBP/USD\n🔥 **SESSION SEMAINE 2 (13h30 - 16h30) :** EUR/USD, AUD/USD\n🌉 **SESSION SEMAINE 3 (20h00 - 08h00) :** AUD/JPY, USD/JPY, EUR/JPY\n🪙 **SESSION WEEK-END :** CRYPTOMONNAIES UNIQUEMENT", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    jour_semaine = datetime.datetime.now().weekday()
    
    if jour_semaine >= 5:
        markup.add(InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD"))
        message_texte = "Mode Week-End 🪙 : Sélectionne la Crypto :"
    else:
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
            InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
        )
        message_texte = "Mode Semaine 💱 : Sélectionne ta cible :"
    bot.send_message(message.chat.id, message_texte, reply_markup=markup)

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
    if not candles: return bot.edit_message_text("⚠️ Impossible de scanner.", message.chat.id, msg.message_id)
        
    try:
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        ema_200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator().iloc[-1]
        adx = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14).adx().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        position_bb = "🔴 Au Plafond" if prix_actuel >= bb_haute else "🟢 Au Plancher" if prix_actuel <= bb_basse else "⚪ Au Milieu"
        
        rapport = f"👁️ **VISION RAYONS X : {symbole}** 👁️\n──────────────────\n💰 **Prix :** `{prix_actuel:.5f}`\n🛡️ **Tendance (EMA 200) :** `{'Hausse 🟢' if prix_actuel > ema_200 else 'Baisse 🔴'}`\n🚀 **Force (ADX) :** `{adx:.2f}`\n📊 **RSI :** `{rsi:.2f}`\n📉 **Stochastique :** `{stoch_k:.2f}`\n──────────────────"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except Exception as e: bot.edit_message_text(f"❌ Erreur", message.chat.id, msg.message_id)

def scanner_marche_auto():
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: continue
                
            maintenant = datetime.datetime.now()
            devises_a_surveiller = CRYPTO_PAIRS if maintenant.weekday() >= 5 else FOREX_PAIRS
            
            for actif in devises_a_surveiller:
                action, confiance, exp, duree, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif)
                if action and "⚠️" not in action and confiance:
                    temps_actuel = time.time()
                    if actif in derniere_alerte_auto and (temps_actuel - derniere_alerte_auto[actif] < 3600): continue
                    derniere_alerte_auto[actif] = temps_actuel
                    nom_affiche = f"{actif[:3]}/{actif[3:]}"
                    
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"📊 Verrouiller {nom_affiche}", callback_data=f"set_{actif}"))
                    alerte_msg = f"🔔 **MOUVEMENT DÉTECTÉ SUR {nom_affiche}** 🔔\n\n👇 *Clique pour que le bot gère l'entrée (Fantôme ou VIP) !*"
                        
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except: pass
        except: pass

def gestion_horaires_et_bilan():
    global stats_journee, bilan_envoye_aujourdhui
    while True:
        try:
            heure, minute = datetime.datetime.now().hour, datetime.datetime.now().minute
            if heure == 22 and minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                if total_trades > 0:
                    winrate = round((stats_journee['ITM'] / total_trades) * 100)
                    texte_bilan_admin = f"📊 **BILAN VIP** 📊\n✅ ITM : {stats_journee['ITM']} | ❌ OTM : {stats_journee['OTM']}\n📈 Winrate : {winrate}%\n"
                    try: bot.send_message(ADMIN_ID, texte_bilan_admin, parse_mode="Markdown")
                    except: pass
                stats_journee, bilan_envoye_aujourdhui = {'ITM': 0, 'OTM': 0, 'details': []}, True
            elif heure == 23: bilan_envoye_aujourdhui = False
            time.sleep(30)
        except: time.sleep(60)

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestion_horaires_et_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition PULLBACK + PRICE ACTION Démarrée.", flush=True)
    bot.infinity_polling()
