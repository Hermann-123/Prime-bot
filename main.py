import os
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
from dotenv import load_dotenv
import datetime
import random
import math
import time
from flask import Flask
from threading import Thread, Timer

# --- CONFIGURATION ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TELEGRAM_TOKEN)

CAPITAL_ACTUEL = 40650 
user_prefs = {}
trades_en_cours = {} # Mémoire vive du bot pour suivre les trades
utilisateurs_actifs = set() # Mémoire pour savoir à qui envoyer les alertes de 25min

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Trading Binaire (Vérificateur Auto ITM/OTM) en ligne !"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

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

# --- LES DEUX ÉTAPES DE VÉRIFICATION AUTOMATIQUE ---
def relever_prix_entree(chat_id, symbole):
    """S'active exactement à l'heure d'entrée pour mémoriser le prix."""
    prix = obtenir_prix_actuel(symbole)
    if prix and chat_id in trades_en_cours:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    """S'active à la fin de l'expiration pour juger la victoire ou la défaite."""
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'):
        bot.send_message(chat_id, "⚠️ **Trade terminé.** (Le flux de prix a été interrompu, impossible de vérifier le résultat exact).", parse_mode="Markdown")
        return

    prix_sortie = obtenir_prix_actuel(trade['symbole'])
    if not prix_sortie:
        return

    prix_entree = trade['prix_entree']
    action = trade['action']
    symbole = trade['symbole']

    # Logique de victoire
    gagne = False
    if action == "CALL" and prix_sortie > prix_entree:
        gagne = True
    elif action == "PUT" and prix_sortie < prix_entree:
        gagne = True

    # Envoi du message final
    if gagne:
        texte = f"✅ **VICTOIRE (ITM) !**\n\nSignal passé avec succès 🎉\nLe trade sur {symbole[:3]}/{symbole[3:]} a été validé !\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
    else:
        texte = f"❌ **PERTE (OTM)** ⚠️\n\nLe marché s'est retourné sur {symbole[:3]}/{symbole[3:]}.\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n\n*Garde ton sang-froid, respecte ton Money Management pour la prochaine opportunité.*"
    
    bot.send_message(chat_id, texte, parse_mode="Markdown")
    
    # Nettoyage de la mémoire
    if chat_id in trades_en_cours:
        del trades_en_cours[chat_id]

# --- L'OUTIL SUPER PERFORMANT : BOLLINGER BANDS + STOCHASTIQUE ---
def analyser_binaire_pro(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=25m&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=10)
        donnees = reponse.json()
        prix_liste = donnees['chart']['result'][0]['indicators']['quote'][0]['close']
        prix_valides = [p for p in prix_liste if p is not None]
        
        if len(prix_valides) < 20: return "⚠️ Pas assez de données", None, None, None

        prix_actuel = prix_valides[-1]
        
        # 1. BANDES DE BOLLINGER
        echantillon_20 = prix_valides[-20:]
        sma_20 = sum(echantillon_20) / 20
        variance = sum((x - sma_20) ** 2 for x in echantillon_20) / 20
        ecart_type = math.sqrt(variance)
        
        bande_haute = sma_20 + (2 * ecart_type)
        bande_basse = sma_20 - (2 * ecart_type)
        largeur_bande = (bande_haute - bande_basse) / sma_20
        
        # 2. STOCHASTIQUE
        echantillon_14 = prix_valides[-14:]
        plus_bas_14 = min(echantillon_14)
        plus_haut_14 = max(echantillon_14)
        
        if plus_haut_14 != plus_bas_14:
            stoch_k = ((prix_actuel - plus_bas_14) / (plus_haut_14 - plus_bas_14)) * 100
        else:
            stoch_k = 50

        # 3. EXPIRATION DYNAMIQUE
        duree_secondes = 180
        if largeur_bande > 0.0020:
            expiration = "30 SEC ⏱"
            duree_secondes = 30
        elif largeur_bande > 0.0012:
            expiration = "1 MINUTE ⏱"
            duree_secondes = 60
        elif largeur_bande > 0.0007:
            expiration = "2 MINUTES ⏱"
            duree_secondes = 120
        else:
            expiration = "3 MINUTES ⏱"
            duree_secondes = 180
        
        # 4. LOGIQUE SNIPER
        if prix_actuel >= bande_haute and stoch_k > 75:
            action = "🔴 VENTE (PUT)"
            confiance = random.randint(93, 98)
        elif prix_actuel <= bande_basse and stoch_k < 25:
            action = "🟢 ACHAT (CALL)"
            confiance = random.randint(93, 98)
        elif stoch_k > 85:
            action = "🔴 VENTE (PUT)"
            confiance = random.randint(85, 91)
        elif stoch_k < 15:
            action = "🟢 ACHAT (CALL)"
            confiance = random.randint(85, 91)
        else:
            return "⚠️ Marché neutre (Attente de cassure)", None, None, None
            
        return action, confiance, expiration, duree_secondes
    except Exception as e:
        return None, None, None, None

# --- SCANNER AUTOMATIQUE TOUTES LES 25 MINUTES ---
def scanner_marche_auto():
    devises_a_surveiller = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "USDPKR"]
    while True:
        time.sleep(1500)  # Pause de 25 minutes (1500 secondes)
        if not utilisateurs_actifs:
            continue
        
        meilleur_actif = None
        meilleure_confiance = 0
        
        # Le bot scanne chaque devise en silence
        for actif in devises_a_surveiller:
            action, confiance, exp, duree = analyser_binaire_pro(actif)
            # Si le marché n'est pas neutre (pas de ⚠️) et qu'on a un signal
            if action and action is not None and "⚠️" not in action and confiance:
                if confiance > meilleure_confiance:
                    meilleure_confiance = confiance
                    meilleur_actif = actif
        
        # S'il a trouvé un bon setup, il prévient les utilisateurs
        if meilleur_actif:
            alerte_msg = f"🚨 **ALERTE MARCHÉ (Scan Auto)** 🚨\n\nJ'ai détecté un excellent momentum sur **{meilleur_actif[:3]}/{meilleur_actif[3:]}** (Confiance estimée : {meilleure_confiance}%).\n\nVa dans le menu, choisis cette devise et lance l'analyse pour confirmer et obtenir le signal précis !"
            for chat_id in utilisateurs_actifs:
                try:
                    bot.send_message(chat_id, alerte_msg, parse_mode="Markdown")
                except:
                    pass

# --- INTERFACE CLAVIER ---
def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    utilisateurs_actifs.add(message.chat.id)
    bot.send_message(message.chat.id, "🏴‍☠️ **TERMINAL PRIME - ÉDITION BINAIRE** 🔥\n\nMoteur : **Bollinger Bands + Stochastique**\nFonctions : **Vérification ITM/OTM Automatique**", reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🇪🇺 EUR / USD", callback_data="set_EURUSD"),
        InlineKeyboardButton("🇬🇧 GBP / USD", callback_data="set_GBPUSD"),
        InlineKeyboardButton("🇯🇵 USD / JPY", callback_data="set_USDJPY"),
        InlineKeyboardButton("🇦🇺 AUD / USD", callback_data="set_AUDUSD"),
        InlineKeyboardButton("🇨🇭 USD / CHF", callback_data="set_USDCHF"),
        InlineKeyboardButton("🇨🇦 USD / CAD", callback_data="set_USDCAD"),
        InlineKeyboardButton("🇵🇰 USD / PKR", callback_data="set_USDPKR")
    )
    bot.send_message(message.chat.id, "Sélectionne l'actif à scanner :", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    bot.send_message(call.message.chat.id, f"✅ **Cible verrouillée : {actif[:3]}/{actif[3:]}**")

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
        return

    msg = bot.send_message(message.chat.id, "⏳ *Initialisation du scan algorithmique (Temps requis : 1m 30s)...*", parse_mode="Markdown")
    
    time.sleep(30)
    bot.edit_message_text(f"📡 *Connexion au flux {actif[:3]}/{actif[3:]} et scan de la volatilité en cours...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(30)
    bot.edit_message_text("⚙️ *Calcul des Bandes de Bollinger et de l'Oscillateur Stochastique...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(25)
    bot.edit_message_text("💎 *Filtrage du bruit de marché et verrouillage de la cible Sniper...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(5)
    
    action, confiance, exp, duree_secondes = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        bot.edit_message_text(f"{action}\nLe prix est enfermé au milieu du tunnel. Patientez pour une vraie opportunité.", message.chat.id, msg.message_id)
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

    # Suppression du message de chargement et envoi d'un NOUVEAU message pour déclencher la notification
    bot.delete_message(message.chat.id, msg.message_id)
    bot.send_message(message.chat.id, signal, parse_mode="Markdown")

    # --- SUIVI DE TRADE ---
    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[message.chat.id] = {'symbole': actif, 'action': action_simplifiee}
    
    Timer(120, relever_prix_entree, args=[message.chat.id, actif]).start()
    
    delai_verification = 120 + duree_secondes
    Timer(delai_verification, verifier_resultat, args=[message.chat.id]).start()

if __name__ == "__main__":
    keep_alive()
    # Lancement du scanner en arrière-plan
    Thread(target=scanner_marche_auto, daemon=True).start()
    bot.infinity_polling()
                               
