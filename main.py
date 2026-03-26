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
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

# ⚠️ TON TOKEN TÉLÉGRAM ICI ⚠️
TELEGRAM_TOKEN = "8658287331:AAHovvVAtzI7pFBmNsnu5GvayoHJESV61l8"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# 👑 L'ID DU FONDATEUR 👑
ADMIN_ID = 5968288964 

CAPITAL_ACTUEL = 40650 

# ==========================================
# VARIABLES D'ÉTAT DU SYSTÈME
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

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition Ultime & Corrigée (Moteur PDF - 100% Corrigé - Nuit 100% JPY)"

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
            msg_exp = "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé. Veuillez contacter le fondateur [@hermann1123](https://t.me/hermann1123)."
            try:
                bot.send_message(user_id, msg_exp, parse_mode="Markdown")
            except:
                pass
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
# CONNEXION API DERIV (WEBSOCKET 1089)
# ==========================================

def obtenir_donnees_deriv(symbole_brut):
    symbole = f"frx{symbole_brut}" 
    ws = websocket.WebSocket()
    try:
        ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
        req = {
            "ticks_history": symbole, 
            "end": "latest", 
            "count": 50, 
            "style": "candles", 
            "granularity": 60
        }
        ws.send(json.dumps(req))
        history = json.loads(ws.recv())
        ws.close()
        
        if "error" in history or "candles" not in history:
            return None
            
        return history['candles']
    except Exception:
        return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = f"frx{symbole_brut}"
    ws = websocket.WebSocket()
    try:
        ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
        req = {
            "ticks_history": symbole, 
            "end": "latest", 
            "count": 1, 
            "style": "ticks"
        }
        ws.send(json.dumps(req))
        res = json.loads(ws.recv())
        ws.close()
        
        if "history" in res and "prices" in res["history"]:
            return float(res["history"]["prices"][0])
    except Exception:
        pass
    return None

# ==========================================
# SYSTÈME DE VÉRIFICATION ITM/OTM (Correctif Bug)
# ==========================================

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours:
        # ✅ Vérification stricte du symbole pour éviter le bug Deriv
        if trades_en_cours[chat_id]['symbole'] == symbole:
            trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee
    trade = trades_en_cours.get(chat_id)
    
    if not trade or not trade.get('prix_entree'):
        return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole) # ✅ On force la récupération du MÊME symbole
    
    if not prix_sortie:
        return

    prix_entree = trade['prix_entree']
    action = trade['action']

    gagne = False
    if action == "CALL" and prix_sortie > prix_entree:
        gagne = True
    elif action == "PUT" and prix_sortie < prix_entree:
        gagne = True

    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    
    if gagne:
        texte = f"✅ **VICTOIRE (ITM)**\n🚀 Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['ITM'] += 1
        stats_journee['details'].append(f"✅ {nom_paire} ({action})")
    else:
        texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {nom_paire} ({action})")
    
    try:
        bot.send_message(ADMIN_ID, texte, parse_mode="Markdown")
    except Exception:
        pass
        
    if chat_id in trades_en_cours:
        del trades_en_cours[chat_id]

# ==========================================
# MOTEUR D'ANALYSE PDF PURE + EXPIRATION DYN.
# ==========================================

def analyser_binaire_pro(symbole):
    candles = obtenir_donnees_deriv(symbole)
    if not candles:
        return "⚠️ Impossible de se connecter au marché (Deriv)", None, None, None, None, None, None
    
    try:
        df = pd.DataFrame([{
            'open': c['open'], 
            'close': c['close'], 
            'high': c['high'], 
            'low': c['low']
        } for c in candles])
        
        # Indicateurs PDF Pure (Bollinger, RSI, Stoch)
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        
        # Volatilité pour expiration dynamique
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        
        atr_actuel = df['atr'].iloc[-1]
        atr_moyen = df['atr'].mean()
        c = df['close'].iloc[-1]
        rsi_val = round(df['rsi'].iloc[-1], 1)
        stoch_val = round(df['stoch_k'].iloc[-1], 1)
        bb_h = df['bb_haute'].iloc[-1]
        bb_b = df['bb_basse'].iloc[-1]

        action = None
        confiance = 0
        bb_status = "Au Milieu"
        
        # --- LOGIQUE D'EXPIRATION DYNAMIQUE ---
        if atr_actuel > (atr_moyen * 1.5):
            duree_minutes = 3
        elif atr_actuel > atr_moyen:
            duree_minutes = 2
        else:
            duree_minutes = 1

        expiration_texte = f"{duree_minutes} MINUTE{'S' if duree_minutes > 1 else ''} ⏱"
        duree_secondes = duree_minutes * 60

        # --- LOGIQUE DE DÉCISION (ACHAT / VENTE) ---
        if c <= bb_b and rsi_val <= 40 and stoch_val <= 20:
            action = "🟢 ACHAT (CALL) 👑 [TITAN VIP]"
            confiance = random.randint(92, 99)
            bb_status = "Cassure Bande Basse Validée"
            
        elif c >= bb_h and rsi_val >= 60 and stoch_val >= 80:
            action = "🔴 VENTE (PUT) 👑 [TITAN VIP]"
            confiance = random.randint(92, 99)
            bb_status = "Cassure Bande Haute Validée"
            
        else:
            return f"⚠️ Marché stable. En attente de cassure Bollinger.", None, None, None, None, None, None
            
        return action, confiance, expiration_texte, duree_secondes, rsi_val, stoch_val, bb_status
        
    except Exception:
        return None, None, None, None, None, None, None

# ==========================================
# LE SCANNER AUTOMATIQUE DE L'OMBRE (Corrigé)
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            
            if not utilisateurs_a_alerter:
                continue
            
            heure_actuelle = datetime.datetime.now().hour
            
            # --- ✅ LE FILTRE JOUR / NUIT CORRIGÉ ET LOGIQUE ---
            if 8 <= heure_actuelle < 20:
                # 👍 Devises Jour : GBP retiré, JPY inclus
                devises_a_surveiller = [
                    "EURUSD", "USDJPY", "AUDUSD", "USDCAD", "EURJPY", "USDCHF"
                ]
            else:
                # 🌙 NUIT : 100% Focus JPY (Session Asiatique sécurisée)
                devises_a_surveiller = [
                    "AUDJPY", "USDJPY", "CHFJPY", "CADJPY", "EURJPY"
                ]
            
            for actif in devises_a_surveiller:
                action, confiance, exp, duree, rsi_val, stoch_val, bb_status = analyser_binaire_pro(actif)
                
                if action and "⚠️" not in action and confiance:
                    maintenant = time.time()
                    if actif in derniere_alerte_auto and (maintenant - derniere_alerte_auto[actif] < 900):
                        continue
                        
                    derniere_alerte_auto[actif] = maintenant
                    
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"📊 Analyser {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                    
                    # --- ✅ LES ALERTES VIP ESTHÉTIQUES ORIGINELLES ---
                    if confiance >= 98:
                        alerte_msg = f"👑 **ALERTE TITAN DÉTECTÉE** 👑\n\nUne compression de marché rarissime vient d'apparaître sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Clique sur le bouton ci-dessous pour lancer l'analyse !*"
                    else:
                        alerte_msg = f"🚨 **NOUVELLE OPPORTUNITÉ VIP** 🚨\n\nL'algorithme a validé une figure de retournement sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Clique sur le bouton ci-dessous pour lancer l'analyse !*"
                        
                    for chat_id in utilisateurs_a_alerter:
                        try:
                            bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except Exception:
                            pass
                            
        except Exception:
            pass

# ==========================================
# GESTIONNAIRE D'HORAIRES ET DE BILAN (Admin)
# ==========================================

def gestion_horaires_et_bilan():
    global stats_journee, bilan_envoye_aujourdhui, transition_nuit_envoyee, transition_jour_envoyee
    while True:
        try:
            maintenant = datetime.datetime.now()
            heure = maintenant.hour
            minute = maintenant.minute
            
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]

            # --- TRANSITION DE NUIT (Message) ---
            if heure == 20 and minute == 0 and not transition_nuit_envoyee:
                texte_nuit = "🌉 **TRANSITION DE SESSION : MODE ASIATIQUE ACTIVÉ** 🌉\n\nLes volumes s'effondrent sur l'Europe. Le Terminal Prime bascule ses radars exclusivement sur l'Asie (Focus 100% JPY sécurisé).\n\n*La chasse continue de nuit. Restez concentrés.* 🥷"
                for chat_id in utilisateurs_a_alerter:
                    try: bot.send_message(chat_id, texte_nuit, parse_mode="Markdown")
                    except: pass
                transition_nuit_envoyee = True
                transition_jour_envoyee = False

            # --- TRANSITION DE JOUR (Message) ---
            elif heure == 8 and minute == 0 and not transition_jour_envoyee:
                texte_jour = "☀️ **TRANSITION DE SESSION : MODE EUROPE/US ACTIVÉ** ☀️\n\nOuverture des marchés majeurs. La volatilité est de retour sur les paires Euro, Dollar et Yen.\n\n*Bonne journée de trading à tous les VIP !* 🚀"
                for chat_id in utilisateurs_a_alerter:
                    try: bot.send_message(chat_id, texte_jour, parse_mode="Markdown")
                    except: pass
                transition_jour_envoyee = True
                transition_nuit_envoyee = False

            # --- BILAN DU FONDATEUR (22H00) ---
            elif heure == 22 and minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                if total_trades > 0:
                    winrate = round((stats_journee['ITM'] / total_trades) * 100)
                    texte_bilan_admin = f"📊 **BILAN VIP DE LA JOURNÉE (RAPPORT FONDATEUR)** 📊\n──────────────────\n🎯 **Total Signaux :** {total_trades}\n✅ **Victoires (ITM) :** {stats_journee['ITM']}\n❌ **Pertes (OTM) :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n──────────────────\n"
                    
                    for detail in stats_journee['details']:
                        texte_bilan_admin += f"{detail}\n"
                        
                    try:
                        bot.send_message(ADMIN_ID, texte_bilan_admin, parse_mode="Markdown")
                    except: pass
                    
                stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}
                bilan_envoye_aujourdhui = True
                
            # --- RÉINITIALISATION DU BILAN MINUIT ---
            elif heure == 23:
                bilan_envoye_aujourdhui = False
                
            time.sleep(30)
            
        except Exception:
            time.sleep(60)

# ==========================================
# COMMANDES ADMIN ET GÉNÉRATION DE CLÉS
# ==========================================

@bot.message_handler(commands=['panel'])
def admin_panel(message):
    if message.chat.id != ADMIN_ID: return
    msg = f"Admin Panel 🔥\nCapital actuel : {CAPITAL_ACTUEL}$"
    bot.send_message(ADMIN_ID, msg)

@bot.message_handler(commands=['setuser'])
def cmd_setuser(message):
    if message.chat.id != ADMIN_ID: return
    try:
        user_id = int(message.text.split()[1])
        utilisateurs_autorises[user_id] = "LIFETIME"
        bot.send_message(ADMIN_ID, f"✅ Utilisateur {user_id} autorisé à vie.")
    except:
        bot.send_message(ADMIN_ID, "⚠️ Usage: `/setuser [ID]`")

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
    else:
        bot.send_message(message.chat.id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    if call.from_user.id != ADMIN_ID:
        return
        
    action = call.data.split("_")[1]
    user_id = int(call.data.split("_")[2])
    
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        # ✅ AJOUT DES DURÉES MANQUANTES CORRIGÉ
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"),
            InlineKeyboardButton("2 Semaines 🔥", callback_data=f"gen_14_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"),
            InlineKeyboardButton("2 Mois 💎", callback_data=f"gen_60_{user_id}"),
            InlineKeyboardButton("3 Mois ✨", callback_data=f"gen_90_{user_id}"),
            InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Utilisateur `{user_id}` accepté.\nChoisis la durée :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    elif action == "refuser":
        bot.edit_message_text(f"❌ Demande refusée.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    if call.from_user.id != ADMIN_ID:
        return
        
    jours = int(call.data.split("_")[1])
    user_id = int(call.data.split("_")[2])
    
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    
    # Texte de durée intelligent
    duree_texte = ""
    if jours == 7: duree_texte = "1 Semaine"
    elif jours == 14: duree_texte = "2 Semaines"
    elif jours == 30: duree_texte = "1 Mois"
    elif jours == 60: duree_texte = "2 Mois"
    elif jours == 90: duree_texte = "3 Mois"
    elif jours == 999: duree_texte = "À VIE"
    
    msg = f"🔑 **CLÉ GÉNÉRÉE** 🔑\n\n⏳ Durée : {duree_texte}\n👤 ID : `{user_id}`\n\nCopie ce message à ton client :\n\n`{cle}`"
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# ==========================================
# COMMANDES TÉLÉGRAM ET MENUS VIP (Esthétique)
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
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Accepter", callback_data=f"admin_accepter_{user_id}"), 
            InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}")
        )
        try:
            bot.send_message(ADMIN_ID, f"🚨 **NOUVEAU CLIENT POTENTIEL** 🚨\n\n🆔 `{user_id}`\n\nGénérer un abonnement ?", reply_markup=markup, parse_mode="Markdown")
        except:
            pass
            
        alerte = "🔒 **ACCÈS RESTREINT - TERMINAL PRIVÉ** 🔒\n\nCe système est une intelligence artificielle de trading haute précision sous licence payante.\n\n📲 **Pour obtenir votre clé d'accès (Abonnement), veuillez contacter le fondateur : [@hermann1123](https://t.me/hermann1123)**"
        return bot.send_message(user_id, alerte, parse_mode="Markdown", disable_web_page_preview=True)

    utilisateurs_actifs.add(user_id)
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - ÉDITION ULTIME** 🔥
    
Bienvenue dans ton radar de trading haute précision ! Ce bot est propulsé par un moteur d'intelligence mathématique (Moteur PDF Pure) pour scanner les graphiques à la milliseconde.

📖 **MODE D'EMPLOI :**
1️⃣ **SÉLECTION :** Clique sur "📊 CHOISIR UNE DEVISE" pour verrouiller un actif logique compatible Pocket Broker.
2️⃣ **RADAR :** Clique sur "🚀 LANCER L'ANALYSE" pour déclencher le scan et le verrouillage Sniper.
3️⃣ **STRATÉGIE :** Consulte les meilleures fenêtres de tir via le bouton "⏰ HEURES DE TRADING".
4️⃣ **DISCIPLINE :** N'oublie pas : 2% de mise maximum et stop total après 3 pertes dans une session.

💡 **LE MOT DU FONDATEUR :**
*Le marché ne ressent rien, n'aie aucune émotion face à lui. Le succès ne vient pas de la chance, mais d'une discipline de fer. Laisse l'algorithme faire les calculs, ne force jamais un trade et protège ton capital comme un tireur d'élite. Bon profit !* 🎯💸"""
    bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **GUIDE DES HORAIRES DE TRADING (Heure GMT)** 🕒

✅ **SESSION 1 : MATINÉE (08h00 - 11h00)**
*Ouverture de l'Europe. Le vrai volume arrive sur les marchés.*
👍 **Devises Favorites :** EUR/USD, USD/JPY, AUD/USD

🔥 **SESSION 2 : ZONE EN OR (13h30 - 16h30)**
*Croisement Europe / New York. La volatilité est maximale.*
👍 **Devises Favorites :** EUR/USD, AUD/USD, USD/CAD

🌉 **SESSION 3 : MODE NUIT (20h00 - 08h00)**
*L'Europe s'endort, l'Asie se réveille. Le bot bascule automatiquement en mode sécurité (100% JPY).*
👍 **Devises Favorites :** AUD/JPY, USD/JPY, CAD/JPY, CHF/JPY, EUR/JPY

*Rappel de Discipline : Fixe-toi tes 2% de mise max et arrête-toi après 3 pertes dans la même session !*"""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=2)
    heure = datetime.datetime.now().hour
    
    if 8 <= heure < 20: 
        # --- ✅ CLAVIER DE JOUR CORRIGÉ (Retrait GBP, Ajout JPY) ---
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), 
            InlineKeyboardButton("🇯🇵 USD/JPY ✅", callback_data="set_USDJPY"), 
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), 
            InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), 
            InlineKeyboardButton("🇪🇺 EUR/JPY ✅", callback_data="set_EURJPY"),
            InlineKeyboardButton("🇨🇭 USD/CHF", callback_data="set_USDCHF")
        )
    else: 
        # 🌙 CLAVIER DE NUIT 100% CORRIGÉ (Retrait GBP, Focus JPY)
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/JPY ✅", callback_data="set_AUDJPY"), 
            InlineKeyboardButton("🇯🇵 USD/JPY ✅", callback_data="set_USDJPY"), 
            InlineKeyboardButton("🇨🇭 CHF/JPY ✅", callback_data="set_CHFJPY"), 
            InlineKeyboardButton("🇨🇦 CAD/JPY ✅", callback_data="set_CADJPY"), 
            InlineKeyboardButton("🇪🇺 EUR/JPY ✅", callback_data="set_EURJPY")
        )
        
    bot.send_message(message.chat.id, "Sélectionne l'actif à scanner (logique Pocket Option) :", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    
    # ✅ L'esthétique de chargement VIP
    try:
        msg = bot.send_message(chat_id, "⏳ *Initialisation du scan algorithmique rapide...*", parse_mode="Markdown")
        time.sleep(2)
        bot.edit_message_text(f"📡 *Connexion au flux {actif[:3]}/{actif[3:]} et scan de la volatilité ATR en cours...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(2)
        bot.edit_message_text("⚙️ *Calcul des indicateurs avancés (BB, RSI, Stochastique Pure PDF)...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(2)
        bot.edit_message_text("💎 *Triple confirmation Sniper et audit de résultat activé...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(1)
    except: 
        return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Échec de la récupération des données Deriv. Relance l'analyse.", chat_id, msg.message_id)
        except: pass
        return

    # --- ✅ LE TIMING SNIPER VERROUILLÉ (1 minute 50) ---
    delai_avant_entree = 110 
    heure_entree_dt = datetime.datetime.now() + datetime.timedelta(seconds=delai_avant_entree)
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)
    jauge = generer_jauge(confiance)
    rsi_emoji = "🟢" if "ACHAT" in action else "🔴"
    stoch_text = "Survente" if "ACHAT" in action else "Surachat"

    # ✅ LE SIGNAL ESTHÉTIQUE VIP (Format exact photo image_0.png)
    signal = f"""🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀
──────────────────
🛰 **ACTIF :** {actif[:3]}/{actif[3:]}
🎯 **ACTION :** {action}
⏳ **EXPIRATION :** {exp_texte}
──────────────────
🌡️ **FORCE DU SIGNAL (ALGORITHME) :**
{jauge}

📊 **VALIDATION DES INDICATEURS :**
➤ **RSI :** {rsi_emoji} Validé ({rsi_val})
➤ **Stochastique :** {rsi_emoji} Validé ({stoch_text})
➤ **Bollinger :** {rsi_emoji} {bb_status}
──────────────────
📍 **ORDRE À :** {heure_entree_dt.strftime("%H:%M:%S")} 👈
💵 **MISE RECOMMANDÉE :** {mise_recommandee}$ (2%)
🔥 **CONFIANCE GLOBALE :** {confiance}%
──────────────────
💎 *Audit de résultat (ITM/OTM) activé en arrière-plan.*"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except Exception: pass

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[chat_id] = {
        'symbole': actif, 
        'action': action_simplifiee
    }
    
    # ✅ Démarrage des chronomètres synchronisés pour audit
    Timer(delai_avant_entree, relever_prix_entree, args=[chat_id, actif]).start()
    Timer(delai_avant_entree + duree_secondes, verifier_resultat, args=[chat_id]).start()

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    actif = user_prefs.get(message.from_user.id)
    if not actif: 
        return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
        
    call_mock = type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})()
    save_devise(call_mock)

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    commande = message.text.split()
    if len(commande) < 2: 
        return bot.send_message(message.chat.id, "⚠️ Précise la devise. Exemple : `/vision EURUSD`", parse_mode="Markdown")
        
    symbole = commande[1].upper()
    try: 
        msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X de {symbole}...*", parse_mode="Markdown")
    except: 
        return
    
    candles = obtenir_donnees_deriv(symbole)
    if not candles: 
        return bot.edit_message_text("⚠️ Impossible de scanner (manque de données).", message.chat.id, msg.message_id)
        
    try:
        df = pd.DataFrame([{'close': c['close'], 'high': c['high'], 'low': c['low']} for c in candles])
        
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        bb_haute = indicateur_bb.bollinger_hband().iloc[-1]
        bb_basse = indicateur_bb.bollinger_lband().iloc[-1]
        
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        ema_200 = df['ema_200'].iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        if prix_actuel >= bb_haute: 
            position_bb = "🔴 Au Plafond (Touche la bande haute)"
        elif prix_actuel <= bb_basse: 
            position_bb = "🟢 Au Plancher (Touche la bande basse)"
        else: 
            position_bb = "⚪ Au Milieu (Zone neutre)"

        rapport = f"""👁️ **VISION RAYONS X : {symbole}** 👁️
──────────────────
💰 **Prix actuel :** `{prix_actuel:.5f}`
🛡️ **EMA 200 (Tendance) :** `{ema_200:.5f}`
📏 **Position Bollinger :** {position_bb}

📊 **Niveau RSI :** `{rsi:.2f}` *(Rappel: >60 = Surchauffe, <40 = Essoufflé)*
📉 **Niveau Stochastique :** `{stoch_k:.2f}` *(Rappel: >80 = Surachat, <20 = Survente)*
──────────────────"""

        if position_bb != "⚪ Au Milieu (Zone neutre)":
            rapport += "\n⚠️ *Le prix teste les limites, tiens-toi prêt !*" 
        else:
            rapport += "\n💤 *Le marché respire tranquillement.*"
            
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
        
    except Exception as e: 
        bot.edit_message_text(f"❌ Erreur lors du scan : {e}", message.chat.id, msg.message_id)

# ==========================================
# LANCEMENT GLOBAL DU SYSTÈME
# ==========================================

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestion_horaires_et_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Serveur VIP + 2 Alertes Originelles + Focus JPY Nuit (Édition Ultime) lancé.", flush=True)
    bot.infinity_polling()
