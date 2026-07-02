"""
╔════════════════════════════════════════════════════════════════════════════╗
║              TERMINAL PRIME V45 — THE MASTER CLASS EDITION                ║
║                                                                            ║
║  Base V44 (Moteur Ultra-Rapide + Filets de Sécurité Anti-Bug)             ║
║  + STRATÉGIES 100% REMPLACÉES PAR LES CONCEPTS DES 6 PDF FOURNIS :        ║
║                                                                            ║
║   📘 Stratégie 1: CPR Pullback & Price Action (Vikram Prabhu)             ║
║   📘 Stratégie 2: Open Drive Breakout PDH/PDL (Vikram Prabhu)             ║
║   📘 Stratégie 3: RSI Extremes & Exhaustion Wicks (Dr Investors / SRC)    ║
║   📘 Modèles de Chandeliers stricts: Pin Bar, Engulfing, Marubozu         ║
║   📘 Gestion Smart Raja: Risque 1% statique, TP partiel, Break-Even       ║
╚════════════════════════════════════════════════════════════════════════════╝
"""

import os
import datetime
import random
import time
import string
import json
import math
import websocket
import pandas as pd
import ta
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Lock
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAGA1Tsa-Qw5iGTzGASeYAWZhMl4fggsGiA"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# RISK MANAGEMENT — CONFIGURATION GLOBALE
# ==========================================

RISK_CONFIG = {
    "risk_per_trade_pct": 1.0,        
    "daily_loss_limit_pct": 5.0,      
    "max_consecutive_losses": 3,      
    "pause_duration_minutes": 120,    
    "partial_tp_ratio": 0.85,         
    "breakeven_buffer_pct": 0.0005,   
    "trailing_stop_distance_pct": 0.003, 
    "max_trades_per_day": 8,          
    "max_trade_age_hours": 12,        
    "signal_validity_seconds": 45,    
    "max_rr_degradation_pct": 40,     
}

# ==========================================
# ÉTATS DE TRADE & LISTES DE PAIRES
# ==========================================

class TradeState(Enum):
    TRADE_OPEN      = "TRADE_OUVERT"
    TRADE_PARTIAL   = "TP1_PARTIEL_BE"     
    TRADE_WIN       = "GAGNÉ"
    TRADE_LOSS      = "PERDU"
    CANCELLED       = "ANNULÉ"

VOLATILE_PAIRS  = ["V10","V25","V50","V75","V100"]
COMMODITY_PAIRS = ["XAUUSD","XAGUSD"]
FOREX_PAIRS     = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD","AUDJPY",
                   "EURAUD","EURUSD","AUDCAD","USDCHF","CADCHF","EURCHF",
                   "USDJPY","GBPUSD"]

ELITE_PAIRS_MT5 = VOLATILE_PAIRS + COMMODITY_PAIRS
ALL_PAIRS       = VOLATILE_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS

NOMS_AFFICHAGE = {
    "XAUUSD":"🥇 GOLD","XAGUSD":"🥈 ARGENT",
    "V10":"🔥 V10","V25":"🔥 V25","V50":"🔥 V50",
    "V75":"⚡ V75","V100":"💥 V100",
}

# ==========================================
# VARIABLES D'ÉTAT GLOBALES
# ==========================================

user_prefs           = {}
plateforme_trading   = {}
utilisateurs_actifs  = set()
derniere_alerte_auto = {}
signaux_cache        = {}
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees        = {}

volatility_pairs_active = {"V10": True, "V25": True, "V50": True, "V75": True, "V100": True}

trades_actifs     = {}   
trades_historique = {}   
prix_broker       = {}   

pnl_total  = {}
win_count  = {}
loss_count = {}

contexte_marche_cache = {}
daily_stats = {}   
lock_trade = Lock()

_candles_cache = {}
_candles_cache_lock = Lock()
CANDLES_CACHE_TTL = 20  

# ==========================================
# KEEP ALIVE
# ==========================================
app = Flask(__name__)
@app.route('/')
def home(): return "Terminal Prime V45 — Master Class Edition"
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

# ==========================================
# UTILITAIRES PRIX & CACHE MOTEUR V44
# ==========================================

def prefixer_symbole(s):
    if s in {"XAUUSD":"frxXAUUSD","XAGUSD":"frxXAGUSD"}: return {"XAUUSD":"frxXAUUSD","XAGUSD":"frxXAGUSD"}[s]
    if s in VOLATILE_PAIRS: return f"R_{s.replace('V','')}"
    return f"frx{s}"

def _obtenir_donnees_deriv_reseau(symbole_brut, granularite=300):
    if symbole_brut in ALL_PAIRS:
        tf = "5min" if granularite == 300 else ("1hour" if granularite == 3600 else "4hour")
        sym_fmp = {"XAUUSD":"FOREX:XAUUSD","XAGUSD":"FOREX:XAGUSD"}.get(symbole_brut, symbole_brut)
        try:
            url = f"https://financialmodelingprep.com/api/v3/historical-chart/{tf}/{sym_fmp}?apikey={FMP_API_KEY}"
            res = requests.get(url, timeout=3).json()
            if isinstance(res, list) and len(res) > 0:
                bougies = []
                for b in reversed(res[:250]):
                    bougies.append({"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"]), "epoch": int(time.time())})
                return bougies
        except: pass

    sym = prefixer_symbole(symbole_brut)
    gran_real = granularite if granularite in (300, 3600) else 14400
    for _ in range(2):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=4)
            ws.send(json.dumps({"ticks_history": sym, "end": "latest", "count": 250, "style": "candles", "granularity": gran_real}))
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res and "error" not in res: return res["candles"]
        except: time.sleep(0.2)
    return None

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    cle = (symbole_brut, granularite)
    now = time.time()
    with _candles_cache_lock:
        cached = _candles_cache.get(cle)
        if cached and (now - cached[0]) < CANDLES_CACHE_TTL: return cached[1]
    data = _obtenir_donnees_deriv_reseau(symbole_brut, granularite)
    if data is not None:
        with _candles_cache_lock: _candles_cache[cle] = (now, data)
    return data

def obtenir_prix_broker_realtime(symbole):
    try:
        sym_fmp = {"XAUUSD":"FOREX:XAUUSD","XAGUSD":"FOREX:XAGUSD"}.get(symbole, symbole)
        url = f"https://financialmodelingprep.com/api/v3/quote/{sym_fmp}?apikey={FMP_API_KEY}"
        res = requests.get(url, timeout=2).json()
        if isinstance(res, list) and len(res) > 0:
            prix = float(res[0]["price"])
            prix_broker[symbole] = {"price": prix, "timestamp": time.time()}
            return prix
    except: pass

    sym = prefixer_symbole(symbole)
    for _ in range(2):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=3)
            ws.send(json.dumps({"ticks": sym}))
            res = json.loads(ws.recv())
            ws.close()
            if "tick" in res:
                prix = float(res["tick"]["quote"])
                prix_broker[symbole] = {"price": prix, "timestamp": time.time()}
                return prix
        except: time.sleep(0.2)
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.001):
    prix_real = obtenir_prix_broker_realtime(symbole)
    if not prix_real: return False
    decalage = abs(prix_bot - prix_real) / prix_real
    return decalage <= tolerance

# ==========================================
# 📘 NOUVEAUX INDICATEURS (PDF)
# ==========================================

def calculer_cpr_journalier(symbole):
    """
    Extrait le CPR de la veille (Pivot, BCPR, TCPR) et les niveaux PDH/PDL.
    Agrège les bougies H1 pour créer un profil journalier précis.
    """
    h1 = obtenir_donnees_deriv(symbole, 3600)
    if not h1 or len(h1) < 48: return None
    
    df = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"]), "epoch":c["epoch"]} for c in h1])
    df['date'] = pd.to_datetime(df['epoch'], unit='s').dt.date
    daily = df.groupby('date').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last'}).reset_index()
    
    if len(daily) < 2: return None
    prev_day = daily.iloc[-2] # La journée complétée la plus récente
    
    pdh, pdl, pdc = prev_day['high'], prev_day['low'], prev_day['close']
    pivot = (pdh + pdl + pdc) / 3
    bcpr = (pdh + pdl) / 2
    tcpr = (pivot - bcpr) + pivot
    
    top_cpr = max(bcpr, tcpr)
    bot_cpr = min(bcpr, tcpr)
    
    # Évaluation de la largeur du CPR (Tendance ou Range selon Vikram Prabhu)
    cpr_width_pct = ((top_cpr - bot_cpr) / pivot) * 100
    etat_cpr = "Étroit (Tendance)" if cpr_width_pct < 0.15 else "Large (Range)"
    
    return {
        "PDH": pdh, "PDL": pdl, "PIVOT": pivot, 
        "TCPR": top_cpr, "BCPR": bot_cpr, 
        "ETAT": etat_cpr, "WIDTH": cpr_width_pct
    }

def detecter_chandeliers_pdf(df):
    """Détecte les Pin Bars, Engulfing et Marubozu selon les règles du PDF Candlesticks."""
    if len(df) < 3: return "NONE", 0
    last = df.iloc[-2] # Bougie fraîchement clôturée
    prev = df.iloc[-3]
    
    body = abs(last['close'] - last['open'])
    range_total = last['high'] - last['low']
    if range_total == 0: return "NONE", 0
    
    upper_wick = last['high'] - max(last['open'], last['close'])
    lower_wick = min(last['open'], last['close']) - last['low']
    
    # Pin Bar (Marteau / Etoile filante) - Mèche > 2x corps
    if lower_wick > body * 2.0 and upper_wick < body: return "PIN_BULL", lower_wick
    if upper_wick > body * 2.0 and lower_wick < body: return "PIN_BEAR", upper_wick
    
    # Engulfing (Avalement)
    prev_body = abs(prev['close'] - prev['open'])
    if prev['close'] < prev['open'] and last['close'] > last['open'] and last['close'] > prev['open'] and last['open'] < prev['close']:
        return "ENGULFING_BULL", body
    if prev['close'] > prev['open'] and last['close'] < last['open'] and last['close'] < prev['open'] and last['open'] > prev['close']:
        return "ENGULFING_BEAR", body
        
    # Marubozu (85% corps, pas de mèche)
    if body > range_total * 0.85:
        return "MARUBOZU_BULL" if last['close'] > last['open'] else "MARUBOZU_BEAR", body
        
    return "NONE", 0

# ==========================================
# 📘 STRATÉGIE 1 : CPR REJECTION (Vikram Prabhu)
# ==========================================

def analyser_cpr_rejection(symbole):
    """
    Le prix retourne vers le CPR (ou PDH/PDL) et forme une bougie de rejet.
    (Candlestick Patterns aux Niveaux Clés).
    """
    cpr = calculer_cpr_journalier(symbole)
    c15 = obtenir_donnees_deriv(symbole, 900) # 15 min ou 5 min fallback
    if not cpr or not c15: return None
    
    df15 = pd.DataFrame(c15)
    px = float(df15['close'].iloc[-1])
    pattern, force = detecter_chandeliers_pdf(df15)
    
    if pattern == "NONE": return None

    # Tendance générale (Prix vs Pivot)
    biais = "BULL" if px > cpr["PIVOT"] else "BEAR"
    
    signal = None
    sl = 0.0
    tp1 = 0.0
    tp_final = 0.0
    zone_nom = ""
    
    # Configurations Haussières (BULL) sur Support
    if biais == "BULL" and pattern in ["PIN_BULL", "ENGULFING_BULL"]:
        # Rebond sur Top CPR ou Pivot
        dist_tcpr = abs(px - cpr["TCPR"]) / px
        dist_pivot = abs(px - cpr["PIVOT"]) / px
        
        if dist_tcpr < 0.002: zone_nom = "Top CPR"
        elif dist_pivot < 0.002: zone_nom = "Point Pivot Central"
        
        if zone_nom:
            signal = "BUY"
            sl = df15['low'].iloc[-2] * 0.999 # Sous la mèche de la Pin Bar
            distance_risque = px - sl
            if distance_risque <= 0: return None
            tp1 = px + (distance_risque * 1.5)
            tp_final = cpr["PDH"] # Objectif = Haut de la veille
            
    # Configurations Baissières (BEAR) sur Résistance
    elif biais == "BEAR" and pattern in ["PIN_BEAR", "ENGULFING_BEAR"]:
        # Rejet sur Bottom CPR ou Pivot
        dist_bcpr = abs(px - cpr["BCPR"]) / px
        dist_pivot = abs(px - cpr["PIVOT"]) / px
        
        if dist_bcpr < 0.002: zone_nom = "Bottom CPR"
        elif dist_pivot < 0.002: zone_nom = "Point Pivot Central"
        
        if zone_nom:
            signal = "SELL"
            sl = df15['high'].iloc[-2] * 1.001 # Au-dessus de la mèche
            distance_risque = sl - px
            if distance_risque <= 0: return None
            tp1 = px - (distance_risque * 1.5)
            tp_final = cpr["PDL"] # Objectif = Bas de la veille

    if not signal: return None
    
    rr = abs(tp_final - px) / abs(px - sl) if abs(px - sl) > 0 else 0
    if rr < 1.5: return None

    return {
        "action": f"🟢 ACHAT (BUY)" if signal == "BUY" else f"🔴 VENTE (SELL)",
        "tendance": biais, "force": cpr["ETAT"],
        "msg": f"Rejet Chandelier ({pattern.replace('_', ' ')}) sur {zone_nom}",
        "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp_final, 5), 
        "rr": round(rr, 2), "px": round(px, 5),
        "strategie": 1, "confiance": 85 if cpr["ETAT"] == "Large (Range)" else 75,
        "label": "CPR PULLBACK & REJECTION"
    }

# ==========================================
# 📘 STRATÉGIE 2 : OPEN DRIVE BREAKOUT (PDH/PDL)
# ==========================================

def analyser_open_drive(symbole):
    """
    Une forte bougie (Marubozu) ou Pin Bar casse décisivement le Plus Haut 
    ou Plus Bas de la veille (PDH/PDL) sans hésitation.
    """
    cpr = calculer_cpr_journalier(symbole)
    c5 = obtenir_donnees_deriv(symbole, 300) 
    if not cpr or not c5: return None
    
    df5 = pd.DataFrame(c5)
    px = float(df5['close'].iloc[-1])
    pattern, force = detecter_chandeliers_pdf(df5)
    
    last_candle = df5.iloc[-2]
    
    signal = None
    sl = 0.0
    tp_final = 0.0
    
    # Breakout Haussier (OD)
    if pattern in ["MARUBOZU_BULL", "PIN_BULL"]:
        # La bougie a ouvert sous/près du PDH et clôturé strictement au-dessus
        if last_candle['open'] < cpr["PDH"] * 1.001 and last_candle['close'] > cpr["PDH"]:
            signal = "BUY"
            sl = cpr["PDH"] * 0.998 # SL juste sous la ligne cassée (Smart Risk)
            dist = px - sl
            if dist > 0: tp_final = px + (dist * 2.5) # Objectif 2.5R

    # Breakout Baissier (OD)
    elif pattern in ["MARUBOZU_BEAR", "PIN_BEAR"]:
        if last_candle['open'] > cpr["PDL"] * 0.999 and last_candle['close'] < cpr["PDL"]:
            signal = "SELL"
            sl = cpr["PDL"] * 1.002
            dist = sl - px
            if dist > 0: tp_final = px - (dist * 2.5)

    if not signal or tp_final == 0: return None
    
    rr = 2.5
    tp1 = px + (abs(px - sl) * 1.0) if signal == "BUY" else px - (abs(px - sl) * 1.0) # TP1 rapide 1R

    return {
        "action": f"🟢 ACHAT (BUY)" if signal == "BUY" else f"🔴 VENTE (SELL)",
        "tendance": "BREAKOUT", "force": "Impulsion Forte",
        "msg": f"Open Drive : Cassure du {'PDH' if signal=='BUY' else 'PDL'} par {pattern.replace('_', ' ')}",
        "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp_final, 5), 
        "rr": round(rr, 2), "px": round(px, 5),
        "strategie": 2, "confiance": 90,
        "label": "OPEN DRIVE BREAKOUT"
    }

# ==========================================
# 📘 STRATÉGIE 3 : RSI EXTREMES & EXHAUSTION
# ==========================================

def analyser_rsi_exhaustion(symbole):
    """
    Combinaison Dr Investors (RSI Extrême) + Smart Raja (Mèche d'épuisement).
    """
    c1h = obtenir_donnees_deriv(symbole, 3600)
    if not c1h: return None
    
    df1h = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"])} for c in c1h])
    rsi = ta.momentum.RSIIndicator(close=df1h["close"], window=14).rsi().iloc[-2]
    
    px = df1h['close'].iloc[-1]
    pattern, wick_size = detecter_chandeliers_pdf(df1h)
    
    signal = None
    sl = 0.0
    tp_final = 0.0
    
    # RSI Sur-vendu (<30) + Grosse Mèche de rejet à la baisse
    if rsi < 30 and pattern == "PIN_BULL":
        signal = "BUY"
        sl = df1h['low'].iloc[-2] * 0.999
        dist = px - sl
        if dist > 0: tp_final = px + (dist * 3.0) # 3R sur retournement majeur
            
    # RSI Sur-acheté (>70) + Grosse Mèche de rejet à la hausse
    elif rsi > 70 and pattern == "PIN_BEAR":
        signal = "SELL"
        sl = df1h['high'].iloc[-2] * 1.001
        dist = sl - px
        if dist > 0: tp_final = px - (dist * 3.0)

    if not signal or tp_final == 0: return None
    
    tp1 = px + (abs(px - sl) * 1.5) if signal == "BUY" else px - (abs(px - sl) * 1.5)

    return {
        "action": f"🟢 ACHAT (BUY)" if signal == "BUY" else f"🔴 VENTE (SELL)",
        "tendance": "REVERSAL", "force": f"RSI Extrême ({round(rsi,1)})",
        "msg": f"Épuisement : Rejet massif des prix avec RSI critique",
        "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp_final, 5), 
        "rr": 3.0, "px": round(px, 5),
        "strategie": 3, "confiance": 80,
        "label": "RSI EXHAUSTION & REVERSAL"
    }

# ==========================================
# 🧠 CERVEAU PRO TRADER (ROUTAGE PDF)
# ==========================================

def cerveau_pro_trader(symbole):
    """Exécute les stratégies PDF dans l'ordre de probabilité."""
    
    res = analyser_open_drive(symbole)
    if res: return res, "🚀 BREAKOUT PDH/PDL"
    
    res = analyser_cpr_rejection(symbole)
    if res: return res, "🧱 REBOND CPR (Vikram)"
    
    res = analyser_rsi_exhaustion(symbole)
    if res: return res, "⚠️ EXTRÊME RSI (Dr Investors)"
    
    return None, "INDECIS"

# ==========================================
# FONCTIONS DE GESTION DES RISQUES & TRADES V44
# ==========================================

def get_today_str(): return datetime.datetime.utcnow().strftime("%Y-%m-%d")

def init_daily_stats(uid):
    today = get_today_str()
    if uid not in daily_stats or daily_stats[uid]["date"] != today:
        daily_stats[uid] = {"date": today, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "consecutive_losses": 0, "paused_until": None, "best_trade": 0.0, "worst_trade": 0.0}
    return daily_stats[uid]

def utilisateur_en_pause(uid):
    stats = init_daily_stats(uid)
    if stats["paused_until"] and time.time() < stats["paused_until"]: return True, stats["paused_until"]
    return False, None

def daily_loss_limit_atteinte(uid):
    stats = init_daily_stats(uid)
    return stats["pnl"] <= -(CAPITAL_ACTUEL * RISK_CONFIG["daily_loss_limit_pct"] / 100.0)

def utilisateur_peut_trader(uid):
    stats = init_daily_stats(uid)
    if daily_loss_limit_atteinte(uid): return False, "🛑 Limite de perte journalière atteinte."
    en_pause, jusqua = utilisateur_en_pause(uid)
    if en_pause: return False, f"⏸️ Pause anti-tilt active ({int((jusqua - time.time()) / 60)} min)."
    if stats["trades"] >= RISK_CONFIG["max_trades_per_day"]: return False, "🛑 Limite de trades journalière atteinte."
    return True, None

def calculer_position_size(capital, risk_pct, prix_entree, prix_sl, symbole):
    montant_risque = capital * (risk_pct / 100.0)
    distance_sl = abs(prix_entree - prix_sl)
    if distance_sl <= 0: return {"montant_risque": montant_risque, "lot_factor": 0, "distance_sl": 0}
    return {"montant_risque": round(montant_risque, 2), "lot_factor": round(montant_risque / distance_sl, 4), "distance_sl": round(distance_sl, 5)}

def enregistrer_resultat_trade(uid, pnl, win, pnl_pour_bilan=None):
    stats = init_daily_stats(uid)
    stats["pnl"] += pnl
    stats["trades"] += 1
    valeur_bilan = pnl_pour_bilan if pnl_pour_bilan is not None else pnl

    if win:
        stats["wins"] += 1
        stats["consecutive_losses"] = 0
        win_count[uid] = win_count.get(uid, 0) + 1
    else:
        stats["losses"] += 1
        stats["consecutive_losses"] += 1
        loss_count[uid] = loss_count.get(uid, 0) + 1

    if valeur_bilan > stats["best_trade"]: stats["best_trade"] = valeur_bilan
    if valeur_bilan < stats["worst_trade"]: stats["worst_trade"] = valeur_bilan

    if stats["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
        stats["paused_until"] = time.time() + (RISK_CONFIG["pause_duration_minutes"] * 60)
    return stats

def create_trade_id(): return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance, label="SIGNAL"):
    trade_id = create_trade_id()
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"], entry_price, sl, symbole)
    trades_actifs[uid] = {
        "trade_id": trade_id, "symbol": symbole, "direction": direction, "entry_price": entry_price,
        "sl": sl, "tp1": tp1, "tp_final": tp_final, "strategy": strategy, "confiance": confiance, "label": label,
        "state": TradeState.TRADE_OPEN, "timestamp_open": time.time(), "partial_closed": False,
        "partial_pnl": 0.0, "breakeven_active": False, "trailing_active": False, "sizing": sizing,
    }
    return trade_id, sizing

def fermer_trade_complet(uid, exit_price, win):
    with lock_trade:
        if uid not in trades_actifs: return None
        trade = trades_actifs[uid]
        trade_id = trade["trade_id"]
        try:
            risque_initial = trade["sizing"]["montant_risque"]
            portion_restante = (1 - RISK_CONFIG["partial_tp_ratio"]) if trade.get("partial_closed") else 1.0
            risque_portion = risque_initial * portion_restante

            if win:
                gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
                pnl_final = risque_portion * gain_ratio
            else: pnl_final = -risque_portion

            pnl_trade_total = trade.get("partial_pnl", 0.0) + pnl_final
            trade["state"] = TradeState.TRADE_WIN if win else TradeState.TRADE_LOSS
            duration_seconds = time.time() - trade["timestamp_open"]

            if uid not in trades_historique: trades_historique[uid] = []
            trades_historique[uid].append({"trade_id": trade_id, "symbol": trade["symbol"], "direction": trade["direction"], "pnl": pnl_trade_total, "win": win, "timestamp": time.time()})

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_final
            enregistrer_resultat_trade(uid, pnl_final, win, pnl_pour_bilan=pnl_trade_total)
            return {"trade_id": trade_id, "pnl": pnl_trade_total, "win": win, "duration": duration_seconds}
        except: return {"trade_id": trade_id, "pnl": 0.0, "win": win, "duration": 0}
        finally: trades_actifs.pop(uid, None)

def fermer_trade_partiel(uid, exit_price):
    with lock_trade:
        if uid not in trades_actifs: return None
        trade = trades_actifs[uid]
        if trade["partial_closed"]: return None
        try:
            risque_initial = trade["sizing"]["montant_risque"]
            gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
            pnl_partiel = risque_initial * gain_ratio * RISK_CONFIG["partial_tp_ratio"]

            trade["partial_closed"] = True
            trade["partial_pnl"] = pnl_partiel
            trade["breakeven_active"] = True
            trade["state"] = TradeState.TRADE_PARTIAL

            buffer = trade["entry_price"] * RISK_CONFIG["breakeven_buffer_pct"]
            trade["sl"] = trade["entry_price"] + buffer if trade["direction"] == "BUY" else trade["entry_price"] - buffer

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_partiel
            init_daily_stats(uid)["pnl"] += pnl_partiel
            return {"pnl_partiel": round(pnl_partiel, 2), "nouveau_sl": trade["sl"]}
        except: return None

def appliquer_trailing_stop(uid, prix_current):
    if uid not in trades_actifs: return False
    trade = trades_actifs[uid]
    if not trade["breakeven_active"]: return False
    distance_trail = prix_current * RISK_CONFIG["trailing_stop_distance_pct"]
    if trade["direction"] == "BUY":
        nouveau_sl = prix_current - distance_trail
        if nouveau_sl > trade["sl"]: trade["sl"], trade["trailing_active"] = nouveau_sl, True
    else:
        nouveau_sl = prix_current + distance_trail
        if nouveau_sl < trade["sl"]: trade["sl"], trade["trailing_active"] = nouveau_sl, True
    return trade["trailing_active"]

def utilisateur_a_trade_actif(uid): return uid in trades_actifs and trades_actifs[uid]["state"] in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL)

# ==========================================
# WATCHDOG & MONITORING
# ==========================================

def watchdog_trades_bloques():
    while True:
        try:
            time.sleep(300) 
            maintenant = time.time()
            for uid in list(trades_actifs.keys()):
                trade = trades_actifs.get(uid)
                if not trade: continue
                age_heures = (maintenant - trade.get("timestamp_open", maintenant)) / 3600
                if trade["state"] not in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL): trades_actifs.pop(uid, None); continue
                if age_heures >= RISK_CONFIG["max_trade_age_hours"]:
                    px = obtenir_prix_broker_realtime(trade["symbol"])
                    if px: fermer_trade_complet(uid, px, win=(px >= trade["entry_price"] if trade["direction"] == "BUY" else px <= trade["entry_price"]))
        except: pass

def monitorer_trades_actifs():
    while True:
        try:
            time.sleep(5)
            for uid in list(trades_actifs.keys()):
                if uid not in trades_actifs: continue
                trade = trades_actifs[uid]
                px = obtenir_prix_broker_realtime(trade["symbol"])
                if not px: continue
                direction = trade["direction"]

                if trade["state"] == TradeState.TRADE_OPEN:
                    hit_tp1 = (direction == "BUY" and px >= trade["tp1"]) or (direction == "SELL" and px <= trade["tp1"])
                    hit_sl = (direction == "BUY" and px <= trade["sl"]) or (direction == "SELL" and px >= trade["sl"])
                    if hit_sl:
                        result = fermer_trade_complet(uid, px, win=False)
                        if result: envoyer_message_resultat(uid, trade, result, perte_totale=True)
                        continue
                    if hit_tp1:
                        partiel = fermer_trade_partiel(uid, px)
                        if partiel: envoyer_message_partiel(uid, trade, partiel, px)
                        continue

                elif trade["state"] == TradeState.TRADE_PARTIAL:
                    appliquer_trailing_stop(uid, px)
                    hit_tp_final = (direction == "BUY" and px >= trade["tp_final"]) or (direction == "SELL" and px <= trade["tp_final"])
                    hit_be_sl = (direction == "BUY" and px <= trade["sl"]) or (direction == "SELL" and px >= trade["sl"])
                    if hit_tp_final:
                        result = fermer_trade_complet(uid, px, win=True)
                        if result: envoyer_message_resultat(uid, trade, result, perte_totale=False, sortie_be=False)
                        continue
                    if hit_be_sl:
                        result = fermer_trade_complet(uid, px, win=True)
                        if result: envoyer_message_resultat(uid, trade, result, perte_totale=False, sortie_be=True)
                        continue
        except: pass

def envoyer_message_partiel(uid, trade, partiel, px):
    bot.send_message(uid, f"🟡 *TP1 ATTEINT — 85% SÉCURISÉ!*\n📊 {trade['symbol']} | TP1 : {px:.5f}\n💰 *Profit partiel : +{partiel['pnl_partiel']:.2f} USD*\n🛡️ SL en *Breakeven* : {partiel['nouveau_sl']:.5f}\n🏃 15% restant continue vers le TP final.", parse_mode="Markdown")

def envoyer_message_resultat(uid, trade, result, perte_totale, sortie_be=False):
    if perte_totale: msg = f"❌ *TRADE PERDU*\n📊 {trade['symbol']} | Sortie : {result['pnl']:+.2f} USD (SL)\n💔 *Perte : {result['pnl']:.2f} USD*"
    elif sortie_be: msg = f"🛡️ *SORTIE BREAKEVEN/TRAILING*\n📊 {trade['symbol']} | Le 15% restant est sorti au niveau sécurisé.\n💰 Gain sur cette portion : {result['pnl']:+.2f} USD"
    else: msg = f"✅ *TP FINAL ATTEINT !* 🎉\n📊 {trade['symbol']}\n💰 *Profit (15% final) : +{result['pnl']:.2f} USD*"
    bot.send_message(uid, msg, parse_mode="Markdown")

# ==========================================
# PARALLÈLISATION SCANNER V45
# ==========================================

def _analyser_une_paire(paire):
    try:
        res, contexte = cerveau_pro_trader(paire)
        if not res: return None
        px = obtenir_prix_broker_realtime(paire) or res["px"]
        if not valider_prix_avant_signal(paire, px): return None
        return (paire, res, px)
    except: return None

def scanner_marche_auto():
    toutes_paires = ELITE_PAIRS_MT5
    while True:
        try:
            time.sleep(15)
            libres = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not libres: continue

            resultats = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(_analyser_une_paire, p): p for p in toutes_paires}
                for future in as_completed(futures, timeout=25):
                    try:
                        r = future.result()
                        if r: resultats.append(r)
                    except: pass

            for paire, res, px in resultats:
                cle = f"{paire}_PRO"
                signaux_cache[cle] = {"time": time.time(), "action": res["action"], "mt5_sl": res["sl"], "mt5_tp1": res["tp1"], "mt5_tp": res["tp"], "mt5_rr": res["rr"], "strategie": res["strategie"], "label": res["label"], "confiance": res["confiance"]}
                
                nom = NOMS_AFFICHAGE.get(paire, paire)
                for uid in libres:
                    if utilisateur_a_trade_actif(uid): continue
                    peut_trader, _ = utilisateur_peut_trader(uid)
                    if not peut_trader: continue

                    markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Copier {nom}", callback_data=f"set_{paire}"))
                    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"], px, res["sl"], paire)
                    
                    txt = (
                        f"💼 *TERMINAL PRIME V45 (MASTER CLASS)*\n"
                        f"{nom}  {'🟢 BUY' if 'BUY' in res['action'] else '🔴 SELL'}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Modèle : *{res['label']}*\n"
                        f"📊 Tendance : {res['tendance']} | Force : {res['force']}\n"
                        f"📍 {res['msg']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚖️ R/R Prévu : {res['rr']}R\n"
                        f"🎖️ Confiance : {res['confiance']}%\n"
                        f"💰 Prix réel : {px:.5f}\n"
                        f"💵 Risque calculé : ${sizing['montant_risque']} (1%)\n"
                        f"⏳ Signal valide {RISK_CONFIG['signal_validity_seconds']}s"
                    )
                    try: bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                    except: pass
        except: pass

# ==========================================
# CALLBACK / VIP / BOOT
# ==========================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): return
    if uid in trades_actifs: return bot.answer_callback_query(call.id, "Trade actif! Attendez la clôture.", show_alert=True)
    peut_trader, raison = utilisateur_peut_trader(uid)
    if not peut_trader: return bot.answer_callback_query(call.id, raison, show_alert=True)

    actif = call.data.replace("set_", "")
    bot.delete_message(uid, call.message.message_id)

    cache = signaux_cache.get(f"{actif}_PRO")
    if not cache or (time.time() - cache["time"]) > RISK_CONFIG["signal_validity_seconds"]:
        return bot.send_message(uid, "⏱️ Signal expiré. Attends le prochain.")

    px = obtenir_prix_broker_realtime(actif) or 0
    if px <= 0: return bot.send_message(uid, "⚠️ Impossible de récupérer le prix. Réessaie.")

    entry_dir = "BUY" if "BUY" in cache["action"] else "SELL"
    sl_cache, tp1_cache, tp_final_cache = cache["mt5_sl"], cache["mt5_tp1"], cache["mt5_tp"]

    if (entry_dir == "BUY" and (px <= sl_cache or px >= tp1_cache)) or (entry_dir == "SELL" and (px >= sl_cache or px <= tp1_cache)):
        return bot.send_message(uid, "❌ *Signal annulé* — Le marché a déjà trop bougé (TP1 ou SL atteint).")

    trade_id, sizing = ouvrir_trade(uid, actif, entry_dir, px, sl_cache, tp1_cache, tp_final_cache, cache["strategie"], cache["confiance"], label=cache.get("label"))
    bot.send_message(uid, f"✅ *TRADE OUVERT (ID: {trade_id})*\n{actif} | Entrée: {px}\nSL: {sl_cache}\nTP1 (85%): {tp1_cache}\nTP Final: {tp_final_cache}\nRisque: ${sizing['montant_risque']}", parse_mode="Markdown")

def est_autorise(uid): return True

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    utilisateurs_actifs.add(uid)
    init_daily_stats(uid)
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"), KeyboardButton("📜 HISTORIQUE"))
    bot.send_message(uid, "💼 *TERMINAL PRIME V45 — MASTER CLASS*\n\n100% alimenté par la Stratégie des PDFs (CPR, Open Drive, Candlesticks, SMC).\nGestion de risque stricte à 1%.", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📜 HISTORIQUE")
def historique_bouton(message):
    uid = message.chat.id
    hist = trades_historique.get(uid, [])
    if not hist: return bot.send_message(uid, "📭 Aucun trade.")
    lignes = ["📜 *HISTORIQUE (10 derniers)*"]
    for t in hist[-10:][::-1]: lignes.append(f"{'✅' if t['win'] else '❌'} {t['symbol']} {t['direction']} | {t['pnl']:+.2f}$")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE"])
def devises(message):
    uid = message.chat.id
    markup = InlineKeyboardMarkup(row_width=3)
    btns_vol = [InlineKeyboardButton(NOMS_AFFICHAGE.get(p, p), callback_data=f"set_{p}") for p, actif in volatility_pairs_active.items() if actif]
    if btns_vol: markup.add(*btns_vol)
    markup.add(InlineKeyboardButton("🥇 GOLD", callback_data="set_XAUUSD"), InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"))
    bot.send_message(uid, "🎯 Sélectionne ta cible :", reply_markup=markup, parse_mode="Markdown")

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=monitorer_trades_actifs, daemon=True).start()
    Thread(target=watchdog_trades_bloques, daemon=True).start()
    print("💼 TERMINAL PRIME V45 ACTIF", flush=True)
    bot.infinity_polling()
