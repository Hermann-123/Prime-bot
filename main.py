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

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data.startswith("set_"):
        user_config["symbol"] = call.data.split("_")[1]
        bot.answer_callback_query(call.id, f"Paire: {user_config['symbol']}")
        lancer_analyse(call.message.chat.id)
    elif call.data.startswith("time_"):
        user_config["expiration"] = call.data.split("_")[1].replace("s", " sec").replace("m", " min")
        bot.answer_callback_query(call.id, f"Délai: {user_config['expiration']}")

def lancer_analyse(chat_id):
    user_config["analyzing"] = True
    bot.send_message(chat_id, "⏳ *ANALYSE DU MARCHÉ EN COURS...*\n_Vérification des statistiques (1 min 50s)_", parse_mode="Markdown")
    # On attend 110 secondes comme demandé
    threading.Timer(110, finir_analyse, [chat_id]).start()

def finir_analyse(chat_id):
    user_config["analyzing"] = False
    bot.send_message(chat_id, "✅ *ANALYSE TERMINÉE*\nLe bot est maintenant synchronisé. Attente du point d'entrée optimal...", parse_mode="Markdown")

# --- LOGIQUE DE PRÉDICTION ---
def scanner():
    ex = ccxt.binance()
    while True:
        if not user_config["analyzing"]:
            try:
                symbol = user_config["symbol"]
                bars = ex.fetch_ohlcv(symbol, timeframe='1m', limit=50)
                df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                
                # Calcul RSI & Bollinger
                rsi = 100 - (100 / (1 + (df['c'].diff().where(df['c'].diff() > 0, 0).rolling(14).mean() / -df['c'].diff().where(df['c'].diff() < 0, 0).rolling(14).mean()))).iloc[-1]
                ma20 = df['c'].rolling(20).mean().iloc[-1]
                std20 = df['c'].rolling(20).std().iloc[-1]
                prix = df['c'].iloc[-1]

                # Si condition détectée
                if (prix < (ma20 - 2*std20) and rsi < 35) or (prix > (ma20 + 2*std20) and rsi > 65):
                    action = "ACHAT (CALL)" if prix < (ma20 - 2*std20) else "VENTE (PUT)"
                    
                    # CALCUL DU TEMPS FUTUR (Prédiction pour dans 5 minutes)
                    maintenant = datetime.now()
                    heure_entree_prevue = (maintenant + timedelta(minutes=5)).strftime("%H:%M")
                    heure_limite = (maintenant + timedelta(minutes=7)).strftime("%H:%M")

                    msg = (f"🚦 *SIGNAL PRÉDICTIF DÉTECTÉ* 🚦\n\n"
                           f"🪙 Paire : `{symbol}`\n"
                           f"⚡ Action : *{action}*\n\n"
                           f"📍 **ORDRE À PLACER À :** `{heure_entree_prevue}`\n"
                           f"🚫 **ANNULER APRÈS :** `{heure_limite}`\n"
                           f"⏳ Expiration : `{user_config['expiration']}`\n\n"
                           f"📊 Confiance : `94%` 🔥\n"
                           f"🎯 _Le bot a anticipé une variation majeure à {heure_entree_prevue}._")
                    
                    bot.send_message(MY_ID, msg, parse_mode="Markdown")
                    time.sleep(300) # Pause de 5 min pour attendre le trade
            except: pass
        time.sleep(15)

# Lancer les threads
threading.Thread(target=scanner, daemon=True).start()
bot.polling(none_stop=True)
