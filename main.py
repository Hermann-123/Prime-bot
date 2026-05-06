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

TELEGRAM_TOKEN = "8658287331:AAF85Poh7yWC42faZmy7BCfsZiupI5ppyYQ"
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
mode_trading = {} # STOCKAGE DU MODE HYBRIDE (STANDARD ou SCALP)
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
    return "Terminal Prime VIP : Édition HYBRIDE V13.5 (Standard + Scalp + Silence Radio)"

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

def generer_jauge(pourcentage):
    if pourcentage >= 99:
        return "[██████████] 👑 MAX"
    pleins = int(pourcentage / 10)
    vides = 10 - pleins
    return f"[{'█' * pleins}{'░' * vides}] {pourcentage}%"

# ==========================================
# GESTION DES ABONNEMENTS ET CLÉS (ADMIN)
# ==========================================

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "⛔ Accès refusé. Commande réservée au Boss.")
    try:
        argument = message.text.split()[1].lower()
        if argument == '1s': jours = 7
        elif argument == '2s': jours = 14
        elif argument == '1m': jours = 30
        elif argument == '3m': jours = 90
        elif argument == 'vie': jours = "LIFETIME"
        else:
            jours = int(argument) 
            if jours <= 0: raise ValueError
            
        cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cles_generees[cle] = jours
        
        texte = f"✅ **CLÉ GÉNÉRÉE AVEC SUCCÈS**\n\n🔑 **Clé :** `{cle}`\n"
        if jours == "LIFETIME": texte += f"⏳ **Durée :** À VIE 👑\n\n"
        else: texte += f"⏳ **Durée :** {jours} Jours\n\n"
        texte += f"👉 *Donnez cette clé à votre client. Il devra taper :*\n`/vip {cle}`"
        bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except:
        aide = "⚠️ **Erreur de format.**\nUtilisez vos raccourcis de forfaits :\n\n"
        aide += "`/keygen 1s` ➔ (1 Semaine)\n`/keygen 2s` ➔ (2 Semaines)\n`/keygen 1m` ➔ (1 Mois)\n`/keygen 3m` ➔ (3 Mois)\n`/keygen vie` ➔ (Accès à vie 👑)"
        bot.send_message(message.chat.id, aide, parse_mode="Markdown")

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
        else:
            bot.send_message(chat_id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")
    except:
        bot.send_message(chat_id, "⚠️ **Erreur de format.**\nUtilisez : `/vip [votre_clé]`", parse_mode="Markdown")

# ==========================================
# FONCTIONS PRO & ROUTEUR DERIV
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

# ==========================================
# SYSTÈME DE VÉRIFICATION & SILENCE RADIO
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
    mode_trade = mode_trading.get(chat_id, "STANDARD")

    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    type_emoji = "🪙" if symbole in CRYPTO_PAIRS else "💱"
    
    if gagne:
        niveaux_martingale[chat_id] = 0 # RESET
        
        # GESTION DES MESSAGES DE VICTOIRE (FANTÔME VS VRAI TRADE)
        if palier_actuel == 0:
            texte = f"👻 **FANTÔME RÉUSSI (ITM)**\nLe trade virtuel sur {nom_paire} est passé sans nous. Le bot reprend ses recherches.\n🔓 *Radar déverrouillé.*"
        else:
            texte = f"✅ **CIBLE ABATTUE (ITM)**\n🚀 {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n🔓 *Radar déverrouillé, reprise du scan...*"
            stats_journee['ITM'] += 1
            stats_journee['details'].append(f"✅ {type_emoji} {nom_paire} ({action})")
            
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
        
        # PORTE OUVERTE
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        
    else:
        if palier_actuel < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier_actuel + 1
            
            # GESTION DES MESSAGES D'ÉCHEC ET SILENCE RADIO
            if palier_actuel == 0:
                texte = f"⚠️ **PIÈGE BROKER DÉTECTÉ (Fantôme Échoué)**\n📉 Sortie : `{prix_sortie}`\n\n🔥 Le piège est désamorcé ! **PRÉPAREZ-VOUS POUR LE PALIER 1 ({nom_paire})**.\n🤫 *Silence radio activé pendant votre tir.*"
            else:
                texte = f"⚠️ **TIR RATÉ - PRÉPARATION PALIER {palier_actuel + 1}**\n📉 Sortie : `{prix_sortie}`\n\n🤫 *Silence radio maintenu.*"
            
            # 🔴 SILENCE RADIO : On relance le timer (Pause de 1 min + Durée du trade)
            duree_blocage = 120 if mode_trade == "SCALP" else 360 
            Timer(duree_blocage, verifier_resultat, args=[chat_id]).start()
            
        else:
            niveaux_martingale[chat_id] = 0
            texte = f"🛑 **FIN DE SÉQUENCE ATTEINTE (OTM)**\n⚠️ {nom_paire} ({action})\nRepli tactique. Radar réactivé."
            if palier_actuel > 0:
                stats_journee['OTM'] += 1
                stats_journee['details'].append(f"❌ {type_emoji} {nom_paire} ({action})")
            cooldown_actifs[symbole] = {'time': time.time(), 'action': action}
            
            # PORTE OUVERTE
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
    
    try: 
        bot.send_message(chat_id, texte, parse_mode="Markdown")
        # CORRECTION DOUBLE MESSAGE ADMIN : On envoie la copie admin SEULEMENT si ce n'est pas le compte du boss
        if chat_id != ADMIN_ID:
            if palier_actuel > 0 or gagne:
                bot.send_message(ADMIN_ID, f"👑 COPIE ADMIN :\n{texte}", parse_mode="Markdown")
    except: pass

# ==========================================
# MOTEUR D'ANALYSE HYBRIDE (STANDARD / SCALP)
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE NEWS : Marché manipulé, radar coupé.", None, None, None, None, None, None, None

    granularite = 60 if mode == "SCALP" else 300
    candles = obtenir_donnees_deriv(symbole, granularite)
    if not candles: return "⚠️ Impossible de se connecter au marché", None, None, None, None, None, None, None
    
    try:
        df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        df['corps_bougie'] = abs(df['close'] - df['open'])
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        
        last = df.iloc[-1]
        c = last['close']
        rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
        action, confiance, bb_status, score_algo = None, 0, "En Attente", 5
        
        # ----------------------------------------
        # 🛡️ LOGIQUE V13 (STANDARD 5 MIN)
        # ----------------------------------------
        if mode == "STANDARD":
            indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
            df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
            df['adx'] = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14).adx()
            df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
            df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
            df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']
            
            atr_actuel = df['atr'].iloc[-1]
            atr_moyen = df['atr'].rolling(window=20).mean().iloc[-1]
            
            prev = df.iloc[-2]
            last_is_green = last['close'] > last['open']
            last_is_red = last['close'] < last['open']
            prev_is_green = prev['close'] > prev['open']
            prev_is_red = prev['close'] < prev['open']
            
            avalement_haussier = prev_is_red and last_is_green and (last['close'] > prev['open']) and (last['open'] <= prev['close'])
            avalement_baissier = prev_is_green and last_is_red and (last['close'] < prev['open']) and (last['open'] >= prev['close'])
            rejet_haussier = last['meche_basse'] > (last['corps_bougie'] * 2.0)
            rejet_baissier = last['meche_haute'] > (last['corps_bougie'] * 2.0)

            tendance_haussiere = c > df['ema_200'].iloc[-1]
            tendance_baissiere = c < df['ema_200'].iloc[-1]
            marche_en_mouvement = last['adx'] > 18 
            
            if atr_actuel > (atr_moyen * 1.5): duree_secondes, expiration_texte = 120, "2 MIN (Vitesse Élevée)"
            elif atr_actuel < (atr_moyen * 0.8): duree_secondes, expiration_texte = 600, "10 MIN (Marché Lent)"
            else: duree_secondes, expiration_texte = 300, "5 MIN (Standard)"
            
            if tendance_haussiere and marche_en_mouvement and (stoch_val < 35) and (rsi_val > 45):
                action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 85, 9.0, "🎯 Pullback Haussier (5 Min)"
                if avalement_haussier or rejet_haussier:
                    score_algo, confiance, bb_status = 10.0, 99, "👑 PULLBACK + PRICE ACTION 🚀"
            elif tendance_baissiere and marche_en_mouvement and (stoch_val > 65) and (rsi_val < 55):
                action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 85, 9.0, "🎯 Pullback Baissier (5 Min)"
                if avalement_baissier or rejet_baissier:
                    score_algo, confiance, bb_status = 10.0, 99, "👑 PULLBACK + PRICE ACTION ☄️"

        # ----------------------------------------
        # 🔥 LOGIQUE V14 (SCALP 1 MIN AJUSTÉ)
        # ----------------------------------------
        elif mode == "SCALP":
            # Bollinger ajusté à 2.2 pour 15-20 opportunités par jour
            indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2)
            bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
            
            duree_secondes, expiration_texte = 60, "1 MINUTE (SCALP EXTRÊME ⚡)"
            
            # Épuisement RSI 30/70
            if (c <= bb_basse) and (rsi_val < 30):
                action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 90, 9.0, "⚡ CASSURE BROKER BASSE"
            elif (c >= bb_haute) and (rsi_val > 70):
                action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 90, 9.0, "⚡ CASSURE BROKER HAUTE"

        if action:
            action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
            delai_blocage = 1800 if mode == "SCALP" else 3600
            if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole]['time'] < delai_blocage):
                if action_simplifiee == cooldown_actifs[symbole]['action']:
                    return f"⚠️ **BLOCAGE ANTI-FAKEOUT**", None, None, None, None, None, None, None
            return action, min(confiance, 99), expiration_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo

        return f"⚠️ En attente d'une opportunité ({mode}).", None, None, None, None, None, None, None
            
    except Exception as e: 
        return None, None, None, None, None, None, None, None

# ==========================================
# LA GESTION DES SIGNAUX & DESIGN PREMIUM
# ==========================================

def obtenir_clavier(user_id):
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    btn_mode = "🛡️ MODE: STANDARD (5 Min)" if mode_actuel == "STANDARD" else "🔥 MODE: SCALP (1 Min)"
    
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton(btn_mode), KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours:
        return bot.send_message(user_id, "⚠️ Impossible de changer de mode : Silence Radio actif. Vous êtes en plein combat !")
        
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    if mode_actuel == "STANDARD":
        mode_trading[user_id] = "SCALP"
        bot.send_message(user_id, "🔥 **MODE SCALPING 1 MINUTE ACTIVÉ**\nLe bot passe en haute fréquence pour traquer les failles du broker.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE PULLBACK 5 MINUTES ACTIVÉ**\nLe bot retourne en mode sniper sécurisé sur la tendance lourde.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT - TERMINAL PRIVÉ** 🔒", parse_mode="Markdown")

    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    
    texte = """🏴‍☠️ **TERMINAL PRIME - V13.5 HYBRIDE** 🔥
    
Le bot intègre maintenant les 2 stratégies suprêmes. Utilise le bouton **MODE** en bas pour basculer :
🛡️ **STANDARD :** Tendances et Pullbacks (5 Min).
🔥 **SCALP :** Anticipation du Broker avec Plan Séquentiel (1 Min)."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    
    if chat_id in trades_en_cours:
        symbole_en_cours = trades_en_cours[chat_id]['symbole']
        bot.answer_callback_query(call.id, f"⚠️ Silence Radio activé sur {symbole_en_cours}. Focus !", show_alert=True)
        return
    
    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id] = actif
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    nom_affiche = f"{actif[:3]}/{actif[3:]}"
    
    try:
        msg = bot.send_message(chat_id, f"⏳ *Initialisation Scanner {mode_actuel}...*", parse_mode="Markdown")
        time.sleep(1)
    except: return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif, mode_actuel)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Le marché ne donne rien pour le moment.", chat_id, msg.message_id)
        except: pass
        return

    maintenant = datetime.datetime.now()
    
    # ⏱️ NOUVEAU CALCUL DE LA MARGE DE SÉCURITÉ DE PRÉPARATION
    if mode_actuel == "SCALP":
        secondes_restantes = (60 - maintenant.second)
        # Si moins de 45 secondes pour se préparer, on ajoute une minute complète !
        if secondes_restantes < 45: 
            secondes_restantes += 60 
    else:
        secondes_restantes = (60 - maintenant.second) + 60
        if (60 - maintenant.second) < 15: 
            secondes_restantes += 60
        
    heure_entree_p0 = maintenant + datetime.timedelta(seconds=secondes_restantes)
    
    fmt = "%H:%M:%S" if mode_actuel == "SCALP" else "%H:%M:00"
    jauge_visuelle = generer_jauge(score * 10) 
    
    # -----------------------------------------------
    # PLAN DE TIR SÉQUENTIEL (SCALP) vs STANDARD
    # -----------------------------------------------
    if mode_actuel == "SCALP":
        temps_pause = 60
        heure_entree_p1 = heure_entree_p0 + datetime.timedelta(seconds=duree_secondes + temps_pause)
        heure_entree_p2 = heure_entree_p1 + datetime.timedelta(seconds=duree_secondes + temps_pause)
        
        str_p0, str_p1, str_p2 = heure_entree_p0.strftime(fmt), heure_entree_p1.strftime(fmt), heure_entree_p2.strftime(fmt)
        
        # MODIFICATION : Intégration du Fantôme (Palier 0 = 0$)
        mise_p1 = int(CAPITAL_ACTUEL * 0.02)
        mise_p2 = int(mise_p1 * COEF_MARTINGALE)
        
        signal = f"""⚡ **SCALP HAUTE FRÉQUENCE** ⚡

🌐 **ACTIF :** {nom_affiche}
👉 **ACTION :** {action}
⏳ **EXPIRATION :** {exp_texte}
🧠 **CONFIANCE :** {jauge_visuelle}

📋 **PLAN DE TIR (AVEC BOUCLIER FANTÔME) :**
──────────────────
👻 **1️⃣ TIR INITIAL (Fantôme)**
⏱ Heure : `{str_p0}` 
💵 Mise : `0$ (TEST VIRTUEL - NE PAS CLIQUER)`

🔥 **2️⃣ SI FANTÔME PERD ➔ PALIER 1 (Vrai Tir)**
⏱ Heure : `{str_p1}` *(1 min de pause pour préparer)*
💵 Mise : `{mise_p1}$`

💥 **3️⃣ SI PALIER 1 PERD ➔ PALIER 2 (Martingale)**
⏱ Heure : `{str_p2}` *(1 min de pause pour préparer)*
💵 Mise : `{mise_p2}$`
──────────────────
🛡️ {bb_status}

📌 *Instruction : Laissez le bot encaisser le premier faux mouvement à l'heure du Fantôme. S'il perd, entrez en force au Palier 1 !*"""

    else:
        # Affichage classique Standard
        str_p0 = heure_entree_p0.strftime(fmt)
        palier = niveaux_martingale.get(chat_id, 0)
        mise_calculee = (CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier)
        
        if palier == 0:
            signal = f"""👻 **MODE FANTÔME (PULLBACK STANDARD)** 👻\n──────────────────\n🌐 **ACTIF :** {nom_affiche}\n🎯 **ACTION :** {action}\n\n*Le bot prend ce trade virtuellement. NE RENTREZ PAS. L'alerte VIP se déclenchera s'il échoue.*"""
        else:
            signal = f"""🚨 **ALERTE PULLBACK VIP** 🚨\n\n🌐 **ACTIF :** {nom_affiche}\n⏱ **ENTRÉE :** {str_p0}\n⏳ **EXPIRATION :** {exp_texte}\n\n👉 **ACTION :** {action}\n🛡️ {bb_status}\n🧠 Confiance : {jauge_visuelle}\n\n💵 **MISE CALCULÉE :** {int(mise_calculee)}$\n*(Statut : MARTINGALE Palier {palier})*"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except: pass

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[chat_id] = {'symbole': actif, 'action': action_simplifiee}
    
    Timer(secondes_restantes, relever_prix_entree, args=[chat_id, actif]).start()
    Timer(secondes_restantes + duree_secondes, verifier_resultat, args=[chat_id]).start()

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    bot.send_message(message.chat.id, "🕒 **GUIDE DES HORAIRES DE TRADING (Heure GMT)** 🕒\n\n✅ **SESSION SEMAINE 1 (08h00 - 11h00) :** EUR/USD, GBP/USD\n🔥 **SESSION SEMAINE 2 (13h30 - 16h30) :** EUR/USD, AUD/USD\n🌉 **SESSION SEMAINE 3 (20h00 - 08h00) :** AUD/JPY, USD/JPY, EUR/JPY\n🪙 **SESSION WEEK-END :** CRYPTOMONNAIES", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    if datetime.datetime.now().weekday() >= 5:
        markup.add(InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD"))
    else:
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
            InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
        )
    bot.send_message(message.chat.id, "Sélectionne ta cible :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours:
        symbole = trades_en_cours[chat_id]['symbole']
        return bot.send_message(chat_id, f"⚠️ **SILENCE RADIO** : Combat en cours sur **{symbole}**.", parse_mode="Markdown")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            utilisateurs_libres = [uid for uid in utilisateurs_actifs if est_autorise(uid) and uid not in trades_en_cours]
            if not utilisateurs_libres: continue
                
            maintenant = datetime.datetime.now()
            devises_a_surveiller = CRYPTO_PAIRS if maintenant.weekday() >= 5 else FOREX_PAIRS
            
            for actif in devises_a_surveiller:
                users_std = [u for u in utilisateurs_libres if mode_trading.get(u, "STANDARD") == "STANDARD"]
                if users_std:
                    action, conf, exp, dur, rsi, stoch, bb, sc = analyser_binaire_pro(actif, "STANDARD")
                    if action and "⚠️" not in action:
                        if actif not in derniere_alerte_auto or (time.time() - derniere_alerte_auto[actif] > 3600):
                            derniere_alerte_auto[actif] = time.time()
                            markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"📊 Verrouiller {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                            for uid in users_std:
                                palier = niveaux_martingale.get(uid, 0)
                                if palier == 0 or actif == user_prefs.get(uid):
                                    try: bot.send_message(uid, f"🔔 **PULLBACK 5 MIN : {actif[:3]}/{actif[3:]}**", reply_markup=markup)
                                    except: pass

                users_sclp = [u for u in utilisateurs_libres if mode_trading.get(u, "STANDARD") == "SCALP"]
                if users_sclp:
                    action, conf, exp, dur, rsi, stoch, bb, sc = analyser_binaire_pro(actif, "SCALP")
                    if action and "⚠️" not in action:
                        if actif not in derniere_alerte_auto or (time.time() - derniere_alerte_auto[actif] > 120):
                            derniere_alerte_auto[actif] = time.time()
                            markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                            for uid in users_sclp:
                                palier = niveaux_martingale.get(uid, 0)
                                if palier == 0 or actif == user_prefs.get(uid):
                                    try: bot.send_message(uid, f"🔔 **PIC VOLATILITÉ 1 MIN : {actif[:3]}/{actif[3:]}**\n👉 Dégaine vite !", reply_markup=markup)
                                    except: pass
        except: pass

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition V13.5 HYBRIDE PRIME Démarrée.", flush=True)
    bot.infinity_polling()
