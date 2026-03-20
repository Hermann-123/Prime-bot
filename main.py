import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import google.generativeai as genai
from dotenv import load_dotenv
import datetime
from flask import Flask
from threading import Thread

# --- CONFIGURATION ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

# --- SYSTÈME ANTI-COUPURE (POUR RENDER) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Le bot Telegram est en ligne et actif !"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- FONCTION MARCHÉ ---
def obtenir_prix_actuel(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X"
    headers = {'User-Agent': 'Mozilla/5.0'} 
    try:
        reponse = requests.get(url, headers=headers, timeout=5)
        donnees = reponse.json()
        prix = donnees['chart']['result'][0]['meta']['regularMarketPrice']
        return round(float(prix), 5)
    except Exception as e:
        print(f"Erreur API Forex: {e}")
        return None

# --- INTERFACE MENU ---
@bot.message_handler(commands=['start'])
def envoyer_menu(message):
    markup = InlineKeyboardMarkup(row_width=2)
    btn_eur = InlineKeyboardButton("🇪🇺 EUR / USD", callback_data="actif_EURUSD")
    btn_jpy = InlineKeyboardButton("🇯🇵 USD / JPY", callback_data="actif_USDJPY")
    btn_pkr = InlineKeyboardButton("🇵🇰 USD / PKR", callback_data="actif_USDPKR")
    
    markup.add(btn_eur, btn_jpy, btn_pkr)
    
    texte_accueil = (
        "🏴‍☠️ **TERMINAL SIGNAUX PRIME** 🤯\n\n"
        "Connecté au flux de données Forex en temps réel.\n"
        "Sélectionne la devise à analyser pour générer un ordre :"
    )
    bot.send_message(message.chat.id, texte_accueil, reply_markup=markup, parse_mode="Markdown")

# --- LE CERVEAU (GÉNÉRATION DU SIGNAL) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("actif_"))
def traitement_signal(call):
    actif_choisi = call.data.split("_")[1]
    actif_formate = f"{actif_choisi[:3]}/{actif_choisi[3:]}"
    
    bot.edit_message_text(f"🔄 *Acquisition des données en direct pour {actif_formate}...*", 
                          call.message.chat.id, 
                          call.message.message_id, 
                          parse_mode="Markdown")
    
    prix_direct = obtenir_prix_actuel(actif_choisi) 
    
    if not prix_direct:
        bot.edit_message_text("❌ Flux de données interrompu ou marché fermé. Réessaie.", 
                              call.message.chat.id, 
                              call.message.message_id)
        return

    maintenant = datetime.datetime.now()
    heure_entree = (maintenant + datetime.timedelta(minutes=1)).replace(second=0, microsecond=0).strftime("%H:%M:00")

    # PROMPT SNIPER STRICT
    prompt = f"""Tu es un algorithme de trading haute fréquence (HFT).
    Le prix en direct de {actif_formate} est de {prix_direct}.
    
    Génère un signal EXACTEMENT avec le modèle ci-dessous. 
    RÈGLE ABSOLUE : N'ajoute AUCUN texte avant ou après ce bloc. Ne modifie pas les emojis.

    🚀 SIGNAL SNIPER GÉNÉRÉ 🚀
    ──────────────────
    🛰 ACTIF : {actif_formate}
    🎯 ACTION : [Choisis 🟢 ACHAT (CALL) ou 🔴 VENTE (PUT)]
    ⏳ EXPIRATION : 30 SEC ⏱
    ──────────────────
    📍 ORDRE À : {heure_entree} 👈
    📊 CONFIANCE : [Génère un pourcentage entre 82% et 98%] 🔥
    ──────────────────
    💎 Prêt pour l'entrée à la seconde 00."""

    try:
        reponse_ia = model.generate_content(prompt)
        
        markup = InlineKeyboardMarkup()
        btn_retour = InlineKeyboardButton("🔙 Changer de Devise", callback_data="retour_menu")
        btn_relancer = InlineKeyboardButton("🎯 RELANCER", callback_data=call.data)
        markup.add(btn_relancer, btn_retour)
        
        bot.edit_message_text(reponse_ia.text, 
                              call.message.chat.id, 
                              call.message.message_id, 
                              reply_markup=markup, 
                              parse_mode="Markdown")
                              
    except Exception as e:
        bot.edit_message_text("❌ Échec de la génération HFT.", 
                              call.message.chat.id, 
                              call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "retour_menu")
def retour_menu(call):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🇪🇺 EUR / USD", callback_data="actif_EURUSD"),
        InlineKeyboardButton("🇯🇵 USD / JPY", callback_data="actif_USDJPY"),
        InlineKeyboardButton("🇵🇰 USD / PKR", callback_data="actif_USDPKR")
    )
    bot.edit_message_text("Sélectionne la devise à analyser :", 
                          call.message.chat.id, 
                          call.message.message_id, 
                          reply_markup=markup)

if __name__ == "__main__":
    # 1. On lance le serveur fantôme pour satisfaire Render
    keep_alive()
    # 2. On lance le bot Telegram
    print("Terminal Forex Sniper en ligne et sécurisé...")
    bot.infinity_polling()
    
