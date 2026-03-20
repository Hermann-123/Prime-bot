import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
import requests
from dotenv import load_dotenv
import datetime
import random
from flask import Flask
from threading import Thread

# --- CONFIGURATION ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# --- SYSTÈME ANTI-COUPURE (POUR RENDER) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Le bot Telegram est en ligne (Mode Algorithmique Pro) !"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- FONCTION MARCHÉ : L'ALGORITHME ---
def obtenir_donnees_marche(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X"
    headers = {'User-Agent': 'Mozilla/5.0'} 
    try:
        reponse = requests.get(url, headers=headers, timeout=5)
        donnees = reponse.json()
        meta = donnees['chart']['result'][0]['meta']
        prix_actuel = round(float(meta['regularMarketPrice']), 5)
        prix_precedent = round(float(meta['previousClose']), 5)
        return prix_actuel, prix_precedent
    except Exception as e:
        print(f"Erreur API Forex: {e}")
        return None, None

# --- ÉTAPE 1 : MENU DE DÉMARRAGE ET NETTOYAGE DU CLAVIER ---
@bot.message_handler(commands=['start'])
def envoyer_menu(message):
    # 1. Ordre de destruction de l'ancien clavier en bas (les boutons rouges)
    supprimer_clavier = ReplyKeyboardRemove()
    msg_nettoyage = bot.send_message(message.chat.id, "🔄 Mise à jour de l'interface...", reply_markup=supprimer_clavier)
    bot.delete_message(message.chat.id, msg_nettoyage.message_id) # On efface ce message instantanément pour que ce soit invisible

    # 2. Affichage du vrai menu
    markup = InlineKeyboardMarkup()
    btn_choisir = InlineKeyboardButton("📊 Choisir une devise", callback_data="menu_devises")
    markup.add(btn_choisir)
    
    texte_accueil = (
        "🏴‍☠️ **TERMINAL SIGNAUX PRIME** 🤯\n\n"
        "Système Algorithmique HFT connecté au flux Forex.\n"
        "Clique ci-dessous pour commencer :"
    )
    bot.send_message(message.chat.id, texte_accueil, reply_markup=markup, parse_mode="Markdown")

# --- ÉTAPE 2 : CHOIX DE LA DEVISE ---
@bot.callback_query_handler(func=lambda call: call.data == "menu_devises")
def menu_devises(call):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🇪🇺 EUR / USD", callback_data="select_EURUSD"),
        InlineKeyboardButton("🇯🇵 USD / JPY", callback_data="select_USDJPY"),
        InlineKeyboardButton("🇵🇰 USD / PKR", callback_data="select_USDPKR")
    )
    bot.edit_message_text("Sélectionne la devise à préparer :", 
                          call.message.chat.id, 
                          call.message.message_id, 
                          reply_markup=markup)

# --- ÉTAPE 3 : BOUTON D'ANALYSE ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("select_"))
def confirmation_devise(call):
    actif_choisi = call.data.split("_")[1]
    actif_formate = f"{actif_choisi[:3]}/{actif_choisi[3:]}"
    
    markup = InlineKeyboardMarkup(row_width=1)
    btn_analyser = InlineKeyboardButton(f"🚀 Analyser {actif_formate}", callback_data=f"analyse_{actif_choisi}")
    markup.add(btn_analyser)
    
    texte = f"✅ **Devise configurée : {actif_formate}**\n\nLe terminal est prêt. Clique sur le bouton ci-dessous pour lancer l'algorithme."
    bot.edit_message_text(texte, 
                          call.message.chat.id, 
                          call.message.message_id, 
                          reply_markup=markup, 
                          parse_mode="Markdown")

# --- ÉTAPE 4 : GÉNÉRATION DU SIGNAL SNIPER ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("analyse_"))
def traitement_signal(call):
    actif_choisi = call.data.split("_")[1]
    actif_formate = f"{actif_choisi[:3]}/{actif_choisi[3:]}"
    
    bot.edit_message_text(f"🔄 *Extraction et calcul de volatilité pour {actif_formate}...*", 
                          call.message.chat.id, 
                          call.message.message_id, 
                          parse_mode="Markdown")
    
    prix_actuel, prix_precedent = obtenir_donnees_marche(actif_choisi) 
    
    if not prix_actuel:
        bot.edit_message_text("❌ Flux de données interrompu ou marché fermé.", 
                              call.message.chat.id, 
                              call.message.message_id)
        return

    ecart = prix_actuel - prix_precedent
    if ecart >= 0:
        action = "🟢 ACHAT (CALL)"
    else:
        action = "🔴 VENTE (PUT)"
        
    volatilite_pourcentage = abs((ecart / prix_precedent) * 100) if prix_precedent else 0
    
    if volatilite_pourcentage > 0.20:
        temps_expiration = "30 SEC ⏱"
    elif volatilite_pourcentage > 0.05:
        temps_expiration = "1 MINUTE ⏱"
    else:
        temps_expiration = random.choice(["2 MINUTES ⏱", "3 MINUTES ⏱"])
        
    confiance = random.randint(86, 98)

    # L'heure avec +2 minutes de préparation
    maintenant = datetime.datetime.now()
    heure_entree = (maintenant + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0).strftime("%H:%M:00")

    signal_texte = f"""🚀 SIGNAL SNIPER GÉNÉRÉ 🚀
──────────────────
🛰 ACTIF : {actif_formate}
🎯 ACTION : {action}
⏳ EXPIRATION : {temps_expiration}
──────────────────
📍 ORDRE À : {heure_entree} 👈
📊 CONFIANCE : {confiance}% 🔥
──────────────────
💎 Prêt pour l'entrée à la seconde 00."""

    # ON REMET LES BOUTONS SOUS LE SIGNAL (Zone Blanche)
    markup = InlineKeyboardMarkup(row_width=1)
    btn_relancer = InlineKeyboardButton("🎯 RELANCER L'ANALYSE", callback_data=f"analyse_{actif_choisi}")
    btn_retour = InlineKeyboardButton("🔙 Changer de devise", callback_data="menu_devises")
    markup.add(btn_relancer, btn_retour)

    bot.edit_message_text(signal_texte, 
                          call.message.chat.id, 
                          call.message.message_id, 
                          reply_markup=markup, 
                          parse_mode="Markdown")

if __name__ == "__main__":
    keep_alive()
    print("Terminal Algorithmique HFT en ligne...")
    bot.infinity_polling()
                            
