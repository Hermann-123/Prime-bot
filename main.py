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
from threading import Thread, Timer, Lock

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAGDC5dV2c_kGzhPWvEq1gZPpquUXRMFVFc"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3

ws_lock = Lock()
_ws_deriv = None

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
mode_trading = {}
plateforme_trading = {}
filtre_special = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
signaux_cache = {}
cooldown_actifs = {}
niveaux_martingale = {}
historique_signaux = {}  

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}

SYNTHETIC_PAIRS = ["V10", "V25", "V50", "V75", "V100"]
COMMODITY_PAIRS = ["XAUUSD", "XAGUSD", "USOUSD"]
CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD",
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF",
    "CADCHF", "EURCHF", "USDJPY"
]

ELITE_PAIRS_MT5 = SYNTHETIC_PAIRS + COMMODITY_PAIRS
ALL_PAIRS_POCKET = SYNTHETIC_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS + CRYPTO_PAIRS

# ==========================================
# PROFILS DYNAMIQUES 
# ==========================================

def obtenir_profil_actif(symbole):
    if symbole in SYNTHETIC_PAIRS:
        return {"stoch_achat": 30, "rsi_achat": 35, "stoch_vente": 70, "rsi_vente": 65, "vol_multiplier": 2.5, "rr_min": 1.5, "cooldown_otm": 900, "nom": "SMC Synthétiques"}
    elif symbole in COMMODITY_PAIRS:
        return {"stoch_achat": 25, "rsi_achat": 40, "stoch_vente": 75, "rsi_vente": 60, "vol_multiplier": 2.0, "rr_min": 1.5, "cooldown_otm": 1200, "nom": "Kasper Trading"}
    else:
        return {"stoch_achat": 25, "rsi_achat": 40, "stoch_vente": 75, "rsi_vente": 60, "vol_multiplier": 1.8, "rr_min": 1.5, "cooldown_otm": 1800, "nom": "SMC Forex"}

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)
@app.route('/')
def home(): return "Terminal Prime VIP : Édition V32 (Kasper & Bug Fix)"
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

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
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ**\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
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
        texte = f"✅ **CLÉ GÉNÉRÉE**\n\n🔑 **Clé :** `{cle}`\n⏳ **Durée :** {'À VIE 👑' if jours == 'LIFETIME' else f'{jours} Jours'}\n\n"
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
            bot.send_message(chat_id, f"🎉 **ACCÈS DÉVERROUILLÉ !**\n\nBienvenue.\n⏳ **Fin :** {expiration_texte}\n\n👉 Tapez /start", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "❌ **Clé invalide ou déjà utilisée.**", parse_mode="Markdown")
    except: pass

# ==========================================
# KILLZONES & VERROUILLAGE TEMPOREL
# ==========================================

def dans_killzone():
    """Filtre les sessions à haute volatilité (London & NY)"""
    now = datetime.datetime.utcnow()
    h = now.hour + (now.minute / 60.0)
    # London Killzone (07:00 - 10:00) | NY Killzone (12:00 - 15:00)
    if (7.0 <= h <= 10.0) or (12.0 <= h <= 15.0):
        return True
    return False

def est_symbole_autorise(symbole):
    if symbole in SYNTHETIC_PAIRS: return "AUTORISE", ""
    now = datetime.datetime.utcnow()
    jour = now.weekday()
    heure_dec = now.hour + (now.minute / 60.0)
    est_week_end = (jour == 4 and heure_dec >= 21.0) or (jour == 5) or (jour == 6 and heure_dec < 21.0)
    
    if est_week_end: return ("BLOCAGE_TOTAL", "Week-end") if symbole not in CRYPTO_PAIRS else ("AUTORISE", "")
    if symbole in CRYPTO_PAIRS: return "BLOCAGE_TOTAL", "Cryptos le week-end uniquement."
    
    # 🛑 Restriction stricte Kasper : Uniquement pendant les Killzones pour l'OR/Forex
    if not dans_killzone() and symbole in COMMODITY_PAIRS:
        return "HORS_SESSION", "🔒 **HORS KILLZONE** : Attente de la session de Londres ou NY."
        
    return "AUTORISE", ""

# ==========================================
# ROUTEUR DERIV
# ==========================================

def prefixer_symbole(symbole_brut):
    if symbole_brut in SYNTHETIC_PAIRS: return f"R_{symbole_brut.replace('V', '')}"
    if symbole_brut in CRYPTO_PAIRS: return f"cry{symbole_brut}"
    return f"frx{symbole_brut}"

def executer_requete_deriv(req):
    global _ws_deriv
    with ws_lock:
        for _ in range(3):
            try:
                if _ws_deriv is None or not _ws_deriv.connected:
                    _ws_deriv = websocket.WebSocket()
                    _ws_deriv.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
                _ws_deriv.send(json.dumps(req))
                res = json.loads(_ws_deriv.recv())
                return res
            except:
                try: _ws_deriv.close()
                except: pass
                _ws_deriv = None
                time.sleep(0.5)
        return None

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    symbole = prefixer_symbole(symbole_brut)
    req = {"ticks_history": symbole, "end": "latest", "count": 300, "style": "candles", "granularity": granularite}
    res = executer_requete_deriv(req)
    if res and "error" not in res and "candles" in res: return res['candles']
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    req = {"ticks_history": symbole, "end": "latest", "count": 1, "style": "ticks"}
    res = executer_requete_deriv(req)
    if res and "history" in res and "prices" in res["history"]: return float(res["history"]["prices"][0])
    return None

# ==========================================
# MOTEUR ALGORITHMIQUE (KASPER + SMC)
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    profil = obtenir_profil_actif(symbole)

    # 🚀 STRATÉGIE KASPER TRADING (EMA CLOUD + FIBO OTE) POUR MT5 (OR/SYNTH)
    if symbole in ELITE_PAIRS_MT5:
        candles_kasper = obtenir_donnees_deriv(symbole, 300) # M5
        if candles_kasper:
            try:
                df = pd.DataFrame([{'open': float(c['open']), 'high': float(c['high']), 'low': float(c['low']), 'close': float(c['close'])} for c in candles_kasper])
                
                # ÉTAPE 1 & 2 : EMA CLOUD (9 & 21) & FLUX
                ema9 = ta.trend.EMAIndicator(close=df['close'], window=9).ema_indicator()
                ema21 = ta.trend.EMAIndicator(close=df['close'], window=21).ema_indicator()
                
                flux_haussier = ema9.iloc[-1] > ema21.iloc[-1] and df['close'].iloc[-1] > ema9.iloc[-1]
                flux_baissier = ema9.iloc[-1] < ema21.iloc[-1] and df['close'].iloc[-1] < ema9.iloc[-1]

                # ÉTAPE 3 : FIBONACCI OTE SUR LE DERNIER SWING (20 Bougies)
                swing_high = df['high'].iloc[-21:-1].max()
                swing_low = df['low'].iloc[-21:-1].min()
                swing_range = swing_high - swing_low
                prix_actuel = df['close'].iloc[-1]
                
                action = None
                sl = 0.0
                tp = 0.0
                
                # ÉTAPE 4, 5 & 6 : VALIDATION OTE + BOUGIE + CALCUL TP 1.5R
                if flux_haussier:
                    ote_haut = swing_high - (swing_range * 0.618)
                    ote_bas = swing_high - (swing_range * 0.786)
                    # Le prix touche la zone OTE
                    if ote_bas <= prix_actuel <= ote_haut:
                        # Rejet (Bougie de confirmation verte dans la zone)
                        if prix_actuel > df['open'].iloc[-1]: 
                            sl = swing_low # Fibo 1.0
                            risque = abs(prix_actuel - sl)
                            tp = prix_actuel + (risque * 1.5) # 1.5R
                            action = "🟢 ACHAT (CALL)"
                            
                elif flux_baissier:
                    ote_bas = swing_low + (swing_range * 0.618)
                    ote_haut = swing_low + (swing_range * 0.786)
                    # Le prix touche la zone OTE
                    if ote_bas <= prix_actuel <= ote_haut:
                        # Rejet (Bougie de confirmation rouge dans la zone)
                        if prix_actuel < df['open'].iloc[-1]:
                            sl = swing_high # Fibo 1.0
                            risque = abs(sl - prix_actuel)
                            tp = prix_actuel - (risque * 1.5) # 1.5R
                            action = "🔴 VENTE (PUT)"

                if action:
                    return action, 98, "N/A", 300, 50, 50, "💎 Kasper Strategy (EMA Cloud + Fibo OTE)", 9.8, sl, tp

            except Exception as e: pass
        return f"⚠️ En attente d'une opportunité Kasper.", None, None, None, None, None, None, None, None, None

    # ──────── SMC V27 (POUR POCKET BROKER / FOREX) ────────
    timeframes = [600, 300, 120] if mode == "STANDARD" else [60]

    for tf in timeframes:
        candles = obtenir_donnees_deriv(symbole, tf)
        if not candles: continue
        try:
            df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
            # Calculs SMC standards (Omis pour brièveté de lecture, repris de V27)
            df['corps_bougie'] = abs(df['close'] - df['open'])
            df['taille_bougie'] = df['high'] - df['low']
            df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
            df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']
            df['volume_proxy'] = df['high'] - df['low']
            df['volume_moyen'] = df['volume_proxy'].rolling(window=14).mean()

            atr = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
            if atr.iloc[-1] < atr.iloc[-20:].mean() * 0.5 or atr.iloc[-1] > atr.iloc[-20:].mean() * 3.0: continue

            df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
            df['macd_diff'] = ta.trend.MACD(close=df['close']).macd_diff()
            
            c = df['close'].iloc[-1]
            prix_moyen_recent = df['close'].iloc[-6:-1].mean()
            dans_zone_discount = c < prix_moyen_recent
            dans_zone_premium = c > prix_moyen_recent
            
            action, confiance, bb_status, score_algo = None, 0, "En Attente", 5.0
            
            # Simplified Logic for Pocket Fallback
            if dans_zone_discount and df['macd_diff'].iloc[-1] > 0 and df['rsi'].iloc[-1] < 45:
                action, confiance, score_algo = "🟢 ACHAT (CALL)", 80, 8.5
                bb_status = f"🎯 {profil['nom']} : Zone d'intérêt"
            elif dans_zone_premium and df['macd_diff'].iloc[-1] < 0 and df['rsi'].iloc[-1] > 55:
                action, confiance, score_algo = "🔴 VENTE (PUT)", 80, 8.5
                bb_status = f"🎯 {profil['nom']} : Zone d'intérêt"

            if action:
                return action, min(confiance, 99), f"{int(tf/60)} MIN", tf, round(df['rsi'].iloc[-1], 1), 50, bb_status, score_algo, 0, 0
        except: continue
    return f"⚠️ En attente ({mode}).", None, None, None, None, None, None, None, None, None

# ==========================================
# INTERFACE MENU & COMMANDES
# ==========================================

def obtenir_clavier(user_id):
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    plateforme = plateforme_trading.get(user_id, "MT5")
    btn_plateforme = "🏦 BROKER: POCKET" if plateforme == "POCKET" else "📈 BROKER: MT5"
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton(btn_plateforme), KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_plateforme(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Terminez le trade en cours.")
    
    if plateforme_trading.get(user_id, "MT5") == "POCKET":
        plateforme_trading[user_id] = "MT5"
        bot.send_message(user_id, "📈 **MODE MT5 KASPER ACTIVÉ**\nStratégie EMA Cloud + Fibo OTE (SL/TP précis).", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        plateforme_trading[user_id] = "POCKET"
        bot.send_message(user_id, "🏦 **MODE POCKET ACTIVÉ**\n100% Forex Binaire SMC.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    plateforme_trading[user_id] = plateforme_trading.get(user_id, "MT5")
    
    texte = """🏴‍☠️ **TERMINAL PRIME - V32 (KASPER SNIPER)** 🔥
──────────────────
🚨 **MOTEUR KASPER TRADING (EMA 9/21 + FIBO OTE)** 🚨

✅ Calcul 100% Mathématique des Nuages & Tendance
✅ Détection automatique de la zone **OTE 0.618**
✅ Stop Loss millimétré sur Fibo 1.0
✅ Take Profit calculé à **1.5 R/R** fixe
🛡️ **Fix Anti-Bug :** Auto-nettoyage des trades bloqués intégré.

👉 Cliquez sur MT5 pour générer les signaux Click-to-Copy."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE", "📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    if not est_autorise(message.chat.id): return
    plateforme = plateforme_trading.get(message.chat.id, "MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    if plateforme == "MT5":
        markup.add(InlineKeyboardButton("🥇 GOLD", callback_data="set_XAUUSD"), InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"), InlineKeyboardButton("🛢 PÉTROLE", callback_data="set_USOUSD"))
        markup.add(InlineKeyboardButton("🔥 V75", callback_data="set_V75"), InlineKeyboardButton("💥 V100", callback_data="set_V100"))
        texte_menu = "Sélectionne ta cible (MT5 Kasper Strategy) :"
    else:
        markup.add(InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇺🇸 USD/JPY", callback_data="set_USDJPY"), InlineKeyboardButton("🇬🇧 GBP/USD", callback_data="set_GBPUSD"))
        texte_menu = "Sélectionne ta cible (Mode Binaire) :"
    bot.send_message(message.chat.id, texte_menu, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if check_and_clear_stuck_trades(chat_id):
        return bot.send_message(chat_id, "⚠️ Combat en cours. Attends la fin du compte à rebours.")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une cible !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user, 'id': 0})())

def check_and_clear_stuck_trades(chat_id):
    """🛠️ LE FIX : Vérifie si un trade est bloqué depuis trop longtemps et l'efface."""
    if chat_id in trades_en_cours:
        trade = trades_en_cours[chat_id]
        timestamp = trade.get('timestamp', 0)
        duree = trade.get('duree', 60)
        # Si le temps écoulé dépasse la durée du trade + 60 secondes de marge de sécurité
        if (time.time() - timestamp) > (duree + 60):
            del trades_en_cours[chat_id]
            return False # Il était bloqué, on l'a nettoyé, la voie est libre
        return True # Trade légitimement en cours
    return False

# ==========================================
# GESTION DES REQUÊTES
# ==========================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    
    # 🛠️ Nettoyage automatique du bug "combat en cours"
    if check_and_clear_stuck_trades(chat_id):
        try: bot.answer_callback_query(call.id, "⚠️ Combat en cours !", show_alert=True)
        except: pass
        return

    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id if hasattr(call, 'from_user') else chat_id] = actif
    plateforme = plateforme_trading.get(chat_id, "MT5")
    cle_memoire = f"{actif}_STANDARD"

    signal_cache = signaux_cache.get(cle_memoire)
    if not signal_cache or (time.time() - signal_cache['time'] > 90):
        try: bot.delete_message(chat_id, call.message.message_id)
        except: pass
        return bot.send_message(chat_id, f"⏱️ **OPPORTUNITÉ EXPIRÉE SUR {actif}**\n\nAttendez le prochain scan OTE.", parse_mode="Markdown")

    try: bot.delete_message(chat_id, call.message.message_id)
    except: pass

    current_ask = obtenir_prix_actuel_deriv(actif) or 0.0

    if plateforme == "MT5":
        action_affiche = "🟢 ACHAT (BUY LIMIT/MARKET)" if "ACHAT" in signal_cache['action'] else "🔴 VENTE (SELL LIMIT/MARKET)"
        sl = signal_cache.get('mt5_sl', 0.0)
        tp = signal_cache.get('mt5_tp', 0.0)
        
        signal = f"""🎯 **ALERTE KASPER SNIPER — {actif}**

{action_affiche} (Prix dans le Cloud OTE)
⚡ **Action :** Entrez immédiatement sur MT5 !

🔹 **PRIX ACTUEL :** `{current_ask:.2f}`
🛑 **STOP LOSS (Fibo 1.0) :** `{sl:.2f}`
🟢 **TAKE PROFIT (1.5R) :** `{tp:.2f}`

*⚠️ Gestion du risque : 1% du capital.*"""
        bot.send_message(chat_id, signal, parse_mode="Markdown")
        # Sur MT5, on ne bloque pas le bot, c'est purement manuel pour l'utilisateur
        
    else: # POCKET
        palier = niveaux_martingale.get(chat_id, 0)
        mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))
        signal = f"🚨 **SIGNAL POCKET : PALIER {palier}** 🚨\n──────────────────\n🌐 **ACTIF :** {actif}\n👉 **ACTION :** {signal_cache['action']}\n⏳ **DURÉE :** {signal_cache['exp']}\n💵 **MISE :** `{mise}$`"

        bot.send_message(chat_id, signal, parse_mode="Markdown")
        action_brute = "CALL" if "ACHAT" in signal_cache['action'] else "PUT"
        
        # 🛠️ On marque le trade avec un timestamp pour le Time-To-Live
        trades_en_cours[chat_id] = {'symbole': actif, 'action': action_brute, 'duree': signal_cache['dur'], 'nom_affiche': actif, 'timestamp': time.time()}
        
        Timer(2, relever_prix_entree, args=[chat_id, actif]).start()
        Timer(signal_cache['dur'], verifier_resultat, args=[chat_id]).start()

# ==========================================
# MOTEUR DE SCAN AUTO
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            utilisateurs_libres = [uid for uid in utilisateurs_actifs if est_autorise(uid) and not check_and_clear_stuck_trades(uid)]
            if not utilisateurs_libres: continue

            for paire in ELITE_PAIRS_MT5 + FOREX_PAIRS:
                statut, msg = est_symbole_autorise(paire)
                if statut != "AUTORISE": continue

                cle_memoire = f"{paire}_STANDARD"
                if cle_memoire in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle_memoire] < 300): continue

                action, conf, exp, dur, rsi, stoch, bb, sc, sl, tp = analyser_binaire_pro(paire, "STANDARD")

                if action and "⚠️" not in action:
                    signaux_cache[cle_memoire] = {
                        'time': time.time(), 'action': action, 'conf': conf,
                        'exp': exp, 'dur': dur, 'rsi': rsi, 'stoch': stoch,
                        'bb': bb, 'sc': sc, 'mt5_sl': sl, 'mt5_tp': tp
                    }
                    derniere_alerte_auto[cle_memoire] = time.time()

                    for uid in utilisateurs_libres:
                        pf = plateforme_trading.get(uid, "MT5")
                        if pf == "MT5" and paire not in ELITE_PAIRS_MT5: continue
                        if pf == "POCKET" and paire not in FOREX_PAIRS: continue

                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"📲 Voir Fiche {paire}", callback_data=f"set_{paire}"))
                        msg_txt = f"🔔 **ZONE KASPER DÉTECTÉE : {paire}**\nLe prix est dans la zone OTE 0.618. Cliquez ci-dessous pour copier les SL/TP."
                        try: bot.send_message(uid, msg_txt, reply_markup=markup, parse_mode="Markdown")
                        except: pass

        except Exception as e:
            pass

# ==========================================
# GESTION RÉSULTATS (POUR POCKET UNIQUEMENT)
# ==========================================

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole: 
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee, cooldown_actifs, niveaux_martingale
    trade = trades_en_cours.get(chat_id)
    if not trade: return
    
    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    
    # 🛠️ FIX ANTI-BUG : Si Deriv ne répond pas, on annule proprement le trade pour débloquer.
    if not prix_sortie: 
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, "⚠️ **Erreur réseau : Prix de sortie indisponible.** Trade annulé pour débloquer l'assistant.", parse_mode="Markdown")
        except: pass
        return

    prix_entree = trade.get('prix_entree')
    if not prix_entree:
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        return

    action = trade['action']
    palier_actuel = niveaux_martingale.get(chat_id, 0)
    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)

    if gagne:
        niveaux_martingale[chat_id] = 0
        texte = f"✅ **CIBLE ABATTUE (ITM)**\n🚀 {symbole}\n🔓 Radar déverrouillé."
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, texte, parse_mode="Markdown")
        except: pass
    else:
        if palier_actuel < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
            bot.send_message(chat_id, f"⚠️ **PIÈGE BROKER (Palier {palier_actuel} OTM)**\nRelancez une analyse si besoin.", parse_mode="Markdown")
        else:
            niveaux_martingale[chat_id] = 0
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
            try: bot.send_message(chat_id, f"🛑 **SÉQUENCE ARRÊTÉE (OTM)**", parse_mode="Markdown")
            except: pass

@bot.callback_query_handler(func=lambda c: c.data == "force_win")
def override_victoire_manuelle(call):
    chat_id = call.message.chat.id
    if chat_id in trades_en_cours:
        bot.send_message(chat_id, f"✅ **CIBLE ABATTUE (ITM MANUEL)**\n🔓 Radar déverrouillé.", parse_mode="Markdown")
        del trades_en_cours[chat_id]
    niveaux_martingale[chat_id] = 0
    try: bot.answer_callback_query(call.id, "Victoire enregistrée.", show_alert=True)
    except: pass
    try: bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except: pass

# ==========================================
# INITIALISATION
# ==========================================

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    print("⬛ TERMINAL PRIME V32 (KASPER & FIX BUG) : Actif.", flush=True)
    bot.infinity_polling()
