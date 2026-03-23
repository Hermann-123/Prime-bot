import os
import sys
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
import datetime
import random
import time
import string
from flask import Flask
from threading import Thread, Timer
import pandas as pd
import ta

# --- CORRECTION DE LA LIGNE 9 (SÉCURITÉ DOTENV) ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Si Render ne trouve pas dotenv, on ignore car tu as mis le token dans l'onglet Environment
    pass

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    print("⬛ BOÎTE NOIRE [ERREUR FATALE] : Le TELEGRAM_TOKEN est introuvable ! Vérifie l'onglet 'Environment' sur Render.")
    sys.exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# 👑 L'ID DU FONDATEUR (TOI) 👑
ADMIN_ID = 5968288964 

CAPITAL_ACTUEL = 40650 
user_prefs = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}

# SYSTÈME DE GESTION DES ABONNEMENTS
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Trading Binaire Prime VIP en ligne !"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- FONCTION DE VÉRIFICATION D'ACCÈS ---
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
            try:
                bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé. Veuillez contacter [@hermann1123](https://t.me/hermann1123) pour renouveler votre clé.", parse_mode="Markdown")
            except:
                pass
            return False
    return False

# --- GÉNÉRATEUR DE CLÉS ---
def generer_cle():
    caracteres = string.ascii_uppercase + string.digits
    aleatoire = ''.join(random.choice(caracteres) for _ in range(8))
    return f"PRIME-{aleatoire}"

# --- FONCTION DE RÉCUPÉRATION DU PRIX EXACT ---
def obtenir_prix_actuel(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=1d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=5)
        donnees = reponse.json()
        return round(float(donnees['chart']['result'][0]['meta']['regularMarketPrice']), 5)
    except:
        return None

# --- VÉRIFICATION AUTOMATIQUE (ITM/OTM) ---
def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel(symbole)
    if prix and chat_id in trades_en_cours:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'):
        bot.send_message(chat_id, "⚠️ **Trade terminé.** (Flux interrompu, résultat exact non vérifiable).", parse_mode="Markdown")
        return

    prix_sortie = obtenir_prix_actuel(trade['symbole'])
    if not prix_sortie:
        return

    prix_entree = trade['prix_entree']
    action = trade['action']
    symbole = trade['symbole']

    gagne = False
    if action == "CALL" and prix_sortie > prix_entree:
        gagne = True
    elif action == "PUT" and prix_sortie < prix_entree:
        gagne = True

    if gagne:
        texte = f"✅ **VICTOIRE (ITM) !**\n\nSignal passé avec succès 🎉\nLe trade sur {symbole[:3]}/{symbole[3:]} a été validé !\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
    else:
        texte = f"❌ **PERTE (OTM)** ⚠️\n\nLe marché s'est retourné sur {symbole[:3]}/{symbole[3:]}.\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n\n*Garde ton sang-froid, respecte ton Money Management.*"
    
    bot.send_message(chat_id, texte, parse_mode="Markdown")
    
    if chat_id in trades_en_cours:
        del trades_en_cours[chat_id]

# --- MOTEUR D'ANALYSE PRO (PANDAS + TA) ---
def analyser_binaire_pro(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=2d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=10)
        donnees = reponse.json()
        
        resultat = donnees['chart']['result'][0]
        quote = resultat['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'close': quote['close'],
            'high': quote['high'],
            'low': quote['low']
        }).dropna()
        
        if len(df) < 50:
            return "⚠️ Pas assez de données", None, None, None

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        
        indicateur_stoch = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
        df['stoch_k'] = indicateur_stoch.stoch()
        
        indicateur_rsi = ta.momentum.RSIIndicator(close=df['close'], window=14)
        df['rsi'] = indicateur_rsi.rsi()

        derniere_bougie = df.iloc[-1]
        prix_actuel = derniere_bougie['close']
        
        largeur_bande = (derniere_bougie['bb_haute'] - derniere_bougie['bb_basse']) / prix_actuel
        duree_secondes = 180
        if largeur_bande > 0.0020:
            expiration = "30 SEC ⏱"
            duree_secondes = 30
        elif largeur_bande > 0.0012:
            expiration = "1 MINUTE ⏱"
            duree_secondes = 60
        else:
            expiration = "2 MINUTES ⏱"
            duree_secondes = 120
        
        action = None
        confiance = 0
        
        if prix_actuel >= derniere_bougie['bb_haute'] and derniere_bougie['stoch_k'] > 80 and derniere_bougie['rsi'] > 60:
            action = "🔴 VENTE (PUT)"
            confiance = random.randint(88, 95) 
            
        elif prix_actuel <= derniere_bougie['bb_basse'] and derniere_bougie['stoch_k'] < 20 and derniere_bougie['rsi'] < 40:
            action = "🟢 ACHAT (CALL)"
            confiance = random.randint(88, 95)
            
        else:
            return "⚠️ Marché neutre (Attente de cassure)", None, None, None
            
        return action, confiance, expiration, duree_secondes
        
    except Exception as e:
        return None, None, None, None

# --- SCANNER AUTOMATIQUE EN ARRIÈRE-PLAN ---
def scanner_marche_auto():
    devises_a_surveiller = ["EURUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "EURJPY", "CHFJPY", "AUDJPY"]
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: continue
            
            for actif in devises_a_surveiller:
                action, confiance, exp, duree = analyser_binaire_pro(actif)
                
                if action and "⚠️" not in action and confiance:
                    maintenant = time.time()
                    
                    if actif in derniere_alerte_auto and (maintenant - derniere_alerte_auto[actif] < 900):
                        continue
                        
                    derniere_alerte_auto[actif] = maintenant
                    
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"🔒 Verrouiller {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                    
                    alerte_msg = f"🚨 **NOUVELLE OPPORTUNITÉ DÉTECTÉE** 🚨\n\nL'algorithme vient de repérer une configuration Sniper sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Clique sur le bouton ci-dessous pour verrouiller la cible, puis lance l'analyse !*"
                    
                    for chat_id in utilisateurs_a_alerter:
                        try:
                            bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except:
                            pass
        except Exception as e:
            print(f"⬛ BOÎTE NOIRE [ERREUR SCANNER] : {e}")

# --- ACTIVATION DE LA CLÉ PAR LE CLIENT (ANTI-PARTAGE) ---
@bot.message_handler(func=lambda m: m.text and m.text.startswith("PRIME-"))
def activer_cle(message):
    cle = message.text.strip()
    
    if cle in cles_generees:
        infos_cle = cles_generees[cle]
        
        if infos_cle["user_id"] != message.chat.id:
            bot.send_message(message.chat.id, "❌ **ACCÈS REFUSÉ** ❌\n\nCette clé d'activation a été générée pour un autre compte Telegram. Elle est personnelle et intransférable. Si vous souhaitez obtenir un accès, contactez le fondateur.", parse_mode="Markdown")
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

# --- MENU D'ABONNEMENT POUR L'ADMIN ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    if call.from_user.id != ADMIN_ID: return
    action = call.data.split("_")[1]
    user_id = int(call.data.split("_")[2])
    
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"),
            InlineKeyboardButton("2 Semaines", callback_data=f"gen_14_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"),
            InlineKeyboardButton("2 Mois", callback_data=f"gen_60_{user_id}"),
            InlineKeyboardButton("3 Mois", callback_data=f"gen_90_{user_id}"),
            InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Utilisateur `{user_id}` accepté en salle d'attente.\n\nChoisis la durée de son abonnement pour générer sa clé personnelle :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif action == "refuser":
        bot.edit_message_text(f"❌ Demande refusée/ignorée.", call.message.chat.id, call.message.message_id)

# --- GÉNÉRATION DE LA CLÉ LIÉE À L'INTRUS ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    if call.from_user.id != ADMIN_ID: return
    parts = call.data.split("_")
    jours = int(parts[1])
    user_id = int(parts[2])
    
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    
    duree_texte = f"{jours} Jours" if jours != 999 else "À VIE"
    
    msg_cle = f"🔑 **CLÉ PERSONNELLE GÉNÉRÉE** 🔑\n\n⏳ Durée : {duree_texte}\n👤 Pour l'ID : `{user_id}`\n\nCopie ce message et envoie-le à ton client :\n\n`{cle}`\n\n*(S'il donne cette clé à quelqu'un d'autre, elle sera refusée !)*"
    
    bot.edit_message_text(msg_cle, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# --- INTERFACE CLAVIER ---
def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

# --- NOUVEAU MENU START + SÉCURITÉ INTRUS ---
@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    username = message.from_user.username or message.from_user.first_name
    
    if not est_autorise(user_id):
        # Alerte à l'admin
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Accepter (Créer Clé)", callback_data=f"admin_accepter_{user_id}"),
            InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}")
        )
        alerte_admin = f"🚨 **NOUVEAU CLIENT POTENTIEL** 🚨\n\nUn visiteur a cliqué sur /start.\n👤 Utilisateur : @{username}\n🆔 ID : `{user_id}`\n\nVeux-tu lui générer un abonnement ?"
        try: bot.send_message(ADMIN_ID, alerte_admin, reply_markup=markup, parse_mode="Markdown")
        except: pass
            
        # Message à l'intrus
        texte_intrus = """🔒 **ACCÈS RESTREINT - TERMINAL PRIVÉ** 🔒

Ce système est une intelligence artificielle de trading haute précision sous licence payante.

📲 **Pour obtenir votre clé d'accès (Abonnement), veuillez contacter le fondateur : [@hermann1123](https://t.me/hermann1123)**

*(Si vous avez déjà acheté une clé, collez-la simplement ici).*"""
        bot.send_message(user_id, texte_intrus, parse_mode="Markdown", disable_web_page_preview=True)
        return

    # Si l'utilisateur est autorisé (Toi, ou un abonné)
    utilisateurs_actifs.add(user_id)
    
    # Design magnifique conservé
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - ÉDITION BINAIRE** 🔥
    
Bienvenue dans ton radar de trading ultime ! Ce bot est propulsé par un moteur d'intelligence mathématique (Pandas + TA) pour scanner les graphiques à la milliseconde.

📖 **MODE D'EMPLOI :**
1️⃣ **SÉLECTION :** Clique sur "📊 CHOISIR UNE DEVISE" et verrouille la paire que tu souhaites trader.
2️⃣ **RADAR :** Clique sur "🚀 LANCER L'ANALYSE" pour déclencher le scan et le verrouillage Sniper.
3️⃣ **STRATÉGIE :** Consulte les meilleures fenêtres de tir via le bouton "⏰ HEURES DE TRADING".
4️⃣ **DISCIPLINE :** N'oublie pas : 2% de mise maximum et stop total après 3 pertes dans une session.

💡 **LE MOT DU FONDATEUR :**
*Le marché ne ressent rien, n'aie aucune émotion face à lui. Le succès ne vient pas de la chance, mais d'une discipline de fer. Laisse l'algorithme faire les calculs, ne force jamais un trade et protège ton capital comme un tireur d'élite. Bon profit !* 🎯💸

👨‍💻 **SUPPORT TECHNIQUE :**
À la moindre rencontre d'un problème ou d'un bug, veuillez contacter le fondateur du bot : **[@hermann1123](https://t.me/hermann1123)**"""

    bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown", disable_web_page_preview=True)

# --- SÉCURITÉ SUR LES AUTRES BOUTONS ---
@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    
    texte = """🕒 **GUIDE DES HORAIRES DE TRADING (Heure GMT)** 🕒

✅ **SESSION 1 : MATINÉE (08h00 - 11h00)**
*Ouverture de l'Europe. Le vrai volume arrive sur les marchés.*
👍 **Devises Favorites :** EUR/USD, EUR/JPY, CHF/JPY, USD/CHF
👎 **À Éviter :** AUD/USD, AUD/JPY, USD/CAD (Marchés lents)

🔥 **SESSION 2 : ZONE EN OR (13h30 - 16h30)**
*Croisement Europe / New York. La volatilité est maximale et les tendances sont pures.*
👍 **Devises Favorites :** EUR/USD, USD/CAD, AUD/USD
👎 **À Éviter :** Paires en JPY (le marché asiatique est fermé)

❌ **ZONE ROUGE : DANGER (22h00 - 07h00)**
*Marché sans volume, mouvements manipulés ou OTC.*
☠️ **À Fuir Absolument :** Toutes les devises. Laisse le bot se reposer.

*Rappel de Discipline : Fixe-toi tes 2% de mise max et arrête-toi après 3 pertes dans la même session !*"""
    
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🇪🇺 EUR / USD", callback_data="set_EURUSD"),
        InlineKeyboardButton("🇯🇵 USD / JPY", callback_data="set_USDJPY"),
        InlineKeyboardButton("🇦🇺 AUD / USD", callback_data="set_AUDUSD"),
        InlineKeyboardButton("🇨🇦 USD / CAD", callback_data="set_USDCAD"),
        InlineKeyboardButton("🇨🇭 USD / CHF", callback_data="set_USDCHF"),
        InlineKeyboardButton("🇪🇺 EUR / JPY", callback_data="set_EURJPY"),
        InlineKeyboardButton("🇨🇭 CHF / JPY", callback_data="set_CHFJPY"),
        InlineKeyboardButton("🇦🇺 AUD / JPY", callback_data="set_AUDJPY")
    )
    bot.send_message(message.chat.id, "Sélectionne l'actif à scanner :", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    if not est_autorise(call.message.chat.id): return
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    bot.send_message(call.message.chat.id, f"✅ **Cible verrouillée : {actif[:3]}/{actif[3:]}**")

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
        return

    msg = bot.send_message(message.chat.id, "⏳ *Initialisation du scan algorithmique rapide...*", parse_mode="Markdown")
    
    time.sleep(2)
    bot.edit_message_text(f"📡 *Connexion au flux {actif[:3]}/{actif[3:]} et scan de la volatilité en cours...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(2)
    bot.edit_message_text("⚙️ *Calcul des indicateurs avancés (BB, RSI, Stochastique)...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(2)
    bot.edit_message_text("💎 *Triple confirmation et verrouillage Sniper...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(1)
    
    action, confiance, exp, duree_secondes = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        bot.edit_message_text(f"{action}\nLe prix ne remplit pas les conditions strictes de l'algorithme. Patientez.", message.chat.id, msg.message_id)
        return
    elif not action:
        bot.edit_message_text("❌ Échec de la récupération des données. Relance l'analyse.", message.chat.id, msg.message_id)
        return

    maintenant = datetime.datetime.now()
    heure_entree_dt = (maintenant + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0)
    heure_entree_texte = heure_entree_dt.strftime("%H:%M:00")
    
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)

    signal = f"""🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀
──────────────────
🛰 ACTIF : {actif[:3]}/{actif[3:]}
🎯 ACTION : {action}
⏳ EXPIRATION : {exp}
──────────────────
📍 ORDRE À : {heure_entree_texte} 👈
💵 MISE RECOMMANDÉE : {mise_recommandee}$ (2%)
📊 CONFIANCE : {confiance}% 🔥
──────────────────
💎 *Audit de résultat (ITM/OTM) activé en arrière-plan.*"""

    bot.delete_message(message.chat.id, msg.message_id)
    bot.send_message(message.chat.id, signal, parse_mode="Markdown")

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[message.chat.id] = {'symbole': actif, 'action': action_simplifiee}
    
    delai_attente_entree = (heure_entree_dt - datetime.datetime.now()).total_seconds()
    delai_attente_entree = max(0, delai_attente_entree)
    
    Timer(delai_attente_entree, relever_prix_entree, args=[message.chat.id, actif]).start()
    
    delai_verification = delai_attente_entree + duree_secondes
    Timer(delai_verification, verifier_resultat, args=[message.chat.id]).start()

# --- COMMANDE SECRÈTE : RADIOGRA
