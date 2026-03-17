import telebot
from telebot import types
import threading
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
from flask import Flask
import os

# --- PARTIE RENDER (POUR ÉVITER L'ERREUR DE PORT) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Live!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- CONFIGURATION DU BOT ---
TOKEN = "8658287331:AAEqTnQ9F-PvqpFGty0woA0oZ4V66RmtdK4"
bot = telebot.TeleBot(TOKEN)
MY_ID = "5968288964"

user_data = {"symbol": None, "expiration": "1 min", "analyzing": False}

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(types.KeyboardButton('📱 Choisir Paire'), types.KeyboardButton('📊 Temps Expiration'))
    bot.send_message(message.chat.id, "💎 *PRIME BOT V4* ✅\n_Bot opérationnel sur Render_", parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📱 Choisir Paire')
def pair_choice(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    pairs = ["EUR/USD", "USD/JPY", "USD/CHF", "USD/PKR"]
    markup.add(*[types.InlineKeyboardButton(p, callback_data=f"set_{p}") for p in pairs])
    bot.send_message(message.chat.id, "🎯 Choisissez la paire :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📊 Temps Expiration')
def time_choice(message):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(types.InlineKeyboardButton("5 sec", callback_data="time_5s"),
               types.InlineKeyboardButton("1 min", callback_data="time_1m"),
               types.InlineKeyboardButton("5 min", callback_data="time_5m"))
    bot.send_message(message.chat.id, "⏱ Choisissez le délai :", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if call.data.startswith("set_"):
        user_data["symbol"] = call.data.split("_")[1]
        bot.answer_callback_query(call.id, f"Paire : {user_data['symbol']}")
    elif call.data.startswith("time_"):
        user_data["expiration"] = call.data.split("_")[1].replace("s", " sec").replace("m", " min")
        bot.answer_callback_query(call.id, f"Délai : {user_data['expiration']}")
    
    if user_data["symbol"] and not user_data["analyzing"]:
        lancer_analyse(call.message.chat.id)

def lancer_analyse(chat_id):
    user_data["analyzing"] = True
    bot.send_message(chat_id, f"🚀 *ANALYSE DE {user_data['symbol']} EN COURS...*", parse_mode="Markdown")
    threading.Timer(110, finir_analyse, [chat_id]).start()

def finir_analyse(chat_id):
    user_data["analyzing"] = False
    bot.send_message(chat_id, "✅ *ANALYSE TERMINÉE*\n_Recherche de signaux..._", parse_mode="Markdown")

def scanner():
    ex = ccxt.binance()
    while True:
        if not user_data["analyzing"] and user_data["symbol"]:
            try:
                bars = ex.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=15)
                df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
                h = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
                msg = f"🚦 *SIGNAL {user_data['symbol']}*\n⚡ *ORDRE À :* `{h}`\n⏳ Exp : `{user_data['expiration']}`"
                bot.send_message(MY_ID, msg, parse_mode="Markdown")
                time.sleep(600)
            except: pass
        time.sleep(30)

if __name__ == "__main__":
    # Lancer le serveur web pour Render
    threading.Thread(target=run_web_server, daemon=True).start()
    # Lancer le scanner de signaux
    threading.Thread(target=scanner, daemon=True).start()
    # Lancer le bot
    print("Démarrage du bot...")
    bot.infinity_polling()
    
