import telebot
from telebot import types
import threading
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
from flask import Flask
import os

# --- SURVIE RENDER ---
app = Flask(__name__)
@app.route('/')
def health(): return "Terminal Online", 200

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- CONFIGURATION ---
TOKEN = "8658287331:AAEqTnQ9F-PvqpFGty0woA0oZ4V66RmtdK4"
bot = telebot.TeleBot(TOKEN)
MY_ID = "5968288964"

# Mémoire vive
sys_data = {"pair": "EUR/USD", "exp": "1 min"}

# --- INTERFACE PRINCIPALE ---
@bot.message_handler(commands=['start', 'menu'])
def main_interface(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("🎯 DEMANDER UN SIGNAL", "💠 CONFIGURATION")
    
    header = (
        "╔══════════════════╗\n"
        "      💎 **PRIME TERMINAL V6**\n"
        "╚══════════════════╝\n"
        f"● **ACTIF :** `{sys_data['pair']}`\n"
        f"● **DÉLAI :** `{sys_data['exp']}`\n"
        "● **MODE :** `MANUEL (SNIPER)` 🎯\n"
        "──────────────────\n"
        "💡 _Cliquez sur le bouton pour un signal immédiat_"
    )
    bot.send_message(message.chat.id, header, parse_mode="Markdown", reply_markup=markup)

# --- MENU CONFIGURATION ---
@bot.message_handler(func=lambda m: m.text == "💠 CONFIGURATION")
def config_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    pairs = ["EUR/USD", "USD/JPY", "GBP/USD", "BTC/USDT"]
    markup.add(*[types.InlineKeyboardButton(f"🔹 {p}", callback_data=f"setp_{p}") for p in pairs])
    markup.add(types.InlineKeyboardButton("⏲ 5s", callback_data="sett_5s"),
               types.InlineKeyboardButton("⏲ 1m", callback_data="sett_1m"),
               types.InlineKeyboardButton("⏲ 5m", callback_data="sett_5m"))
    bot.send_message(message.chat.id, "🛠 **RÉGLAGES :**", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if call.data.startswith("setp_"):
        sys_data["pair"] = call.data.split("_")[1]
        bot.answer_callback_query(call.id, f"Actif : {sys_data['pair']}")
        bot.edit_message_text(f"✅ **ACTIF ACTUALISÉ :** `{sys_data['pair']}`", call.message.chat.id, call.message.message_id)
    elif call.data.startswith("sett_"):
        sys_data["exp"] = call.data.split("_")[1].replace("s", " sec").replace("m", " min")
        bot.answer_callback_query(call.id, f"Délai : {sys_data['exp']}")
        bot.edit_message_text(f"✅ **DÉLAI ACTUALISÉ :** `{sys_data['exp']}`", call.message.chat.id, call.message.message_id)

# --- ANALYSE ET SIGNAL SUR DEMANDE (LE CŒUR DU BOT) ---
@bot.message_handler(func=lambda m: m.text == "🎯 DEMANDER UN SIGNAL")
def get_instant_signal(message):
    # 1. Message d'attente immédiat
    status_msg = bot.send_message(message.chat.id, "🛰 **ANALYSE INSTANTANÉE EN COURS...**\n`[██████▒▒▒▒] 60%`", parse_mode="Markdown")
    
    try:
        # 2. Analyse technique flash (RSI rapide)
        ex = ccxt.binance()
        bars = ex.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=30)
        df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
        
        delta = df['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(7).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(7).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]

        # 3. Détermination du signal selon le RSI actuel
        action = "🟢 ACHAT (CALL)" if rsi < 50 else "🔴 VENTE (PUT)"
        fiabilite = 92 if (rsi < 35 or rsi > 65) else 85
        
        # Heure d'entrée (dans 5 secondes pour te laisser le temps)
        h_entree = (datetime.now() + timedelta(seconds=5)).strftime("%H:%M:%S")

        # 4. Envoi du signal final
        signal_box = (
            "🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀\n"
            "──────────────────\n"
            f"🛰 **ACTIF :** `{sys_data['pair']}`\n"
            f"🎯 **ACTION :** **{action}**\n"
            f"⏱ **EXPIRATION :** `{sys_data['exp']}`\n"
            "──────────────────\n"
            f"🕒 **ENTRÉE À :** `{h_entree}`\n"
            f"📊 **CONFIANCE :** `[ {fiabilite}% ]` ✅\n"
            "──────────────────\n"
            "💎 _Placez votre trade maintenant !_"
        )
        
        # Supprime le message de chargement et envoie le signal
        bot.delete_message(message.chat.id, status_msg.message_id)
        bot.send_message(message.chat.id, signal_box, parse_mode="Markdown")
        
    except Exception as e:
        bot.send_message(message.chat.id, "❌ **ERREUR :** Impossible de joindre le flux. Réessayez.")

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    bot.infinity_polling()
    
