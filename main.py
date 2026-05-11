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

TELEGRAM_TOKEN = "8658287331:AAHYtXPxfBBqkYGfiRpSyXjoWliMFOSV4t4"
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

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V16.9 ULTIMATE (Crypto Exclusivement Week-end)"

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

def generer_jauge(pourcentage):
    if pourcentage >= 99: return "[██████████] 👑 MAX"
    pleins = int(pourcentage / 10)
    vides = 10 - pleins
    return f"[{'█' * pleins}{'░' * vides}] {pourcentage}%"

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
# VERROUILLAGE TEMPOREL & EXCEPTION 10/10 (V16.9)
# ==========================================

def est_symbole_autorise(symbole):
    """
    Retourne (statut, message).
    statut = "AUTORISE", "HORS_SESSION", "BLOCAGE_TOTAL"
    """
    now = datetime.datetime.utcnow()
    jour = now.weekday()
    heure = now.hour
    minute = now.minute
    heure_dec = heure + (minute / 60.0)

    est_week_end = False
    if jour == 4 and heure_dec >= 21.0: est_week_end = True
    elif jour == 5: est_week_end = True
    elif jour == 6 and heure_dec < 21.0: est_week_end = True

    # 1. GESTION DU WEEK-END
    if est_week_end:
        if symbole in CRYPTO_PAIRS:
            return "AUTORISE", ""
        else:
            return "BLOCAGE_TOTAL", f"🔒 **ACCÈS REFUSÉ** : Le marché Forex est fermé (OTC manipulé). Seules les cryptos sont autorisées le week-end."

    # 2. SEMAINE - LES CRYPTOS SONT STRICTEMENT INTERDITES
    if symbole in CRYPTO_PAIRS:
        return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Les Cryptomonnaies sont verrouillées la semaine. Elles sont réservées exclusivement pour le week-end."

    # 3. SEMAINE - COUVRE FEU (17h30 à 00h00 GMT)
    if heure_dec >= 17.5:
        return "HORS_SESSION", f"🛑 **REPLI TACTIQUE** : Couvre-feu en cours (17h30 - 00h00 GMT)."

    # 4. SEMAINE - ASIE (00h00 à 08h00 GMT)
    if heure_dec >= 0.0 and heure_dec < 8.0:
        paires_autorisees = ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"]
        if symbole in paires_autorisees: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Session Asiatique."

    # 5. SEMAINE - EUROPE (07h00 à 12h00 GMT)
    if heure_dec >= 7.0 and heure_dec < 12.0:
        paires_autorisees = ["EURUSD", "EURJPY", "EURAUD", "EURCHF", "USDCHF", "CADCHF"]
        if heure_dec < 8.0: 
            paires_autorisees.extend(["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"])
            
        if symbole in paires_autorisees: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Session Européenne."

    # 6. SEMAINE - ZONE DE GUERRE US/CA (12h00 à 17h30 GMT)
    if heure_dec >= 12.0 and heure_dec < 17.5:
        paires_autorisees = ["EURUSD", "USDCAD", "AUDUSD"]
        if symbole in paires_autorisees: return "AUTORISE", ""
        return "HORS_SESSION", f"🔒 **ACCÈS REFUSÉ** : Hors Zone de Guerre US/CA."

    return "BLOCAGE_TOTAL", "🛑 Erreur temporelle inconnue."

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
        ema200_c = ta.trend.EMAIndicator(close=df_c['close'], window=200).ema_indicator().iloc[-1]
        prix_c = df_c['close'].iloc[-1]
        tendance_corr = "HAUSSE" if prix_c > ema200_c else "BAISSE"
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
    try: msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X...*", parse_mode="Markdown")
    except: return
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return bot.edit_message_text("⚠️ Impossible de scanner.", message.chat.id, msg.message_id)
    try:
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        df['volume_proxy'] = df['high'] - df['low']
        vol_moyen = df['volume_proxy'].rolling(window=10).mean().iloc[-1]
        vol_actuel = df['volume_proxy'].iloc[-1]
        etat_vol = "Actif 💥" if vol_actuel > vol_moyen else "Faible 💤"
        ema_200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close']).rsi().iloc[-1]
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close']).stoch().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        rapport = f"👁️ **VISION RAYONS X : {symbole}** 👁️\n──────────────────\n💰 **Prix :** `{prix_actuel:.5f}`\n🛡️ **Tendance (EMA 200) :** `{'Hausse 🟢' if prix_actuel > ema_200 else 'Baisse 🔴'}`\n⛽ **Volume/Tick :** `{etat_vol}`\n📊 **RSI :** `{rsi:.2f}`\n📉 **Stochastique :** `{stoch_k:.2f}`\n──────────────────"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except: bot.edit_message_text("❌ Erreur d'analyse.", message.chat.id, msg.message_id)

# ==========================================
# MOTEUR SYNCHRONISÉ MARTINGALE
# ==========================================

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole:
        trades_en_cours[chat_id]['prix_entree'] = prix

def lancer_nouveau_palier(chat_id, symbole, action_brute, duree, palier):
    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))
    exp_texte = f"{int(duree/60)} MIN" if duree >= 60 else f"{duree} SEC"
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    heure_actuelle = datetime.datetime.now().strftime("%H:%M:%S")
    
    texte = f"🚨 **TIR IMMÉDIAT : PALIER {palier}** 🚨\n──────────────────\n🌐 **ACTIF :** {nom_paire}\n⏱ **HEURE EXACTE :** `{heure_actuelle}`\n👉 **ACTION :** {action_affichage}\n⏳ **DURÉE :** {exp_texte}\n💵 **MISE :** `{mise}$`\n──────────────────\n🔥 **CLIQUEZ SUR {action_affichage} MAINTENANT !**"
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET (Reprendre)", callback_data="force_win"))
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
    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
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
            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id] 
            
            mode = mode_trading.get(chat_id, "STANDARD")
            temps_pause = 60 if mode == "SCALP" else 0
            
            if palier_actuel == 0: msg_fail = f"⚠️ **PIÈGE BROKER DÉTECTÉ (Fantôme Échoué)**\n📉 Sortie : `{prix_sortie}`"
            else: msg_fail = f"⚠️ **TIR RATÉ (Palier {palier_actuel} Échoué)**\n📉 Sortie : `{prix_sortie}`"

            if temps_pause > 0:
                msg_fail += f"\n\n⏳ *Pause stratégique de 60s avant le signal du Palier {palier_actuel + 1}...*"
                bot.send_message(chat_id, msg_fail, parse_mode="Markdown")
                Timer(temps_pause, lancer_nouveau_palier, args=[chat_id, symbole, action, trade['duree'], palier_actuel + 1]).start()
            else:
                bot.send_message(chat_id, msg_fail, parse_mode="Markdown")
                lancer_nouveau_palier(chat_id, symbole, action, trade['duree'], palier_actuel + 1)
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
# MOTEUR ULTIMATE V16.9 (MTFA + VOLUME + PRICE ACTION)
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return "⚠️ ALERTE NEWS : Marché manipulé.", None, None, None, None, None, None, None

    timeframes = [600, 300, 120] if mode == "STANDARD" else [60]
    
    for tf in timeframes:
        candles = obtenir_donnees_deriv(symbole, tf)
        if not candles: continue
        
        try:
            df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
            df['corps_bougie'] = abs(df['close'] - df['open'])
            df['taille_bougie'] = df['high'] - df['low']
            df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
            df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']
            
            df['volume_proxy'] = df['high'] - df['low']
            df['volume_moyen'] = df['volume_proxy'].rolling(window=10).mean()
            volume_ok = df['volume_proxy'].iloc[-1] > df['volume_moyen'].iloc[-1]

            avg_taille = df['taille_bougie'].iloc[-4:-1].mean()
            avg_corps = df['corps_bougie'].iloc[-4:-1].mean()
            if avg_corps > 0 and (avg_taille > avg_corps * 3.5):
                return "⚠️ Filtre Anti-Chaos activé (Marché Hache-Viande).", None, None, None, None, None, None, None

            df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
            df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close']).stoch()
            
            last, prev, p_prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            c = last['close']
            rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
            action, confiance, bb_status, score_algo = None, 0, "En Attente", 5
            
            last_is_green = last['close'] > last['open']
            last_is_red = last['close'] < last['open']
            prev_is_green = prev['close'] > prev['open']
            prev_is_red = prev['close'] < prev['open']
            
            rejet_haussier = last['meche_basse'] > (last['corps_bougie'] * 2.0)
            rejet_baissier = last['meche_haute'] > (last['corps_bougie'] * 2.0)
            avalement_haussier = prev_is_red and last_is_green and (last['close'] > prev['open']) and (last['open'] <= prev['close'])
            avalement_baissier = prev_is_green and last_is_red and (last['close'] < prev['open']) and (last['open'] >= prev['close'])
            doji = last['corps_bougie'] <= (last['taille_bougie'] * 0.1)
            harami_bull = prev_is_red and last_is_green and (last['open'] > prev['close']) and (last['close'] < prev['open'])
            harami_bear = prev_is_green and last_is_red and (last['open'] < prev['close']) and (last['close'] > prev['open'])
            
            if mode == "STANDARD":
                indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
                df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
                
                tendance_haussiere = c > df['ema_200'].iloc[-1]
                tendance_baissiere = c < df['ema_200'].iloc[-1]
                duree_secondes = tf
                exp_texte = f"{int(tf/60)} MIN"
                
                if tendance_haussiere and volume_ok and (stoch_val < 35) and (rsi_val > 45):
                    action, confiance, score_algo = "🟢 ACHAT (CALL)", 85, 8.0
                    bb_status = f"🎯 Pullback {exp_texte} + Volume OK"
                    if avalement_haussier or rejet_haussier or harami_bull or (doji and p_prev['close'] > p_prev['open']):
                        score_algo, confiance, bb_status = 10.0, 99, f"👑 SETUP ULTIME 10/10 🚀"
                        
                elif tendance_baissiere and volume_ok and (stoch_val > 65) and (rsi_val < 55):
                    action, confiance, score_algo = "🔴 VENTE (PUT)", 85, 8.0
                    bb_status = f"🎯 Pullback {exp_texte} + Volume OK"
                    if avalement_baissier or rejet_baissier or harami_bear or (doji and p_prev['close'] < p_prev['open']):
                        score_algo, confiance, bb_status = 10.0, 99, f"👑 SETUP ULTIME 10/10 ☄️"

            elif mode == "SCALP":
                indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2)
                bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
                df['bb_width'] = indicateur_bb.bollinger_wband()
                squeeze = df['bb_width'].iloc[-1] < (df['bb_width'].rolling(window=20).mean().iloc[-1] * 0.8)

                df['ema_50'] = ta.trend.EMAIndicator(close=df['close'], window=50).ema_indicator()
                ema_50 = df['ema_50'].iloc[-1]
                duree_secondes, exp_texte = 60, "1 MINUTE (SCALP 🛡️)"
                
                if not squeeze and volume_ok:
                    if (last['low'] <= bb_basse) and (rsi_val < 30) and (c > ema_50) and rejet_haussier:
                        action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 95, 9.5, "🛡️ Rejet Bas + Volume"
                    elif (last['high'] >= bb_haute) and (rsi_val > 70) and (c < ema_50) and rejet_baissier:
                        action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 95, 9.5, "🛡️ Rejet Haut + Volume"

            if action:
                if not verifier_correlation(symbole, action):
                    return f"⚠️ **FAKEOUT DÉTECTÉ**", None, None, None, None, None, None, None

                action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
                delai_blocage = 600 if mode == "SCALP" else 1800
                if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole]['time'] < delai_blocage):
                    if action_simplifiee == cooldown_actifs[symbole]['action']:
                        return f"⚠️ **BLOCAGE ANTI-FAKEOUT**", None, None, None, None, None, None, None
                return action, min(confiance, 99), exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo
                
        except: continue

    return f"⚠️ En attente d'une opportunité ({mode}).", None, None, None, None, None, None, None

# ==========================================
# LA GESTION DES SIGNAUX & DESIGN PREMIUM
# ==========================================

def obtenir_clavier(user_id):
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    btn_mode = "🛡️ MODE: STANDARD" if mode_actuel == "STANDARD" else "🔥 MODE: SCALP"
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
        bot.send_message(user_id, "🔥 **MODE SCALPING 1 MINUTE ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE STANDARD MTFA ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    texte = """🏴‍☠️ **TERMINAL PRIME - V16.9 ULTIMATE** 🔥
    
Modules activés : Exception du Sniper & Verrouillage Temporel. 
🪙 *Les Cryptomonnaies sont verrouillées la semaine et actives uniquement le week-end.*"""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours:
        bot.answer_callback_query(call.id, f"⚠️ Focus activé !", show_alert=True)
        return
    
    actif = call.data.replace("set_", "")
    
    # 🔒 VÉRIFICATION DU STATUT TEMPOREL
    statut, msg_erreur = est_symbole_autorise(actif)
    
    # Si le week-end est fermé totalement (OTC) OU Crypto tentée la semaine
    if statut == "BLOCAGE_TOTAL":
        bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
        return
        
    user_prefs[call.from_user.id] = actif
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    nom_affiche = f"{actif[:3]}/{actif[3:]}"
    
    try: msg = bot.send_message(chat_id, f"⏳ *Initialisation Scanner...*", parse_mode="Markdown")
    except: return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif, mode_actuel)
    
    # EXCEPTION DU SNIPER : Si la session est mauvaise mais qu'on n'a PAS de 10/10
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
        
    heure_entree_p0 = maintenant + datetime.timedelta(seconds=sec_rest)
    fmt = "%H:%M:%S"
    
    palier = niveaux_martingale.get(chat_id, 0)
    
    if palier == 0 and score >= 10.0:
        palier = 1 
        niveaux_martingale[chat_id] = 1 
        if statut == "HORS_SESSION":
            fantome_texte = "👑 **EXCEPTION 10/10 HORS SESSION !**\nSetup parfait validé, on attaque en réel direct malgré l'horaire !"
        else:
            fantome_texte = "🧠 **FANTÔME DÉSACTIVÉ PAR L'IA**\nSetup parfait validé, on attaque en réel direct !"
    else:
        fantome_texte = "*Le bot prend ce trade virtuellement (Fantôme). NE RENTREZ PAS.*"

    mise_calculee = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** (palier - 1 if palier > 0 else 0)))

    str_p0 = heure_entree_p0.strftime(fmt)

    if palier == 0:
        signal = f"""👻 **MODE FANTÔME (PALIER 0)** 👻
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **HEURE EXACTE :** `{str_p0}`
👉 **ACTION :** {action}
⏳ **DURÉE :** {exp_texte}

{fantome_texte}
──────────────────
*(Si échec, le signal complet du Palier 1 arrivera automatiquement)*"""
    else:
        signal = f"""🚨 **ALERTE DE TIR RÉEL VIP** 🚨
──────────────────
🌐 **ACTIF :** {nom_affiche}
⏱ **ENTRÉE EXACTE :** `{str_p0}`
⏳ **EXPIRATION :** {exp_texte}
👉 **ACTION :** {action}
🛡️ {bb_status}

{fantome_texte if 'EXCEPTION' in fantome_texte else ''}
💵 **MISE CALCULÉE :** `{mise_calculee}$`
*(Statut : Palier {palier})*"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except: pass

    trades_en_cours[chat_id] = {'symbole': actif, 'action': "CALL" if "ACHAT" in action else "PUT", 'duree': duree_secondes}
    Timer(sec_rest, relever_prix_entree, args=[chat_id, actif]).start()
    Timer(sec_rest + duree_secondes, verifier_resultat, args=[chat_id]).start()

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **GUIDE DES HORAIRES (Verrouillage IA Actif)** 🕒
    
✅ **Session Asiatique (00h00 - 08h00) :** JPY, AUD, CAD, CHF
🇪🇺 **Session Europe (07h00 - 12h00) :** EUR, USD, CHF
🔥 **Zone de Guerre (12h00 - 17h30) :** EUR/USD, AUD/USD, USD/CAD
🛑 **Repli Tactique (17h30 - 00h00) :** Le Forex est bloqué.
🪙 **Week-end (Ven 21h - Dim 21h) :** EXCLUSIVEMENT pour les Cryptos (bloquées la semaine).

*(Note : Si le bot repère un setup parfait à 10/10 hors de sa session Forex, il forcera l'alerte !)*"""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    
    markup.add(
        InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD"),
        InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
        InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
        InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
        InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"),
        InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
    )
    bot.send_message(message.chat.id, "Sélectionne ta cible (L'IA bloquera les devises fermées) :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours: return bot.send_message(chat_id, f"⚠️ Combat en cours sur **{trades_en_cours[chat_id]['symbole']}**.", parse_mode="Markdown")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    
    # Validation initiale du bouton Lancer
    statut, msg_erreur = est_symbole_autorise(actif)
    if statut == "BLOCAGE_TOTAL": 
        return bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
        
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            utilisateurs_libres = [uid for uid in utilisateurs_actifs if est_autorise(uid) and uid not in trades_en_cours]
            if not utilisateurs_libres: continue
                
            for paire in CRYPTO_PAIRS + FOREX_PAIRS:
                statut, _ = est_symbole_autorise(paire)
                
                # Le scanner ignore complètement les paires en BLOCAGE_TOTAL (OTC ou Cryptos la semaine)
                if statut == "BLOCAGE_TOTAL": continue
                    
                for mode in ["STANDARD", "SCALP"]:
                    delai_repos = 300 if mode == "STANDARD" else 120
                    cle_memoire = f"{paire}_{mode}"
                    if cle_memoire in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle_memoire] < delai_repos): continue
                        
                    action, conf, exp, dur, rsi, stoch, bb, sc = analyser_binaire_pro(paire, mode)
                    
                    if action and "⚠️" not in action:
                        # Si hors session, on exige un score parfait de 10/10 pour alerter
                        if statut == "HORS_SESSION" and (sc is None or sc < 10.0):
                            continue
                            
                        derniere_alerte_auto[cle_memoire] = time.time()
                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {paire[:3]}/{paire[3:]}" if mode == "SCALP" else f"📊 Verrouiller {paire[:3]}/{paire[3:]}", callback_data=f"set_{paire}"))
                        
                        prefixe = "👑 **EXCEPTION 10/10 HORS SESSION** 👑\n" if statut == "HORS_SESSION" else ""
                        for uid in utilisateurs_libres:
                            if mode_trading.get(uid, "STANDARD") == mode:
                                msg = f"{prefixe}🔔 **PIC SCALP (Volume OK) : {paire[:3]}/{paire[3:]}**\n👉 Dégaine !" if mode == "SCALP" else f"{prefixe}🔔 **PULLBACK {exp} : {paire[:3]}/{paire[3:]}**"
                                try: bot.send_message(uid, msg, reply_markup=markup)
                                except: pass
        except Exception as e: pass

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition V16.9 ULTIMATE Démarrée.", flush=True)
    bot.infinity_polling()
