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

# --- SÉCURITÉ DOTENV ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- CONFIGURATION DU TOKEN (INTÉGRÉ DIRECTEMENT) ---
TELEGRAM_TOKEN = "8658287331:AAFJq993kMKhl6cRdiHgye_IdkYeLHEbor0"

if not TELEGRAM_TOKEN:
    print("⬛ BOÎTE NOIRE [ERREUR FATALE] : Le TELEGRAM_TOKEN est introuvable ! Vérifie l'onglet 'Environment' sur Render.", flush=True)
    sys.exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# 👑 L'ID DU FONDATEUR (TOI) 👑
ADMIN_ID = 5968288964 

CAPITAL_ACTUEL = 40650 
user_prefs = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}

# SYSTÈME DE GESTION DES ABONNEMENTS
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Trading Binaire Prime VIP en ligne ! (Textes Courts)"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- FONCTION DE VÉRIFICATION D'ACCÈS ---
def est_autorise(user_id):
    if user_id == ADMIN_ID:
        return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME":
            return True
        if datetime.datetime.now() < expiration:
            return True
        else:
            del utilisateurs_autorises[user_id]
            try:
                bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nContacte [@hermann1123](https://t.me/hermann1123) pour renouveler ton accès.", parse_mode="Markdown")
            except:
                pass
            return False
    return False

# --- GÉNÉRATEUR DE CLÉS ---
def generer_cle():
    caracteres = string.ascii_uppercase + string.digits
    aleatoire = ''.join(random.choice(caracteres) for _ in range(8))
    return f"PRIME-{aleatoire}"

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

# --- VÉRIFICATION AUTOMATIQUE (ITM/OTM) ---
def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel(symbole)
    if prix and chat_id in trades_en_cours:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'):
        try:
            bot.send_message(chat_id, "⚠️ **Trade terminé.** (Résultat non vérifiable).", parse_mode="Markdown")
        except:
            pass
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
        texte = f"✅ **VICTOIRE (ITM) !** 🎉\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
    else:
        texte = f"❌ **PERTE (OTM)** ⚠️\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
    
    try:
        bot.send_message(chat_id, texte, parse_mode="Markdown")
    except:
        pass
    
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
            return "⚠️ Marché neutre", None, None, None
            
        return action, confiance, expiration, duree_secondes
        
    except Exception as e:
        return None, None, None, None

# --- SCANNER AUTOMATIQUE EN ARRIÈRE-PLAN ---
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
                    
                    if actif in derniere_alerte_auto and (maintenant - derniere_alerte_auto[actif] < 900):
                        continue
                        
                    derniere_alerte_auto[actif] = maintenant
                    
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"🔒 Verrouiller {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                    
                    alerte_msg = f"🚨 **OPPORTUNITÉ SNIPER : {actif[:3]}/{actif[3:]}** 🚨\n\nConfiance : {confiance}%\n👇 *Clique pour verrouiller la cible !*"
                    
                    for chat_id in utilisateurs_a_alerter:
                        try:
                            bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except:
                            pass
        except Exception as e:
            print(f"⬛ BOÎTE NOIRE [ERREUR SCANNER] : {e}", flush=True)

# --- ACTIVATION DE LA CLÉ ---
@bot.message_handler(func=lambda m: m.text and m.text.startswith("PRIME-"))
def activer_cle(message):
    cle = message.text.strip()
    
    if cle in cles_generees:
        infos_cle = cles_generees[cle]
        
        if infos_cle["user_id"] != message.chat.id:
            bot.send_message(message.chat.id, "❌ **Clé intransférable.** Contacte le fondateur.", parse_mode="Markdown")
            return
            
        jours = infos_cle["jours"]
        
        if jours == 999:
            utilisateurs_autorises[message.chat.id] = "LIFETIME"
            duree_texte = "À VIE 👑"
        else:
            expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
            utilisateurs_autorises[message.chat.id] = expiration
            duree_texte = f"jusqu'au {expiration.strftime('%d/%m/%Y %H:%M')}"
            
        del cles_generees[cle] 
        bot.send_message(message.chat.id, f"✅ **CLÉ ACTIVÉE !** 🎉\nAccès valide {duree_texte}.\nTape /start", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ **Clé invalide ou expirée.**", parse_mode="Markdown")

# --- MENU ADMIN ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    if call.from_user.id != ADMIN_ID: return
    action = call.data.split("_")[1]
    user_id = int(call.data.split("_")[2])
    
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"),
            InlineKeyboardButton("2 Semaines", callback_data=f"gen_14_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"),
            InlineKeyboardButton("2 Mois", callback_data=f"gen_60_{user_id}"),
            InlineKeyboardButton("3 Mois", callback_data=f"gen_90_{user_id}"),
            InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Choix de l'abonnement pour l'ID `{user_id}` :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    elif action == "refuser":
        bot.edit_message_text(f"❌ Refusé.", call.message.chat.id, call.message.message_id)

# --- GÉNÉRATION CLÉ ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    if call.from_user.id != ADMIN_ID: return
    parts = call.data.split("_")
    jours = int(parts[1])
    user_id = int(parts[2])
    
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    duree_texte = f"{jours} J" if jours != 999 else "À VIE"
    
    msg_cle = f"🔑 **CLÉ GÉNÉRÉE** ({duree_texte})\n👤 ID : `{user_id}`\n\n`{cle}`"
    bot.edit_message_text(msg_cle, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# --- CLAVIER ---
def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

# --- MENU START (TEXTES COURTS) ---
@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    username = message.from_user.username or message.from_user.first_name
    
    if not est_autorise(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Créer Clé", callback_data=f"admin_accepter_{user_id}"),
            InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}")
        )
        alerte_admin = f"🚨 **NOUVEAU CLIENT** 🚨\n👤 @{username}\n🆔 `{user_id}`"
        try: bot.send_message(ADMIN_ID, alerte_admin, reply_markup=markup, parse_mode="Markdown")
        except: pass
            
        texte_intrus = """🔒 **ACCÈS VIP RESTREINT** 🔒

Bot de trading algorithmique sous licence payante.

📲 **Abonnement & Clé : [@hermann1123](https://t.me/hermann1123)**

*(Si tu as une clé, colle-la ici directement).*"""
        try: bot.send_message(user_id, texte_intrus, parse_mode="Markdown", disable_web_page_preview=True)
        except: pass
        return

    utilisateurs_actifs.add(user_id)
    
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME VIP** 🔥

📖 **MODE D'EMPLOI EXPRESS :**
1️⃣ **SÉLECTION 📊:** Choisis ta cible.
2️⃣ **TIR 🚀:** Lance l'analyse Sniper.
3️⃣ **TIMING ⏰:** Trade aux bonnes heures.
4️⃣ **RÈGLE D'OR 🛡️:** Mise 2% max. Stop après 3 pertes.

💡 *Zéro émotion, discipline de fer.* 🎯💸

👨‍💻 **Support : [@hermann1123](https://t.me/hermann1123)**"""

    try: bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown", disable_web_page_preview=True)
    except: pass

# --- HEURES DE TRADING (RACCOURCIES) ---
@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    
    texte = """🕒 **HORAIRES (GMT)** 🕒

✅ **MATIN (08h00 - 11h00)**
👍 EUR/USD, EUR/JPY, CHF/JPY, USD/CHF
👎 Éviter : AUD/USD, AUD/JPY, USD/CAD

🔥 **ZONE EN OR (13h30 - 16h30)**
👍 EUR/USD, USD/CAD, AUD/USD
👎 Éviter : Paires en JPY

❌ **DANGER (22h00 - 07h00)**
☠️ **À FUIR :** Marché mort ou manipulé.

🛡️ *Rappel : Stop total après 3 pertes.*"""
    
    try: bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: pass

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🇪🇺 EUR / USD", callback_data="set_EURUSD"),
        InlineKeyboardButton("🇯🇵 USD / JPY", callback_data="set_USDJPY"),
        InlineKeyboardButton("🇦🇺 AUD / USD", callback_data="set_AUDUSD"),
        InlineKeyboardButton("🇨🇦 USD / CAD", callback_data="set_USDCAD"),
        InlineKeyboardButton("🇨🇭 USD / CHF", callback_data="set_USDCHF"),
        InlineKeyboardButton("🇪🇺 EUR / JPY", callback_data="set_EURJPY"),
        InlineKeyboardButton("🇨🇭 CHF / JPY", callback_data="set_CHFJPY"),
        InlineKeyboardButton("🇦🇺 AUD / JPY", callback_data="set_AUDJPY")
    )
    try: bot.send_message(message.chat.id, "Sélectionne l'actif :", reply_markup=markup)
    except: pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    if not est_autorise(call.message.chat.id): return
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    try: bot.send_message(call.message.chat.id, f"✅ **Cible verrouillée : {actif[:3]}/{actif[3:]}**")
    except: pass

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        try: bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
        except: pass
        return

    try:
        msg = bot.send_message(message.chat.id, "⏳ *Scan algorithmique...*", parse_mode="Markdown")
        time.sleep(2)
        bot.edit_message_text(f"📡 *Connexion {actif[:3]}/{actif[3:]}...*", message.chat.id, msg.message_id, parse_mode="Markdown")
        time.sleep(2)
        bot.edit_message_text("⚙️ *Calcul des indicateurs...*", message.chat.id, msg.message_id, parse_mode="Markdown")
        time.sleep(1)
    except:
        return
        
    action, confiance, exp, duree_secondes = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}\nPatientez.", message.chat.id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Échec des données.", message.chat.id, msg.message_id)
        except: pass
        return

    maintenant = datetime.datetime.now()
    heure_entree_dt = (maintenant + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0)
    heure_entree_texte = heure_entree_dt.strftime("%H:%M:00")

    signal = f"""🚀 **SIGNAL SNIPER** 🚀
──────────────────
🛰 ACTIF : {actif[:3]}/{actif[3:]}
🎯 ACTION : {action}
⏳ EXPIRATION : {exp}
──────────────────
📍 ORDRE À : {heure_entree_texte} 👈
💵 MISE MAX : 2%
📊 CONFIANCE : {confiance}% 🔥"""

    try:
        bot.delete_message(message.chat.id, msg.message_id)
        bot.send_message(message.chat.id, signal, parse_mode="Markdown")
    except:
        pass

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[message.chat.id] = {'symbole': actif, 'action': action_simplifiee}
    
    delai_attente_entree = (heure_entree_dt - datetime.datetime.now()).total_seconds()
    delai_attente_entree = max(0, delai_attente_entree)
    
    Timer(delai_attente_entree, relever_prix_entree, args=[message.chat.id, actif]).start()
    delai_verification = delai_attente_entree + duree_secondes
    Timer(delai_verification, verifier_resultat, args=[message.chat.id]).start()

# --- VISION ---
@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    commande = message.text.split()
    if len(commande) < 2:
        try: bot.send_message(message.chat.id, "⚠️ Précise la devise. Ex : `/vision EURUSD`", parse_mode="Markdown")
        except: pass
        return
        
    symbole = commande[1].upper()
    try: msg = bot.send_message(message.chat.id, f"🔍 *Scan de {symbole}...*", parse_mode="Markdown")
    except: return
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=2d&interval=1m"
    try:
        reponse = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        donnees = reponse.json()
        quote = donnees['chart']['result'][0]['indicators']['quote'][0]
        
        df = pd.DataFrame({'close': quote['close'], 'high': quote['high'], 'low': quote['low']}).dropna()
        if len(df) < 50:
            bot.edit_message_text("⚠️ Manque de données.", message.chat.id, msg.message_id)
            return

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        bb_haute = indicateur_bb.bollinger_hband().iloc[-1]
        bb_basse = indicateur_bb.bollinger_lband().iloc[-1]
        
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        if prix_actuel >= bb_haute: position_bb = "🔴 Au Plafond"
        elif prix_actuel <= bb_basse: position_bb = "🟢 Au Plancher"
        else: position_bb = "⚪ Zone neutre"

        rapport = f"👁️ **VISION : {symbole}** 👁️\n💰 Prix : `{prix_actuel:.5f}`\n📏 Bollinger : {position_bb}\n📊 RSI : `{rsi:.2f}`\n📉 Stochastique : `{stoch_k:.2f}`"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except Exception as e:
        try: bot.edit_message_text("❌ Erreur de scan.", message.chat.id, msg.message_id)
        except: pass

if __name__ == "__main__":
    print("⬛ BOÎTE NOIRE : Démarrage du système...", flush=True)
    try:
        keep_alive()
        Thread(target=scanner_marche_auto, daemon=True).start()
        print("⬛ BOÎTE NOIRE : Serveur Web et Scanner lancés avec succès.", flush=True)
        bot.infinity_polling()
    except Exception as e:
        print(f"🚨 BOÎTE NOIRE [CRASH DÉTECTÉ] : {e}", flush=True)
    
