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

# --- 1. CONFIGURATION (MISE À JOUR MODÈLE) ---
GEMINI_API_KEY = "AIzaSyDqIELRqLMeoV9bYBkXBuvpSacpuzzOAiA"
genai.configure(api_key=GEMINI_API_KEY)
# On utilise 'gemini-1.5-flash' qui est plus stable sur les serveurs gratuits
model = genai.GenerativeModel('gemini-1.5-flash')

TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
sys_data = {"pair": "EUR/USD"}

app = Flask(__name__)
@app.route('/')
def health(): return "IA ONLINE", 200

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. CERVEAU IA AVEC DÉBOGAGE ---
def get_ai_advice(prompt):
    try:
        response = model.generate_content(f"Tu es un expert trading. Réponds en 1 phrase courte : {prompt}")
        return response.text
    except Exception as e:
        # Cela nous dira EXACTEMENT quel est le problème
        return f"❌ Erreur IA : {str(e)[:50]}"

# --- 3. INTERFACE ---
@bot.message_handler(commands=['start', 'menu'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("🎯 ANALYSE IA SNIPER", "📱 CHOISIR LA PAIRE", "🧠 DEMANDER À L'IA")
    bot.send_message(message.chat.id, "💎 **PRIME TERMINAL V8.1**\n_Mode Flash IA activé._", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎯 ANALYSE IA SNIPER")
def get_ia_signal(message):
    try:
        ex = ccxt.binance()
        bars = ex.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=10)
        prices = [b[4] for b in bars]
        
        prompt = f"Prix actuels {prices}. Pour {sys_data['pair']}, donne : DIRECTION (CALL/PUT) et RAISON."
        ai_decision = get_ai_advice(prompt)
        
        h = (datetime.now() + timedelta(seconds=90)).replace(second=0, microsecond=0) + timedelta(minutes=1)
        
        signal = (
            f"🚀 **SIGNAL IA FLASH**\n"
            f"🛰 **ACTIF :** `{sys_data['pair']}`\n"
            f"📊 **IA :** {ai_decision}\n"
            f"📍 **ENTRÉE :** `{h.strftime('%H:%M')}:00`"
        )
        bot.send_message(message.chat.id, signal)
    except:
        bot.send_message(message.chat.id, "❌ Erreur Flux")

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
        
