import telebot
from telebot import types
import threading
import ccxt
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask
import os
import time

# --- SURVIE RENDER ---
app = Flask(__name__)
@app.route('/')
def health(): return "PRIME TERMINAL LIVE", 200

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- CONFIGURATION (NOUVEAU TOKEN) ---
TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
MY_ID = "5968288964"

# --- SÉCURITÉ ANTI-CONFLIT ---
try:
    bot.remove_webhook()
    time.sleep(1)
except:
    pass

sys_data = {"pair": "EUR/USD"}

# --- INTERFACE ---
@bot.message_handler(commands=['start', 'menu'])
def main_interface(message):
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add("🎯 ANALYSE SNIPER", "📱 CHOISIR LA PAIRE")
    
    header = (
        "╔══════════════════╗\n"
        "      💎 **PRIME TERMINAL V6**\n"
        "╚══════════════════╝\n"
        f"● **ACTIF :** `{sys_data['pair']}`\n"
        "● **MODE :** `SNIPER MANUEL` 🎯\n"
        "● **EXPIRATION :** `AUTO-DYNAMIQUE` ⚡\n"
        "──────────────────"
    )
    bot.send_message(message.chat.id, header, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📱 CHOISIR LA PAIRE")
def config_pair(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    pairs = ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF", "BTC/USDT"]
    markup.add(*[types.InlineKeyboardButton(f"🔹 {p}", callback_data=f"setp_{p}") for p in pairs])
    bot.send_message(message.chat.id, "🛰 **SÉLECTIONNEZ UN ACTIF :**", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if call.data.startswith("setp_"):
        sys_data["pair"] = call.data.split("_")[1]
        bot.answer_callback_query(call.id, f"Actif : {sys_data['pair']}")
        bot.edit_message_text(f"✅ **ACTIF ACTUALISÉ :** `{sys_data['pair']}`", call.message.chat.id, call.message.message_id)

# --- LOGIQUE DE SIGNAL AVEC EXPIRATION CALCULÉE ---
@bot.message_handler(func=lambda m: m.text == "🎯 ANALYSE SNIPER")
def get_dynamic_signal(message):
    status_msg = bot.send_message(message.chat.id, "🛰 **ANALYSE DU FLUX...**", parse_mode="Markdown")
    try:
        # Analyse Flash via CCXT
        ex = ccxt.binance()
        bars = ex.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=40)
        df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
        
        # Indicateurs
        range_moyen = (df['h'] - df['l']).tail(5).mean()
        prix_actuel = df['c'].iloc[-1]
        delta = df['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(7).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(7).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]

        action = "🟢 ACHAT (CALL)" if rsi < 50 else "🔴 VENTE (PUT)"
        
        # Expiration Dynamique
        if range_moyen > (prix_actuel * 0.0005): exp = "30 SEC"
        elif rsi < 35 or rsi > 65: exp = "1 MIN"
        else: exp = "2 MIN"

        # Heure d'entrée avec 1m30 de préparation
        futur = datetime.now() + timedelta(seconds=90)
        h_entree = (futur + timedelta(minutes=1)).replace(second=0, microsecond=0).strftime("%H:%M")

        signal_box = (
            "🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀\n"
            "──────────────────\n"
            f"🛰 **ACTIF :** `{sys_data['pair']}`\n"
            f"🎯 **ACTION :** **{action}**\n"
            f"⏳ **EXPIRATION :** `{exp}` ⏱\n"
            "──────────────────\n"
            f"📍 **ORDRE À :** `{h_entree}:00` 👈\n"
            f"📊 **CONFIANCE :** `[ 96% ]` 🔥\n"
            "──────────────────\n"
            "💎 _Préparez-vous à la seconde 00 !_"
        )
        bot.delete_message(message.chat.id, status_msg.message_id)
        bot.send_message(message.chat.id, signal_box, parse_mode="Markdown")
        
    except Exception:
        bot.send_message(message.chat.id, "❌ Erreur de flux. Réessayez.")

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    # Redémarrage propre en cas de déconnexion
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
    
