            import telebot
from telebot import types
import threading
import os
import google.generativeai as genai
from flask import Flask

# --- CONFIGURATION ---
TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
API_KEY = os.environ.get("GEMINI_API_KEY")

app = Flask(__name__)
@app.route('/')
def health(): return "PRIME V13 READY", 200

# --- MOTEUR IA ---
def get_ai_response(prompt):
    if not API_KEY:
        return "❌ Erreur : Clé API absente de Render."
    
    try:
        genai.configure(api_key=API_KEY)
        # On essaie le nom complet du modèle pour forcer la détection
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        # Si ça échoue encore, on essaie une version alternative
        try:
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            return model.generate_content(prompt).text
        except:
            return f"🚨 Erreur persistante : {str(e)}"

# --- COMMANDES ---
@bot.message_handler(commands=['start'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎯 SIGNAL TRADING", "🧠 QUESTION IA")
    bot.send_message(message.chat.id, "⚡ **PRIME V13 (FORCE UPDATE)**", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎯 SIGNAL TRADING")
def signal(message):
    bot.send_chat_action(message.chat.id, 'typing')
    res = get_ai_response("Donne un signal court CALL ou PUT pour EUR/USD.")
    bot.send_message(message.chat.id, f"🚀 **SIGNAL :**\n\n{res}")

@bot.message_handler(func=lambda m: True)
def chat(message):
    bot.reply_to(message, get_ai_response(message.text))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
        
