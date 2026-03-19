import telebot
from telebot import types
import threading
import ccxt
import os
import time
import google.generativeai as genai
from flask import Flask

# --- CONFIG ---
TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)

# On récupère la clé depuis Render
API_KEY = os.environ.get("GEMINI_API_KEY")

app = Flask(__name__)
@app.route('/')
def health(): return "DEBUG MODE ACTIVE", 200

# --- LE DÉTECTEUR D'ERREUR ---
def get_ai_response(prompt):
    if not API_KEY:
        return "❌ ERREUR : La clé API n'est pas détectée par Render."
    
    try:
        genai.configure(api_key=API_KEY)
        # On utilise 'gemini-pro' qui est le plus compatible partout
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        # ICI : On affiche l'erreur réelle pour savoir quoi réparer
        return f"🚨 ERREUR IA RÉELLE : {str(e)}"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎯 TESTER LE SIGNAL", "🧠 POSER UNE QUESTION")
    bot.send_message(message.chat.id, "🛠 **MODE DIAGNOSTIC V11**", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎯 TESTER LE SIGNAL")
def test_signal(message):
    res = get_ai_response("Donne un signal CALL ou PUT pour EUR/USD avec une courte raison.")
    bot.send_message(message.chat.id, f"📊 **RÉSULTAT DU TEST :**\n\n{res}")

@bot.message_handler(func=lambda m: m.text == "🧠 POSER UNE QUESTION")
def ask(message):
    bot.send_message(message.chat.id, "Pose ta question :")

@bot.message_handler(func=lambda m: True)
def chat(message):
    answer = get_ai_response(message.text)
    bot.reply_to(message, answer)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
    
