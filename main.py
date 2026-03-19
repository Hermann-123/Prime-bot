import telebot
from telebot import types
import threading
import os
import google.generativeai as genai
from flask import Flask

# --- 1. CONFIGURATION ---
TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
API_KEY = os.environ.get("GEMINI_API_KEY")

# --- 2. SERVEUR RENDER (POUR GARDER LE BOT EN LIGNE) ---
app = Flask(__name__)
@app.route('/')
def health(): return "BOT ACTIF ET EN LIGNE", 200

# --- 3. MOTEUR IA CENTRALISÉ ---
def get_ai_response(prompt):
    if not API_KEY:
        return "❌ ERREUR : La clé API manque sur Render."
    
    try:
        genai.configure(api_key=API_KEY)
        # Utilisation du modèle le plus rapide et stable
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        # Si Google bloque, le bot ne crashe pas, il explique l'erreur
        return f"🚨 Erreur serveur IA : {str(e)}"

# --- 4. COMMANDES TELEGRAM ---
@bot.message_handler(commands=['start', 'menu'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎯 SIGNAL TRADING", "🧠 QUESTION IA")
    bot.send_message(message.chat.id, "✅ **SYSTÈME PRO INITIALISÉ**\nChoisissez une option ci-dessous :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎯 SIGNAL TRADING")
def signal(message):
    # Fait apparaître "Le bot écrit..." sur Telegram
    bot.send_chat_action(message.chat.id, 'typing')
    
    # Ordre précis donné à l'IA
    prompt = "Agis comme un trader expert. Donne un signal court CALL ou PUT pour EUR/USD maintenant, avec 2 lignes d'explication maximum."
    res = get_ai_response(prompt)
    
    bot.send_message(message.chat.id, f"🚀 **ANALYSE DU MARCHÉ :**\n\n{res}")

@bot.message_handler(func=lambda m: True)
def chat(message):
    # Si l'utilisateur tape autre chose, le bot lui répond comme ChatGPT
    bot.send_chat_action(message.chat.id, 'typing')
    bot.reply_to(message, get_ai_response(message.text))

# --- 5. DÉMARRAGE SÉCURISÉ ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # Lancement du serveur Web dans un thread séparé
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    # Lancement du bot Telegram
    print("Démarrage du bot...")
    bot.infinity_polling()
