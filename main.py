"""
╔════════════════════════════════════════════════════════════════════════════╗
║                   TERMINAL PRIME V38 — TRADE STATE MANAGEMENT             ║
║              Real-time Price Sync + Trade Tracking + Result Notifications ║
╚════════════════════════════════════════════════════════════════════════════╝

V38 FIXES:
  ✅ Synchronisation des prix en temps réel (broker real-time)
  ✅ Validation des prix AVANT signal (bot vs broker)
  ✅ Gestion d'état des trades (Signal → Ouvert → Fermé)
  ✅ Blocage des signaux pendant un trade actif
  ✅ Message final: ✅ GAGNÉ ou ❌ PERDU avec P&L
  ✅ Historique détaillé des trades
"""

import os
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
from threading import Thread
from enum import Enum

# ==========================================
# CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAH-g5x2raGDziwZMReadzJtUT8VljE3c2A"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# ÉNUMÉRATION DES ÉTATS DE TRADE (V38 NEW)
# ==========================================

class TradeState(Enum):
    SIGNAL_SENT = "SIGNAL_ENVOYÉ"      # Signal envoyé, en attente d'action
    TRADE_OPEN = "TRADE_OUVERT"        # Trade actif en cours
    TRADE_WIN = "GAGNÉ"                # Trade fermé en profit
    TRADE_LOSS = "PERDU"               # Trade fermé en perte
    CANCELLED = "ANNULÉ"               # Trade annulé

# ==========================================
# LISTES DE PAIRES — V38
# ==========================================

VOLATILE_PAIRS = ["V10","V25","V50","V75","V100"]
COMMODITY_PAIRS = ["XAUUSD","XAGUSD"]
FOREX_PAIRS = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD","AUDJPY",
               "EURAUD","EURUSD","AUDCAD","USDCHF","CADCHF","EURCHF",
               "USDJPY","GBPUSD"]

ELITE_PAIRS_MT5 = VOLATILE_PAIRS + COMMODITY_PAIRS
ALL_PAIRS = VOLATILE_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS

NOMS_AFFICHAGE = {
    "XAUUSD":"🥇 GOLD", "XAGUSD":"🥈 ARGENT",
    "V10":"🔥 V10", "V25":"🔥 V25", "V50":"🔥 V50",
    "V75":"⚡ V75", "V100":"💥 V100",
}

# ==========================================
# VARIABLES D'ÉTAT — V38 AMÉLIORÉ
# ==========================================

user_prefs = {}
plateforme_trading = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
signaux_cache = {}

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0}

volatility_scan_active = True

# ✅ V38 NEW: Gestion détaillée des trades
trades_actifs = {}  # uid -> {trade_id, symbol, entry, sl, tp, direction, state, timestamp}
trades_historique = {}  # uid -> [{trade_id, symbol, entry, exit, pl, win_rate, duration}]
prix_cache = {}  # symbol -> {price, timestamp}
prix_broker = {}  # symbol -> {price_mt5, price_pocket, timestamp}

# Compteurs pour P&L
pnl_total = {uid: 0 for uid in utilisateurs_actifs}
win_count = {uid: 0 for uid in utilisateurs_actifs}
loss_count = {uid: 0 for uid in utilisateurs_actifs}

# ==========================================
# KEEP ALIVE
# ==========================================

app = Flask(__name__)
@app.route('/')
def home(): 
    return "Terminal Prime V38 (Trade State Management + Real-time Price Sync)"

def run(): 
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive(): 
    Thread(target=run, daemon=True).start()

# ==========================================
# ✅ V38 NEW: SYNCHRONISATION DES PRIX EN TEMPS RÉEL
# ==========================================

def obtenir_prix_broker_realtime(symbole):
    """
    Obtenir le prix RÉEL du broker en temps réel.
    Compare les prix de FMP/Deriv et retourne le plus fiable.
    """
    try:
        # Essayer FMP d'abord (plus rapide, plus fiable)
        mapping_fmp = {
            "XAUUSD": "FOREX:XAUUSD",
            "XAGUSD": "FOREX:XAGUSD",
        }
        sym_fmp = mapping_fmp.get(symbole, symbole)
        
        url = f"https://financialmodelingprep.com/api/v3/quote/{sym_fmp}?apikey={FMP_API_KEY}"
        res = requests.get(url, timeout=3).json()
        if isinstance(res, list) and len(res) > 0:
            prix = float(res[0]["price"])
            timestamp = time.time()
            
            prix_broker[symbole] = {
                "price": prix,
                "source": "FMP",
                "timestamp": timestamp,
                "bid": float(res[0].get("bid", prix)),
                "ask": float(res[0].get("ask", prix))
            }
            return prix
    except Exception as e:
        print(f"[FMP Real-time] {symbole} Error: {e}", flush=True)
    
    # Fallback: Deriv WebSocket (temps réel)
    sym = prefixer_symbole(symbole)
    for attempt in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            ws.send(json.dumps({"ticks": sym}))
            
            res = json.loads(ws.recv())
            ws.close()
            
            if "tick" in res:
                prix = float(res["tick"]["quote"])
                timestamp = time.time()
                
                prix_broker[symbole] = {
                    "price": prix,
                    "source": "Deriv",
                    "timestamp": timestamp
                }
                return prix
        except:
            try:
                ws.close()
            except:
                pass
            time.sleep(0.5)
    
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.001):
    """
    V38 NEW: Valider que le prix du bot correspond au prix du broker.
    Décale acceptable: 0.1% (tolerance)
    Retourne True si prix valide, False si trop décalé
    """
    prix_real = obtenir_prix_broker_realtime(symbole)
    
    if not prix_real:
        print(f"[Validation] {symbole} - Impossible obtenir prix broker", flush=True)
        return False  # Rejeter si pas de prix
    
    decalage = abs(prix_bot - prix_real) / prix_real
    
    if decalage > tolerance:
        print(f"[Validation] {symbole} - DÉCALAGE TROP GRAND: Bot={prix_bot}, Broker={prix_real}, Écart={decalage*100:.2f}%", flush=True)
        return False
    
    print(f"[Validation] {symbole} - OK: Bot={prix_bot}, Broker={prix_real}, Écart={decalage*100:.4f}%", flush=True)
    return True

# ==========================================
# ✅ V38 NEW: GESTION D'ÉTAT DES TRADES
# ==========================================

def create_trade_id():
    """Générer un ID unique pour chaque trade"""
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp, strategy, confiance):
    """
    V38 NEW: Ouvrir un trade et le tracker
    """
    trade_id = create_trade_id()
    
    trades_actifs[uid] = {
        "trade_id": trade_id,
        "symbol": symbole,
        "direction": direction,  # BUY ou SELL
        "entry_price": entry_price,
        "sl": sl,
        "tp": tp,
        "strategy": strategy,
        "confiance": confiance,
        "state": TradeState.TRADE_OPEN,
        "timestamp_open": time.time(),
        "exit_price": None,
        "exit_time": None,
        "pnl": None
    }
    
    print(f"[Trade Opened] {uid}: {trade_id} {symbole} {direction} @ {entry_price}", flush=True)
    return trade_id

def fermer_trade(uid, exit_price, win=True):
    """
    V38 NEW: Fermer un trade et calculer P&L
    """
    if uid not in trades_actifs:
        return None
    
    trade = trades_actifs[uid]
    trade_id = trade["trade_id"]
    
    # Calculer P&L
    if trade["direction"] == "BUY":
        pnl = (exit_price - trade["entry_price"]) * 1000  # Simple pour exemple
    else:
        pnl = (trade["entry_price"] - exit_price) * 1000
    
    # Déterminer si gagné ou perdu
    if (win and pnl > 0) or (not win and pnl < 0):
        trade["state"] = TradeState.TRADE_WIN
        win_count[uid] = win_count.get(uid, 0) + 1
    else:
        trade["state"] = TradeState.TRADE_LOSS
        loss_count[uid] = loss_count.get(uid, 0) + 1
    
    # Enregistrer
    trade["exit_price"] = exit_price
    trade["exit_time"] = time.time()
    trade["pnl"] = pnl
    
    # Ajouter à l'historique
    if uid not in trades_historique:
        trades_historique[uid] = []
    
    duration_seconds = trade["exit_time"] - trade["timestamp_open"]
    
    trades_historique[uid].append({
        "trade_id": trade_id,
        "symbol": trade["symbol"],
        "direction": trade["direction"],
        "entry": trade["entry_price"],
        "exit": exit_price,
        "pnl": pnl,
        "duration": duration_seconds,
        "win": trade["state"] == TradeState.TRADE_WIN,
        "timestamp": trade["exit_time"]
    })
    
    # Mettre à jour totaux
    pnl_total[uid] = pnl_total.get(uid, 0) + pnl
    
    # Supprimer des actifs
    del trades_actifs[uid]
    
    print(f"[Trade Closed] {uid}: {trade_id} {trade['symbol']} PnL={pnl:.0f}", flush=True)
    
    return {
        "trade_id": trade_id,
        "pnl": pnl,
        "win": trade["state"] == TradeState.TRADE_WIN,
        "duration": duration_seconds
    }

def utilisateur_a_trade_actif(uid):
    """
    V38 NEW: Vérifier si l'utilisateur a un trade ouvert
    Retourne True si un trade est OUVERT, False sinon
    """
    return uid in trades_actifs and trades_actifs[uid]["state"] == TradeState.TRADE_OPEN

# ==========================================
# AUTRES FONCTIONS (inchangées de V37)
# ==========================================

def prefixer_symbole(s):
    mapping_specifique = {
        "XAUUSD": "frxXAUUSD",
        "XAGUSD": "frxXAGUSD",
    }
    if s in mapping_specifique: 
        return mapping_specifique[s]
    if s in VOLATILE_PAIRS: 
        return f"R_{s.replace('V','')}"
    return f"frx{s}"

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    if symbole_brut in ALL_PAIRS:
        tf = "5min" if granularite == 300 else "1hour"
        
        mapping_fmp = {
            "XAUUSD": "FOREX:XAUUSD",
            "XAGUSD": "FOREX:XAGUSD",
        }
        sym_fmp = mapping_fmp.get(symbole_brut, symbole_brut)
        
        try:
            url = f"https://financialmodelingprep.com/api/v3/historical-chart/{tf}/{sym_fmp}?apikey={FMP_API_KEY}"
            res = requests.get(url, timeout=5).json()
            if isinstance(res, list) and len(res) > 0:
                bougies = []
                for b in reversed(res[:250]):
                    bougies.append({
                        "open": float(b["open"]),
                        "high": float(b["high"]),
                        "low": float(b["low"]),
                        "close": float(b["close"]),
                        "epoch": int(time.time())
                    })
                return bougies
        except Exception as e:
            print(f"[FMP Chart Error - {symbole_brut}] {e}", flush=True)
    
    sym = prefixer_symbole(symbole_brut)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history":sym,"end":"latest","count":250,"style":"candles","granularity":granularite}))
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res and "error" not in res: 
                return res["candles"]
        except:
            try: 
                ws.close()
            except: 
                pass
            time.sleep(0.3)
    return None

def calculer_ema_cloud(df):
    e72 = ta.trend.EMAIndicator(close=df['close'], window=72).ema_indicator()
    e89 = ta.trend.EMAIndicator(close=df['close'], window=89).ema_indicator()
    e180 = ta.trend.EMAIndicator(close=df['close'], window=180).ema_indicator()
    e200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
    r = "BULL" if e72.iloc[-1] > e89.iloc[-1] else "BEAR"
    l = "BULL" if e180.iloc[-1] > e200.iloc[-1] else "BEAR"
    if r=="BULL" and l=="BULL": 
        return "BULL","FORT 🟢🟢"
    if r=="BEAR" and l=="BEAR": 
        return "BEAR","FORT 🔴🔴"
    return r, "MODÉRÉ 🟡"

def trouver_dernier_swing(df, tendance):
    n = 3
    highs = df['high'].values
    lows = df['low'].values
    swing_highs, swing_lows = [], []
    for i in range(n, len(highs)-n):
        if all(highs[i]>highs[i-k] for k in range(1,n+1)) and all(highs[i]>highs[i+k] for k in range(1,n+1)):
            swing_highs.append((i, highs[i]))
        if all(lows[i]<lows[i-k] for k in range(1,n+1)) and all(lows[i]<lows[i+k] for k in range(1,n+1)):
            swing_lows.append((i, lows[i]))
    if not swing_highs or not swing_lows:
        return df['high'].iloc[-40:].max(), df['low'].iloc[-40:].min()
    if tendance == "BEAR":
        for sh in reversed(swing_highs[-5:]):
            after = [sl for sl in swing_lows if sl[0]>sh[0]]
            if after: 
                return sh[1], min(after, key=lambda x: x[1])[1]
    else:
        for sl in reversed(swing_lows[-5:]):
            after = [sh for sh in swing_highs if sh[0]>sl[0]]
            if after: 
                return max(after, key=lambda x: x[1])[1], sl[1]
    return max(swing_highs[-3:], key=lambda x:x[0])[1], max(swing_lows[-3:], key=lambda x:x[0])[1]

def calculer_zone_ote(sh, sl, tendance):
    diff = sh - sl
    if tendance == "BEAR":
        ob, oh = sl+diff*0.618, sl+diff*0.786
        sl_lvl = sh + diff*0.05
        dist = abs(oh - sl_lvl)
        tp1, tp15 = oh - dist, oh - dist*1.5
    else:
        ob, oh = sh-diff*0.786, sh-diff*0.618
        sl_lvl = sl - diff*0.05
        dist = abs(ob - sl_lvl)
        tp1, tp15 = ob + dist, ob + dist*1.5
    return {"ote_bas":round(ob,5),"ote_haut":round(oh,5),"sl":round(sl_lvl,5),"tp_1r":round(tp1,5),"tp_15r":round(tp15,5)}

def detecter_reaction_ote(df, zone, tendance):
    last = df.iloc[-2]
    prev = df.iloc[-3]
    px = last['close']
    dans = zone["ote_bas"] <= px <= zone["ote_haut"]
    pdans= zone["ote_bas"] <= prev['close'] <= zone["ote_haut"]
    if not (dans or pdans): 
        return False, "Hors zone OTE"
    corps = abs(last['close']-last['open'])
    taille = last['high']-last['low']
    meche_h = last['high']-max(last['open'],last['close'])
    meche_b = min(last['open'],last['close'])-last['low']
    if taille == 0: 
        return False, "Bougie doji"
    if tendance == "BEAR":
        if prev['close']>prev['open'] and last['close']<last['open'] and last['close']<prev['open']: 
            return True,"🕯️ Engulfing Baissier"
        if meche_h > corps*2.0: 
            return True,"📍 Pin Bar Baissier"
        if last['close']<last['open'] and corps>taille*0.4: 
            return True,"📉 Rejet Baissier"
    else:
        if prev['close']<prev['open'] and last['close']>last['open'] and last['close']>prev['open']: 
            return True,"🕯️ Engulfing Haussier"
        if meche_b > corps*2.0: 
            return True,"📍 Pin Bar Haussier"
        if last['close']>last['open'] and corps>taille*0.4: 
            return True,"📈 Rejet Haussier"
    return False,"Pas de réaction nette"

def calculer_score_confiance(symbole, tendance, force_ema, rr_ratio, reaction_type, volatilite):
    score = 50
    if "FORT" in force_ema:
        score += 20
    elif "MODÉRÉ" in force_ema:
        score += 10
    else:
        score -= 15
    
    if rr_ratio >= 2.0:
        score += 15
    elif rr_ratio >= 1.5:
        score += 10
    else:
        score -= 10
    
    if "Engulfing" in reaction_type:
        score += 15
    elif "Pin Bar" in reaction_type:
        score += 12
    elif "Rejet" in reaction_type:
        score += 8
    else:
        score -= 10
    
    if volatilite < 0.7:
        score += 5
    elif volatilite > 1.5:
        score -= 10
    
    return max(0, min(100, score))

def analyser_strategie_1_kasper(symbole):
    c5 = obtenir_donnees_deriv(symbole, 300)
    c1h= obtenir_donnees_deriv(symbole, 3600)
    if not c5 or not c1h: 
        return None
    try:
        df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
        dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
        tendance, force = calculer_ema_cloud(dfh)
        
        if "FORT" not in force:
            return None
        
        sh, sl = trouver_dernier_swing(df5, tendance)
        if sh <= sl: 
            return None
        
        zone = calculer_zone_ote(sh, sl, tendance)
        px = df5['close'].iloc[-1]
        if tendance=="BEAR" and px > zone["sl"]: 
            return None
        if tendance=="BULL" and px < zone["sl"]: 
            return None
        react, msg_r = detecter_reaction_ote(df5, zone, tendance)
        if not react: 
            return None
        risque = abs(px-zone["sl"])
        recomp = abs(zone["tp_15r"]-px)
        rr = round(recomp/risque,2) if risque>0 else 0
        
        if rr < 1.5: 
            return None
        
        atr = (dfh['high'] - dfh['low']).rolling(14).mean().iloc[-1]
        volatilite = atr / px if px > 0 else 1.0
        
        confiance = calculer_score_confiance(symbole, tendance, force, rr, msg_r, volatilite)
        
        if confiance < 75:
            return None
        
        return {
            "action": "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force":force, "msg":msg_r,
            "sh":round(sh,5), "sl_swing":round(sl,5),
            "zone":zone, "sl":zone["sl"], "tp1":zone["tp_1r"],
            "tp":zone["tp_15r"], "rr":rr, "px":round(px,5),
            "strategie": 1,
            "confiance": confiance
        }
    except Exception as e:
        print(f"[Kasper/{symbole}] {e}", flush=True)
    return None

def analyser_strategie_2_scalp(symbole):
    c5 = obtenir_donnees_deriv(symbole, 300)
    c1h= obtenir_donnees_deriv(symbole, 3600)
    if not c5 or not c1h: 
        return None
    try:
        df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
        dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
        tendance, force = calculer_ema_cloud(dfh)
        
        sh, sl = trouver_dernier_swing(df5, tendance)
        if sh <= sl: 
            return None
        
        zone = calculer_zone_ote(sh, sl, tendance)
        px = df5['close'].iloc[-1]
        if tendance=="BEAR" and px > zone["sl"]: 
            return None
        if tendance=="BULL" and px < zone["sl"]: 
            return None
        react, msg_r = detecter_reaction_ote(df5, zone, tendance)
        if not react: 
            return None
        risque = abs(px-zone["sl"])
        recomp = abs(zone["tp_15r"]-px)
        rr = round(recomp/risque,2) if risque>0 else 0
        
        if rr < 1.3: 
            return None
        
        atr = (dfh['high'] - dfh['low']).rolling(14).mean().iloc[-1]
        volatilite = atr / px if px > 0 else 1.0
        
        confiance = calculer_score_confiance(symbole, tendance, force, rr, msg_r, volatilite)
        
        if confiance < 55:
            return None
        
        return {
            "action": "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force":force, "msg":msg_r,
            "sh":round(sh,5), "sl_swing":round(sl,5),
            "zone":zone, "sl":zone["sl"], "tp1":zone["tp_1r"],
            "tp":zone["tp_15r"], "rr":rr, "px":round(px,5),
            "strategie": 2,
            "confiance": confiance
        }
    except Exception as e:
        print(f"[Scalping/{symbole}] {e}", flush=True)
    return None

# ==========================================
# GESTION DES CLÉS VIP
# ==========================================

DUREES_VALIDES = {
    "1s": (7, "1 Semaine"),
    "2s": (14, "2 Semaines"),
    "1m": (30, "1 Mois"),
    "3m": (90, "3 Mois"),
    "6m": (180, "6 Mois"),
    "1a": (365, "1 An"),
    "vie": ("LIFETIME", "À VIE 👑"),
}

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: 
        return
    
    parts = message.text.strip().split()
    if len(parts) < 2:
        aide = (
            "⚙️ GÉNÉRATEUR DE CLÉS VIP\n"
            "──────────────────\n"
            "Usage : /keygen <durée>\n\n"
            "Durées disponibles :\n"
            "/keygen 1s → 1 Semaine (7j)\n"
            "/keygen 2s → 2 Semaines (14j)\n"
            "/keygen 1m → 1 Mois (30j)\n"
            "/keygen 3m → 3 Mois (90j)\n"
            "/keygen 6m → 6 Mois (180j)\n"
            "/keygen 1a → 1 An (365j)\n"
            "/keygen vie → À vie 👑\n"
            "/keygen 45 → Nombre de jours personnalisé"
        )
        return bot.send_message(message.chat.id, aide, parse_mode="Markdown")
    
    arg = parts[1].lower().strip()
    
    if arg in DUREES_VALIDES:
        jours, label = DUREES_VALIDES[arg]
    else:
        try:
            jours = int(arg)
            if jours <= 0:
                return bot.send_message(message.chat.id, "❌ Erreur : Le nombre de jours doit être positif.", parse_mode="Markdown")
            label = f"{jours} jours"
        except ValueError:
            valides = " | ".join(DUREES_VALIDES.keys())
            return bot.send_message(message.chat.id, f"❌ Argument invalide. Valeurs : {valides} ou un nombre entier.", parse_mode="Markdown")
    
    cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    cles_generees[cle] = jours
    
    msg = (
        f"✅ CLÉ VIP GÉNÉRÉE\n"
        f"──────────────────\n"
        f"🔑 Clé : {cle}\n"
        f"⏳ Durée : {label}\n"
        f"──────────────────\n"
        f"L'abonné active avec : /vip {cle}"
    )
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    cid = message.chat.id
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(cid, "⚠️ Usage : /vip VOTRE-CLÉ", parse_mode="Markdown")
    cle = parts[1].strip()
    if cle not in cles_generees:
        return bot.send_message(cid, "❌ Clé invalide, expirée ou déjà utilisée.", parse_mode="Markdown")
    
    jours = cles_generees.pop(cle)
    if jours == "LIFETIME":
        utilisateurs_autorises[cid] = "LIFETIME"
        txt = "À VIE 👑"
    else:
        exp = datetime.datetime.now() + datetime.timedelta(days=jours)
        utilisateurs_autorises[cid] = exp
        txt = exp.strftime('%d/%m/%Y à %H:%M')
    
    bot.send_message(cid,
        f"🎉 ACCÈS TERMINAL PRIME DÉVERROUILLÉ !\n"
        f"──────────────────\n"
        f"⏳ Expiration : {txt}\n\n"
        f"👉 Tape /start pour initialiser ton tableau de bord.",
        parse_mode="Markdown")

@bot.message_handler(commands=['abonnes'])
def lister_abonnes(message):
    if message.chat.id != ADMIN_ID: 
        return
    if not utilisateurs_autorises: 
        return bot.send_message(message.chat.id, "Aucun abonné actif.")
    
    lignes = ["👥 ABONNÉS ACTIFS :\n──────────────────"]
    now = datetime.datetime.now()
    for uid, exp in utilisateurs_autorises.items():
        if uid == ADMIN_ID: 
            continue
        if exp == "LIFETIME": 
            statut = "👑 À vie"
        elif now < exp: 
            statut = f"✅ {(exp - now).days}j restants (exp: {exp.strftime('%d/%m/%Y')})"
        else: 
            statut = "❌ Expiré"
        lignes.append(f"• {uid} → {statut}")
    bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(commands=['cles'])
def lister_cles(message):
    if message.chat.id != ADMIN_ID: 
        return
    if not cles_generees: 
        return bot.send_message(message.chat.id, "Aucune clé en attente.")
    
    lignes = ["🔑 CLÉS EN ATTENTE :\n──────────────────"]
    for cle, jours in cles_generees.items():
        dur = "À VIE" if jours == "LIFETIME" else f"{jours}j"
        lignes.append(f"{cle} → {dur}")
    bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")

def est_autorise(uid):
    if uid == ADMIN_ID: 
        return True
    if uid in utilisateurs_autorises:
        exp = utilisateurs_autorises[uid]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: 
            return True
        del utilisateurs_autorises[uid]
        try: 
            bot.send_message(uid, "⚠️ Abonnement expiré.\nContacte l'admin.", parse_mode="Markdown")
        except: 
            pass
    return False

# ==========================================
# KILLZONES & HORAIRES
# ==========================================

PAIRES_SESSION_ASIE = ["AUDJPY","CADJPY","CHFJPY","USDJPY","EURJPY","AUDUSD","AUDCAD","XAUUSD","XAGUSD"]
PAIRES_SESSION_LONDRES = ["EURUSD","GBPUSD","EURCHF","USDCHF","CADCHF","EURJPY","EURAUD","XAUUSD","XAGUSD"]
PAIRES_SESSION_NY = ["EURUSD","GBPUSD","USDCAD","USDCHF","AUDUSD","XAUUSD","XAGUSD"]

def get_session_active():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute/60.0
    paires_actives, sessions_actives = [], []
    
    if 0.0 <= h < 7.0:
        paires_actives += PAIRES_SESSION_ASIE
        sessions_actives.append("ASIE")
    if 7.0 <= h < 8.0:
        paires_actives += PAIRES_SESSION_ASIE + PAIRES_SESSION_LONDRES
        sessions_actives.append("ASIE+LONDRES")
    if 8.0 <= h <= 10.0:
        paires_actives += PAIRES_SESSION_LONDRES
        sessions_actives.append("LONDRES")
    if 12.0 <= h <= 15.0:
        paires_actives += PAIRES_SESSION_NY
        sessions_actives.append("NEW_YORK")
    
    if not sessions_actives: 
        return None, []
    return "+".join(sessions_actives), list(dict.fromkeys(paires_actives))

def dans_killzone():
    session, _ = get_session_active()
    return session is not None

def nom_killzone():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute/60.0
    if 7.0 <= h < 8.0: 
        return "🌏🇬🇧 Asie+Londres (07h-08h)"
    if 0.0 <= h < 7.0: 
        return "🌏 Asian Killzone (00h-07h)"
    if 8.0 <= h <= 10.0: 
        return "🇬🇧 London Killzone (08h-10h)"
    if 12.0 <= h <= 15.0: 
        return "🇺🇸 New York Killzone (12h-15h)"
    return "⏳ Hors session"

def est_symbole_autorise(symbole):
    if symbole in VOLATILE_PAIRS:
        if not volatility_scan_active:
            return "BLOCAGE_TOTAL", "Volatility désactivé"
        return "AUTORISE", ""
    
    now = datetime.datetime.utcnow()
    j = now.weekday()
    h = now.hour + now.minute/60.0
    weekend = (j==4 and h>=21) or j==5 or (j==6 and h<21)
    
    if weekend:
        return ("AUTORISE","") if symbole in VOLATILE_PAIRS else ("BLOCAGE_TOTAL","Week-end")
    
    if symbole in COMMODITY_PAIRS:
        return "AUTORISE", ""
    
    session, paires_session = get_session_active()
    if session is None: 
        return "HORS_SESSION", "🔒 Hors Killzone"
    if symbole in paires_session: 
        return "AUTORISE", ""
    
    return "HORS_SESSION", f"🔒 {symbole} inactif en session {session}"

# ==========================================
# ✅ V38 NEW: SCANNER AVEC BLOCAGE DE TRADES
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            libres = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not libres: 
                continue
            
            paires_a_scanner = ELITE_PAIRS_MT5 + FOREX_PAIRS
            
            for paire in paires_a_scanner:
                statut,_ = est_symbole_autorise(paire)
                if statut != "AUTORISE": 
                    continue
                
                # ✅ V38: Analyser STRATÉGIE 1
                res1 = analyser_strategie_1_kasper(paire)
                if res1:
                    px = obtenir_prix_broker_realtime(paire) or res1['px']
                    
                    # ✅ V38 NEW: VALIDER LE PRIX AVANT ENVOI
                    if not valider_prix_avant_signal(paire, px):
                        print(f"[Scanner] {paire} REJETÉ - Désynchronisation détectée", flush=True)
                        continue
                    
                    cle = f"{paire}_STR1"
                    signaux_cache[cle] = {
                        'time':time.time(), 'action':res1['action'],
                        'mt5_sl':res1['sl'], 'mt5_tp':res1['tp'],
                        'mt5_tp1':res1['tp1'], 'mt5_rr':res1['rr'],
                        'zone':res1['zone'], 'sh':res1['sh'],
                        'sl_swing':res1['sl_swing'], 'force':res1['force'],
                        'msg':res1['msg'], 'dur':300, 'confiance':res1['confiance'],
                        'strategie':1
                    }
                    derniere_alerte_auto[cle] = time.time()
                    
                    nom = NOMS_AFFICHAGE.get(paire, f"{paire[:3]}/{paire[3:]}")
                    dir_ = "🟢 BUY" if "BUY" in res1['action'] else "🔴 SELL"
                    z = res1['zone']
                    
                    for uid in libres:
                        # ✅ V38 NEW: VÉRIFIER QUE L'UTILISATEUR N'A PAS DE TRADE ACTIF
                        if utilisateur_a_trade_actif(uid):
                            continue  # Skipper cet utilisateur, il a un trade ouvert
                        
                        pf = plateforme_trading.get(uid,"MT5")
                        if pf=="MT5" and paire not in ELITE_PAIRS_MT5: 
                            continue
                        if pf=="POCKET" and paire not in FOREX_PAIRS: 
                            continue
                        
                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Copier {nom}", callback_data=f"set_{paire}"))
                        txt = (
                            f"🎯 **STRATÉGIE 1** — KASPER OTE STRICT\n"
                            f"{nom} {dir_}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"☁️ EMA : {res1['force']}\n"
                            f"📍 {res1['msg']}\n"
                            f"⏰ {nom_killzone()}\n"
                            f"🔶 Zone OTE : {z['ote_bas']:.5f} → {z['ote_haut']:.5f}\n"
                            f"⚖️ R/R : {res1['rr']}R\n"
                            f"🎖️ Confiance : {res1['confiance']}%\n"
                            f"💰 Prix réel (validé) : {px:.5f}"
                        )
                        try: 
                            bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                        except: 
                            pass
                
                # ✅ V38: Analyser STRATÉGIE 2
                res2 = analyser_strategie_2_scalp(paire)
                if res2:
                    px = obtenir_prix_broker_realtime(paire) or res2['px']
                    
                    # ✅ V38 NEW: VALIDER LE PRIX AVANT ENVOI
                    if not valider_prix_avant_signal(paire, px):
                        print(f"[Scanner] {paire} REJETÉ - Désynchronisation détectée", flush=True)
                        continue
                    
                    cle = f"{paire}_STR2"
                    signaux_cache[cle] = {
                        'time':time.time(), 'action':res2['action'],
                        'mt5_sl':res2['sl'], 'mt5_tp':res2['tp'],
                        'mt5_tp1':res2['tp1'], 'mt5_rr':res2['rr'],
                        'zone':res2['zone'], 'sh':res2['sh'],
                        'sl_swing':res2['sl_swing'], 'force':res2['force'],
                        'msg':res2['msg'], 'dur':300, 'confiance':res2['confiance'],
                        'strategie':2
                    }
                    derniere_alerte_auto[cle] = time.time()
                    
                    nom = NOMS_AFFICHAGE.get(paire, f"{paire[:3]}/{paire[3:]}")
                    dir_ = "🟢 BUY" if "BUY" in res2['action'] else "🔴 SELL"
                    z = res2['zone']
                    
                    for uid in libres:
                        # ✅ V38 NEW: VÉRIFIER QUE L'UTILISATEUR N'A PAS DE TRADE ACTIF
                        if utilisateur_a_trade_actif(uid):
                            continue
                        
                        pf = plateforme_trading.get(uid,"MT5")
                        if pf=="MT5" and paire not in ELITE_PAIRS_MT5: 
                            continue
                        if pf=="POCKET" and paire not in FOREX_PAIRS: 
                            continue
                        
                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Copier {nom}", callback_data=f"set_{paire}"))
                        txt = (
                            f"🎯 **STRATÉGIE 2** — OTE SCALPING AGRESSIF\n"
                            f"{nom} {dir_}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"☁️ EMA : {res2['force']}\n"
                            f"📍 {res2['msg']}\n"
                            f"⏰ {nom_killzone()}\n"
                            f"🔶 Zone OTE : {z['ote_bas']:.5f} → {z['ote_haut']:.5f}\n"
                            f"⚖️ R/R : {res2['rr']}R\n"
                            f"🎖️ Confiance : {res2['confiance']}%\n"
                            f"💰 Prix réel (validé) : {px:.5f}"
                        )
                        try: 
                            bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                        except: 
                            pass
        
        except Exception as e:
            print(f"[Scanner] ⚠️ {e}", flush=True)

# ==========================================
# ✅ V38 NEW: MONITORING DES TRADES ACTIFS
# ==========================================

def monitorer_trades_actifs():
    """
    V38 NEW: Monitoring des trades en temps réel.
    Vérifie si le TP ou SL est atteint toutes les 5 secondes.
    Envoie message de clôture à l'utilisateur.
    """
    while True:
        try:
            time.sleep(5)
            
            for uid in list(trades_actifs.keys()):
                if uid not in trades_actifs:
                    continue
                
                trade = trades_actifs[uid]
                if trade["state"] != TradeState.TRADE_OPEN:
                    continue
                
                symbole = trade["symbol"]
                prix_current = obtenir_prix_broker_realtime(symbole)
                
                if not prix_current:
                    continue
                
                # Vérifier TP
                if trade["direction"] == "BUY":
                    if prix_current >= trade["tp"]:
                        result = fermer_trade(uid, prix_current, win=True)
                        if result:
                            msg = (
                                f"✅ **TRADE GAGNÉ!** 🎉\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 {symbole}\n"
                                f"Entrée: {trade['entry_price']:.5f}\n"
                                f"Sortie: {prix_current:.5f}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"💰 **Profit: +{result['pnl']:.0f} USD**\n"
                                f"⏱️ Durée: {int(result['duration']/60)} minutes\n"
                                f"🎖️ Stratégie {trade['strategy']} (Confiance {trade['confiance']}%)\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏦 Balance: ${pnl_total.get(uid, 0):.0f} USD"
                            )
                            try:
                                bot.send_message(uid, msg, parse_mode="Markdown")
                            except:
                                pass
                    
                    # Vérifier SL
                    elif prix_current <= trade["sl"]:
                        result = fermer_trade(uid, prix_current, win=False)
                        if result:
                            msg = (
                                f"❌ **TRADE PERDU** 😔\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 {symbole}\n"
                                f"Entrée: {trade['entry_price']:.5f}\n"
                                f"Sortie: {prix_current:.5f}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"💔 **Perte: {result['pnl']:.0f} USD**\n"
                                f"⏱️ Durée: {int(result['duration']/60)} minutes\n"
                                f"🎖️ Stratégie {trade['strategy']} (Confiance {trade['confiance']}%)\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏦 Balance: ${pnl_total.get(uid, 0):.0f} USD"
                            )
                            try:
                                bot.send_message(uid, msg, parse_mode="Markdown")
                            except:
                                pass
                
                else:  # SELL
                    if prix_current <= trade["tp"]:
                        result = fermer_trade(uid, prix_current, win=True)
                        if result:
                            msg = (
                                f"✅ **TRADE GAGNÉ!** 🎉\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 {symbole}\n"
                                f"Entrée: {trade['entry_price']:.5f}\n"
                                f"Sortie: {prix_current:.5f}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"💰 **Profit: +{result['pnl']:.0f} USD**\n"
                                f"⏱️ Durée: {int(result['duration']/60)} minutes\n"
                                f"🎖️ Stratégie {trade['strategy']} (Confiance {trade['confiance']}%)\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏦 Balance: ${pnl_total.get(uid, 0):.0f} USD"
                            )
                            try:
                                bot.send_message(uid, msg, parse_mode="Markdown")
                            except:
                                pass
                    
                    elif prix_current >= trade["sl"]:
                        result = fermer_trade(uid, prix_current, win=False)
                        if result:
                            msg = (
                                f"❌ **TRADE PERDU** 😔\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 {symbole}\n"
                                f"Entrée: {trade['entry_price']:.5f}\n"
                                f"Sortie: {prix_current:.5f}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"💔 **Perte: {result['pnl']:.0f} USD**\n"
                                f"⏱️ Durée: {int(result['duration']/60)} minutes\n"
                                f"🎖️ Stratégie {trade['strategy']} (Confiance {trade['confiance']}%)\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏦 Balance: ${pnl_total.get(uid, 0):.0f} USD"
                            )
                            try:
                                bot.send_message(uid, msg, parse_mode="Markdown")
                            except:
                                pass
        
        except Exception as e:
            print(f"[Trade Monitor] ⚠️ {e}", flush=True)

# ==========================================
# INTERFACE TELEGRAM
# ==========================================

def obtenir_clavier(uid):
    pf = plateforme_trading.get(uid,"MT5")
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("🏦 BROKER: POCKET" if pf=="POCKET" else "📈 BROKER: MT5"), KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid): 
        return bot.send_message(uid,"🔒 Accès restreint. Utilise /vip VOTRE-CLÉ pour activer.")
    utilisateurs_actifs.add(uid)
    plateforme_trading.setdefault(uid,"MT5")
    kz = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    volatility_status = "✅ Activé" if volatility_scan_active else "❌ Désactivé"
    
    # Infos du trading actif
    trade_info = ""
    if uid in trades_actifs:
        t = trades_actifs[uid]
        trade_info = f"\n🟠 **TRADE ACTIF:**\n{t['symbol']} {t['direction']} @ {t['entry_price']}"
    
    bot.send_message(uid,
        f"🏴‍☠️ **TERMINAL PRIME V38** 🔥\n"
        f"──────────────────\n"
        f"✅ **V38 FIXES:**\n"
        f"  ✓ Prix réel-time (broker sync)\n"
        f"  ✓ Blocage signaux pendant trade\n"
        f"  ✓ Messages: ✅ GAGNÉ / ❌ PERDU\n"
        f"──────────────────\n"
        f"✅ **Dual Strategy:**\n"
        f"  🔹 Kasper OTE STRICT (75%+)\n"
        f"  🔹 OTE Scalping AGRESSIF (55%+)\n"
        f"──────────────────\n"
        f"📈 **Mode MT5:**\n"
        f"  ⚡ Volatility | 🥇 Gold | 🥈 Argent\n"
        f"  {volatility_status}\n"
        f"⏰ **Killzone:** {kz}{trade_info}",
        reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_pf(message):
    uid = message.chat.id
    if not est_autorise(uid): 
        return
    if plateforme_trading.get(uid,"MT5")=="POCKET":
        plateforme_trading[uid]="MT5"
        bot.send_message(uid,"📈 **MT5 ACTIVÉ**\n🔥 Volatility | 🥇 Gold | 🥈 Argent",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")
    else:
        plateforme_trading[uid]="POCKET"
        bot.send_message(uid,"🏦 **POCKET ACTIVÉ**\nForex Binaire",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text=="⏰ HEURES DE TRADING")
def horaires(message):
    kz = "🟢 EN COURS" if dans_killzone() else "🔴 INACTIVE"
    volatility_status = "✅ Activé" if volatility_scan_active else "❌ Désactivé"
    bot.send_message(message.chat.id,
        f"🕒 **KILLZONES OTE**\n\n"
        f"🌏 Asie : 00:00 – 07:00 GMT\n"
        f"🇬🇧 Londres : 08:00 – 10:00 GMT\n"
        f"🇺🇸 New York : 12:00 – 15:00 GMT\n\n"
        f"⏰ Statut : {kz}\n"
        f"🔥 Volatility : {volatility_status}",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE","📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): 
        return
    
    # ✅ V38 NEW: Vérifier si utilisateur a un trade actif
    if uid in trades_actifs:
        return bot.send_message(uid,
            f"🟠 **TRADE ACTIF EN COURS**\n"
            f"⏳ Vous ne pouvez pas ouvrir un autre trade pour le moment.\n"
            f"Attendez la clôture: TP ou SL atteint.",
            parse_mode="Markdown")
    
    pf = plateforme_trading.get(uid,"MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    
    if pf == "MT5":
        if volatility_scan_active:
            markup.add(InlineKeyboardButton("🔥 V10", callback_data="set_V10"), InlineKeyboardButton("🔥 V25", callback_data="set_V25"), InlineKeyboardButton("🔥 V50", callback_data="set_V50"))
            markup.add(InlineKeyboardButton("⚡ V75", callback_data="set_V75"), InlineKeyboardButton("💥 V100", callback_data="set_V100"))
        markup.add(InlineKeyboardButton("🥇 GOLD", callback_data="set_XAUUSD"), InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"))
        bot.send_message(uid,"🎯 Sélectionne ta cible MT5 :",reply_markup=markup,parse_mode="Markdown")
    else:
        markup.add(InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇬🇧 GBP/USD", callback_data="set_GBPUSD"), InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"))
        markup.add(InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"))
        bot.send_message(uid,"🎯 Sélectionne ta cible Pocket Forex :",reply_markup=markup,parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): 
        return
    
    # ✅ V38 NEW: Vérifier blocage
    if uid in trades_actifs:
        try:
            bot.answer_callback_query(call.id,"🟠 Trade actif! Attendez la clôture.",show_alert=True)
        except:
            pass
        return
    
    actif = call.data.replace("set_","")
    user_prefs[getattr(call,'from_user',type('o',(object,),{'id':uid})()).id] = actif
    
    try: 
        bot.delete_message(uid, call.message.message_id)
    except: 
        pass
    
    cle1 = f"{actif}_STR1"
    cle2 = f"{actif}_STR2"
    cache = signaux_cache.get(cle1) or signaux_cache.get(cle2)
    
    if not cache or (time.time()-cache['time']) > 90:
        return bot.send_message(uid, f"⏱️ Signal OTE expiré sur {NOMS_AFFICHAGE.get(actif,actif)}\nAttends le radar auto.", parse_mode="Markdown")
    
    px = obtenir_prix_broker_realtime(actif) or cache['mt5_rr']
    nom = NOMS_AFFICHAGE.get(actif, actif)
    z = cache.get('zone',{})
    dir_ = "🟢 BUY MARKET" if "BUY" in cache['action'] else "🔴 SELL MARKET"
    fmt = ".0f" if actif in VOLATILE_PAIRS else ".5f"
    strat_label = "STR 1 (STRICT)" if cache['strategie'] == 1 else "STR 2 (AGRESSIF)"
    
    # ✅ V38 NEW: Ouvrir le trade
    entry_direction = "BUY" if "BUY" in cache['action'] else "SELL"
    trade_id = ouvrir_trade(uid, actif, entry_direction, px, cache['mt5_sl'], cache['mt5_tp'], cache['strategie'], cache['confiance'])
    
    signal = (
        f"🎯 {strat_label} — {nom}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{dir_}\n"
        f"☁️ EMA : {cache.get('force','—')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Prix entrée: {px:{fmt}}\n"
        f"🛑 SL : {cache['mt5_sl']:{fmt}}\n"
        f"🎯 TP 1.5R : {cache['mt5_tp']:{fmt}}\n"
        f"⚖️ R/R : {cache['mt5_rr']:.2f}R\n"
        f"🎖️ Confiance : {cache.get('confiance',0)}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ **TRADE OUVERT**\n"
        f"🆔 {trade_id}\n"
        f"⏳ En attente de TP ou SL...\n"
        f"💬 Vous recevrez un message quand le trade sera fermé."
    )
    bot.send_message(uid, signal, parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================


# ==========================================
# ✅ V39 NEW: STRATÉGIE 3 — ZONE TRADING
# ==========================================

def identifier_zone_consolidation(df, lookback=50):
    """Identifier une zone de consolidation (range-bound)"""
    df_recent = df.iloc[-lookback:] if len(df) > lookback else df
    
    recent_high = df_recent['high'].max()
    recent_low = df_recent['low'].min()
    
    zone = {
        'resistance': recent_high,
        'support': recent_low,
        'width': recent_high - recent_low
    }
    
    rebond_count_up = 0
    rebond_count_down = 0
    
    for i in range(len(df_recent)):
        low = df_recent['low'].iloc[i]
        high = df_recent['high'].iloc[i]
        close = df_recent['close'].iloc[i]
        
        if low < zone['support'] * 1.002 and close > zone['support'] * 1.005:
            rebond_count_up += 1
        
        if high > zone['resistance'] * 0.998 and close < zone['resistance'] * 0.995:
            rebond_count_down += 1
    
    zone['rebond_count'] = rebond_count_up + rebond_count_down
    
    if zone['rebond_count'] < 3:
        return None
    
    return zone

def analyser_strategie_3_zone_trading(symbole):
    """V39 NEW: Zone Trading Strategy"""
    c4h = obtenir_donnees_deriv(symbole, 14400)  # 4H
    c1h = obtenir_donnees_deriv(symbole, 3600)   # 1H
    
    if not c4h or not c1h:
        return None
    
    try:
        df4h = pd.DataFrame([{
            'open': float(c['open']),
            'close': float(c['close']),
            'high': float(c['high']),
            'low': float(c['low'])
        } for c in c4h])
        
        df1h = pd.DataFrame([{
            'open': float(c['open']),
            'close': float(c['close']),
            'high': float(c['high']),
            'low': float(c['low'])
        } for c in c1h])
        
        zone = identifier_zone_consolidation(df4h, lookback=50)
        if not zone:
            return None
        
        vol_zone = (df4h.iloc[-50:]['high'] - df4h.iloc[-50:]['low']).std()
        vol_general = (df4h['high'] - df4h['low']).std()
        
        if vol_zone > vol_general * 0.7:
            return None
        
        px_current = df4h['close'].iloc[-1]
        zone_width = zone['resistance'] - zone['support']
        
        distance_from_support = px_current - zone['support']
        distance_from_resistance = zone['resistance'] - px_current
        
        signal = None
        direction = None
        
        if distance_from_support < zone_width * 0.2:
            last = df1h.iloc[-2]
            if last['low'] < zone['support'] * 1.002 and last['close'] > zone['support']:
                signal = "BUY"
                direction = "BULL"
                sl = zone['support'] - (zone_width * 0.05)
                tp = zone['resistance']
        
        elif distance_from_resistance < zone_width * 0.2:
            last = df1h.iloc[-2]
            if last['high'] > zone['resistance'] * 0.998 and last['close'] < zone['resistance']:
                signal = "SELL"
                direction = "BEAR"
                sl = zone['resistance'] + (zone_width * 0.05)
                tp = zone['support']
        
        if not signal:
            return None
        
        risque = abs(px_current - sl)
        recompense = abs(tp - px_current)
        rr = round(recompense / risque, 2) if risque > 0 else 0
        
        if rr < 1.5:
            return None
        
        try:
            rsi = ta.momentum.RSIIndicator(close=df1h['close'], window=14).rsi()
            rsi_current = rsi.iloc[-1]
            
            if direction == "BULL":
                oscillateur_ok = rsi_current < 70 and rsi_current > 20
            else:
                oscillateur_ok = rsi_current > 30 and rsi_current < 80
        except:
            oscillateur_ok = False
        
        confiance = 50
        if zone['rebond_count'] >= 5:
            confiance += 15
        elif zone['rebond_count'] >= 3:
            confiance += 10
        
        if vol_zone / vol_general < 0.5:
            confiance += 15
        elif vol_zone / vol_general < 0.7:
            confiance += 8
        
        if rr >= 2.0:
            confiance += 15
        elif rr >= 1.5:
            confiance += 10
        
        if oscillateur_ok:
            confiance += 10
        
        confiance = max(0, min(100, confiance))
        
        if confiance < 60:
            return None
        
        return {
            "action": "🟢 ACHAT (BUY)" if signal == "BUY" else "🔴 VENTE (SELL)",
            "direction": direction,
            "strategy": 3,
            "confiance": confiance,
            "zone_support": round(zone['support'], 5),
            "zone_resistance": round(zone['resistance'], 5),
            "zone_width": round(zone_width, 5),
            "zone_rebonds": zone['rebond_count'],
            "sl": round(sl, 5),
            "tp": round(tp, 5),
            "rr": rr,
            "px": round(px_current, 5),
            "vol_ratio": round(vol_zone / vol_general, 2)
        }
    except Exception as e:
        print(f"[Zone Trading/{symbole}] {e}", flush=True)
    
    return None

# V39: Variables d'état Stratégie 3
signaux_strategie_3 = {}

if __name__=="__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=monitorer_trades_actifs, daemon=True).start()  # ✅ V38 NEW
    print("⬛ TERMINAL PRIME V38 — Real-time Price Sync + Trade State Management", flush=True)
    bot.infinity_polling()

