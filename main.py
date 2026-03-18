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

# --- 1. CONFIGURATION DES CLÉS (VÉRIFIÉES) ---
GEMINI_API_KEY = "AIzaSyDqIELRqLMeoV9bYBkXBuvpSacpuzzOAiA"
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
sys_data = {"pair": "EUR/USD"}

# --- 2. SURVIE RENDER (FLASK) ---
app = Flask(__name__)
@app.route('/')
def health(): return "IA TRADING ONLINE", 200

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 3. FONCTION DE RÉFLEXION IA (CERVEAU GEMINI) ---
def get_ai_advice(prompt):
    try:
        response = model.generate_content(f"Tu es un expert en trading d'options binaires. Réponds de façon courte et technique : {prompt}")
        return response.text
    except Exception as e:
        return "🧠 L'IA analyse les graphiques... (Erreur de connexion temporaire)"

# --- 4. INTERFACE DU BOT ---
@bot.message_handler(commands=['start', 'menu'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("🎯 ANALYSE IA SNIPER", "📱 CHOISIR LA PAIRE", "🧠 DEMANDER À L'IA")
    text = (
        "╔══════════════════╗\n"
        "      💎 **PRIME TERMINAL V8**\n"
        "╚══════════════════╝\n"
        "● **IA :** `GOOGLE GEMINI PRO` ✅\n"
        "● **MODE :** `SNIPER INTELLIGENT` 🎯\n"
        "──────────────────\n"
        "Posez-moi vos questions ou lancez un signal !"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📱 CHOISIR LA PAIRE")
def config_pair(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    pairs = ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF", "BTC/USDT"]
    markup.add(*[types.InlineKeyboardButton(f"🔹 {p}", callback_data=f"p_{p}") for p in pairs])
    bot.send_message(message.chat.id, "🛰 **SÉLECTIONNEZ UN ACTIF :**", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("p_"))
def handle_query(call):
    sys_data["pair"] = call.data.split("_")[1]
    bot.answer_callback_query(call.id, f"Actif : {sys_data['pair']}")
    bot.edit_message_text(f"✅ **ACTIF ACTUALISÉ :** `{sys_data['pair']}`", call.message.chat.id, call.message.message_id)

# --- 5. LOGIQUE SIGNAL IA SNIPER ---
@bot.message_handler(func=lambda m: m.text == "🎯 ANALYSE IA SNIPER")
def get_ia_signal(message):
    status = bot.send_message(message.chat.id, "🔍 **L'IA ANALYSE LE FLUX FINANCIER...**")
    try:
        # Récupération des données réelles
        ex = ccxt.binance()
        bars = ex.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=15)
        prices = [b[4] for b in bars]
        
        # L'IA analyse les prix
        prompt = f"Analyse ces prix : {prices}. Dis moi si je dois faire un CALL ou PUT pour l'actif {sys_data['pair']}. Sois précis et rapide."
        ai_decision = get_ai_advice(prompt)
        
        # Calcul du temps d'entrée (prochaine bougie à la seconde 00)
        h = (datetime.now() + timedelta(seconds=90)).replace(second=0, microsecond=0) + timedelta(minutes=1)
        
        signal = (
            f"🚀 **PRÉDICTION IA GÉNÉRÉE**\n"
            "──────────────────\n"
            f"🛰 **ACTIF :** `{sys_data['pair']}`\n"
            f"📊 **ANALYSE :** \n{ai_decision}\n"
            "──────────────────\n"
            f"📍 **ENTRÉE À :** `{h.strftime('%H:%M')}:00` 👈\n"
            "──────────────────\n"
            "💎 _Suivez l'IA pour maximiser la précision._"
        )
        bot.delete_message(message.chat.id, status.message_id)
        bot.send_message(message.chat.id, signal, parse_mode="Markdown")
    except:
        bot.send_message(message.chat.id, "❌ Erreur de flux. Réessayez.")

# --- 6. CHAT LIBRE AVEC L'IA ---
@bot.message_handler(func=lambda m: m.text == "🧠 DEMANDER À L'IA")
def ask_mode(message):
    bot.send_message(message.chat.id, "💬 **MODE DISCUSSION IA ACTIVÉ**\nPosez votre question sur le trading :")

@bot.message_handler(func=lambda m: True)
def handle_chat(message):
    if len(message.text) > 2:
        reponse = get_ai_advice(message.text)
        bot.reply_to(message, reponse)

# --- 7. LANCEMENT ET SÉCURITÉ ---
if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    
    print("--- DÉMARRAGE DU BOT IA PRIME V8 ---")
    while True:
        try:
            bot.remove_webhook()
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            print(f"Relance dans 5s... {e}")
            time.sleep(5)
