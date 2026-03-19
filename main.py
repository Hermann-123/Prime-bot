import telebot
from telebot import types
import threading
import os
import google.generativeai as genai
from flask import Flask

# --- CONFIG ---
TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)

# Récupération de la clé depuis Render
API_KEY = os.environ.get("GEMINI_API_KEY")

app = Flask(__name__)
@app.route('/')
def health(): return "PRIME V12 LIVE", 200

# --- MOTEUR IA CORRIGÉ ---
def get_ai_response(prompt):
    if not API_KEY:
        return "❌ Erreur : Clé API absente de Render."
    
    try:
        genai.configure(api_key=API_KEY)
        # CHANGEMENT ICI : Utilisation du nom de modèle ultra-stable
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # On limite la réponse pour éviter que Render ne coupe la connexion
        response = model.generate_content(f"Réponds très brièvement en français : {prompt}")
        return response.text
    except Exception as e:
        return f"🚨 Erreur technique : {str(e)}"

# --- COMMANDES ---
@bot.message_handler(commands=['start', 'menu'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎯 SIGNAL TRADING", "🧠 QUESTION IA")
    bot.send_message(message.chat.id, "✅ **PRIME V12 STABLE**\nLe problème de connexion est résolu.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎯 SIGNAL TRADING")
def signal(message):
    bot.send_chat_action(message.chat.id, 'typing')
    res = get_ai_response("Donne un signal CALL ou PUT pour EUR/USD maintenant.")
    bot.send_message(message.chat.id, f"🚀 **SIGNAL IA :**\n\n{res}")

@bot.message_handler(func=lambda m: m.text == "🧠 QUESTION IA")
def ask(message):
    bot.send_message(message.chat.id, "💬 Posez votre question :")

@bot.message_handler(func=lambda m: True)
def chat(message):
    if len(message.text) > 1:
        answer = get_ai_response(message.text)
        bot.reply_to(message, answer)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
            
