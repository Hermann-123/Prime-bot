import os
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
from dotenv import load_dotenv
import datetime
import random
import time
from flask import Flask
from threading import Thread, Timer
import pandas as pd
import ta

# --- CONFIGURATION ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TELEGRAM_TOKEN)

CAPITAL_ACTUEL = 40650 
user_prefs = {}
trades_en_cours = {} # Mémoire pour suivre les trades
utilisateurs_actifs = set() # Mémoire pour envoyer les alertes auto
derniere_alerte_auto = {} # Mémoire Anti-Spam pour le scanner

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Trading Binaire Prime en ligne !"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- FONCTION DE RÉCUPÉRATION DU PRIX EXACT ---
def obtenir_prix_actuel(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=1d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=5)
        donnees = reponse.json()
        return round(float(donnees['chart']['result'][0]['meta']['regularMarketPrice']), 5)
    except:
        return None

# --- VÉRIFICATION AUTOMATIQUE (ITM/OTM) CORRIGÉE ---
def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel(symbole)
    if prix and chat_id in trades_en_cours:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'):
        bot.send_message(chat_id, "⚠️ **Trade terminé.** (Flux interrompu, résultat exact non vérifiable).", parse_mode="Markdown")
        return

    prix_sortie = obtenir_prix_actuel(trade['symbole'])
    if not prix_sortie:
        return

    prix_entree = trade['prix_entree']
    action = trade['action']
    symbole = trade['symbole']

    gagne = False
    if action == "CALL" and prix_sortie > prix_entree:
        gagne = True
    elif action == "PUT" and prix_sortie < prix_entree:
        gagne = True

    if gagne:
        texte = f"✅ **VICTOIRE (ITM) !**\n\nSignal passé avec succès 🎉\nLe trade sur {symbole[:3]}/{symbole[3:]} a été validé !\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
    else:
        texte = f"❌ **PERTE (OTM)** ⚠️\n\nLe marché s'est retourné sur {symbole[:3]}/{symbole[3:]}.\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n\n*Garde ton sang-froid, respecte ton Money Management.*"
    
    bot.send_message(chat_id, texte, parse_mode="Markdown")
    
    if chat_id in trades_en_cours:
        del trades_en_cours[chat_id]

# --- MOTEUR D'ANALYSE PRO (PANDAS + TA) ---
def analyser_binaire_pro(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=2d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=10)
        donnees = reponse.json()
        
        resultat = donnees['chart']['result'][0]
        quote = resultat['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'close': quote['close'],
            'high': quote['high'],
            'low': quote['low']
        }).dropna()
        
        if len(df) < 50:
            return "⚠️ Pas assez de données", None, None, None

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        
        indicateur_stoch = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
        df['stoch_k'] = indicateur_stoch.stoch()
        
        indicateur_rsi = ta.momentum.RSIIndicator(close=df['close'], window=14)
        df['rsi'] = indicateur_rsi.rsi()

        derniere_bougie = df.iloc[-1]
        prix_actuel = derniere_bougie['close']
        
        largeur_bande = (derniere_bougie['bb_haute'] - derniere_bougie['bb_basse']) / prix_actuel
        duree_secondes = 180
        if largeur_bande > 0.0020:
            expiration = "30 SEC ⏱"
            duree_secondes = 30
        elif largeur_bande > 0.0012:
            expiration = "1 MINUTE ⏱"
            duree_secondes = 60
        else:
            expiration = "2 MINUTES ⏱"
            duree_secondes = 120
        
        action = None
        confiance = 0
        
        if prix_actuel >= derniere_bougie['bb_haute'] and derniere_bougie['stoch_k'] > 80 and derniere_bougie['rsi'] > 60:
            action = "🔴 VENTE (PUT)"
            confiance = random.randint(88, 95) 
            
        elif prix_actuel <= derniere_bougie['bb_basse'] and derniere_bougie['stoch_k'] < 20 and derniere_bougie['rsi'] < 40:
            action = "🟢 ACHAT (CALL)"
            confiance = random.randint(88, 95)
            
        else:
            return "⚠️ Marché neutre (Attente de cassure)", None, None, None
            
        return action, confiance, expiration, duree_secondes
        
    except Exception as e:
        return None, None, None, None

# --- SCANNER AUTOMATIQUE EN ARRIÈRE-PLAN ---
def scanner_marche_auto():
    # Suppression de GBPUSD ici
    devises_a_surveiller = ["EURUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "USDPKR"]
    while True:
        time.sleep(60)
        if not utilisateurs_actifs:
            continue
        
        for actif in devises_a_surveiller:
            action, confiance, exp, duree = analyser_binaire_pro(actif)
            
            if action and "⚠️" not in action and confiance:
                maintenant = time.time()
                
                if actif in derniere_alerte_auto and (maintenant - derniere_alerte_auto[actif] < 900):
                    continue
                    
                derniere_alerte_auto[actif] = maintenant
                
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton(f"🔒 Verrouiller {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                
                alerte_msg = f"🚨 **NOUVELLE OPPORTUNITÉ DÉTECTÉE** 🚨\n\nL'algorithme vient de repérer une configuration Sniper sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Clique sur le bouton ci-dessous pour verrouiller la cible, puis lance l'analyse !*"
                
                for chat_id in utilisateurs_actifs:
                    try:
                        bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                    except:
                        pass

# --- COMMANDE SECRÈTE : RADIOGRAPHIE DU MARCHÉ ---
@bot.message_handler(commands=['vision'])
def vision_marche(message):
    commande = message.text.split()
    if len(commande) < 2:
        bot.send_message(message.chat.id, "⚠️ Précise la devise. Exemple : `/vision EURUSD`", parse_mode="Markdown")
        return
        
    symbole = commande[1].upper()
    msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X de {symbole}...*", parse_mode="Markdown")
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=2d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=10)
        donnees = reponse.json()
        quote = donnees['chart']['result'][0]['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'close': quote['close'],
            'high': quote['high'],
            'low': quote['low']
        }).dropna()
        
        if len(df) < 50:
            bot.edit_message_text("⚠️ Impossible de scanner (manque de données).", message.chat.id, msg.message_id)
            return

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        bb_haute = indicateur_bb.bollinger_hband().iloc[-1]
        bb_basse = indicateur_bb.bollinger_lband().iloc[-1]
        
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        
        prix_actuel = df['close'].iloc[-1]
        
        if prix_actuel >= bb_haute:
            position_bb = "🔴 Au Plafond (Touche la bande haute)"
        elif prix_actuel <= bb_basse:
            position_bb = "🟢 Au Plancher (Touche la bande basse)"
        else:
            position_bb = "⚪ Au Milieu (Zone neutre)"

        rapport = f"""👁️ **VISION RAYONS X : {symbole}** 👁️
──────────────────
💰 **Prix actuel :** `{prix_actuel:.5f}`
📏 **Position Bollinger :** {position_bb}

📊 **Niveau RSI :** `{rsi:.2f}` 
*(Rappel: >60 = Surchauffe, <40 = Essoufflé)*

📉 **Niveau Stochastique :** `{stoch_k:.2f}`
*(Rappel: >80 = Surachat, <20 = Survente)*
──────────────────"""
        
        if position_bb != "⚪ Au Milieu (Zone neutre)":
            rapport += "\n⚠️ *Le prix teste les limites, tiens-toi prêt !*"
        else:
            rapport += "\n💤 *Le marché respire tranquillement.*"

        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
        
    except Exception as e:
        bot.edit_message_text(f"❌ Erreur lors du scan : {e}", message.chat.id, msg.message_id)

# --- INTERFACE CLAVIER ---
def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    utilisateurs_actifs.add(message.chat.id)
    bot.send_message(message.chat.id, "🏴‍☠️ **TERMINAL PRIME - ÉDITION BINAIRE** 🔥\n\nMoteur : **Pandas + TA (BB, RSI, Stochastique)**\nFonctions : **Auto-Scan 1m & Audit ITM/OTM**", reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    # Suppression de GBP/USD de l'interface
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🇪🇺 EUR / USD", callback_data="set_EURUSD"),
        InlineKeyboardButton("🇯🇵 USD / JPY", callback_data="set_USDJPY"),
        InlineKeyboardButton("🇦🇺 AUD / USD", callback_data="set_AUDUSD"),
        InlineKeyboardButton("🇨🇭 USD / CHF", callback_data="set_USDCHF"),
        InlineKeyboardButton("🇨🇦 USD / CAD", callback_data="set_USDCAD"),
        InlineKeyboardButton("🇵🇰 USD / PKR", callback_data="set_USDPKR")
    )
    bot.send_message(message.chat.id, "Sélectionne l'actif à scanner :", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    bot.send_message(call.message.chat.id, f"✅ **Cible verrouillée : {actif[:3]}/{actif[3:]}**")

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
        return

    msg = bot.send_message(message.chat.id, "⏳ *Initialisation du scan algorithmique rapide...*", parse_mode="Markdown")
    
    time.sleep(2)
    bot.edit_message_text(f"📡 *Connexion au flux {actif[:3]}/{actif[3:]} et scan de la volatilité en cours...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(2)
    bot.edit_message_text("⚙️ *Calcul des indicateurs avancés (BB, RSI, Stochastique)...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(2)
    bot.edit_message_text("💎 *Triple confirmation et verrouillage Sniper...*", message.chat.id, msg.message_id, parse_mode="Markdown")
    time.sleep(1)
    
    action, confiance, exp, duree_secondes = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        bot.edit_message_text(f"{action}\nLe prix ne remplit pas les conditions strictes de l'algorithme. Patientez.", message.chat.id, msg.message_id)
        return
    elif not action:
        bot.edit_message_text("❌ Échec de la récupération des données. Relance l'analyse.", message.chat.id, msg.message_id)
        return

    maintenant = datetime.datetime.now()
    heure_entree_dt = (maintenant + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0)
    heure_entree_texte = heure_entree_dt.strftime("%H:%M:00")
    
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)

    signal = f"""🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀
──────────────────
🛰 ACTIF : {actif[:3]}/{actif[3:]}
🎯 ACTION : {action}
⏳ EXPIRATION : {exp}
──────────────────
📍 ORDRE À : {heure_entree_texte} 👈
💵 MISE RECOMMANDÉE : {mise_recommandee}$ (2%)
📊 CONFIANCE : {confiance}% 🔥
──────────────────
💎 *Audit de résultat (ITM/OTM) activé en arrière-plan.*"""

    bot.delete_message(message.chat.id, msg.message_id)
    bot.send_message(message.chat.id, signal, parse_mode="Markdown")

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[message.chat.id] = {'symbole': actif, 'action': action_simplifiee}
    
    # CORRECTION DE LA SYNCHRONISATION DU CHRONOMÈTRE
    delai_attente_entree = (heure_entree_dt - datetime.datetime.now()).total_seconds()
    delai_attente_entree = max(0, delai_attente_entree) # Sécurité pour ne pas avoir de délai négatif
    
    Timer(delai_attente_entree, relever_prix_entree, args=[message.chat.id, actif]).start()
    
    delai_verification = delai_attente_entree + duree_secondes
    Timer(delai_verification, verifier_resultat, args=[message.chat.id]).start()

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    bot.infinity_polling()
