import telebot
from telebot import types
import threading
import ccxt
import pandas as pd
import time

# --- CONFIG ---
TOKEN = "8658287331:AAEqTnQ9F-PvqpFGty0woA0oZ4V66RmtdK4"
bot = telebot.TeleBot(TOKEN)

# Variables par défaut
user_config = {
    "symbol": "BTC/USDT",
    "timeframe": "1m",
    "active": True
}

# --- MENU PRINCIPAL ---
@bot.message_handler(commands=['start', 'menu'])
def main_menu(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_device = types.KeyboardButton('📱 Device')
    btn_signals = types.KeyboardButton('📊 Signaux')
    markup.add(btn_device, btn_signals)
    bot.send_message(message.chat.id, "🛠 *Contrôle du Bot Prime*", 
                     parse_mode="Markdown", reply_markup=markup)

# --- GESTION DES BOUTONS ---
@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    if message.text == '📱 Device':
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("BTC/USDT", callback_data="set_BTC/USDT"),
            types.InlineKeyboardButton("ETH/USDT", callback_data="set_ETH/USDT"),
            types.InlineKeyboardButton("SOL/USDT", callback_data="set_SOL/USDT"),
            types.InlineKeyboardButton("BNB/USDT", callback_data="set_BNB/USDT")
        )
        bot.send_message(message.chat.id, "Sélectionnez la monnaie :", reply_markup=markup)

    elif message.text == '📊 Signaux':
        markup = types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            types.InlineKeyboardButton("5 sec", callback_data="time_5s"),
            types.InlineKeyboardButton("1 min", callback_data="time_1m"),
            types.InlineKeyboardButton("5 min", callback_data="time_5m")
        )
        bot.send_message(message.chat.id, "Choisir le temps d'expiration :", reply_markup=markup)

# --- CALLBACKS (Clics sur boutons bleus) ---
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data.startswith("set_"):
        new_symbol = call.data.split("_")[1]
        user_config["symbol"] = new_symbol
        bot.answer_callback_query(call.id, f"Suivi de {new_symbol}")
        bot.send_message(call.message.chat.id, f"✅ Configuré sur : *{new_symbol}*", parse_mode="Markdown")
    
    elif call.data.startswith("time_"):
        t = call.data.split("_")[1]
        bot.answer_callback_query(call.id, f"Expiration fixée à {t}")
        bot.send_message(call.message.chat.id, f"⏱ Temps d'expiration : *{t}*", parse_mode="Markdown")

# --- LOGIQUE DE SCAN (Tourne en arrière-plan) ---
def scanner():
    ex = ccxt.binance()
    while True:
        try:
            symbol = user_config["symbol"]
            bars = ex.fetch_ohlcv(symbol, timeframe='1m', limit=50)
            df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            
            # Calcul RSI simplifié
            delta = df['c'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
            
            # Bollinger
            ma20 = df['c'].rolling(20).mean().iloc[-1]
            std20 = df['c'].rolling(20).std().iloc[-1]
            lower = ma20 - (2 * std20)
            upper = ma20 + (2 * std20)
            prix = df['c'].iloc[-1]

            if prix < lower and rsi < 30:
                bot.send_message("5968288964", f"🟢 *SIGNAL ACHAT*\n💰 {symbol}\n📉 RSI: {int(rsi)}", parse_mode="Markdown")
            elif prix > upper and rsi > 70:
                bot.send_message("5968288964", f"🔴 *SIGNAL VENTE*\n💰 {symbol}\n📈 RSI: {int(rsi)}", parse_mode="Markdown")
        except: pass
        time.sleep(30)

# Lancer le scan dans un fil séparé
threading.Thread(target=scanner, daemon=True).start()

# Lancer le bot Telegram
print("Bot opérationnel avec menus...")
bot.polling(none_stop=True)
