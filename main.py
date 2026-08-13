"""
╔════════════════════════════════════════════════════════════════════════════╗
║   SMART MONEY PRIME V51.1 — TRADER AUTONOME + COMMANDE /testgroq           ║
║                                                                            ║
║  - Python calcule les indicateurs (ADX, ATR, MACD, Contexte)             ║
║  - Groq (Llama 3.1 8B) prend la décision de A à Z (JSON strict)            ║
║  - Python applique les filets de sécurité et le gestionnaire de risque   ║
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

TELEGRAM_TOKEN = "8658287331:AAG0-ligM2yqNwIa4-AUWMKVyH4nhBoLCSk"
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
    "trailing_stop_activation_rr": 1.0,  
    "trailing_stop_distance_pct": 0.003, 
    "max_trades_per_day": 8,          
    "max_trade_age_hours": 12,        
    "signal_validity_seconds": 45,    
    "max_rr_degradation_pct": 40,     
}

# ==========================================
# ÉTATS DE TRADE
# ==========================================

class TradeState(Enum):
    SIGNAL_SENT     = "SIGNAL_ENVOYÉ"
    TRADE_OPEN      = "TRADE_OUVERT"
    TRADE_PARTIAL   = "TP1_PARTIEL_BE"     
    TRADE_WIN       = "GAGNÉ"
    TRADE_LOSS      = "PERDU"
    CANCELLED       = "ANNULÉ"

# ==========================================
# LISTES DE PAIRES
# ==========================================

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
cles_generees           = {}

volatility_pairs_active = {
    "V10": True, "V25": True, "V50": True, "V75": True, "V100": True,
}

trades_actifs     = {}   
trades_historique = {}   
prix_broker       = {}   

pnl_total  = {}
win_count  = {}
loss_count = {}

contexte_marche_cache = {}
daily_stats = {}   
lock_trade = Lock()

# ==========================================
# KEEP ALIVE
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Smart Money Prime V51.1 — Trader Autonome Actif"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# UTILITAIRES PRIX
# ==========================================

def prefixer_symbole(s):
    mapping = {"XAUUSD":"frxXAUUSD","XAGUSD":"frxXAGUSD"}
    if s in mapping:
        return mapping[s]
    if s in VOLATILE_PAIRS:
        return f"R_{s.replace('V','')}"
    return f"frx{s}"

_candles_cache = {}
_candles_cache_lock = Lock()
CANDLES_CACHE_TTL = 20  

def _obtenir_donnees_deriv_reseau(symbole_brut, granularite=300):
    if symbole_brut in ALL_PAIRS:
        tf_map = {300: "5min", 900: "15min", 3600: "1hour"}
        tf = tf_map.get(granularite, "4hour")
        mapping_fmp = {"XAUUSD":"FOREX:XAUUSD","XAGUSD":"FOREX:XAGUSD"}
        sym_fmp = mapping_fmp.get(symbole_brut, symbole_brut)
        try:
            url = (f"https://financialmodelingprep.com/api/v3/historical-chart/"
                   f"{tf}/{sym_fmp}?apikey={FMP_API_KEY}")
            res = requests.get(url, timeout=3).json()
            if isinstance(res, list) and len(res) > 0:
                bougies = []
                for idx, b in enumerate(reversed(res[:250])):
                    epoch_val = None
                    date_str = b.get("date")
                    if date_str:
                        try:
                            epoch_val = int(datetime.datetime.strptime(
                                date_str, "%Y-%m-%d %H:%M:%S").timestamp())
                        except:
                            try:
                                epoch_val = int(datetime.datetime.strptime(
                                    date_str, "%Y-%m-%d").timestamp())
                            except:
                                epoch_val = None
                    if epoch_val is None:
                        epoch_val = int(time.time()) - (250 - idx) * granularite
                    bougies.append({
                        "open":  float(b["open"]),
                        "high":  float(b["high"]),
                        "low":   float(b["low"]),
                        "close": float(b["close"]),
                        "epoch": epoch_val
                    })
                return bougies
        except Exception as e:
            print(f"[FMP Chart - {symbole_brut}] {e}", flush=True)

    sym = prefixer_symbole(symbole_brut)
    gran_valides = (60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400)
    gran_real = granularite if granularite in gran_valides else 14400
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=4)
            ws.send(json.dumps({"ticks_history": sym, "end": "latest",
                                "count": 250, "style": "candles",
                                "granularity": gran_real}))
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res and "error" not in res:
                return res["candles"]
        except:
            try: ws.close()
            except: pass
            time.sleep(0.2)
    return None

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    cle = (symbole_brut, granularite)
    now = time.time()
    with _candles_cache_lock:
        cached = _candles_cache.get(cle)
        if cached and (now - cached[0]) < CANDLES_CACHE_TTL:
            return cached[1]
    data = _obtenir_donnees_deriv_reseau(symbole_brut, granularite)
    if data is not None:
        with _candles_cache_lock:
            _candles_cache[cle] = (now, data)
    return data

def obtenir_donnees_h4(symbole):
    data = obtenir_donnees_deriv(symbole, 14400)
    if data and len(data) > 20:
        return data
    h1 = obtenir_donnees_deriv(symbole, 3600)
    if not h1 or len(h1) < 8:
        return None
    agg = []
    for i in range(0, len(h1) - 3, 4):
        chunk = h1[i:i+4]
        agg.append({
            "open":  float(chunk[0]["open"]),
            "high":  max(float(c["high"]) for c in chunk),
            "low":   min(float(c["low"])  for c in chunk),
            "close": float(chunk[-1]["close"]),
            "epoch": int(time.time())
        })
    return agg

def obtenir_prix_broker_realtime(symbole):
    try:
        mapping_fmp = {"XAUUSD":"FOREX:XAUUSD","XAGUSD":"FOREX:XAGUSD"}
        sym_fmp = mapping_fmp.get(symbole, symbole)
        url = f"https://financialmodelingprep.com/api/v3/quote/{sym_fmp}?apikey={FMP_API_KEY}"
        res = requests.get(url, timeout=3).json()
        if isinstance(res, list) and len(res) > 0:
            prix = float(res[0]["price"])
            prix_broker[symbole] = {
                "price": prix, "source": "FMP", "timestamp": time.time(),
                "bid": float(res[0].get("bid", prix)),
                "ask": float(res[0].get("ask", prix))
            }
            return prix
    except Exception as e:
        print(f"[FMP Real-time {symbole}] {e}", flush=True)

    sym = prefixer_symbole(symbole)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=3)
            ws.send(json.dumps({"ticks": sym}))
            res = json.loads(ws.recv())
            ws.close()
            if "tick" in res:
                prix = float(res["tick"]["quote"])
                prix_broker[symbole] = {"price": prix, "source": "Deriv",
                                        "timestamp": time.time()}
                return prix
        except:
            try: ws.close()
            except: pass
            time.sleep(0.5)
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.001):
    prix_real = obtenir_prix_broker_realtime(symbole)
    if not prix_real:
        return False
    decalage = abs(prix_bot - prix_real) / prix_real
    if decalage > tolerance:
        return False
    return True

# ==========================================
# GESTION DU RISQUE PROFESSIONNELLE
# ==========================================

def get_today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

def init_daily_stats(uid):
    today = get_today_str()
    if uid not in daily_stats or daily_stats[uid]["date"] != today:
        daily_stats[uid] = {
            "date": today, "pnl": 0.0, "trades": 0,
            "wins": 0, "losses": 0, "consecutive_losses": 0,
            "paused_until": None, "best_trade": 0.0, "worst_trade": 0.0,
        }
    return daily_stats[uid]

def utilisateur_en_pause(uid):
    stats = init_daily_stats(uid)
    if stats["paused_until"] and time.time() < stats["paused_until"]:
        return True, stats["paused_until"]
    return False, None

def daily_loss_limit_atteinte(uid):
    stats = init_daily_stats(uid)
    limite = -(CAPITAL_ACTUEL * RISK_CONFIG["daily_loss_limit_pct"] / 100.0)
    return stats["pnl"] <= limite

def max_trades_jour_atteint(uid):
    stats = init_daily_stats(uid)
    return stats["trades"] >= RISK_CONFIG["max_trades_per_day"]

def utilisateur_peut_trader(uid):
    stats = init_daily_stats(uid)
    if daily_loss_limit_atteinte(uid):
        return False, "🛑 Limite de perte journalière atteinte."
    en_pause, jusqua = utilisateur_en_pause(uid)
    if en_pause:
        minutes_restantes = int((jusqua - time.time()) / 60)
        return False, f"⏸️ Pause anti-tilt active. Reprise dans {minutes_restantes} minutes."
    if max_trades_jour_atteint(uid):
        return False, "🛑 Limite de trades/jour atteinte."
    return True, None

def calculer_position_size(capital, risk_pct, prix_entree, prix_sl, symbole):
    montant_risque = capital * (risk_pct / 100.0)
    distance_sl = abs(prix_entree - prix_sl)
    if distance_sl <= 0:
        return {"montant_risque": montant_risque, "lot_factor": 0, "distance_sl": 0}
    lot_factor = montant_risque / distance_sl
    return {
        "montant_risque": round(montant_risque, 2),
        "lot_factor": round(lot_factor, 4),
        "distance_sl": round(distance_sl, 5),
        "distance_sl_pct": round((distance_sl / prix_entree) * 100, 3) if prix_entree else 0
    }

def enregistrer_resultat_trade(uid, pnl, win, pnl_pour_bilan=None):
    stats = init_daily_stats(uid)
    stats["pnl"]    += pnl
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

# ==========================================
# EXECUTION ET SUIVI DES TRADES
# ==========================================

def create_trade_id():
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance,
                 label="SIGNAL", strategie_nom_ia="?", ia_score=None, gemini_score=None, contexte_marche=None):
    trade_id = create_trade_id()
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"], entry_price, sl, symbole)
    trades_actifs[uid] = {
        "trade_id": trade_id, "symbol": symbole, "direction": direction, "entry_price": entry_price,
        "sl": sl, "sl_original": sl, "tp1": tp1, "tp_final": tp_final,
        "strategy": strategy, "confiance": confiance, "label": label,
        "strategie_nom_ia": strategie_nom_ia, "ia_score": ia_score,
        "gemini_score": gemini_score, "contexte_marche": contexte_marche,
        "state": TradeState.TRADE_OPEN, "timestamp_open": time.time(),
        "exit_price": None, "exit_time": None, "pnl": None,
        "partial_closed": False, "partial_pnl": 0.0,
        "breakeven_active": False, "trailing_active": False, "sizing": sizing,
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
            else:
                pnl_final = -risque_portion

            pnl_trade_total = trade.get("partial_pnl", 0.0) + pnl_final
            trade["state"] = TradeState.TRADE_WIN if win else TradeState.TRADE_LOSS
            duration_seconds = time.time() - trade["timestamp_open"]

            if uid not in trades_historique: trades_historique[uid] = []
            trades_historique[uid].append({
                "trade_id": trade_id, "symbol": trade["symbol"], "direction": trade["direction"], 
                "entry": trade["entry_price"], "exit": exit_price, "pnl": pnl_trade_total, 
                "win": win, "timestamp": time.time(), "label": trade.get("label","")
            })

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_final
            enregistrer_resultat_trade(uid, pnl_final, win, pnl_pour_bilan=pnl_trade_total)
            return {"trade_id": trade_id, "pnl": pnl_trade_total, "pnl_final_portion": pnl_final, "win": win, "duration": duration_seconds}
        except: return {"trade_id": trade_id, "pnl": 0.0, "pnl_final_portion": 0.0, "win": win, "duration": 0, "erreur": True}
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
            stats = init_daily_stats(uid)
            stats["pnl"] += pnl_partiel
            return {"pnl_partiel": round(pnl_partiel, 2), "nouveau_sl": trade["sl"]}
        except: return None

def appliquer_trailing_stop(uid, prix_current):
    if uid not in trades_actifs: return False
    trade = trades_actifs[uid]
    if not trade["breakeven_active"]: return False
    distance_trail = prix_current * RISK_CONFIG["trailing_stop_distance_pct"]
    if trade["direction"] == "BUY":
        if prix_current - distance_trail > trade["sl"]:
            trade["sl"] = prix_current - distance_trail
            trade["trailing_active"] = True
            return True
    else:
        if prix_current + distance_trail < trade["sl"]:
            trade["sl"] = prix_current + distance_trail
            trade["trailing_active"] = True
            return True
    return False

def utilisateur_a_trade_actif(uid):
    return uid in trades_actifs and trades_actifs[uid]["state"] in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL)

def watchdog_trades_bloques():
    while True:
        try:
            time.sleep(300) 
            maintenant = time.time()
            for uid in list(trades_actifs.keys()):
                trade = trades_actifs.get(uid)
                if not trade: continue
                age_heures = (maintenant - trade.get("timestamp_open", maintenant)) / 3600
                if trade["state"] not in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL):
                    trades_actifs.pop(uid, None)
                    continue
                if age_heures >= RISK_CONFIG["max_trade_age_hours"]:
                    prix_current = obtenir_prix_broker_realtime(trade["symbol"])
                    if prix_current:
                        win_watchdog = prix_current >= trade["entry_price"] if trade["direction"] == "BUY" else prix_current <= trade["entry_price"]
                        fermer_trade_complet(uid, prix_current, win=win_watchdog)
        except: pass

# ==========================================
# SESSIONS ET AUTORISATIONS
# ==========================================

def get_session_active():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute / 60.0
    paires, sessions = [], []
    if 0.0 <= h < 7.0:
        paires += ["AUDJPY","CADJPY","CHFJPY","USDJPY","EURJPY","AUDUSD","AUDCAD","XAUUSD","XAGUSD"]; sessions.append("ASIE")
    if 7.0 <= h < 8.0:
        paires += ["AUDJPY","CADJPY","CHFJPY","USDJPY","EURJPY","AUDUSD","AUDCAD","XAUUSD","XAGUSD", "EURUSD","GBPUSD","EURCHF","USDCHF","CADCHF","EURAUD"]; sessions.append("ASIE+LONDRES")
    if 8.0 <= h <= 10.0:
        paires += ["EURUSD","GBPUSD","EURCHF","USDCHF","CADCHF","EURJPY","EURAUD","XAUUSD","XAGUSD"]; sessions.append("LONDRES")
    if 12.0 <= h <= 15.0:
        paires += ["EURUSD","GBPUSD","USDCAD","USDCHF","AUDUSD","XAUUSD","XAGUSD"]; sessions.append("NEW_YORK")
    if not sessions: return None, []
    return "+".join(sessions), list(dict.fromkeys(paires))

def dans_killzone():
    session, _ = get_session_active()
    return session is not None

def nom_killzone():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute / 60.0
    if 7.0 <= h < 8.0:   return "🌏🇬🇧 Asie+Londres (07h-08h)"
    if 0.0 <= h < 7.0:   return "🌏 Asian Killzone (00h-07h)"
    if 8.0 <= h <= 10.0: return "🇬🇧 London Killzone (08h-10h)"
    if 12.0 <= h <= 15.0:return "🇺🇸 New York Killzone (12h-15h)"
    return "⏳ Hors session"

def est_symbole_autorise(symbole):
    if symbole in VOLATILE_PAIRS:
        return ("AUTORISE", "") if volatility_pairs_active.get(symbole, True) else ("BLOCAGE_TOTAL", f"{symbole} désactivé")
    now = datetime.datetime.utcnow()
    j, h = now.weekday(), now.hour + now.minute / 60.0
    if (j == 4 and h >= 21) or j == 5 or (j == 6 and h < 21): return "BLOCAGE_TOTAL", "Week-end"
    if symbole in COMMODITY_PAIRS: return "AUTORISE", ""
    session, paires_session = get_session_active()
    if session is None: return "HORS_SESSION", "🔒 Hors Killzone"
    if symbole in paires_session: return "AUTORISE", ""
    return "HORS_SESSION", f"🔒 {symbole} inactif"

# ==========================================
# MODULES TECHNIQUES POUR L'IA (LES YEUX)
# ==========================================

IA_CONFIG = {
    "seuil_acceptation": 80,   
    "groq_active": True,       
}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
# L'API a déprécié le modèle versatile. Mise à jour vers le modèle actif.
GROQ_MODEL   = "llama-3.1-8b-instant" 
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

def calculer_adx(df, period=14):
    try:
        high, low, close = df['high'], df['low'], df['close']
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, 1e-9))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        adx = dx.rolling(period).mean()
        return float(adx.iloc[-2]) if not adx.isna().iloc[-2] else 20.0
    except: return 20.0

def calculer_macd_signal(df):
    try:
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal_line
        return float(macd_line.iloc[-2]), float(signal_line.iloc[-2]), float(hist.iloc[-2])
    except: return 0.0, 0.0, 0.0

def calculer_atr(df, period=14):
    try:
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-2])
    except: return 0.0

def analyser_contexte_marche(symbole, df1h, df4h):
    try:
        ema20_h1 = df1h['close'].ewm(span=20, adjust=False).mean()
        ema50_h1 = df1h['close'].ewm(span=50, adjust=False).mean()
        pente_ema20 = (ema20_h1.iloc[-2] - ema20_h1.iloc[-10]) / max(abs(ema20_h1.iloc[-10]), 1e-9)
        adx = calculer_adx(df1h)
        atr = calculer_atr(df1h)
        px = float(df1h['close'].iloc[-2])
        atr_pct = (atr / px * 100) if px else 0
        recent_high = df1h['high'].iloc[-30:].max()
        recent_low  = df1h['low'].iloc[-30:].min()
        rng = recent_high - recent_low
        position_dans_range = (px - recent_low) / rng if rng > 0 else 0.5
        proche_cassure = position_dans_range > 0.9 or position_dans_range < 0.1

        tendance = "HAUSSIERE" if adx >= 25 and abs(pente_ema20) > 0.001 and ema20_h1.iloc[-2] > ema50_h1.iloc[-2] else ("BAISSIERE" if adx >= 25 and abs(pente_ema20) > 0.001 else ("RANGE" if adx < 18 else "INDECIS"))
        volatilite = "TRES_VOLATIL" if atr_pct > 1.2 else ("PEU_VOLATIL" if atr_pct < 0.05 else "NORMALE")
        consolidation = (adx < 20 and atr_pct < 0.3)

        return {"tendance": tendance, "volatilite": volatilite, "consolidation": consolidation, "proche_cassure": proche_cassure, "adx": round(adx, 1), "atr_pct": round(atr_pct, 3), "position_dans_range": round(position_dans_range, 2)}
    except: return {"tendance": "INDECIS", "volatilite": "NORMALE", "consolidation": False, "proche_cassure": False, "adx": 20.0, "atr_pct": 0.3, "position_dans_range": 0.5}

# ==========================================
# 🧠 LE CERVEAU IA (TRADER AUTONOME)
# ==========================================

def cerveau_pro_trader(symbole):
    """
    Le bot n'utilise plus de stratégies rigides. Python calcule le contexte de marché
    et envoie le rapport à l'IA (Groq) pour qu'elle prenne la décision finale.
    """
    if not IA_CONFIG.get("groq_active", True) or not GROQ_API_KEY:
        return []

    try:
        c1h = obtenir_donnees_deriv(symbole, 3600)
        c4h = obtenir_donnees_h4(symbole)
        
        if not c1h or len(c1h) < 30:
            return []
            
        df1h = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"])} for c in c1h])
        df4h = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"])} for c in c4h]) if c4h else df1h
        
        px = float(df1h['close'].iloc[-1])

        contexte = analyser_contexte_marche(symbole, df1h, df4h)
        adx = calculer_adx(df1h)
        atr = calculer_atr(df1h)
        macd_line, signal_line, hist = calculer_macd_signal(df1h)
        
        prompt = (
            f"Tu es un algorithme de trading institutionnel. Analyse ce flux de données sur l'actif {symbole}.\n"
            f"- Prix actuel : {px:.5f}\n"
            f"- Tendance H1 : {contexte['tendance']} | Volatilité : {contexte['volatilite']}\n"
            f"- ADX (Force de la tendance) : {adx:.1f}\n"
            f"- ATR (Volatilité actuelle) : {atr:.5f}\n"
            f"- MACD Histogramme : {hist:.5f}\n"
            f"- Marché en consolidation : {contexte['consolidation']}\n"
            f"- Prix proche d'une cassure : {contexte['proche_cassure']}\n\n"
            "Prends une décision de trading. Si les conditions sont mauvaises ou incertaines, réponds 'WAIT'. "
            "Réponds UNIQUEMENT et STRICTEMENT avec ce format JSON (sans markdown ni texte avant/après) :\n"
            "{"
            '"action": "BUY" ou "SELL" ou "WAIT", '
            '"confiance": un entier entre 0 et 100, '
            '"strategie_choisie": "Nom court du setup que tu as identifié", '
            '"justification": "Une phrase expliquant ta décision techniquement", '
            '"distance_sl_pct": pourcentage décimal (ex: 0.005 pour 0.5%), '
            '"distance_tp_pct": pourcentage décimal (ex: 0.015 pour 1.5%)'
            "}"
        )

        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 150,
        }
        resp = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, json=payload, timeout=8)
        
        if resp.status_code != 200:
            return []
            
        texte = resp.json()["choices"][0]["message"]["content"].strip()
        texte = texte.replace("```json", "").replace("```", "").strip()
        ia_decision = json.loads(texte)

        action = ia_decision.get("action", "WAIT")
        confiance = float(ia_decision.get("confiance", 0))
        
        if action not in ["BUY", "SELL"] or confiance < IA_CONFIG.get("seuil_acceptation", 80):
            return []

        dist_sl = min(float(ia_decision.get("distance_sl_pct", 0.005)), 0.015)
        dist_tp = float(ia_decision.get("distance_tp_pct", 0.015))
        
        sl = px * (1 - dist_sl) if action == "BUY" else px * (1 + dist_sl)
        tp_final = px * (1 + dist_tp) if action == "BUY" else px * (1 - dist_tp)
        
        risque = abs(px - sl)
        if risque <= 0:
            return []
            
        tp1 = px + (risque * 1.5) if action == "BUY" else px - (risque * 1.5)
        rr = abs(tp_final - px) / risque

        signal_formate = {
            "action": "🟢 ACHAT (BUY)" if action == "BUY" else "🔴 VENTE (SELL)",
            "tendance": "BULL" if action == "BUY" else "BEAR",
            "force": str(ia_decision.get("strategie_choisie", "Analyse Autonome")).upper()[:25],
            "msg": ia_decision.get("justification", "Décision IA"),
            "sl": round(sl, 5),
            "tp1": round(tp1, 5),
            "tp": round(tp_final, 5),
            "rr": round(rr, 2),
            "px": round(px, 5),
            "strategie": 99,
            "confiance": confiance,
            "label": "🧠 TRADER AUTONOME",
            "strategie_nom_ia": "PURE_IA",
            "ia_score": confiance,
            "ia_justification": [ia_decision.get("justification", "")],
            "gemini_score": confiance,
            "gemini_avis": "Processus 100% autonome",
            "gemini_disponible": True,
            "contexte_detecte": f"🤖 {ia_decision.get('strategie_choisie', 'IA')}",
            "contexte_marche": contexte
        }
        
        return [signal_formate]

    except Exception as e:
        print(f"[Cerveau IA Autonome/{symbole}] Erreur: {e}", flush=True)
        return []

# ==========================================
# COMMANDES TELEGRAM & SCANNER
# ==========================================

@bot.message_handler(commands=['testgroq'])
def test_groq_reel(message):
    """
    Commande pour vérifier instantanément la santé de l'API Groq.
    """
    uid = message.chat.id
    if not est_autorise(uid): return

    if not GROQ_API_KEY:
        return bot.send_message(uid, "❌ *Erreur* : Aucune clé GROQ_API_KEY détectée dans l'environnement.", parse_mode="Markdown")

    bot.send_message(uid, "🔄 *Test de connexion Groq en cours...*", parse_mode="Markdown")
    
    try:
        debut = time.time()
        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": "Réponds uniquement 'OK' formaté en JSON strict: {\"status\": \"OK\"}"}],
            "temperature": 0.1,
            "max_tokens": 20
        }
        resp = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, json=payload, timeout=10)
        duree = round(time.time() - debut, 2)
        
        if resp.status_code == 200:
            data = resp.json()
            texte_recu = data["choices"][0]["message"]["content"].strip()
            bot.send_message(uid, 
                f"✅ *API GROQ OPÉRATIONNELLE*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ Latence : `{duree}s`\n"
                f"🧠 Modèle : `{GROQ_MODEL}`\n"
                f"💬 Réponse brute : `{texte_recu}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Le moteur IA est prêt à analyser les marchés.", 
                parse_mode="Markdown")
        else:
            bot.send_message(uid, 
                f"❌ *ÉCHEC DE L'API*\n"
                f"Code HTTP : {resp.status_code}\n"
                f"Détails : `{resp.text[:200]}`", 
                parse_mode="Markdown")
                
    except requests.exceptions.Timeout:
        bot.send_message(uid, "❌ *Timeout* : L'API Groq a mis plus de 10 secondes à répondre.", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(uid, f"❌ *Erreur interne* : `{e}`", parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid): return bot.send_message(uid, "🔒 Accès restreint.")
    utilisateurs_actifs.add(uid)
    init_daily_stats(uid)
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("📜 HISTORIQUE"))
    bot.send_message(uid, "💼 *SMART MONEY PRIME V51.1*\nTrader Autonome IA + Commande /testgroq active.", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📜 HISTORIQUE")
def historique_bouton(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    hist = trades_historique.get(uid, [])
    if not hist: return bot.send_message(uid, "📭 Aucun trade dans l'historique.")
    lignes = ["📜 *HISTORIQUE*\n━━━━━━━━━━━━━━━━━━━━━━"]
    for t in hist[-10:][::-1]:
        emoji = "✅" if t["win"] else "❌"
        lignes.append(f"{emoji} {t['symbol']} {t['direction']} | {t['pnl']:+.2f}$")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE CIBLE")
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    markup = InlineKeyboardMarkup(row_width=3)
    btns_vol = [InlineKeyboardButton(NOMS_AFFICHAGE.get(p, p), callback_data=f"set_{p}") for p, actif in volatility_pairs_active.items() if actif]
    if btns_vol: markup.add(*btns_vol)
    markup.add(InlineKeyboardButton("🥇 GOLD", callback_data="set_XAUUSD"), InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"))
    bot.send_message(uid, "🎯 Sélectionne ta cible :", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): return
    cle_brute = call.data.replace("set_", "")
    
    if cle_brute in signaux_cache: cle = cle_brute; actif = cle_brute.split("_")[0]
    else:
        actif = cle_brute
        candidats = [k for k in signaux_cache if k.startswith(f"{actif}_")]
        if not candidats: return bot.send_message(uid, f"⏱️ Aucun signal actif sur {actif}.")
        cle = max(candidats, key=lambda k: signaux_cache[k]["time"])

    user_prefs[uid] = actif
    cache = signaux_cache.get(cle)
    px = obtenir_prix_broker_realtime(actif) or 0
    if px <= 0: return bot.send_message(uid, "⚠️ Impossible de récupérer le prix.")
    
    entry_direction = "BUY" if "BUY" in cache["action"] else "SELL"
    sl_cache, tp1_cache, tp_final_cache = cache["mt5_sl"], cache["mt5_tp1"], cache["mt5_tp"]
    
    trade_id, sizing = ouvrir_trade(uid, actif, entry_direction, px, sl_cache, tp1_cache, tp_final_cache, cache["strategie"], cache["confiance"], label=cache.get("label"))
    
    signal = (
        f"💼 *{cache.get('label')}* — {actif}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢 BUY MARKET' if entry_direction == 'BUY' else '🔴 SELL MARKET'}\n"
        f"💰 Entrée  : {px:.5f}\n"
        f"🛑 SL      : {sl_cache:.5f}\n"
        f"🎯 TP1     : {tp1_cache:.5f}\n"
        f"🏁 TP Final: {tp_final_cache:.5f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *TRADE OUVERT* | 🆔 {trade_id}"
    )
    bot.send_message(uid, signal, parse_mode="Markdown")

def _analyser_une_paire(paire):
    try:
        if est_symbole_autorise(paire)[0] != "AUTORISE": return []
        signaux = cerveau_pro_trader(paire)
        resultats = []
        for res in signaux:
            px = obtenir_prix_broker_realtime(paire) or res["px"]
            if valider_prix_avant_signal(paire, px): resultats.append((paire, res, px))
        return resultats
    except: return []

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
                    try: resultats.extend(future.result())
                    except: pass
            
            for paire, res, px in resultats:
                cle = f"{paire}_{res.get('strategie_nom_ia', 'PRO')}"
                signaux_cache[cle] = {
                    "time": time.time(), "action": res["action"], "mt5_sl": res["sl"], 
                    "mt5_tp1": res.get("tp1", res["tp"]), "mt5_tp": res["tp"], "mt5_rr": res["rr"],
                    "confiance": res["confiance"], "strategie": res["strategie"], "label": res["label"]
                }
                for uid in libres:
                    if utilisateur_a_trade_actif(uid): continue
                    markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Copier {paire}", callback_data=f"set_{cle}"))
                    
                    txt = (
                        f"💼 *TRADER AUTONOME IA*\n"
                        f"{paire}  {res['action']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🧠 Setup : {res['force']}\n"
                        f"📍 {res['msg']}\n"
                        f"🤖 Confiance IA : {res.get('ia_score','?')}%\n"
                        f"⚖️ R/R : {res['rr']}R\n"
                        f"💰 Prix actuel : {px:.5f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    try: bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                    except: pass
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
                dir_ = trade["direction"]

                if trade["state"] == TradeState.TRADE_OPEN:
                    hit_tp1 = (dir_ == "BUY" and px >= trade["tp1"]) or (dir_ == "SELL" and px <= trade["tp1"])
                    hit_sl  = (dir_ == "BUY" and px <= trade["sl"]) or (dir_ == "SELL" and px >= trade["sl"])
                    
                    if hit_sl:
                        res = fermer_trade_complet(uid, px, win=False)
                        try: bot.send_message(uid, f"❌ *TRADE PERDU* sur {trade['symbol']}\nPerte: {res['pnl']:.2f}$", parse_mode="Markdown")
                        except: pass
                    elif hit_tp1:
                        partiel = fermer_trade_partiel(uid, px)
                        try: bot.send_message(uid, f"🟡 *TP1 ATTEINT* sur {trade['symbol']}\n85% fermé, SL en Breakeven.", parse_mode="Markdown")
                        except: pass
                
                elif trade["state"] == TradeState.TRADE_PARTIAL:
                    appliquer_trailing_stop(uid, px)
                    hit_tp = (dir_ == "BUY" and px >= trade["tp_final"]) or (dir_ == "SELL" and px <= trade["tp_final"])
                    hit_sl = (dir_ == "BUY" and px <= trade["sl"]) or (dir_ == "SELL" and px >= trade["sl"])
                    
                    if hit_tp or hit_sl:
                        res = fermer_trade_complet(uid, px, win=True)
                        try: bot.send_message(uid, f"✅ *TRADE CLOS (15%)* sur {trade['symbol']}\nGain: +{res['pnl']:.2f}$", parse_mode="Markdown")
                        except: pass
        except: pass

# ==========================================
# ACCES VIP
# ==========================================

def est_autorise(uid):
    if uid == ADMIN_ID: return True
    if uid in utilisateurs_autorises:
        exp = utilisateurs_autorises[uid]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: return True
        del utilisateurs_autorises[uid]
    return False

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    cid = message.chat.id
    parts = message.text.strip().split()
    if len(parts) < 2: return bot.send_message(cid, "⚠️ Usage : /vip VOTRE-CLÉ")
    cle = parts[1].strip()
    if cle not in cles_generees: return bot.send_message(cid, "❌ Clé invalide.")
    utilisateurs_autorises[cid] = "LIFETIME"
    bot.send_message(cid, "🎉 *ACCÈS DÉVERROUILLÉ !*\n/start pour commencer.", parse_mode="Markdown")

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    cles_generees[cle] = "LIFETIME"
    bot.send_message(message.chat.id, f"✅ *CLÉ GÉNÉRÉE*\n`{cle}`", parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=monitorer_trades_actifs, daemon=True).start()
    Thread(target=watchdog_trades_bloques, daemon=True).start()
    print("💼 SMART MONEY PRIME V51.1 (TRADER AUTONOME + /testgroq) ACTIF", flush=True)
    bot.infinity_polling()
