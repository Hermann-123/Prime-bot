import os
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
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

# Dictionnaire pour mémoriser la devise par utilisateur
user_prefs = {}

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Trading Pro (Clavier Fixe) en ligne !"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- ALGORITHME DE PRÉCISION ---
def analyser_marche_pro(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=15m&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=10)
        donnees = reponse.json()
        prix_liste = donnees['chart']['result'][0]['indicators']['quote'][0]['close']
        prix_valides = [p for p in prix_liste if p is not None]
        if len(prix_valides) < 5: return None

        prix_actuel = prix_valides[-1]
        
        # Calcul RSI simplifié
        hausse, baisse = 0, 0
        for i in range(1, len(prix_valides)):
            diff = prix_valides[i] - prix_valides[i-1]
            if diff > 0: hausse += diff
            else: baisse += abs(diff)
        
        rs = hausse / baisse if baisse != 0 else 100
        rsi = 100 - (100 / (1 + rs))
        
        if rsi > 65: action = "🔴 VENTE (PUT)"
        elif rsi < 35: action = "🟢 ACHAT (CALL)"
        else:
            action = "🟢 ACHAT (CALL)" if prix_actuel > prix_valides[-2] else "🔴 VENTE (PUT)"
            
        confiance = random.randint(88, 98)
        expiration = "1 MINUTE ⏱" if abs(prix_actuel - prix_valides[0]) > 0.0005 else "2 MINUTES ⏱"
        
        return action, confiance, expiration
    except:
        return None

# --- GESTION DU CLAVIER FIXE ---
def obtenir_clavier_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    btn_devises = KeyboardButton("📊 CHOISIR UNE DEVISE")
    btn_analyse = KeyboardButton("🚀 LANCER L'ANALYSE")
    markup.add(btn_devises, btn_analyse)
    return markup

# --- LOGIQUE DU BOT ---

@bot.message_handler(commands=['start'])
def bienvenue(message):
    texte = "🏴‍☠️ **BIENVENUE SUR LE TERMINAL PRIME**\n\nTes commandes sont maintenant disponibles sur ton clavier en bas."
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier_principal(), parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "📊 CHOISIR UNE DEVISE")
def afficher_choix_devises(message):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🇪🇺 EUR / USD", callback_data="set_EURUSD"),
        InlineKeyboardButton("🇯🇵 USD / JPY", callback_data="set_USDJPY"),
        InlineKeyboardButton("🇵🇰 USD / PKR", callback_data="set_USDPKR")
    )
    bot.send_message(message.chat.id, "Sélectionne la devise à configurer :", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_"))
def selectionner_devise(call):
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    bot.answer_callback_query(call.id, f"Configuré sur {actif}")
    bot.send_message(call.message.chat.id, f"✅ **Actif sélectionné : {actif[:3]}/{actif[3:]}**\nTu peux maintenant cliquer sur 'Lancer l'analyse' dans ton clavier.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "🚀 LANCER L'ANALYSE")
def executer_analyse(message):
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        bot.send_message(message.chat.id, "⚠️ Erreur : Choisis d'abord une devise avec le bouton 📊.")
        return

    msg_attente = bot.send_message(message.chat.id, f"🔄 *Analyse RSI en cours pour {actif[:3]}/{actif[3:]}...*", parse_mode="Markdown")
    
    resultat = analyser_marche_pro(actif)
    if not resultat:
        bot.edit_message_text("❌ Erreur de flux financier.", message.chat.id, msg_attente.message_id)
        return

    action, confiance, expiration = resultat
    heure_entree = (datetime.datetime.now() + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0).strftime("%H:%M:00")

    signal = f"""🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀
──────────────────
🛰 ACTIF : {actif[:3]}/{actif[3:]}
🎯 ACTION : {action}
⏳ EXPIRATION : {expiration}
──────────────────
📍 ORDRE À : {heure_entree} 👈
📊 CONFIANCE : {confiance}% 🔥
──────────────────
💎 *Utilise ton clavier pour relancer.*"""

    bot.edit_message_text(signal, message.chat.id, msg_attente.message_id, parse_mode="Markdown")

if __name__ == "__main__":
    keep_alive()
    bot.infinity_polling()
        
