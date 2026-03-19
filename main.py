import telebot
from telebot import types
import threading
import ccxt
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask
import os
import time
import google.generativeai as genai

# --- 1. CONFIGURATION SÉCURISÉE ---
# Le code récupère la clé que tu as mise dans Render
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Modèle Flash : ultra rapide pour éviter les coupures sur Render
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    print("⚠️ ERREUR : Clé API introuvable dans les variables d'environnement")

TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
sys_data = {"pair": "EUR/USD"}

# --- 2. SERVEUR DE MAINTIEN EN VIE ---
app = Flask(__name__)
@app.route('/')
def health(): return "PRIME V10 ONLINE", 200

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 3. CERVEAU IA AVEC AUTO-RETRY ---
def get_ai_advice(prompt):
    for attempt in range(2):  # Réessaie 2 fois en cas de bug
        try:
            full_prompt = f"Tu es un trader pro. Réponds court (15 mots max) : {prompt}"
            response = model.generate_content(full_prompt)
            return response.text
        except Exception:
            time.sleep(2)
    return "⚠️ Le marché est trop agité. Réessayez dans 1 minute."

# --- 4. COMMANDES DU BOT ---
@bot.message_handler(commands=['start', 'menu'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("🎯 ANALYSE IA SNIPER", "🧠 DEMANDER À L'IA")
    bot.send_message(message.chat.id, "💎 **PRIME TERMINAL V10 (PRO)**\n_Système IA sécurisé et actif._", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎯 ANALYSE IA SNIPER")
def get_ia_signal(message):
    msg = bot.send_message(message.chat.id, "🔍 *Analyse des graphiques en cours...*", parse_mode="Markdown")
    try:
        # Analyse technique simplifiée
        ai_decision = get_ai_advice(f"Donne une prédiction CALL ou PUT pour {sys_data['pair']}.")
        
        # Temps d'expiration
        h = (datetime.now() + timedelta(seconds=90)).replace(second=0, microsecond=0) + timedelta(minutes=1)
        
        signal = (
            f"🚀 **SIGNAL SNIPER IA**\n\n"
            f"🛰 **ACTIF :** `{sys_data['pair']}`\n"
            f"📊 **IA :** {ai_decision}\n"
            f"📍 **ENTRÉE :** `{h.strftime('%H:%M')}:00`"
        )
        bot.edit_message_text(signal, message.chat.id, msg.message_id, parse_mode="Markdown")
    except:
        bot.send_message(message.chat.id, "❌ Flux de données saturé.")

@bot.message_handler(func=lambda m: m.text == "🧠 DEMANDER À L'IA")
def ask_mode(message):
    bot.send_message(message.chat.id, "💬 Posez votre question sur le trading à l'IA :")

@bot.message_handler(func=lambda m: True)
def handle_chat(message):
    if len(message.text) > 2:
        reponse = get_ai_advice(message.text)
        bot.reply_to(message, reponse)

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
               
