import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import google.generativeai as genai
from dotenv import load_dotenv

# 1. Chargement des clés secrètes (à configurer sur Render)
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 2. Initialisation du bot et de l'IA
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)
# On utilise le modèle IA adapté au texte direct
model = genai.GenerativeModel('gemini-pro') 

# 3. Fonction : Récupérer le vrai prix en direct (Zéro surcharge mémoire)
def obtenir_prix_actuel(symbole="BTCUSDT"):
    """Récupère le prix sur Binance de façon ultra-rapide."""
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbole}"
    try:
        reponse = requests.get(url, timeout=5)
        donnees = reponse.json()
        return round(float(donnees['price']), 4)
    except Exception as e:
        print(f"Erreur API Binance: {e}")
        return None

# 4. Interface : Le menu de démarrage
@bot.message_handler(commands=['start'])
def envoyer_menu(message):
    markup = InlineKeyboardMarkup()
    bouton = InlineKeyboardButton("🎯 SIGNAL TRADING", callback_data="generer_signal")
    markup.add(bouton)
    
    texte_accueil = (
        "🏴‍☠️ **Bienvenue sur Signaux Prime** 🤯\n\n"
        "L'analyseur est connecté aux données en temps réel.\n"
        "Clique ci-dessous pour obtenir un signal immédiat."
    )
    bot.send_message(message.chat.id, texte_accueil, reply_markup=markup, parse_mode="Markdown")

# 5. Le Cerveau : Ce qui se passe quand on clique sur le bouton
@bot.callback_query_handler(func=lambda call: call.data == "generer_signal")
def traitement_signal(call):
    # Message d'attente
    bot.edit_message_text("🔄 *Connexion au marché et analyse en cours...*", 
                          call.message.chat.id, 
                          call.message.message_id, 
                          parse_mode="Markdown")
    
    # Étape A : On chope le prix en direct (ex: Bitcoin)
    # Tu peux changer "BTCUSDT" par "EURUSDT" si tu veux cibler le Forex via Binance
    prix_direct = obtenir_prix_actuel("BTCUSDT") 
    
    if not prix_direct:
        bot.edit_message_text("❌ Impossible de récupérer le prix en direct. Réessaie.", 
                              call.message.chat.id, 
                              call.message.message_id)
        return

    # Étape B : Le Prompt Sniper (Aucun avertissement toléré)
    prompt = f"""Tu es un trader institutionnel expert, froid et précis. 
    Le prix actuel en direct du marché (BTC/USDT) est exactement de : {prix_direct}.
    
    Analyse techniquement la situation à très court terme. Tu dois UNIQUEMENT répondre avec un signal clair selon ce format strict (ne rajoute RIEN d'autre) :
    
    🚀 **SIGNAL :** [CALL ou PUT]
    🎯 **ENTRÉE :** {prix_direct}
    ✅ **TAKE PROFIT :** [Prix calculé logique]
    🛑 **STOP LOSS :** [Prix calculé logique pour protéger le capital]
    📊 **ANALYSE :** [Deux phrases courtes et purement techniques justifiant les niveaux de TP et SL].
    
    RÈGLES ABSOLUES : 
    1. Interdiction stricte de faire des simulations.
    2. Interdiction stricte d'ajouter des avertissements de risque financier.
    3. Interdiction stricte de mentionner que tu es une IA."""

    # Étape C : Envoi à l'IA et affichage de la réponse
    try:
        reponse_ia = model.generate_content(prompt)
        
        # On remet le bouton pour qu'il puisse relancer un signal
        markup = InlineKeyboardMarkup()
        bouton_nouveau = InlineKeyboardButton("🎯 NOUVEAU SIGNAL", callback_data="generer_signal")
        markup.add(bouton_nouveau)
        
        # Affichage du signal final
        bot.edit_message_text(reponse_ia.text, 
                              call.message.chat.id, 
                              call.message.message_id, 
                              reply_markup=markup, 
                              parse_mode="Markdown")
                              
    except Exception as e:
        print(f"Erreur IA: {e}")
        bot.edit_message_text("❌ Erreur lors de la génération du signal par l'IA.", 
                              call.message.chat.id, 
                              call.message.message_id)

# 6. Lancement du bot
if __name__ == "__main__":
    print("Bot en ligne...")
    bot.infinity_polling()
    
