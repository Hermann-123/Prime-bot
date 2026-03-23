import os
import sys
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
import datetime
import random
import time
import string
from flask import Flask
from threading import Thread, Timer
import pandas as pd
import ta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- CONFIGURATION DU TOKEN ---
TELEGRAM_TOKEN = "8658287331:AAFJq993kMKhl6cRdiHgye_IdkYeLHEbor0"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
user_prefs = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Trading Prime VIP - Mode Sécurité Maximale Actif"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

def est_autorise(user_id):
    if user_id == ADMIN_ID: return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME": return True
        if datetime.datetime.now() < expiration: return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nContacte [@hermann1123](https://t.me/hermann1123).", parse_mode="Markdown")
            except: pass
            return False
    return False

def generer_cle():
    caracteres = string.ascii_uppercase + string.digits
    aleatoire = ''.join(random.choice(caracteres) for _ in range(8))
    return f"PRIME-{aleatoire}"

def obtenir_prix_actuel(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=1d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=5)
        donnees = reponse.json()
        return round(float(donnees['chart']['result'][0]['meta']['regularMarketPrice']), 5)
    except:
        return None

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel(symbole)
    if prix and chat_id in trades_en_cours:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'):
        try: bot.send_message(chat_id, "⚠️ **Trade terminé.** (Résultat exact non vérifiable).", parse_mode="Markdown")
        except: pass
        return

    prix_sortie = obtenir_prix_actuel(trade['symbole'])
    if not prix_sortie: return

    prix_entree = trade['prix_entree']
    action = trade['action']
    symbole = trade['symbole']

    gagne = False
    if action == "CALL" and prix_sortie > prix_entree: gagne = True
    elif action == "PUT" and prix_sortie < prix_entree: gagne = True

    if gagne:
        texte = f"✅ **VICTOIRE (ITM) !**\n\nSignal validé avec succès 🎉\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
    else:
        texte = f"❌ **PERTE (OTM)** ⚠️\n\nLe marché a manipulé la zone.\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
    
    try: bot.send_message(chat_id, texte, parse_mode="Markdown")
    except: pass
    
    if chat_id in trades_en_cours: del trades_en_cours[chat_id]

# --- MOTEUR D'ANALYSE VIP (SÉCURISÉ) ---
def analyser_binaire_pro(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=2d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=10)
        donnees = reponse.json()
        quote = donnees['chart']['result'][0]['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'open': quote['open'],  
            'close': quote['close'],
            'high': quote['high'],
            'low': quote['low']
        }).dropna()
        
        if len(df) < 50: return "⚠️ Pas de données", None, None, None

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        
        indicateur_stoch = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
        df['stoch_k'] = indicateur_stoch.stoch()
        
        indicateur_rsi = ta.momentum.RSIIndicator(close=df['close'], window=14)
        df['rsi'] = indicateur_rsi.rsi()

        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
            
        bougie_fermee = df.iloc[-2]
        ema_200 = bougie_fermee['ema_200']
        c = bougie_fermee['close']

        # 🛡️ FIX EXPIRATION : FINI LE CASINO 30 SEC. MINIMUM 3 MINUTES.
        largeur_bande = (bougie_fermee['bb_haute'] - bougie_fermee['bb_basse']) / c
        if largeur_bande > 0.0025:
            expiration = "5 MINUTES ⏱"
            duree_secondes = 300
        else:
            expiration = "3 MINUTES ⏱"
            duree_secondes = 180
        
        action = None
        confiance = 0
        
        # 🛡️ FIX VISEUR : RSI 65/35 & STOCH 80/20 (Le juste milieu)
        if c >= bougie_fermee['bb_haute'] and bougie_fermee['stoch_k'] >= 80 and bougie_fermee['rsi'] >= 65:
            if c < ema_200: 
                action = "🔴 VENTE (PUT)"
                confiance = random.randint(92, 96) 
            else: return "⚠️ Tendance haussière forte (Attente)", None, None, None
                
        elif c <= bougie_fermee['bb_basse'] and bougie_fermee['stoch_k'] <= 20 and bougie_fermee['rsi'] <= 35:
            if c > ema_200: 
                action = "🟢 ACHAT (CALL)"
                confiance = random.randint(92, 96)
            else: return "⚠️ Tendance baissière forte (Attente)", None, None, None
            
        else:
            return "⚠️ Marché neutre", None, None, None
            
        return action, confiance, expiration, duree_secondes
        
    except Exception as e:
        return None, None, None, None

def scanner_marche_auto():
    devises_a_surveiller = ["EURUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "EURJPY", "CHFJPY", "AUDJPY"]
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: continue
            
            for actif in devises_a_surveiller:
                action, confiance, exp, duree = analyser_binaire_pro(actif)
                if action and "⚠️" not in action and confiance:
                    maintenant = time.time()
                    if actif in derniere_alerte_auto and (maintenant - derniere_alerte_auto[actif] < 900): continue
                        
                    derniere_alerte_auto[actif] = maintenant
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"🔒 Verrouiller {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                    
                    alerte_msg = f"🚨 **NOUVELLE OPPORTUNITÉ DÉTECTÉE** 🚨\n\nConfiguration Sniper VIP sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Clique ci-dessous pour verrouiller !*"
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except: pass
        except: pass

@bot.message_handler(func=lambda m: m.text and m.text.startswith("PRIME-"))
def activer_cle(message):
    cle = message.text.strip()
    if cle in cles_generees:
        infos_cle = cles_generees[cle]
        if infos_cle["user_id"] != message.chat.id:
            bot.send_message(message.chat.id, "❌ **ACCÈS REFUSÉ** ❌", parse_mode="Markdown")
            return
        jours = infos_cle["jours"]
        if jours == 999:
            utilisateurs_autorises[message.chat.id] = "LIFETIME"
            duree_texte = "À VIE 👑"
        else:
            expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
            utilisateurs_autorises[message.chat.id] = expiration
            duree_texte = f"jusqu'au {expiration.strftime('%d/%m/%Y à %H:%M')}"
        del cles_generees[cle] 
        bot.send_message(message.chat.id, f"✅ **CLÉ ACCEPTÉE !** 🎉\nAccès activé {duree_texte}.\nTapez /start", parse_mode="Markdown")
    else: bot.send_message(message.chat.id, "❌ **Clé invalide.**", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    if call.from_user.id != ADMIN_ID: return
    action = call.data.split("_")[1]
    user_id = int(call.data.split("_")[2])
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"),
            InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Choix abo pour ID `{user_id}` :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    elif action == "refuser": bot.edit_message_text(f"❌ Refusé.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    if call.from_user.id != ADMIN_ID: return
    jours = int(call.data.split("_")[1])
    user_id = int(call.data.split("_")[2])
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    duree = f"{jours} Jours" if jours != 999 else "À VIE"
    bot.edit_message_text(f"🔑 **CLÉ GÉNÉRÉE** ({duree})\nID: `{user_id}`\n\n`{cle}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Créer Clé", callback_data=f"admin_accepter_{user_id}"),
            InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}")
        )
        try: bot.send_message(ADMIN_ID, f"🚨 **CLIENT** 🚨\nID: `{user_id}`", reply_markup=markup, parse_mode="Markdown")
        except: pass
        try: bot.send_message(user_id, "🔒 **ACCÈS VIP RESTREINT**\nContact : [@hermann1123](https://t.me/hermann1123)", parse_mode="Markdown", disable_web_page_preview=True)
        except: pass
        return
    utilisateurs_actifs.add(user_id)
    texte = """🏴‍☠️ **TERMINAL PRIME VIP** 🔥\n1️⃣ **SÉLECTION:** Choisis ta paire.\n2️⃣ **RADAR:** Lance l'analyse.\n\n*Règle d'or : Mise 2% max. Stop après 3 pertes.*"""
    try: bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(), parse_mode="Markdown")
    except: pass

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **HORAIRES (GMT)** 🕒\n✅ **MATIN (08h-11h):** EUR/USD, EUR/JPY\n🔥 **OR (13h30-16h30):** EUR/USD, USD/CAD\n❌ **DANGER (22h-07h):** OTC/Manipulation."""
    try: bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: pass

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"),
        InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 USD/CAD", callback_data="set_USDCAD"),
        InlineKeyboardButton("🇨🇭 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"),
        InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY")
    )
    try: bot.send_message(message.chat.id, "Sélectionne la cible :", reply_markup=markup)
    except: pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    if not est_autorise(call.message.chat.id): return
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    try: bot.send_message(call.message.chat.id, f"✅ **Verrouillé : {actif[:3]}/{actif[3:]}**")
    except: pass

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        try: bot.send_message(message.chat.id, "⚠️ Choisis une devise !")
        except: pass
        return

    try:
        msg = bot.send_message(message.chat.id, "⏳ *Scan en cours...*", parse_mode="Markdown")
        time.sleep(1)
        bot.edit_message_text(f"💎 *Verrouillage {actif[:3]}/{actif[3:]}...*", message.chat.id, msg.message_id, parse_mode="Markdown")
        time.sleep(1)
    except: return
        
    action, confiance, exp, duree_secondes = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}", message.chat.id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Échec des données.", message.chat.id, msg.message_id)
        except: pass
        return

    maintenant = datetime.datetime.now()
    heure_entree_dt = (maintenant + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0)
    heure_entree_texte = heure_entree_dt.strftime("%H:%M:00")
    
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)

    signal = f"""🚀 **SIGNAL SNIPER VIP** 🚀
──────────────────
🛰 ACTIF : {actif[:3]}/{actif[3:]}
🎯 ACTION : {action}
⏳ EXPIRATION : {exp}
──────────────────
📍 ORDRE À : {heure_entree_texte} 👈
💵 MISE REC. : {mise_recommandee}$ (2%)
📊 CONFIANCE : {confiance}% 🔥"""

    try:
        bot.delete_message(message.chat.id, msg.message_id)
        bot.send_message(message.chat.id, signal, parse_mode="Markdown")
    except: pass

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[message.chat.id] = {'symbole': actif, 'action': action_simplifiee}
    
    delai_attente = max(0, (heure_entree_dt - datetime.datetime.now()).total_seconds())
    Timer(delai_attente, relever_prix_entree, args=[message.chat.id, actif]).start()
    Timer(delai_attente + duree_secondes, verifier_resultat, args=[message.chat.id]).start()

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    commande = message.text.split()
    if len(commande) < 2: return
    symbole = commande[1].upper()
    try: msg = bot.send_message(message.chat.id, f"🔍 *Scan {symbole}...*", parse_mode="Markdown")
    except: return
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=2d&interval=1m"
    try:
        donnees = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10).json()
        quote = donnees['chart']['result'][0]['indicators']['quote'][0]
        df = pd.DataFrame({'close': quote['close'], 'high': quote['high'], 'low': quote['low']}).dropna()
        if len(df) < 50: return
        bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        
        prix_actuel = df['close'].iloc[-1]
        ema_200 = df['ema_200'].iloc[-1]
        bb_haute = bb.bollinger_hband().iloc[-1]
        bb_basse = bb.bollinger_lband().iloc[-1]
        
        pos = "🔴 Plafond" if prix_actuel >= bb_haute else ("🟢 Plancher" if prix_actuel <= bb_basse else "⚪ Neutre")
        rapport = f"👁️ **VISION : {symbole}**\n💰 Prix : `{prix_actuel:.5f}`\n🛡️ EMA 200 : `{ema_200:.5f}`\n📏 BB : {pos}\n📊 RSI : `{rsi:.2f}`\n📉 Stoch : `{stoch_k:.2f}`"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except: pass

if __name__ == "__main__":
    print("⬛ BOÎTE NOIRE : Démarrage...")
    try:
        keep_alive()
        Thread(target=scanner_marche_auto, daemon=True).start()
        bot.infinity_polling()
    except Exception as e: print(f"🚨 CRASH : {e}")
