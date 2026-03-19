import telebot
from telebot import types
import threading
import os
import requests
import time
from flask import Flask

# --- CONFIGURATION ---
TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
API_KEY = os.environ.get("GEMINI_API_KEY")

# --- SERVEUR WEB ---
app = Flask(__name__)
@app.route('/')
def health(): return "BOT V16 (API 2.5) ONLINE", 200

# --- LE BYPASS DIRECT ---
def get_ai_response(prompt):
    if not API_KEY:
        return "❌ ERREUR : La clé API manque."
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        result = response.json()
        
        if response.status_code == 200:
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            error_msg = result.get('error', {}).get('message', 'Erreur inconnue')
            return f"🚨 Refus de Google : {error_msg}"
            
    except Exception as e:
        return f"🚨 Erreur de réseau : {str(e)}"

# --- COMMANDES TELEGRAM ---
@bot.message_handler(commands=['start', 'menu'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎯 SIGNAL TRADING", "🧠 QUESTION IA")
    bot.send_message(message.chat.id, "💎 **PRIME V16 (NOUVELLE IA)**\nConnexion directe établie avec succès.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎯 SIGNAL TRADING")
def signal(message):
    bot.send_chat_action(message.chat.id, 'typing')
    prompt = "Agis comme un trader expert. Donne un signal court CALL ou PUT pour EUR/USD maintenant, avec 2 lignes d'explication maximum."
    res = get_ai_response(prompt)
    bot.send_message(message.chat.id, f"🚀 **ANALYSE DU MARCHÉ :**\n\n{res}")

@bot.message_handler(func=lambda m: True)
def chat(message):
    bot.send_chat_action(message.chat.id, 'typing')
    bot.reply_to(message, get_ai_response(message.text))

# --- DÉMARRAGE ANTI-CRASH ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    
    # On nettoie les anciennes connexions Telegram bloquées
    try:
        bot.remove_webhook()
        time.sleep(1)
    except:
        pass

    # Boucle infinie pour empêcher le script de s'éteindre
    while True:
        try:
            bot.infinity_polling(timeout=15, long_polling_timeout=5)
        except Exception as e:
            time.sleep(3)
    
