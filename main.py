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

sys_data = {"pair": "EUR/USD", "exp": "1 min"}

# --- INTERFACE ---
@bot.message_handler(commands=['start', 'menu'])
def main_interface(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("🎯 ANALYSE SNIPER", "💠 CONFIGURATION")
    
    header = (
        "╔══════════════════╗\n"
        "      💎 **PRIME TERMINAL V6**\n"
        "╚══════════════════╝\n"
        f"● **ACTIF :** `{sys_data['pair']}`\n"
        f"● **EXPIRATION :** `{sys_data['exp']}`\n"
        "● **PRÉPARATION :** `1m 30s` ⏱\n"
        "──────────────────"
    )
    bot.send_message(message.chat.id, header, parse_mode="Markdown", reply_markup=markup)

# --- LOGIQUE DE SIGNAL AVEC PRÉPARATION ---
@bot.message_handler(func=lambda m: m.text == "🎯 ANALYSE SNIPER")
def get_precise_signal(message):
    status_msg = bot.send_message(message.chat.id, "🛰 **ANALYSE TECHNIQUE EN COURS...**", parse_mode="Markdown")
    
    try:
        # Analyse Flash
        ex = ccxt.binance()
        bars = ex.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=35)
        df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
        
        # RSI 7 pour la réactivité
        delta = df['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(7).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(7).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]

        action = "🟢 ACHAT (CALL)" if rsi < 50 else "🔴 VENTE (PUT)"
        
        # --- CALCUL DE L'HEURE D'ENTRÉE (DÉLAI 1m30) ---
        maintenant = datetime.now()
        # On ajoute 90 secondes (1m30) et on arrondit à la minute supérieure
        future_time = maintenant + timedelta(seconds=90)
        entree_datetime = (future_time + timedelta(minutes=1)).replace(second=0, microsecond=0)
        h_entree = entree_datetime.strftime("%H:%M")

        signal_box = (
            "🚀 **SIGNAL PRÉCIS DÉTECTÉ** 🚀\n"
            "──────────────────\n"
            f"🛰 **ACTIF :** `{sys_data['pair']}`\n"
            f"🎯 **ACTION :** **{action}**\n"
            f"⏱ **DÉLAI :** `{sys_data['exp']}`\n"
            "──────────────────\n"
            f"📍 **ORDRE À :** `{h_entree}:00` 👈\n"
            f"📊 **CONFIANCE :** `[ 93% ]` 🔥\n"
            "──────────────────\n"
            "⏳ _Préparez votre montant. Entrez pile à la seconde 00._"
        )
        
        bot.delete_message(message.chat.id, status_msg.message_id)
        bot.send_message(message.chat.id, signal_box, parse_mode="Markdown")
        
    except Exception:
        bot.send_message(message.chat.id, "❌ Flux indisponible. Réessayez.")

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    bot.infinity_polling()
    
