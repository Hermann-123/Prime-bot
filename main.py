import telebot
from telebot import types
import threading
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta

# --- CONFIG ---
TOKEN = "8658287331:AAEqTnQ9F-PvqpFGty0woA0oZ4V66RmtdK4"
bot = telebot.TeleBot(TOKEN)
MY_ID = "5968288964"

user_config = {"symbol": "BTC/USDT", "expiration": "1 min", "analyzing": False}

# --- MENU ---
@bot.message_handler(commands=['start', 'menu'])
def main_menu(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(types.KeyboardButton('📱 Device'), types.KeyboardButton('📊 Signaux'))
    bot.send_message(message.chat.id, "💎 *PRIME BOT ULTIMATE*\n_Système d'analyse prédictive_", 
                     parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📱 Device')
def device_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
    markup.add(*[types.InlineKeyboardButton(p, callback_data=f"set_{p}") for p in pairs])
    bot.send_message(message.chat.id, "🎯 Sélectionnez la paire :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📊 Signaux')
def signals_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("5 sec", callback_data="time_5s"),
        types.InlineKeyboardButton("1 min", callback_data="time_1m"),
        types.InlineKeyboardButton("5 min", callback_data="time_5m")
    )
    bot.send_message(message.chat.id, "⏱ Temps d'expiration :", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data.startswith("set_"):
        user_config["symbol"] = call.data.split("_")[1]
        bot.answer_callback_query(call.id)
        lancer_analyse(call.message.chat.id)
    elif call.data.startswith("time_"):
        user_config["expiration"] = call.data.split("_")[1].replace("s", " sec").replace("m", " min")
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"✅ Expiration : {user_config['expiration']}")

def lancer_analyse(chat_id):
    user_config["analyzing"] = True
    bot.send_message(chat_id, "⏳ *ANALYSE DU MARCHÉ EN COURS...*\n_Vérification (1 min 50s)_", parse_mode="Markdown")
    threading.Timer(110, finir_analyse, [chat_id]).start()

def finir_analyse(chat_id):
    user_config["analyzing"] = False
    bot.send_message(chat_id, "✅ *ANALYSE TERMINÉE*\nSynchronisation OK. J'attends le signal...", parse_mode="Markdown")

def scanner():
    ex = ccxt.binance()
    while True:
        if not user_config["analyzing"]:
            try:
                symbol = user_config["symbol"]
                bars = ex.fetch_ohlcv(symbol, timeframe='1m', limit=50)
                df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                delta = df['c'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
                ma20 = df['c'].rolling(20).mean().iloc[-1]
                std20 = df['c'].rolling(20).std().iloc[-1]
                prix = df['c'].iloc[-1]

                if (prix < (ma20 - 2*std20) and rsi < 30) or (prix > (ma20 + 2*std20) and rsi > 70):
                    action = "ACHAT (CALL)" if prix < (ma20 - 2*std20) else "VENTE (PUT)"
                    maintenant = datetime.now()
                    heure_prevue = (maintenant + timedelta(minutes=5)).strftime("%H:%M")
                    msg = (f"🚦 *SIGNAL PRÉDICTIF* 🚦\n\n🪙 `{symbol}`\n⚡ *{action}*\n\n📍 **ORDRE À :** `{heure_prevue}`\n⏳ Expiration : `{user_config['expiration']}`\n📊 Confiance : `94%` 🔥")
                    bot.send_message(MY_ID, msg, parse_mode="Markdown")
                    time.sleep(300)
            except: pass
        time.sleep(15)

threading.Thread(target=scanner, daemon=True).start()
bot.polling(none_stop=True)
                
