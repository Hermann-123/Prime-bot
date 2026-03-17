import telebot
from telebot import types
import threading
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
import http.server
import socketserver
import os

# --- ASTUCE POUR RENDER (FAUX SERVEUR) ---
def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- CONFIG BOT ---
TOKEN = "8658287331:AAEqTnQ9F-PvqpFGty0woA0oZ4V66RmtdK4"
bot = telebot.TeleBot(TOKEN)
MY_ID = "5968288964"

user_data = {"symbol": None, "expiration": None, "analyzing": False}

# --- MENU ---
@bot.message_handler(commands=['start', 'menu'])
def main_menu(message):
    user_data["symbol"], user_data["expiration"] = None, None
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(types.KeyboardButton('📱 Choisir Paire'), types.KeyboardButton('📊 Temps Expiration'))
    bot.send_message(message.chat.id, "💎 *PRIME BOT ULTIMATE V4*\n_Statut : En ligne ✅_", 
                     parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📱 Choisir Paire')
def device_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    pairs = ["EUR/USD", "USD/JPY", "USD/CHF", "USD/PKR"]
    markup.add(*[types.InlineKeyboardButton(p, callback_data=f"set_{p}") for p in pairs])
    bot.send_message(message.chat.id, "🎯 Choisissez la paire :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📊 Temps Expiration')
def expiration_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(types.InlineKeyboardButton("5 sec", callback_data="time_5s"),
               types.InlineKeyboardButton("1 min", callback_data="time_1m"),
               types.InlineKeyboardButton("5 min", callback_data="time_5m"))
    bot.send_message(message.chat.id, "⏱ Choisissez le délai :", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data.startswith("set_"):
        user_data["symbol"] = call.data.split("_")[1]
        bot.answer_callback_query(call.id, f"Paire : {user_data['symbol']}")
        if user_data["symbol"] and user_data["expiration"]: lancer_analyse(call.message.chat.id)
    elif call.data.startswith("time_"):
        user_data["expiration"] = call.data.split("_")[1].replace("s", " sec").replace("m", " min")
        bot.answer_callback_query(call.id, f"Délai : {user_data['expiration']}")
        if user_data["symbol"] and user_data["expiration"]: lancer_analyse(call.message.chat.id)

def lancer_analyse(chat_id):
    user_data["analyzing"] = True
    bot.send_message(chat_id, f"🚀 *ANALYSE DE {user_data['symbol']} ({user_data['expiration']})*\n_En cours (1 min 50s)..._", parse_mode="Markdown")
    threading.Timer(110, finir_analyse, [chat_id]).start()

def finir_analyse(chat_id):
    user_data["analyzing"] = False
    bot.send_message(chat_id, "✅ *ANALYSE TERMINÉE*\nAttente d'un signal fort...", parse_mode="Markdown")

def scanner():
    ex = ccxt.binance()
    while True:
        if not user_data["analyzing"] and user_data["symbol"]:
            try:
                # Analyse de la volatilité
                bars = ex.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=50)
                df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
                rsi = 100 - (100 / (1 + (df['c'].diff().where(df['c'].diff() > 0, 0).rolling(14).mean() / -df['c'].diff().where(df['c'].diff() < 0, 0).rolling(14).mean()))).iloc[-1]
                if rsi < 28 or rsi > 72:
                    action = "ACHAT (CALL)" if rsi < 28 else "VENTE (PUT)"
                    h = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
                    bot.send_message(MY_ID, f"🚦 *SIGNAL {user_data['symbol']}*\n⚡ *{action}*\n📍 ORDRE À : `{h}`\n⏳ Exp : `{user_data['expiration']}`", parse_mode="Markdown")
                    time.sleep(300)
            except: pass
        time.sleep(20)

threading.Thread(target=scanner, daemon=True).start()
bot.polling(none_stop=True)
    
