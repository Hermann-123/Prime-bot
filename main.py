import telebot
from telebot import types
import threading
import os
import requests
from flask import Flask

# --- CONFIGURATION ---
TOKEN = "8658287331:AAHh4vzRPxMQPDxnjvDdSpfk483cAsvLnbk"
bot = telebot.TeleBot(TOKEN)
API_KEY = os.environ.get("GEMINI_API_KEY")

# --- SERVEUR WEB ---
app = Flask(__name__)
@app.route('/')
def health(): return "BOT V15 DIRECT API ONLINE", 200

# --- LE BYPASS DIRECT (CONNEXION PURE) ---
def get_ai_response(prompt):
    if not API_KEY:
        return "❌ ERREUR : La clé API manque."
    
    # On attaque directement l'URL de Google (aucun bug de bibliothèque possible)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        result = response.json()
        
        # Si Google valide la requête
        if response.status_code == 200:
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            # Si la clé API a un souci, Google nous dira exactement pourquoi
            error_msg = result.get('error', {}).get('message', 'Erreur inconnue')
            return f"🚨 Refus de Google : {error_msg}"
            
    except Exception as e:
        return f"🚨 Erreur de réseau : {str(e)}"

# --- COMMANDES TELEGRAM ---
@bot.message_handler(commands=['start', 'menu'])
def welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎯 SIGNAL TRADING", "🧠 QUESTION IA")
    bot.send_message(message.chat.id, "💎 **PRIME V15 (BYPASS ACTIVÉ)**\nConnexion directe établie.", reply_markup=markup)

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
                                   
