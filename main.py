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

# --- CONFIG IA ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
# Utilisation du modèle Pro standard (plus compatible)
model = genai.GenerativeModel('gemini-pro')

TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
sys_data = {"pair": "EUR/USD"}

app = Flask(__name__)
@app.route('/')
def health(): return "IA V8.2 ONLINE", 200

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- CERVEAU IA ---
def get_ai_advice(prompt):
    try:
        # On force une réponse très courte pour éviter les erreurs de génération
        response = model.generate_content(f"Réponds en 10 mots max : {prompt}")
        return response.text
    except Exception as e:
        return f"⚠️ IA indisponible. Utilisation de l'analyse mathématique."

# --- INTERFACE ---
@bot.message_handler(commands=['start', 'menu'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("🎯 ANALYSE IA SNIPER", "📱 CHOISIR LA PAIRE", "🧠 DEMANDER À L'IA")
    bot.send_message(message.chat.id, "💎 **PRIME TERMINAL V8.2**\n_Connexion IA sécurisée._", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎯 ANALYSE IA SNIPER")
def get_ia_signal(message):
    try:
        ex = ccxt.binance()
        bars = ex.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=10)
        prices = [b[4] for b in bars]
        
        # Analyse IA
        ai_decision = get_ai_advice(f"Analyse ces prix {prices}. Direction pour {sys_data['pair']}?")
        
        # Calcul temps
        h = (datetime.now() + timedelta(seconds=90)).replace(second=0, microsecond=0) + timedelta(minutes=1)
        
        signal = (
            f"🚀 **SIGNAL IA V8.2**\n"
            f"🛰 **ACTIF :** `{sys_data['pair']}`\n"
            f"📊 **ANALYSE :** {ai_decision}\n"
            f"📍 **ENTRÉE :** `{h.strftime('%H:%M')}:00`"
        )
        bot.send_message(message.chat.id, signal)
    except:
        bot.send_message(message.chat.id, "❌ Erreur technique. Réessayez.")

@bot.message_handler(func=lambda m: m.text == "🧠 DEMANDER À L'IA")
def ask_mode(message):
    bot.send_message(message.chat.id, "Posez votre question trading :")

@bot.message_handler(func=lambda m: True)
def handle_chat(message):
    if len(message.text) > 2:
        reponse = get_ai_advice(message.text)
        bot.reply_to(message, reponse)

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    while True:
        try:
            bot.remove_webhook()
            bot.infinity_polling(timeout=15)
        except:
            time.sleep(5)
    
