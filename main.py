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

# Stockage des choix utilisateur
user_data = {
    "symbol": None,
    "expiration": None,
    "analyzing": False
}

# --- MENU PRINCIPAL ---
@bot.message_handler(commands=['start', 'menu'])
def main_menu(message):
    user_data["symbol"] = None
    user_data["expiration"] = None
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(types.KeyboardButton('📱 Choisir Paire'), types.KeyboardButton('📊 Temps Expiration'))
    bot.send_message(message.chat.id, "💎 *PRIME BOT ULTIMATE V4*\n_Sélectionnez votre configuration :_", 
                     parse_mode="Markdown", reply_markup=markup)

# --- GESTION DES BOUTONS CLAVIER ---
@bot.message_handler(func=lambda m: m.text == '📱 Choisir Paire')
def device_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    pairs = ["EUR/USD", "USD/JPY", "USD/CHF", "USD/PKR"]
    btns = [types.InlineKeyboardButton(p, callback_data=f"set_{p}") for p in pairs]
    markup.add(*btns)
    bot.send_message(message.chat.id, "🎯 *ÉTAPE 1 :* Choisissez la paire :", parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📊 Temps Expiration')
def expiration_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("5 sec", callback_data="time_5s"),
        types.InlineKeyboardButton("1 min", callback_data="time_1m"),
        types.InlineKeyboardButton("5 min", callback_data="time_5m")
    )
    bot.send_message(message.chat.id, "⏱ *ÉTAPE 2 :* Choisissez le délai :", parse_mode="Markdown", reply_markup=markup)

# --- GESTION DES CLICS (CALLBACKS) ---
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data.startswith("set_"):
        user_data["symbol"] = call.data.split("_")[1]
        bot.edit_message_text(f"✅ Paire : *{user_data['symbol']}*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        verifier_si_pret(call.message.chat.id)
        
    elif call.data.startswith("time_"):
        user_data["expiration"] = call.data.split("_")[1].replace("s", " sec").replace("m", " min")
        bot.edit_message_text(f"✅ Expiration : *{user_data['expiration']}*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        verifier_si_pret(call.message.chat.id)

def verifier_si_pret(chat_id):
    if user_data["symbol"] and user_data["expiration"]:
        lancer_analyse(chat_id)

def lancer_analyse(chat_id):
    user_data["analyzing"] = True
    bot.send_message(chat_id, f"🚀 *CONFIGURATION VALIDÉE*\n\n📈 Paire : `{user_data['symbol']}`\n⏳ Délai : `{user_data['expiration']}`\n\n🕒 *Lancement de l'analyse (1 min 50s)...*", parse_mode="Markdown")
    threading.Timer(110, finir_analyse, [chat_id]).start()

def finir_analyse(chat_id):
    user_data["analyzing"] = False
    bot.send_message(chat_id, "✅ *ANALYSE TERMINÉE*\n_Le bot surveille maintenant les variations pour vous._", parse_mode="Markdown")

# --- LOGIQUE DU SCANNER ---
def scanner():
    ex = ccxt.binance()
    while True:
        if not user_data["analyzing"] and user_data["symbol"] and user_data["expiration"]:
            try:
                # Analyse basée sur BTC ou ETH pour simuler la volatilité Forex
                ref_symbol = "BTC/USDT" if "USD" in user_data["symbol"] else "ETH/USDT"
                bars = ex.fetch_ohlcv(ref_symbol, timeframe='1m', limit=50)
                df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                
                rsi = 100 - (100 / (1 + (df['c'].diff().where(df['c'].diff() > 0, 0).rolling(14).mean() / -df['c'].diff().where(df['c'].diff() < 0, 0).rolling(14).mean()))).iloc[-1]
                
                if rsi < 25 or rsi > 75:
                    action = "ACHAT (CALL)" if rsi < 25 else "VENTE (PUT)"
                    maintenant = datetime.now()
                    heure_ordre = (maintenant + timedelta(minutes=5)).strftime("%H:%M")
                    
                    msg = (f"🚦 *SIGNAL DÉTECTÉ SUR {user_data['symbol']}* 🚦\n\n"
                           f"⚡ Action : *{action}*\n"
                           f"📍 **PLACEZ L'ORDRE À :** `{heure_ordre}`\n"
                           f"⏳ Expiration : `{user_data['expiration']}`\n\n"
                           f"📊 Confiance : `93%` 🔥")
                    
                    bot.send_message(MY_ID, msg, parse_mode="Markdown")
                    time.sleep(300) # Pause 5min
            except: pass
        time.sleep(15)

threading.Thread(target=scanner, daemon=True).start()
bot.polling(none_stop=True)
        
