import os
import sys
import datetime
import random
import time
import string
import json
import websocket
import pandas as pd
import ta
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAEFYQTQ_V4ppVGoyHI7UXnZHnVwqbXJZ_Y"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# 🔴 CONFIGURATION MARTINGALE SÉCURISÉE
COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3  

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
mode_trading = {} 
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
cooldown_actifs = {} 
niveaux_martingale = {} 

utilisateurs_autorises = {
    ADMIN_ID: "LIFETIME"
}
cles_generees = {}

stats_journee = {
    'ITM': 0, 
    'OTM': 0, 
    'details': []
}

CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD", 
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF", 
    "CADCHF", "EURCHF", "USDJPY"
]

# ✅ V18: Affichage "OTC" — Pocket Option propose des versions simulées
# 24/7 de ces mêmes paires sous ce nom. La SOURCE DE DONNÉES reste Deriv
# (frxXXXYYY, fiable et gratuite) ; seul le LIBELLÉ affiché change pour
# correspondre à ce que l'utilisateur voit sur Pocket Option.
# ⚠️ Les paires exotiques Pocket-Option-only (USD/COP, USD/CNH, AED/CNY,
# EUR/TRY...) ne sont PAS incluses ici car Deriv ne fournit pas leur flux —
# les activer nécessiterait une vraie intégration Pocket Option (SSID
# complet + librairie non-officielle), voir discussion.
def nom_otc(symbole):
    return f"{symbole[:3]}/{symbole[3:]} OTC"

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V18 ULTIMATE (4 Piliers + Killswitch Anti-Fusée)"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# SYSTÈME DE GESTION DES ACCÈS VIP
# ==========================================

def est_autorise(user_id):
    if user_id == ADMIN_ID: return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME" or datetime.datetime.now() < expiration: return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
            except: pass
            return False
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    try:
        argument = message.text.split()[1].lower()
        if argument == '1s': jours = 7
        elif argument == '2s': jours = 14
        elif argument == '1m': jours = 30
        elif argument == '3m': jours = 90
        elif argument == 'vie': jours = "LIFETIME"
        else: jours = int(argument) 
            
        cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cles_generees[cle] = jours
        
        texte = f"✅ **CLÉ GÉNÉRÉE AVEC SUCCÈS**\n\n🔑 **Clé :** `{cle}`\n"
        texte += f"⏳ **Durée :** À VIE 👑\n\n" if jours == "LIFETIME" else f"⏳ **Durée :** {jours} Jours\n\n"
        bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: pass

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    chat_id = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle in cles_generees:
            jours = cles_generees[cle]
            if jours == "LIFETIME":
                utilisateurs_autorises[chat_id] = "LIFETIME"
                expiration_texte = "À VIE 👑"
            else:
                expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
                utilisateurs_autorises[chat_id] = expiration
                expiration_texte = expiration.strftime('%d/%m/%Y à %H:%M')
            del cles_generees[cle] 
            texte = f"🎉 **ACCÈS TERMINAL PRIME DÉVERROUILLÉ !** 🎉\n\nBienvenue dans l'équipe.\n⏳ **Fin de l'abonnement :** {expiration_texte}\n\n👉 Tapez /start pour initialiser votre tableau de bord."
            bot.send_message(chat_id, texte, parse_mode="Markdown")
        else: bot.send_message(chat_id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")
    except: pass

# ==========================================
# VERROUILLAGE TEMPOREL & EXCEPTION 10/10
# ==========================================

def est_symbole_autorise(symbole):
    now = datetime.datetime.utcnow()
    jour = now.weekday()
    heure = now.hour
    minute = now.minute
    heure_dec = heure + (minute / 60.0)

    est_week_end = False
    if jour == 4 and heure_dec >= 21.0: est_week_end = True
    elif jour == 5: est_week_end = True
    elif jour == 6 and heure_dec < 21.0: est_week_end = True

    # ⚠️ IMPORTANT (voir note V18): même si Pocket Option affiche ces paires
    # en "OTC" 24/7, la SOURCE DE DONNÉES du bot (Deriv, frxXXXYYY) reflète
    # le VRAI marché forex, qui est fermé le week-end — les bougies y sont
    # gelées. Générer des signaux sur des données gelées serait trompeur.
    # Le blocage week-end reste donc actif tant que la donnée vient de Deriv.
    if est_week_end:
        if symbole in CRYPTO_PAIRS: return "AUTORISE", ""
        else: return "BLOCAGE_TOTAL", f"🔒 **ACCÈS REFUSÉ** : Le marché Forex réel (source de données) est fermé le week-end, même si Pocket Option affiche du OTC — les prix gelés produiraient des signaux trompeurs. Seules les cryptos sont autorisées."

    if symbole in CRYPTO_PAIRS:
        return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Les Cryptomonnaies sont verrouillées la semaine. Elles sont réservées exclusivement pour le week-end."

    if heure_dec >= 17.5: return "HORS_SESSION", f"🛑 **REPLI TACTIQUE** : Couvre-feu en cours (17h30 - 00h00 GMT)."
    
    if heure_dec >= 0.0 and heure_dec < 8.0:
        if symbole in ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"]: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Session Asiatique."

    if heure_dec >= 7.0 and heure_dec < 12.0:
        paires = ["EURUSD", "EURJPY", "EURAUD", "EURCHF", "USDCHF", "CADCHF"]
        if heure_dec < 8.0: paires.extend(["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"])
        if symbole in paires: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Session Européenne."

    if heure_dec >= 12.0 and heure_dec < 17.5:
        if symbole in ["EURUSD", "USDCAD", "AUDUSD"]: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Zone de Guerre US/CA."

    return "BLOCAGE_TOTAL", "🛑 Erreur temporelle."

# ==========================================
# FONCTIONS PRO & ROUTEUR DERIV
# ==========================================

def est_heure_de_news_dynamique():
    if not FMP_API_KEY: return False
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today}&to={today}&apikey={FMP_API_KEY}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            events = response.json()
            maintenant = datetime.datetime.utcnow()
            for event in events:
                if event.get('impact') == 'High':
                    e_time = datetime.datetime.strptime(event['date'], "%Y-%m-%d %H:%M:%S")
                    diff = abs((maintenant - e_time).total_seconds() / 60)
                    if diff <= 30: return True
    except: pass
    return False

def prefixer_symbole(symbole_brut):
    if symbole_brut in CRYPTO_PAIRS: return f"cry{symbole_brut}"
    return f"frx{symbole_brut}"

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 250, "style": "candles", "granularity": granularite}
            ws.send(json.dumps(req))
            history = json.loads(ws.recv())
            ws.close()
            if "error" not in history and "candles" in history: return history['candles']
        except:
            time.sleep(1)
            continue
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 1, "style": "ticks"}
            ws.send(json.dumps(req))
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]: return float(res["history"]["prices"][0])
        except:
            time.sleep(1)
            continue
    return None

def verifier_correlation(symbole_base, action_visee):
    correlations = {"EURUSD": ("USDCHF", "INVERSE"), "GBPUSD": ("USDCHF", "INVERSE"), "AUDUSD": ("USDCAD", "INVERSE"), "USDCHF": ("EURUSD", "INVERSE"), "USDCAD": ("AUDUSD", "INVERSE")}
    if symbole_base not in correlations: return True 
    symbole_corr, type_corr = correlations[symbole_base]
    candles = obtenir_donnees_deriv(symbole_corr, 300)
    if not candles: return True 
    try:
        df_c = pd.DataFrame([{'close': float(c['close'])} for c in candles])
        c_recent_high = df_c['high'].iloc[-20:-1].max()
        c_recent_low = df_c['low'].iloc[-20:-1].min()
        c_prix = df_c['close'].iloc[-1]
        
        tendance_corr = "HAUSSE" if (c_prix - c_recent_low) > (c_recent_high - c_prix) else "BAISSE"
        action_simplifiee = "CALL" if "ACHAT" in action_visee else "PUT"
        
        if type_corr == "INVERSE":
            if action_simplifiee == "CALL" and tendance_corr == "HAUSSE": return False 
            if action_simplifiee == "PUT" and tendance_corr == "BAISSE": return False 
        return True 
    except: return True

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    if message.chat.id in trades_en_cours: return bot.send_message(message.chat.id, "⚠️ **SILENCE RADIO** : Combat en cours !")
    commande = message.text.split()
    if len(commande) < 2: return bot.send_message(message.chat.id, "⚠️ Précise la devise.")
    symbole = commande[1].upper()
    try: msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X (4 Piliers + Killswitch)...*", parse_mode="Markdown")
    except: return
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return bot.edit_message_text("⚠️ Impossible de scanner.", message.chat.id, msg.message_id)
    try:
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        df['volume_proxy'] = df['high'] - df['low']
        vol_moyen = df['volume_proxy'].rolling(window=10).mean().iloc[-1]
        vol_actuel = df['volume_proxy'].iloc[-1]
        etat_vol = "Actif 💥" if vol_actuel > vol_moyen else "Faible 💤"
        
        swing_high_1 = df['high'].iloc[-20:-10].max()
        swing_low_1 = df['low'].iloc[-20:-10].min()
        swing_high_2 = df['high'].iloc[-10:-1].max()
        swing_low_2 = df['low'].iloc[-10:-1].min()
        
        structure_haussiere = (swing_high_2 > swing_high_1) and (swing_low_2 >= swing_low_1)
        structure_baissiere = (swing_low_2 < swing_low_1) and (swing_high_2 <= swing_high_1)
        tendance = "Order Flow Hausse 🟢" if structure_haussiere else "Order Flow Baisse 🔴" if structure_baissiere else "Consolidation (Liquidity Build) ⚠️"

        rsi = ta.momentum.RSIIndicator(close=df['close']).rsi().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        rapport = f"👁️ **VISION RAYONS X : {nom_otc(symbole)}** 👁️\n──────────────────\n💰 **Prix :** `{prix_actuel:.5f}`\n🧱 **Structure :** `{tendance}`\n⛽ **Volume/Tick :** `{etat_vol}`\n📊 **RSI :** `{rsi:.2f}`\n──────────────────"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except: bot.edit_message_text("❌ Erreur d'analyse.", message.chat.id, msg.message_id)

# ==========================================
# MOTEUR DE TIR V18 (SIGNAL INSTANTANÉ & FLASH)
# ==========================================

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole:
        trades_en_cours[chat_id]['prix_entree'] = prix

def preparer_nouveau_palier(chat_id, symbole, action_brute, duree, palier):
    nom_paire = nom_otc(symbole)
    mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))
    exp_texte = f"{int(duree/60)} MIN" if duree >= 60 else f"{duree} SEC"
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    
    maintenant = datetime.datetime.now()
    sec_rest = 60 - maintenant.second
    if sec_rest < 15: sec_rest += 60 
    
    heure_entree = maintenant + datetime.timedelta(seconds=sec_rest)
    heure_texte = heure_entree.strftime("%H:%M:00")
    
    texte = f"🚨 **SIGNAL DE TIR : PALIER {palier}** 🚨\n"
    texte += f"──────────────────\n"
    texte += f"🌐 **ACTIF :** {nom_paire}\n"
    texte += f"⏱ **ENTRÉE EXACTE :** `{heure_texte}`\n"
    texte += f"👉 **ACTION :** {action_affichage}\n"
    texte += f"⏳ **DURÉE :** {exp_texte}\n"
    texte += f"💵 **MISE :** `{mise}$`\n"
    texte += f"──────────────────\n"
    texte += f"⏳ *Préparez le broker. L'IA enverra un flash pour valider le tir à la seconde 00.*"
    
    try: bot.send_message(chat_id, texte, parse_mode="Markdown")
    except: pass
    
    Timer(sec_rest, executer_tir_flash, args=[chat_id, symbole, action_brute, duree, palier]).start()

def executer_tir_flash(chat_id, symbole, action_brute, duree, palier):
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    nom_paire = nom_otc(symbole)
    
    if palier == 0:
        texte = f"👻 **LE FANTÔME EST LANCÉ ({nom_paire})** 👻\nL'IA observe le marché virtuellement..."
        markup = None
    else:
        texte = f"🔥 **TIR IMMÉDIAT : PALIER {palier} ({nom_paire})** 🔥\n👉 **CLIQUEZ SUR {action_affichage} MAINTENANT !**"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET", callback_data="force_win"))
        
    try: bot.send_message(chat_id, texte, parse_mode="Markdown", reply_markup=markup)
    except: pass
    
    trades_en_cours[chat_id] = {'symbole': symbole, 'action': action_brute, 'duree': duree}
    Timer(2, relever_prix_entree, args=[chat_id, symbole]).start()
    Timer(duree, verifier_resultat, args=[chat_id]).start()

def verifier_resultat(chat_id):
    global stats_journee, cooldown_actifs, niveaux_martingale
    time.sleep(3)
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'): return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie: return

    prix_entree = trade['prix_entree']
    action = trade['action']
    palier_actuel = niveaux_martingale.get(chat_id, 0)
    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = nom_otc(symbole)
    type_emoji = "🪙" if symbole in CRYPTO_PAIRS else "💱"

    if gagne:
        niveaux_martingale[chat_id] = 0 
        if palier_actuel == 0: texte = f"👻 **FANTÔME RÉUSSI (ITM)**\nLe trade virtuel sur {nom_paire} est passé sans nous.\n🔓 *Radar déverrouillé.*"
        else:
            texte = f"✅ **CIBLE ABATTUE (ITM)**\n🚀 {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n🔓 *Radar déverrouillé.*"
            stats_journee['ITM'] += 1
            stats_journee['details'].append(f"✅ {type_emoji} {nom_paire} ({action})")
            
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, texte, parse_mode="Markdown")
        except: pass
    else:
        if palier_actuel < MAX_MARTINGALE:
            # V18 : On vérifie l'état d'urgence avant de relancer la Martingale !
            candles_urgence = obtenir_donnees_deriv(symbole, trade['duree'])
            if candles_urgence:
                df_urg = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close'])} for c in candles_urgence])
                last_3 = df_urg.iloc[-3:]
                fusee_haussiere = all(row['close'] > row['open'] for _, row in last_3.iterrows())
                fusee_baissiere = all(row['close'] < row['open'] for _, row in last_3.iterrows())
                
                if (action == "PUT" and fusee_haussiere) or (action == "CALL" and fusee_baissiere):
                    niveaux_martingale[chat_id] = 0
                    msg_urgence = f"🛑 **KILLSWITCH ACTIVÉ (ANTI-FUSÉE)** 🛑\nLe marché a explosé contre nous de manière anormale sur {nom_paire}.\nMartingale annulée pour protéger le capital. Repli tactique immédiat."
                    stats_journee['OTM'] += 1
                    cooldown_actifs[symbole] = {'time': time.time(), 'action': action}
                    if chat_id in trades_en_cours: del trades_en_cours[chat_id]
                    try: bot.send_message(chat_id, msg_urgence, parse_mode="Markdown")
                    except: pass
                    return

            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id] 
            
            if palier_actuel == 0: 
                msg_fail = f"⚠️ **PIÈGE BROKER DÉTECTÉ (Fantôme Échoué)**\n📉 Sortie : `{prix_sortie}`\n\n⚡ *Génération instantanée du signal Palier 1...*"
            else: 
                msg_fail = f"⚠️ **TIR RATÉ (Palier {palier_actuel} Échoué)**\n📉 Sortie : `{prix_sortie}`\n\n⚡ *Génération instantanée du palier suivant...*"
                
            bot.send_message(chat_id, msg_fail, parse_mode="Markdown")
            preparer_nouveau_palier(chat_id, symbole, action, trade['duree'], palier_actuel + 1)
            
        else:
            niveaux_martingale[chat_id] = 0
            texte = f"🛑 **FIN DE SÉQUENCE ATTEINTE (OTM)**\n⚠️ {nom_paire} ({action})\n📉 Sortie : `{prix_sortie}`\nRepli tactique."
            if palier_actuel > 0: stats_journee['OTM'] += 1
            cooldown_actifs[symbole] = {'time': time.time(), 'action': action}
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
            try: bot.send_message(chat_id, texte, parse_mode="Markdown")
            except: pass

@bot.callback_query_handler(func=lambda c: c.data == "force_win")
def override_victoire_manuelle(call):
    chat_id = call.message.chat.id
    if chat_id in trades_en_cours:
        stats_journee['ITM'] += 1
        del trades_en_cours[chat_id]
    niveaux_martingale[chat_id] = 0
    bot.answer_callback_query(call.id, "✅ Victoire validée ! Le radar est libéré.", show_alert=True)
    try: bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except: pass
    bot.send_message(chat_id, "🔄 **CORRECTION MANUELLE APPLIQUÉE**", parse_mode="Markdown")

# ==========================================
# ✅ V18 NEW: QUATRE PILIERS D'ANALYSE INDÉPENDANTS
# ==========================================
# Remplace l'ancien moteur SMC (Order Block / discount-premium / avalement)
# par les 4 piliers demandés — chacun avec sa propre logique, chacun
# capable de produire un signal de son côté. Le meilleur (score le plus
# élevé) est retourné par timeframe testée, dans le même format que
# l'ancien moteur pour rester compatible avec tout le système Martingale/
# Killswitch/Fantôme (executer_tir_flash, verifier_resultat, etc.) —
# aucun de ces systèmes n'est modifié.

def calculer_aroon(df, period=9):
    high_idx = df['high'].rolling(period + 1).apply(lambda x: period - x.values.argmax(), raw=True)
    low_idx  = df['low'].rolling(period + 1).apply(lambda x: period - x.values.argmin(), raw=True)
    aroon_up   = ((period - high_idx) / period) * 100
    aroon_down = ((period - low_idx) / period) * 100
    return aroon_up, aroon_down

def calculer_stc(df, fast=14, slow=50, cycle=5, d1=3, d2=3):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    low_macd  = macd.rolling(cycle).min()
    high_macd = macd.rolling(cycle).max()
    k1 = 100 * (macd - low_macd) / (high_macd - low_macd).replace(0, 1e-9)
    d1_line = k1.ewm(span=d1, adjust=False).mean()
    low_d  = d1_line.rolling(cycle).min()
    high_d = d1_line.rolling(cycle).max()
    k2 = 100 * (d1_line - low_d) / (high_d - low_d).replace(0, 1e-9)
    stc = k2.ewm(span=d2, adjust=False).mean()
    return stc.clip(0, 100)

def calculer_donchian(df, period=20):
    upper = df['high'].rolling(period).max()
    lower = df['low'].rolling(period).min()
    return upper, lower


def analyser_aroon_rsi(df):
    """PILIER 1 — 'Show The Direction' (Aroon 9 + RSI 6)."""
    try:
        aroon_up, aroon_down = calculer_aroon(df, 9)
        rsi6 = ta.momentum.RSIIndicator(close=df['close'], window=6).rsi()

        au, ad = float(aroon_up.iloc[-2]), float(aroon_down.iloc[-2])
        au_prev, ad_prev = float(aroon_up.iloc[-3]), float(aroon_down.iloc[-3])
        rsi_val = float(rsi6.iloc[-2])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                s += min(35, max(0, (au - ad) * 0.5))
                if au_prev <= ad_prev and au > ad: s += 20; raisons.append("Croisement Aroon Up/Down")
                if 40 <= rsi_val <= 68: s += 20; raisons.append(f"RSI sain ({rsi_val:.1f})")
                if au >= 70: s += 15; raisons.append(f"Aroon Up fort ({au:.0f})")
            else:
                s += min(35, max(0, (ad - au) * 0.5))
                if ad_prev <= au_prev and ad > au: s += 20; raisons.append("Croisement Aroon Down/Up")
                if 32 <= rsi_val <= 60: s += 20; raisons.append(f"RSI sain ({rsi_val:.1f})")
                if ad >= 70: s += 15; raisons.append(f"Aroon Down fort ({ad:.0f})")
            return round(min(100, s), 1), raisons

        score_call, raisons_call = score("CALL")
        score_put, raisons_put = score("PUT")
        return {
            "nom": "AROON_RSI", "label": "Show The Direction",
            "score_call": score_call, "score_put": score_put,
            "raisons_call": raisons_call, "raisons_put": raisons_put,
            "details_txt": f"Aroon Up {au:.0f}/Down {ad:.0f} · RSI(6) {rsi_val:.1f}",
        }
    except Exception:
        return None

def analyser_adx_stc(df):
    """PILIER 2 — 'Identifies Reversal Points' (ADX 14 + Schaff Trend Cycle)."""
    try:
        adx_ind = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        adx = adx_ind.adx(); di_pos = adx_ind.adx_pos(); di_neg = adx_ind.adx_neg()
        stc = calculer_stc(df)

        adx_val = float(adx.iloc[-2])
        dip, din = float(di_pos.iloc[-2]), float(di_neg.iloc[-2])
        stc_val, stc_prev = float(stc.iloc[-2]), float(stc.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                if stc_prev <= 25 and stc_val > stc_prev: s += 35; raisons.append(f"STC remonte depuis zone basse ({stc_val:.0f})")
                elif stc_val < 40: s += 15
                if dip > din: s += 20; raisons.append("+DI > -DI")
                if adx_val >= 15: s += min(20, (adx_val - 15) * 1.2); raisons.append(f"ADX {adx_val:.0f}")
            else:
                if stc_prev >= 75 and stc_val < stc_prev: s += 35; raisons.append(f"STC redescend depuis zone haute ({stc_val:.0f})")
                elif stc_val > 60: s += 15
                if din > dip: s += 20; raisons.append("-DI > +DI")
                if adx_val >= 15: s += min(20, (adx_val - 15) * 1.2); raisons.append(f"ADX {adx_val:.0f}")
            return round(min(100, s), 1), raisons

        score_call, raisons_call = score("CALL")
        score_put, raisons_put = score("PUT")
        return {
            "nom": "ADX_STC", "label": "Identifies Reversal Points",
            "score_call": score_call, "score_put": score_put,
            "raisons_call": raisons_call, "raisons_put": raisons_put,
            "details_txt": f"STC {stc_val:.0f} · ADX {adx_val:.0f} (+DI {dip:.0f}/-DI {din:.0f})",
        }
    except Exception:
        return None

def analyser_cci_macd(df):
    """PILIER 3 — 'A Moment When...' (CCI 10 + MACD 10,25,5)."""
    try:
        cci = ta.trend.CCIIndicator(high=df['high'], low=df['low'], close=df['close'], window=10).cci()
        macd_ind = ta.trend.MACD(close=df['close'], window_slow=25, window_fast=10, window_sign=5)
        macd_hist = macd_ind.macd_diff()

        cci_val, cci_prev = float(cci.iloc[-2]), float(cci.iloc[-3])
        hist_val, hist_prev = float(macd_hist.iloc[-2]), float(macd_hist.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                if cci_prev <= -100 and cci_val > cci_prev: s += 30; raisons.append(f"CCI remonte depuis survente ({cci_val:.0f})")
                elif cci_val < -50: s += 12
                if hist_val > 0: s += 20; raisons.append("MACD histogram positif")
                if hist_val > hist_prev: s += 15; raisons.append("MACD histogram en hausse")
            else:
                if cci_prev >= 100 and cci_val < cci_prev: s += 30; raisons.append(f"CCI redescend depuis surachat ({cci_val:.0f})")
                elif cci_val > 50: s += 12
                if hist_val < 0: s += 20; raisons.append("MACD histogram négatif")
                if hist_val < hist_prev: s += 15; raisons.append("MACD histogram en baisse")
            return round(min(100, s), 1), raisons

        score_call, raisons_call = score("CALL")
        score_put, raisons_put = score("PUT")
        return {
            "nom": "CCI_MACD", "label": "A Moment When...",
            "score_call": score_call, "score_put": score_put,
            "raisons_call": raisons_call, "raisons_put": raisons_put,
            "details_txt": f"CCI(10) {cci_val:.0f} · MACD hist {hist_val:.5f}",
        }
    except Exception:
        return None

def analyser_donchian_cci(df):
    """PILIER 4 — 'You Know And...' (Donchian Channel 20 + CCI 11)."""
    try:
        upper, lower = calculer_donchian(df, 20)
        cci = ta.trend.CCIIndicator(high=df['high'], low=df['low'], close=df['close'], window=11).cci()

        px = float(df['close'].iloc[-2])
        up_val, low_val = float(upper.iloc[-2]), float(lower.iloc[-2])
        largeur = up_val - low_val if (up_val - low_val) > 0 else 1e-9
        position_pct = (px - low_val) / largeur

        cci_val, cci_prev = float(cci.iloc[-2]), float(cci.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                proximite = max(0, 1 - position_pct * 2.5)
                s += proximite * 35
                if proximite > 0.5: raisons.append(f"Prix proche du bas du canal ({position_pct*100:.0f}%)")
                if cci_prev <= -100 and cci_val > cci_prev: s += 30; raisons.append(f"CCI remonte depuis survente ({cci_val:.0f})")
                elif cci_val < -30: s += 12
            else:
                proximite = max(0, (position_pct - 0.6) * 2.5)
                s += proximite * 35
                if proximite > 0.5: raisons.append(f"Prix proche du haut du canal ({position_pct*100:.0f}%)")
                if cci_prev >= 100 and cci_val < cci_prev: s += 30; raisons.append(f"CCI redescend depuis surachat ({cci_val:.0f})")
                elif cci_val > 30: s += 12
            return round(min(100, s), 1), raisons

        score_call, raisons_call = score("CALL")
        score_put, raisons_put = score("PUT")
        return {
            "nom": "DONCHIAN_CCI", "label": "You Know And...",
            "score_call": score_call, "score_put": score_put,
            "raisons_call": raisons_call, "raisons_put": raisons_put,
            "details_txt": f"Position canal {position_pct*100:.0f}% · CCI(11) {cci_val:.0f}",
        }
    except Exception:
        return None


SEUIL_SIGNAL_PILIER = 45  # sur ~100 max par pilier — ajustable directement ici

# ==========================================
# MOTEUR ULTIMATE V18 (4 PILIERS + KILLSWITCH)
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    """
    ✅ V18: le cœur SMC (Order Block/discount-premium/avalement) est
    remplacé par les 4 piliers indépendants. Tout le reste (filtre news,
    filtre anti-chaos, killswitch anti-fusée, corrélation, cooldown
    anti-fakeout, exception 10/10, Martingale) est CONSERVÉ à l'identique
    — ces protections sont génériques et s'appliquent quel que soit le
    pilier qui a déclenché le signal.
    """
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE NEWS : Marché manipulé.", None, None, None, None, None, None, None

    timeframes = [600, 300, 120] if mode == "STANDARD" else [60]

    for tf in timeframes:
        candles = obtenir_donnees_deriv(symbole, tf)
        if not candles or len(candles) < 60: continue

        try:
            df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
            df['corps_bougie'] = abs(df['close'] - df['open'])
            df['taille_bougie'] = df['high'] - df['low']

            # 🛡️ FILTRE ANTI-CHAOS (conservé à l'identique)
            avg_taille = df['taille_bougie'].iloc[-4:-1].mean()
            avg_corps = df['corps_bougie'].iloc[-4:-1].mean()
            if avg_corps > 0 and (avg_taille > avg_corps * 3.5):
                return "⚠️ Filtre Anti-Chaos activé (Marché Hache-Viande).", None, None, None, None, None, None, None

            last, prev, p_prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]

            last_is_green = last['close'] > last['open']
            last_is_red = last['close'] < last['open']
            prev_is_green = prev['close'] > prev['open']
            prev_is_red = prev['close'] < prev['open']
            vrai_corps = last['corps_bougie'] > (last['taille_bougie'] * 0.25)

            # 🛡️ KILLSWITCH ANTI-FUSÉE (conservé à l'identique) — ne jamais
            # entrer À CONTRE-SENS d'une impulsion à 3 bougies pleines.
            fusee_haussiere = last_is_green and prev_is_green and (p_prev['close'] > p_prev['open']) and vrai_corps
            fusee_baissiere = last_is_red and prev_is_red and (p_prev['close'] < p_prev['open']) and vrai_corps

            # ── Exécution des 4 piliers indépendants sur cette timeframe ──
            resultats_piliers = []
            for fn in (analyser_aroon_rsi, analyser_adx_stc, analyser_cci_macd, analyser_donchian_cci):
                r = fn(df)
                if r: resultats_piliers.append(r)

            candidats = []
            for r in resultats_piliers:
                meilleur = max(r["score_call"], r["score_put"])
                if meilleur < SEUIL_SIGNAL_PILIER: continue
                direction = "CALL" if r["score_call"] >= r["score_put"] else "PUT"

                # Veto killswitch : jamais à contre-sens d'une fusée 3 bougies
                if direction == "CALL" and fusee_baissiere: continue
                if direction == "PUT" and fusee_haussiere: continue

                raisons = r["raisons_call"] if direction == "CALL" else r["raisons_put"]
                candidats.append({
                    "pilier": r["nom"], "label": r["label"], "direction": direction,
                    "score": meilleur, "raisons": raisons, "details_txt": r["details_txt"],
                })

            if not candidats:
                continue  # rien de valable sur cette timeframe, on essaie la suivante

            meilleur_candidat = max(candidats, key=lambda x: x["score"])

            direction = meilleur_candidat["direction"]
            action = "🟢 ACHAT (CALL)" if direction == "CALL" else "🔴 VENTE (PUT)"
            # Score pilier (0-100) -> échelle score_algo (5.0-10.0) pour
            # rester compatible avec la logique "Exception 10/10" existante.
            score_algo = round(5 + (meilleur_candidat["score"] / 100) * 5, 1)
            confiance = min(99, 70 + int(meilleur_candidat["score"] * 0.29))
            raisons_txt = " · ".join(meilleur_candidat["raisons"][:2]) if meilleur_candidat["raisons"] else ""
            bb_status = f"🧩 {meilleur_candidat['label']} — {meilleur_candidat['details_txt']}" + (f" ({raisons_txt})" if raisons_txt else "")

            rsi_val = round(float(ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-2]), 1)
            stoch_val = round(float(ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close']).stoch().iloc[-2]), 1)

            if mode == "STANDARD":
                if tf == 300:
                    duree_secondes, exp_texte = 180, "3 MIN (HIT & RUN ⚡)"
                else:
                    duree_secondes, exp_texte = tf, f"{int(tf/60)} MIN"
            else:
                duree_secondes, exp_texte = 60, "1 MINUTE (SCALP 🛡️)"

            if not verifier_correlation(symbole, action):
                return f"⚠️ **FAKEOUT DÉTECTÉ**", None, None, None, None, None, None, None

            action_simplifiee = direction
            delai_blocage = 600 if mode == "SCALP" else 1800
            if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole]['time'] < delai_blocage):
                if action_simplifiee == cooldown_actifs[symbole]['action']:
                    return f"⚠️ **BLOCAGE ANTI-FAKEOUT**", None, None, None, None, None, None, None

            return action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo

        except Exception:
            continue

    return f"⚠️ En attente d'une opportunité ({mode}).", None, None, None, None, None, None, None

# ==========================================
# LA GESTION DES SIGNAUX & DESIGN PREMIUM
# ==========================================

def obtenir_clavier(user_id):
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    btn_mode = "🛡️ MODE: 4 PILIERS STANDARD" if mode_actuel == "STANDARD" else "🔥 MODE: 4 PILIERS SCALP"
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton(btn_mode), KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Silence Radio actif.")
        
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    if mode_actuel == "STANDARD":
        mode_trading[user_id] = "SCALP"
        bot.send_message(user_id, "🔥 **MODE 4 PILIERS SCALPING (1 MIN) ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE 4 PILIERS STANDARD ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    texte = """🏴‍☠️ **TERMINAL PRIME - V18 ULTIMATE 🛑** 🔥

Moteur remplacé par **4 piliers d'analyse indépendants** :
🧩 Show The Direction — Aroon(9) + RSI(6)
🧩 Identifies Reversal Points — ADX(14) + Schaff Trend Cycle
🧩 A Moment When... — CCI(10) + MACD(10,25,5)
🧩 You Know And... — Donchian Channel(20) + CCI(11)

🛑 **KILLSWITCH ANTI-FUSÉE** toujours actif : l'algorithme annule le tir et désactive la Martingale s'il détecte une explosion directionnelle incontrôlable du marché (3 bougies pleines). Protégez votre capital."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours:
        bot.answer_callback_query(call.id, f"⚠️ Focus activé !", show_alert=True)
        return
    
    actif = call.data.replace("set_", "")
    
    statut, msg_erreur = est_symbole_autorise(actif)
    if statut == "BLOCAGE_TOTAL":
        bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
        return
        
    user_prefs[call.from_user.id] = actif
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    nom_affiche = nom_otc(actif)
    
    try: msg = bot.send_message(chat_id, f"⏳ *Initialisation Scanner 4 Piliers...*", parse_mode="Markdown")
    except: return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif, mode_actuel)
    
    if statut == "HORS_SESSION":
        if score is None or score < 10.0:
            try: bot.edit_message_text(f"{msg_erreur}\n\n*(Le setup n'est pas un 10/10 parfait pour forcer l'entrée)*", chat_id, msg.message_id, parse_mode="Markdown")
            except: pass
            return
    
    if not action or "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return

    maintenant = datetime.datetime.now()
    sec_rest = (60 - maintenant.second)
    if mode_actuel == "SCALP" and sec_rest < 45: sec_rest += 60 
    elif mode_actuel == "STANDARD" and sec_rest < 15: sec_rest += 60
        
    palier = niveaux_martingale.get(chat_id, 0)
    
    if palier == 0 and score is not None and score >= 10.0:
        palier = 1 
        niveaux_martingale[chat_id] = 1 
        sec_rest += 60 
        if statut == "HORS_SESSION": 
            fantome_texte = "👑 **EXCEPTION 10/10 HORS SESSION !**\n*Confluence parfaite, on attaque en réel direct !*"
        else: 
            fantome_texte = "🧠 **FANTÔME DÉSACTIVÉ PAR L'IA (10/10)**\n*Confluence parfaite, on attaque en réel direct !*"
    elif palier == 0:
        fantome_texte = "*Le bot prend ce trade virtuellement (Fantôme). NE RENTREZ PAS.*"
    else:
        fantome_texte = ""

    heure_entree_p0 = maintenant + datetime.timedelta(seconds=sec_rest)
    str_p0 = heure_entree_p0.strftime("%H:%M:00")

    mise_calculee = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** (palier - 1 if palier > 0 else 0)))

    if palier == 0:
        signal = f"""👻 **MODE FANTÔME (PALIER 0)** 👻
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_p0}`
👉 **ACTION :** {action}
⏳ **DURÉE :** {exp_texte}

{fantome_texte}
──────────────────
*(Si échec, le bot générera instantanément le signal Palier 1)*"""
    else:
        signal = f"""🚨 **ALERTE DE TIR RÉEL VIP 💎** 🚨
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_p0}`
⏳ **EXPIRATION :** {exp_texte}
👉 **ACTION :** {action}
🛡️ {bb_status}

{fantome_texte if fantome_texte else ''}
💵 **MISE CALCULÉE :** `{mise_calculee}$`
*(Statut : Palier {palier})*"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except: pass

    action_brute = "CALL" if "ACHAT" in action else "PUT"
    Timer(sec_rest, executer_tir_flash, args=[chat_id, actif, action_brute, duree_secondes, palier]).start()

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **GUIDE DES HORAIRES (Verrouillage IA Actif)** 🕒
    
✅ **Session Asiatique (00h00 - 08h00) :** JPY, AUD, CAD, CHF
🇪🇺 **Session Europe (07h00 - 12h00) :** EUR, USD, CHF
🔥 **Zone de Guerre (12h00 - 17h30) :** EUR/USD, AUD/USD, USD/CAD
🛑 **Repli Tactique (17h30 - 00h00) :** Le Forex est bloqué.
🪙 **Week-end (Ven 21h - Dim 21h) :** EXCLUSIVEMENT pour les Cryptos (bloquées la semaine).

⚠️ *Même si Pocket Option affiche ces paires en OTC 24/7, la source de données (Deriv) reflète le vrai marché — fermé le week-end pour le Forex. Le blocage protège contre des signaux basés sur des prix gelés.*

*(Bilan Automatique à 18h00 GMT)*"""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD"),
        InlineKeyboardButton("🇦🇺 AUD/USD OTC", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY OTC", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY OTC", callback_data="set_CHFJPY"),
        InlineKeyboardButton("🇪🇺 EUR/JPY OTC", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD OTC", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY OTC", callback_data="set_AUDJPY"),
        InlineKeyboardButton("🇪🇺 EUR/AUD OTC", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD OTC", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD OTC", callback_data="set_AUDCAD"),
        InlineKeyboardButton("🇺🇸 USD/CHF OTC", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF OTC", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF OTC", callback_data="set_EURCHF"),
        InlineKeyboardButton("🇯🇵 USD/JPY OTC", callback_data="set_USDJPY")
    )
    bot.send_message(message.chat.id, "Sélectionne ta cible (paires affichées façon Pocket Option OTC — l'IA bloquera les devises fermées côté données réelles) :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours: return bot.send_message(chat_id, f"⚠️ Combat en cours sur **{nom_otc(trades_en_cours[chat_id]['symbole'])}**.", parse_mode="Markdown")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    
    statut, msg_erreur = est_symbole_autorise(actif)
    if statut == "BLOCAGE_TOTAL": return bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
        
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            utilisateurs_libres = [uid for uid in utilisateurs_actifs if est_autorise(uid) and uid not in trades_en_cours]
            if not utilisateurs_libres: continue
                
            for paire in CRYPTO_PAIRS + FOREX_PAIRS:
                statut, _ = est_symbole_autorise(paire)
                if statut == "BLOCAGE_TOTAL": continue
                    
                for mode in ["STANDARD", "SCALP"]:
                    delai_repos = 300 if mode == "STANDARD" else 120
                    cle_memoire = f"{paire}_{mode}"
                    if cle_memoire in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle_memoire] < delai_repos): continue
                        
                    action, conf, exp, dur, rsi, stoch, bb, sc = analyser_binaire_pro(paire, mode)
                    
                    if action and "⚠️" not in action:
                        if statut == "HORS_SESSION" and (sc is None or sc < 10.0): continue
                            
                        derniere_alerte_auto[cle_memoire] = time.time()
                        nom_affiche = nom_otc(paire)
                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {nom_affiche}" if mode == "SCALP" else f"📊 Verrouiller {nom_affiche}", callback_data=f"set_{paire}"))
                        
                        prefixe = "👑 **EXCEPTION HORS SESSION** 👑\n" if statut == "HORS_SESSION" else ""
                        for uid in utilisateurs_libres:
                            if mode_trading.get(uid, "STANDARD") == mode:
                                msg = f"{prefixe}🔔 **CHASSE AUX STOPS : {nom_affiche}**\n👉 Dégaine !" if mode == "SCALP" else f"{prefixe}🔔 **SIGNAL {exp} : {nom_affiche}**"
                                try: bot.send_message(uid, msg, reply_markup=markup)
                                except: pass
        except Exception as e: pass

# ==========================================
# TÂCHE PLANIFIÉE : BILAN À 18H00 GMT
# ==========================================

def gestionnaire_bilan():
    global stats_journee
    bilan_envoye_aujourdhui = False
    
    while True:
        try:
            now = datetime.datetime.utcnow()
            
            if now.hour == 18 and now.minute == 0:
                if not bilan_envoye_aujourdhui:
                    total_trades = stats_journee['ITM'] + stats_journee['OTM']
                    winrate = (stats_journee['ITM'] / total_trades * 100) if total_trades > 0 else 0
                    
                    texte_bilan = f"📊 **BILAN JOURNALIER (18h00 GMT)** 📊\n"
                    texte_bilan += f"──────────────────\n"
                    texte_bilan += f"✅ **CIBLES ABATTUES (ITM) :** {stats_journee['ITM']}\n"
                    texte_bilan += f"❌ **TIRS RATÉS (OTM) :** {stats_journee['OTM']}\n"
                    texte_bilan += f"🎯 **TAUX DE RÉUSSITE :** {winrate:.1f}%\n"
                    texte_bilan += f"──────────────────\n"
                    texte_bilan += f"*Nettoyage des serveurs. Prêt pour la Session Asiatique de Minuit.*"
                    
                    for uid in utilisateurs_actifs:
                        if est_autorise(uid):
                            try: bot.send_message(uid, texte_bilan, parse_mode="Markdown")
                            except: pass
                    
                    stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}
                    bilan_envoye_aujourdhui = True
            
            elif now.hour == 18 and now.minute > 5:
                bilan_envoye_aujourdhui = False
                
        except Exception as e:
            pass
        time.sleep(30)

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestionnaire_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition V18 — 4 Piliers + Killswitch Démarrée.", flush=True)
    bot.infinity_polling()
