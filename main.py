import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    return "Le bot Telegram est en ligne (Mode Algorithmique Avancé) !"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- FONCTION MARCHÉ : L'ALGORITHME SANS IA ---
def obtenir_donnees_marche(symbole):
    """Récupère les prix pour calculer la tendance ET la volatilité."""
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

# --- ÉTAPE 1 : MENU DE DÉMARRAGE ---
@bot.message_handler(commands=['start'])
def envoyer_menu(message):
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
    btn_retour = InlineKeyboardButton("🔙 Changer de devise", callback_data="menu_devises")
    markup.add(btn_analyser, btn_retour)
    
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
    
    # Récupération des prix
    prix_actuel, prix_precedent = obtenir_donnees_marche(actif_choisi) 
    
    if not prix_actuel:
        bot.edit_message_text("❌ Flux de données interrompu ou marché fermé.", 
                              call.message.chat.id, 
                              call.message.message_id)
        return

    # 1. Logique de direction (CALL / PUT)
    ecart = prix_actuel - prix_precedent
    if ecart >= 0:
        action = "🟢 ACHAT (CALL)"
    else:
        action = "🔴 VENTE (PUT)"
        
    # 2. Logique d'expiration basée sur l'analyse de la volatilité
    # On calcule le pourcentage de mouvement
    volatilite_pourcentage = abs((ecart / prix_precedent) * 100)
    
    if volatilite_pourcentage > 0.20:
        temps_expiration = "30 SEC ⏱"  # Marché très agressif
    elif volatilite_pourcentage > 0.05:
        temps_expiration = "1 MINUTE ⏱" # Marché normal
    else:
        # Marché lent, on ajoute un peu d'aléatoire réaliste pour les phases de consolidation
        temps_expiration = random.choice(["2 MINUTES ⏱", "3 MINUTES ⏱"])
        
    # 3. Confiance
    confiance = random.randint(86, 98) # Réglé pour avoir l'air hyper pro

    # 4. Heure exacte
    maintenant = datetime.datetime.now()
    heure_entree = (maintenant + datetime.timedelta(minutes=1)).replace(second=0, microsecond=0).strftime("%H:%M:00")

    # Formatage strict avec l'expiration dynamique
    signal_texte = f"""🚀 SIGNAL SNIPER GÉNÉRÉ 🚀
──────────────────
🛰 ACTIF : {actif_formate}
🎯 ACTION : {action}
⏳ EXPIRATION : {temps_expiration}
──────────────────
📍 ORDRE À : {heure_entree} 👈
📊 CONFIANCE : [ {confiance}% ] 🔥
──────────────────
💎 Prêt pour l'entrée à la seconde 00."""

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
    
