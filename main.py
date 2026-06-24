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
# CONFIGURATION PRINCIPALE
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAHbICfEPMhpa7cBSKbKjqDf0oeHxWxjsK4"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")
COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3

# ==========================================
# VARIABLES D'ÉTAT
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
CRYPTO_PAIRS    = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS     = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD",
                   "AUDJPY","EURAUD","EURUSD","AUDCAD","USDCHF",
                   "CADCHF","EURCHF","USDJPY"]
ELITE_PAIRS_MT5  = SYNTHETIC_PAIRS + COMMODITY_PAIRS
ALL_PAIRS_POCKET = SYNTHETIC_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS + CRYPTO_PAIRS

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)
@app.route('/') 
def home(): return "Terminal Prime V33 (Kasper OTE + Killzones)"
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

# ==========================================
# KILLZONES & VERROUILLAGE TEMPOREL
# ==========================================

def dans_killzone():
    """Filtre les sessions à haute volatilité (London & NY)"""
    now = datetime.datetime.utcnow()
    h = now.hour + (now.minute / 60.0)
    # London Killzone (07:00 - 10:00 GMT) | NY Killzone (12:00 - 15:00 GMT)
    if (7.0 <= h <= 10.0) or (12.0 <= h <= 15.0):
        return True
    return False

def est_symbole_autorise(symbole):
    if symbole in SYNTHETIC_PAIRS: return "AUTORISE", ""
    now = datetime.datetime.utcnow()
    j = now.weekday()
    h = now.hour + (now.minute / 60.0)
    weekend = (j == 4 and h >= 21.0) or j == 5 or (j == 6 and h < 21.0)
    
    if weekend: return ("AUTORISE", "") if symbole in CRYPTO_PAIRS else ("BLOCAGE_TOTAL", "Week-end")
    if symbole in CRYPTO_PAIRS: return "BLOCAGE_TOTAL", "Cryptos semaine"
    
    # 🛑 Restriction Kasper : Uniquement pendant les Killzones pour l'OR et le Forex
    if (symbole in COMMODITY_PAIRS or symbole in FOREX_PAIRS) and not dans_killzone():
        return "HORS_SESSION", "🔒 **HORS KILLZONE** : Attente session Londres ou NY."
        
    return "AUTORISE", ""

# ==========================================
# ROUTEUR DERIV (FIABLE SANS DEADLOCK)
# ==========================================

def prefixer_symbole(s):
    if s in SYNTHETIC_PAIRS: return f"R_{s.replace('V','')}"
    if s in CRYPTO_PAIRS: return f"cry{s}"
    return f"frx{s}"

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    sym = prefixer_symbole(symbole_brut)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history":sym,"end":"latest","count":250,"style":"candles","granularity":granularite}))
            ws.settimeout(7)
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res and "error" not in res: return res["candles"]
        except:
            try: ws.close()
            except: pass
            time.sleep(0.3)
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    sym = prefixer_symbole(symbole_brut)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history":sym,"end":"latest","count":1,"style":"ticks"}))
            ws.settimeout(7)
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]: return float(res["history"]["prices"][0])
        except:
            try: ws.close()
            except: pass
            time.sleep(0.3)
    return None

def calculer_entree_precise(duree=60):
    now = datetime.datetime.now()
    sec = now.second
    delai = (60 - sec) + 5
    return delai, (now + datetime.timedelta(seconds=delai)).strftime("%H:%M:%S")

# ============================================================
# MODULE KASPER TRADING (ICT OTE) 
# EMA CLOUD (72/89/180/200) + FIBONACCI OTE (0.618-0.786)
# ============================================================

def calculer_ema_cloud(df):
    ema72  = ta.trend.EMAIndicator(close=df['close'], window=72).ema_indicator()
    ema89  = ta.trend.EMAIndicator(close=df['close'], window=89).ema_indicator()
    ema180 = ta.trend.EMAIndicator(close=df['close'], window=180).ema_indicator()
    ema200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()

    nuage_rapide = "BULL" if ema72.iloc[-1] > ema89.iloc[-1] else "BEAR"
    nuage_lent   = "BULL" if ema180.iloc[-1] > ema200.iloc[-1] else "BEAR"

    if nuage_rapide == "BULL" and nuage_lent == "BULL": tendance, force = "BULL", "FORT 🟢🟢"
    elif nuage_rapide == "BEAR" and nuage_lent == "BEAR": tendance, force = "BEAR", "FORT 🔴🔴"
    else: tendance, force = nuage_rapide, "MODÉRÉ 🟡"

    return tendance, force

def trouver_swing_recant(df, tendance, lookback=30):
    highs = df['high'].iloc[-lookback:]
    lows  = df['low'].iloc[-lookback:]
    if tendance == "BEAR":
        swing_high = highs.max()
        idx_high   = highs.idxmax()
        swing_low  = lows.iloc[lows.index.get_loc(idx_high):].min()
        return swing_high, swing_low
    else:
        swing_low  = lows.min()
        idx_low    = lows.idxmin()
        swing_high = highs.iloc[highs.index.get_loc(idx_low):].max()
        return swing_high, swing_low

def calculer_zone_ote(swing_high, swing_low, tendance):
    diff = swing_high - swing_low
    if tendance == "BEAR":
        ote_bas  = swing_low + diff * 0.618
        ote_haut = swing_low + diff * 0.786
        sl       = swing_high + diff * 0.05 # Fibo 1.0 (Légèrement au dessus)
        tp_15r   = swing_low - diff * 0.5   # 1.5 R/R
    else:
        ote_bas  = swing_high - diff * 0.786
        ote_haut = swing_high - diff * 0.618
        sl       = swing_low - diff * 0.05  # Fibo 1.0 (Légèrement en dessous)
        tp_15r   = swing_high + diff * 0.5  # 1.5 R/R

    return {"ote_bas": ote_bas, "ote_haut": ote_haut, "sl": sl, "tp_15r": tp_15r}

def detecter_reaction_ote(df, zone_ote, tendance):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    px   = last['close']
    dans_zone = zone_ote["ote_bas"] <= px <= zone_ote["ote_haut"]
    prev_dans_zone = zone_ote["ote_bas"] <= prev['close'] <= zone_ote["ote_haut"]

    if not (dans_zone or prev_dans_zone): return False, "Hors zone OTE"

    corps   = abs(last['close'] - last['open'])
    taille  = last['high'] - last['low']
    meche_h = last['high'] - max(last['open'], last['close'])
    meche_b = min(last['open'], last['close']) - last['low']

    if tendance == "BEAR":
        engulfing_bear = prev['close'] > prev['open'] and last['close'] < last['open'] and last['close'] < prev['open']
        if engulfing_bear: return True, "🕯️ Engulfing Baissier en OTE"
        if meche_h > corps * 2.0 and taille > 0: return True, "📍 Pin Bar Baissier en OTE"
        if last['close'] < last['open'] and corps > taille * 0.4: return True, "📉 Rejet Baissier en OTE"
    else:
        engulfing_bull = prev['close'] < prev['open'] and last['close'] > last['open'] and last['close'] > prev['open']
        if engulfing_bull: return True, "🕯️ Engulfing Haussier en OTE"
        if meche_b > corps * 2.0 and taille > 0: return True, "📍 Pin Bar Haussier en OTE"
        if last['close'] > last['open'] and corps > taille * 0.4: return True, "📈 Rejet Haussier en OTE"

    return False, "Pas de réaction nette"

def analyser_kasper_complet(symbole):
    candles_m5 = obtenir_donnees_deriv(symbole, 300)
    candles_h1 = obtenir_donnees_deriv(symbole, 3600)
    if not candles_m5 or not candles_h1: return None

    try:
        df_m5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in candles_m5])
        df_h1 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in candles_h1])

        tendance, force_cloud = calculer_ema_cloud(df_h1)
        swing_h, swing_l = trouver_swing_recant(df_m5, tendance, lookback=40)
        
        if (swing_h - swing_l) < 0.5: return None # Swing trop petit
        
        zone_ote = calculer_zone_ote(swing_h, swing_l, tendance)
        reaction_ok, reaction_msg = detecter_reaction_ote(df_m5, zone_ote, tendance)

        if reaction_ok:
            action = "🟢 ACHAT (BUY)" if tendance == "BULL" else "🔴 VENTE (SELL)"
            risque = abs(df_m5['close'].iloc[-1] - zone_ote["sl"])
            recompense = abs(zone_ote["tp_15r"] - df_m5['close'].iloc[-1])
            rr = recompense / risque if risque > 0 else 0
            
            return {
                "action": action,
                "msg": reaction_msg,
                "sl": zone_ote["sl"],
                "tp": zone_ote["tp_15r"],
                "rr": rr,
                "force": force_cloud
            }
    except: pass
    return None

# ==========================================
# FIX ANTI-BUG : PURGE DES TRADES BLOQUÉS
# ==========================================

def nettoyer_trades_bloques():
    """Vérifie tous les trades en cours et supprime ceux dont le temps est dépassé."""
    now = time.time()
    for uid in list(trades_en_cours.keys()):
        trade = trades_en_cours[uid]
        timestamp = trade.get('timestamp', now)
        duree = trade.get('duree', 300)
        if (now - timestamp) > (duree + 60): # 60 secondes de marge
            del trades_en_cours[uid]

# ==========================================
# SCANNER AUTOMATIQUE & ROUTAGE
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            nettoyer_trades_bloques() # 🛠️ Fix Anti-Bug Exécuté à chaque cycle
            
            libres = [u for u in utilisateurs_actifs if est_autorise(u) and u not in trades_en_cours]
            if not libres: continue

            for paire in ELITE_PAIRS_MT5 + FOREX_PAIRS:
                statut, msg_statut = est_symbole_autorise(paire)
                if statut != "AUTORISE": continue

                cle_memoire = f"{paire}_KASPER"
                if cle_memoire in derniere_alerte_auto and time.time() - derniere_alerte_auto[cle_memoire] < 300: 
                    continue

                res = analyser_kasper_complet(paire)
                if res:
                    px = obtenir_prix_actuel_deriv(paire) or 0.0
                    if res['rr'] < 1.0: continue # Sécurité Ratio

                    signaux_cache[cle_memoire] = {
                        'time': time.time(), 'action': res['action'], 'conf': 95,
                        'exp': "N/A", 'dur': 300, 'sc': 9.8,
                        'mt5_sl': res['sl'], 'mt5_tp': res['tp'], 'mt5_rr': res['rr']
                    }
                    derniere_alerte_auto[cle_memoire] = time.time()

                    for uid in libres:
                        pf = plateforme_trading.get(uid, "MT5")
                        if pf == "MT5" and paire not in ELITE_PAIRS_MT5: continue
                        if pf == "POCKET" and paire not in FOREX_PAIRS: continue

                        nom_a = "🥇 GOLD" if paire == "XAUUSD" else f"{paire[:3]}/{paire[3:]}"
                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {nom_a}", callback_data=f"set_{paire}"))
                        msg = f"🎯 **KASPER OTE DETECTÉ : {nom_a}**\n☁️ Tendance : `{res['force']}`\n📍 {res['msg']}\nClique ci-dessous pour copier tes paramètres de tir."
                        
                        try: bot.send_message(uid, msg, reply_markup=markup, parse_mode="Markdown")
                        except: pass

        except Exception as e:
            print(f"[Scanner] ⚠️ Erreur: {e}", flush=True)

# ==========================================
# INTERFACE TELEGRAM
# ==========================================

def obtenir_clavier(uid):
    pf = plateforme_trading.get(uid, "MT5")
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("🏦 BROKER: POCKET" if pf=="POCKET" else "📈 BROKER: MT5"), KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid): return bot.send_message(uid, "🔒 Accès restreint.")
    utilisateurs_actifs.add(uid)
    plateforme_trading.setdefault(uid, "MT5")
    texte = """🏴‍☠️ **TERMINAL PRIME V33 (KASPER OTE)** 🔥
──────────────────
✅ **Moteur Kasper Trading** (EMA Cloud + Fibonacci OTE)
✅ **Filtre Killzones** (Londres 07h-10h & New York 12h-15h)
✅ **Auto-Clean Anti-Bug** (Plus aucun trade bloqué)
✅ SL sur Fibo 1.0 & Take Profit à 1.5R"""
    bot.send_message(uid, texte, reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_pf(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if plateforme_trading.get(uid,"MT5")=="POCKET":
        plateforme_trading[uid]="MT5"
        bot.send_message(uid,"📈 **MT5 ACTIVÉ (Gold/Élite)**", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")
    else:
        plateforme_trading[uid]="POCKET"
        bot.send_message(uid,"🏦 **POCKET ACTIVÉ (Forex)**", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text=="⏰ HEURES DE TRADING")
def horaires(message):
    bot.send_message(message.chat.id, "🕒 **KILLZONES ACTIVES :**\n🇬🇧 Londres : 07:00 - 10:00 GMT\n🇺🇸 New York : 12:00 - 15:00 GMT", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE", "📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    pf = plateforme_trading.get(message.chat.id, "MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    if pf == "MT5":
        markup.add(InlineKeyboardButton("🥇 GOLD", callback_data="set_XAUUSD"), InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"))
    else:
        markup.add(InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇬🇧 GBP/USD", callback_data="set_GBPUSD"))
    bot.send_message(message.chat.id, "Cibles Kasper :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text=="🚀 LANCER L'ANALYSE")
def lancer(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    nettoyer_trades_bloques() # On nettoie avant de vérifier
    if uid in trades_en_cours: return bot.send_message(uid, "⚠️ Trade en cours.")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(uid, "⚠️ Choisis d'abord une cible !")
    save_devise(type('obj',(object,),{'data':f"set_{actif}",'message':message,'from_user':message.from_user,'id':0})())

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): return
    nettoyer_trades_bloques() # 🛠️ Sécurité Max
    if uid in trades_en_cours:
        try: bot.answer_callback_query(call.id,"⚠️ Trade en cours !",show_alert=True)
        except: pass
        return

    actif = call.data.replace("set_","")
    user_prefs[getattr(call,'from_user',type('o',(object,),{'id':uid})()).id] = actif
    cle = f"{actif}_KASPER"
    cache = signaux_cache.get(cle)
    
    try: bot.delete_message(uid,call.message.message_id)
    except: pass

    if not cache or (time.time() - cache['time']) > 120:
        return bot.send_message(uid, f"⏱️ **Signal expiré sur {actif}**\nL'opportunité OTE est passée.", parse_mode="Markdown")

    px = obtenir_prix_actuel_deriv(actif) or 0.0
    nom = "🥇 GOLD" if actif == "XAUUSD" else actif

    dir_aff = "🟢 BUY MARKET" if "ACHAT" in cache['action'] else "🔴 SELL MARKET"
    signal = f"""🎯 **KASPER SNIPER — {nom}**
──────────────────
{dir_aff} (Prix dans l'OTE)
⚡ **Action :** Exécute immédiatement sur MT5 !

🔹 **PRIX D'ENTRÉE :** `{px:.3f}` *(Tap pour copier)*
🛑 **STOP LOSS (Fibo 1.0) :** `{cache['mt5_sl']:.3f}` *(Tap pour copier)*
🟢 **TAKE PROFIT (1.5R) :** `{cache['mt5_tp']:.3f}` *(Tap pour copier)*

*⚠️ Gestion du risque : 1% du capital max.*"""
    
    bot.send_message(uid, signal, parse_mode="Markdown")
    # Pour MT5, exécution manuelle via Telegram (Pas de blocage dans trades_en_cours requis car pas de gestion de position auto pour l'instant via l'API Render)

# ==========================================
# LANCEMENT
# ==========================================
if __name__=="__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    print("⬛ TERMINAL PRIME V33 (KASPER OTE) — Démarré.", flush=True)
    bot.infinity_polling()
