"""
╔════════════════════════════════════════════════════════════════════════════╗
║              TERMINAL PRIME V43 — THE WINNER'S BRAIN                      ║
║                                                                            ║
║  Base stable V38/V42 + Stratégie 4 (Bougie Pivot / Session Liquidity)     ║
║  + Dispositifs professionnels complets pour agir comme un VRAI gagnant:   ║
║                                                                            ║
║   ✅ Position Sizing réel (lot calculé sur % risque réel du capital)      ║
║   ✅ Partial TP 85% + Move to Breakeven automatique (technique vidéo 4)   ║
║   ✅ Trailing Stop après breakeven                                        ║
║   ✅ Daily Loss Limit (circuit breaker journalier)                        ║
║   ✅ Pause auto après N pertes consécutives (anti-tilt)                   ║
║   ✅ Rapport quotidien automatique (P&L, winrate, meilleur/pire trade)    ║
║   ✅ Asian Range Tracker (high/low session asiatique, détection BOS)      ║
║   ✅ Stratégie 4: Bougie Pivot Session (Order Block + Liquidity Sweep)    ║
║   ✅ Cerveau Pro Trader V2 (4 stratégies, 1 décision contextuelle)        ║
║   ✅ /Volatility granulaire (V10/V25/V50/V75/V100 individuels)            ║
║   ✅ Tous les fixes V38: prix réel-time, gestion d'état, blocage signaux  ║
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

# ==========================================
# CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAEdZNRBuPzt04B4vUvoo1M1_S5L1ixnNbY"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# RISK MANAGEMENT — CONFIGURATION GLOBALE
# ==========================================

RISK_CONFIG = {
    "risk_per_trade_pct": 1.0,        # % du capital risqué par trade
    "daily_loss_limit_pct": 5.0,      # Stop journalier si perte cumulée atteint ce %
    "max_consecutive_losses": 3,      # Pause auto après N pertes d'affilée
    "pause_duration_minutes": 120,    # Durée de la pause anti-tilt
    "partial_tp_ratio": 0.85,         # 85% de la position fermée au TP1 (technique vidéo)
    "breakeven_buffer_pct": 0.0005,   # Petit buffer au-dessus du prix d'entrée pour le BE
    "trailing_stop_activation_rr": 1.0,  # Active le trailing dès que prix atteint 1R après BE
    "trailing_stop_distance_pct": 0.003, # Distance du trailing stop (0.3%)
    "max_trades_per_day": 8,          # Limite de trades par jour (évite sur-trading)
}

# ==========================================
# ÉTATS DE TRADE
# ==========================================

class TradeState(Enum):
    SIGNAL_SENT     = "SIGNAL_ENVOYÉ"
    TRADE_OPEN      = "TRADE_OUVERT"
    TRADE_PARTIAL   = "TP1_PARTIEL_BE"     # 85% fermé, reste en breakeven/trailing
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

# Contrôle granulaire des paires Volatility
volatility_pairs_active = {
    "V10": True, "V25": True, "V50": True, "V75": True, "V100": True,
}

# Gestion des trades (V38 étendu)
trades_actifs     = {}   # uid -> dict trade complet
trades_historique = {}   # uid -> [trades fermés]
prix_broker       = {}   # cache derniers prix

pnl_total  = {}
win_count  = {}
loss_count = {}

# Contexte marché mémorisé (cache 2 min)
contexte_marche_cache = {}

# Asian Range Tracker — mémorise le high/low de la session asiatique par paire et par jour
asian_range_cache = {}   # symbole -> {"date": "YYYY-MM-DD", "high":..., "low":...}

# Risk Management — état par utilisateur
daily_stats = {}   # uid -> {"date":..., "pnl":0, "trades":0, "consecutive_losses":0,
                    #         "paused_until": None, "best_trade":0, "worst_trade":0}

lock_trade = Lock()

# ==========================================
# KEEP ALIVE
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime V43 — The Winner's Brain"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# UTILITAIRES PRIX (base V38 OPTIMISÉE)
# ==========================================

def prefixer_symbole(s):
    mapping = {"XAUUSD":"frxXAUUSD","XAGUSD":"frxXAGUSD"}
    if s in mapping:
        return mapping[s]
    if s in VOLATILE_PAIRS:
        return f"R_{s.replace('V','')}"
    return f"frx{s}"

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    if symbole_brut in ALL_PAIRS:
        tf = "5min" if granularite == 300 else ("1hour" if granularite == 3600 else "4hour")
        mapping_fmp = {"XAUUSD":"FOREX:XAUUSD","XAGUSD":"FOREX:XAGUSD"}
        sym_fmp = mapping_fmp.get(symbole_brut, symbole_brut)
        try:
            url = (f"https://financialmodelingprep.com/api/v3/historical-chart/"
                   f"{tf}/{sym_fmp}?apikey={FMP_API_KEY}")
            res = requests.get(url, timeout=2.0).json()
            if isinstance(res, list) and len(res) > 0:
                bougies = []
                for b in reversed(res[:250]):
                    bougies.append({
                        "open":  float(b["open"]),
                        "high":  float(b["high"]),
                        "low":   float(b["low"]),
                        "close": float(b["close"]),
                        "epoch": int(time.time())
                    })
                return bougies
        except Exception as e:
            print(f"[FMP Chart - {symbole_brut}] {e}", flush=True)

    sym = prefixer_symbole(symbole_brut)
    gran_real = granularite if granularite in (300, 3600) else 14400
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=3.0)
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
            time.sleep(0.1)
    return None

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
        res = requests.get(url, timeout=1.5).json()
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
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=2.0)
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
            time.sleep(0.1)
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.001):
    prix_real = obtenir_prix_broker_realtime(symbole)
    if not prix_real:
        return False
    decalage = abs(prix_bot - prix_real) / prix_real
    if decalage > tolerance:
        print(f"[Validation {symbole}] ÉCART {decalage*100:.2f}% — REJETÉ", flush=True)
        return False
    return True

# ==========================================
# RISK MANAGEMENT — CONFIGURATION GLOBALE
# ==========================================

def get_today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

def init_daily_stats(uid):
    today = get_today_str()
    if uid not in daily_stats or daily_stats[uid]["date"] != today:
        daily_stats[uid] = {
            "date": today, "pnl": 0.0, "trades": 0,
            "wins": 0, "losses": 0,
            "consecutive_losses": 0,
            "paused_until": None,
            "best_trade": 0.0, "worst_trade": 0.0,
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
        return False, (f"🛑 Limite de perte journalière atteinte "
                       f"({RISK_CONFIG['daily_loss_limit_pct']}% du capital). "
                       f"Trading suspendu jusqu'à demain.")

    en_pause, jusqua = utilisateur_en_pause(uid)
    if en_pause:
        minutes_restantes = int((jusqua - time.time()) / 60)
        return False, (f"⏸️ Pause anti-tilt active après "
                       f"{RISK_CONFIG['max_consecutive_losses']} pertes consécutives.\n"
                       f"Reprise dans {minutes_restantes} minutes.")

    if max_trades_jour_atteint(uid):
        return False, (f"🛑 Limite de {RISK_CONFIG['max_trades_per_day']} trades/jour atteinte. "
                       f"Reviens demain — la discipline fait les gagnants.")

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

def enregistrer_resultat_trade(uid, pnl, win):
    stats = init_daily_stats(uid)
    stats["pnl"]    += pnl
    stats["trades"] += 1

    if win:
        stats["wins"] += 1
        stats["consecutive_losses"] = 0
        win_count[uid] = win_count.get(uid, 0) + 1
    else:
        stats["losses"] += 1
        stats["consecutive_losses"] += 1
        loss_count[uid] = loss_count.get(uid, 0) + 1

    if pnl > stats["best_trade"]:
        stats["best_trade"] = pnl
    if pnl < stats["worst_trade"]:
        stats["worst_trade"] = pnl

    if stats["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
        stats["paused_until"] = time.time() + (RISK_CONFIG["pause_duration_minutes"] * 60)
        print(f"[Risk] {uid} EN PAUSE anti-tilt ({stats['consecutive_losses']} pertes consécutives)", flush=True)

    return stats

# ==========================================
# PARTIAL TP 85% + BREAKEVEN + TRAILING STOP
# ==========================================

def create_trade_id():
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance, label="SIGNAL"):
    trade_id = create_trade_id()
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"],
                                    entry_price, sl, symbole)

    trades_actifs[uid] = {
        "trade_id": trade_id, "symbol": symbole,
        "direction": direction, "entry_price": entry_price,
        "sl": sl, "sl_original": sl,
        "tp1": tp1, "tp_final": tp_final,
        "strategy": strategy, "confiance": confiance, "label": label,
        "state": TradeState.TRADE_OPEN,
        "timestamp_open": time.time(),
        "exit_price": None, "exit_time": None, "pnl": None,
        "partial_closed": False,
        "partial_pnl": 0.0,
        "breakeven_active": False,
        "trailing_active": False,
        "sizing": sizing,
    }
    print(f"[Trade Opened] {uid}: {trade_id} {symbole} {direction} @ {entry_price} "
          f"(Risque: ${sizing['montant_risque']})", flush=True)
    return trade_id, sizing

def fermer_trade_complet(uid, exit_price, win):
    if uid not in trades_actifs:
        return None
    trade    = trades_actifs[uid]
    trade_id = trade["trade_id"]

    risque_initial = trade["sizing"]["montant_risque"]

    portion_restante = (1 - RISK_CONFIG["partial_tp_ratio"]) if trade.get("partial_closed") else 1.0
    risque_portion    = risque_initial * portion_restante

    if win:
        gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
        pnl_final = risque_portion * gain_ratio
    else:
        pnl_final = -risque_portion

    pnl_trade_total = trade.get("partial_pnl", 0.0) + pnl_final

    trade["state"]      = TradeState.TRADE_WIN if win else TradeState.TRADE_LOSS
    trade["exit_price"] = exit_price
    trade["exit_time"]  = time.time()
    trade["pnl"]         = pnl_trade_total
    duration_seconds      = trade["exit_time"] - trade["timestamp_open"]

    if uid not in trades_historique:
        trades_historique[uid] = []
    trades_historique[uid].append({
        "trade_id": trade_id, "symbol": trade["symbol"],
        "direction": trade["direction"], "entry": trade["entry_price"],
        "exit": exit_price, "pnl": pnl_trade_total, "duration": duration_seconds,
        "win": win, "timestamp": trade["exit_time"], "label": trade.get("label","")
    })

    pnl_total[uid] = pnl_total.get(uid, 0) + pnl_final
    
    # FIX CORRECTION BUG 2: Retrait du paramètre non supporté pnl_pour_bilan pour éviter le plantage
    enregistrer_resultat_trade(uid, pnl_final, win)

    del trades_actifs[uid]
    print(f"[Trade Closed] {uid}: {trade_id} PnL final={pnl_final:.2f} | "
          f"PnL total trade={pnl_trade_total:.2f}", flush=True)
    return {"trade_id": trade_id, "pnl": pnl_trade_total, "pnl_final_portion": pnl_final,
            "win": win, "duration": duration_seconds}

def fermer_trade_partiel(uid, exit_price):
    if uid not in trades_actifs:
        return None
    trade = trades_actifs[uid]
    if trade["partial_closed"]:
        return None

    risque_initial = trade["sizing"]["montant_risque"]
    ratio = RISK_CONFIG["partial_tp_ratio"]
    gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
    pnl_partiel = risque_initial * gain_ratio * ratio

    trade["partial_closed"]   = True
    trade["partial_pnl"]      = pnl_partiel
    trade["breakeven_active"] = True
    trade["state"]            = TradeState.TRADE_PARTIAL

    buffer = trade["entry_price"] * RISK_CONFIG["breakeven_buffer_pct"]
    if trade["direction"] == "BUY":
        trade["sl"] = trade["entry_price"] + buffer
    else:
        trade["sl"] = trade["entry_price"] - buffer

    pnl_total[uid] = pnl_total.get(uid, 0) + pnl_partiel

    stats = init_daily_stats(uid)
    stats["pnl"] += pnl_partiel

    print(f"[Partial TP] {uid}: {trade['trade_id']} 85% fermé (+{pnl_partiel:.2f}), "
          f"SL → Breakeven {trade['sl']:.5f}", flush=True)

    return {"pnl_partiel": round(pnl_partiel, 2), "nouveau_sl": trade["sl"]}

def appliquer_trailing_stop(uid, prix_current):
    if uid not in trades_actifs:
        return False
    trade = trades_actifs[uid]
    if not trade["breakeven_active"]:
        return False

    distance_trail = prix_current * RISK_CONFIG["trailing_stop_distance_pct"]

    if trade["direction"] == "BUY":
        nouveau_sl_potentiel = prix_current - distance_trail
        if nouveau_sl_potentiel > trade["sl"]:
            trade["sl"] = nouveau_sl_potentiel
            trade["trailing_active"] = True
            return True
    else:
        nouveau_sl_potentiel = prix_current + distance_trail
        if nouveau_sl_potentiel < trade["sl"]:
            trade["sl"] = nouveau_sl_potentiel
            trade["trailing_active"] = True
            return True
    return False

def utilisateur_a_trade_actif(uid):
    return uid in trades_actifs and trades_actifs[uid]["state"] in (
        TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL
    )

# ==========================================
# SESSIONS DE TRADING (Killzones)
# ==========================================

PAIRES_SESSION_ASIE    = ["AUDJPY","CADJPY","CHFJPY","USDJPY","EURJPY","AUDUSD","AUDCAD","XAUUSD","XAGUSD"]
PAIRES_SESSION_LONDRES = ["EURUSD","GBPUSD","EURCHF","USDCHF","CADCHF","EURJPY","EURAUD","XAUUSD","XAGUSD"]
PAIRES_SESSION_NY      = ["EURUSD","GBPUSD","USDCAD","USDCHF","AUDUSD","XAUUSD","XAGUSD"]

def get_session_active():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute / 60.0
    paires, sessions = [], []
    if 0.0 <= h < 7.0:
        paires += PAIRES_SESSION_ASIE;    sessions.append("ASIE")
    if 7.0 <= h < 8.0:
        paires += PAIRES_SESSION_ASIE + PAIRES_SESSION_LONDRES; sessions.append("ASIE+LONDRES")
    if 8.0 <= h <= 10.0:
        paires += PAIRES_SESSION_LONDRES; sessions.append("LONDRES")
    if 12.0 <= h <= 15.0:
        paires += PAIRES_SESSION_NY;      sessions.append("NEW_YORK")
    if not sessions:
        return None, []
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

def session_actuelle_v43():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute / 60.0
    if 1.0 <= h < 6.0:
        return "ASIAN_ACCUMULATION"
    if 6.0 <= h < 8.0:
        return "PRE_LONDON"
    if 8.0 <= h <= 11.0:
        return "LONDON_EXPANSION"
    if 11.0 <= h < 14.0:
        return "LONDON_NY_GAP"
    if 14.0 <= h <= 17.0:
        return "NY_CONTINUATION"
    return "OFF_SESSION"

def est_symbole_autorise(symbole):
    if symbole in VOLATILE_PAIRS:
        if not volatility_pairs_active.get(symbole, True):
            return "BLOCAGE_TOTAL", f"{symbole} désactivé"
        return "AUTORISE", ""

    now     = datetime.datetime.utcnow()
    j, h    = now.weekday(), now.hour + now.minute / 60.0
    weekend = (j == 4 and h >= 21) or j == 5 or (j == 6 and h < 21)

    if weekend:
        return "BLOCAGE_TOTAL", "Week-end"
    if symbole in COMMODITY_PAIRS:
        return "AUTORISE", ""

    session, paires_session = get_session_active()
    if session is None:
        return "HORS_SESSION", "🔒 Hors Killzone"
    if symbole in paires_session:
        return "AUTORISE", ""
    return "HORS_SESSION", f"🔒 {symbole} inactif en {session}"

# ==========================================
# ASIAN RANGE TRACKER
# ==========================================

def maj_asian_range(symbole):
    today = get_today_str()
    cached = asian_range_cache.get(symbole)
    if cached and cached["date"] == today:
        return cached

    c1h = obtenir_donnees_deriv(symbole, 3600)
    if not c1h or len(c1h) < 10:
        return None

    fenetre = c1h[-30:]
    highs, lows = [], []
    for c in fenetre:
        highs.append(float(c["high"]))
        lows.append(float(c["low"]))

    sample = fenetre[-12:-6] if len(fenetre) >= 12 else fenetre[:max(1,len(fenetre)//2)]
    if not sample:
        sample = fenetre

    asian_high = max(float(c["high"]) for c in sample)
    asian_low  = min(float(c["low"])  for c in sample)

    result = {"date": today, "high": asian_high, "low": asian_low,
              "range": asian_high - asian_low, "ts": time.time()}
    asian_range_cache[symbole] = result
    return result

def detecter_bos_londres(symbole, asian_range):
    if not asian_range:
        return None
    px = obtenir_prix_broker_realtime(symbole)
    if not px:
        return None

    if px > asian_range["high"]:
        return "BULL"
    if px < asian_range["low"]:
        return "BEAR"
    return None

# ==========================================
# INDICATEURS TECHNIQUES
# ==========================================

def calculer_ema_cloud(df):
    e72  = ta.trend.EMAIndicator(close=df['close'], window=min(72,len(df)-1)).ema_indicator()
    e89  = ta.trend.EMAIndicator(close=df['close'], window=min(89,len(df)-1)).ema_indicator()
    e180 = ta.trend.EMAIndicator(close=df['close'], window=min(180,len(df)-1)).ema_indicator()
    e200 = ta.trend.EMAIndicator(close=df['close'], window=min(200,len(df)-1)).ema_indicator()
    r = "BULL" if e72.iloc[-1]  > e89.iloc[-1]  else "BEAR"
    l = "BULL" if e180.iloc[-1] > e200.iloc[-1] else "BEAR"
    if r == "BULL" and l == "BULL": return "BULL", "FORT 🟢🟢"
    if r == "BEAR" and l == "BEAR": return "BEAR", "FORT 🔴🔴"
    return r, "MODÉRÉ 🟡"

def trouver_dernier_swing(df, tendance):
    n = 3
    highs, lows = df['high'].values, df['low'].values
    swing_highs, swing_lows = [], []
    for i in range(n, len(highs) - n):
        if all(highs[i] > highs[i-k] for k in range(1,n+1)) and all(highs[i] > highs[i+k] for k in range(1,n+1)):
            swing_highs.append((i, highs[i]))
        if all(lows[i] < lows[i-k] for k in range(1,n+1)) and all(lows[i] < lows[i+k] for k in range(1,n+1)):
            swing_lows.append((i, lows[i]))
    if not swing_highs or not swing_lows:
        return df['high'].iloc[-40:].max(), df['low'].iloc[-40:].min()
    if tendance == "BEAR":
        for sh in reversed(swing_highs[-5:]):
            after = [sl for sl in swing_lows if sl[0] > sh[0]]
            if after: return sh[1], min(after, key=lambda x: x[1])[1]
    else:
        for sl in reversed(swing_lows[-5:]):
            after = [sh for sh in swing_highs if sh[0] > sl[0]]
            if after: return max(after, key=lambda x: x[1])[1], sl[1]
    return max(swing_highs[-3:], key=lambda x: x[0])[1], max(swing_lows[-3:], key=lambda x: x[0])[1]

def calculer_zone_ote(sh, sl, tendance):
    diff = sh - sl
    if tendance == "BEAR":
        ob, oh    = sl + diff*0.618, sl + diff*0.786
        sl_lvl    = sh + diff*0.05
        dist      = abs(oh - sl_lvl)
        tp1, tp15 = oh - dist, oh - dist*1.5
    else:
        ob, oh    = sh - diff*0.786, sh - diff*0.618
        sl_lvl    = sl - diff*0.05
        dist      = abs(ob - sl_lvl)
        tp1, tp15 = ob + dist, ob + dist*1.5
    return {"ote_bas":round(ob,5),"ote_haut":round(oh,5),
            "sl":round(sl_lvl,5),"tp_1r":round(tp1,5),"tp_15r":round(tp15,5)}

def detecter_reaction_ote(df, zone, tendance):
    last  = df.iloc[-2]
    prev  = df.iloc[-3]
    px    = last['close']
    dans  = zone["ote_bas"] <= px        <= zone["ote_haut"]
    pdans = zone["ote_bas"] <= prev['close'] <= zone["ote_haut"]
    if not (dans or pdans): return False, "Hors zone OTE"
    corps   = abs(last['close'] - last['open'])
    taille  = last['high'] - last['low']
    meche_h = last['high']  - max(last['open'], last['close'])
    meche_b = min(last['open'], last['close']) - last['low']
    if taille == 0: return False, "Bougie doji"
    if tendance == "BEAR":
        if prev['close']>prev['open'] and last['close']<last['open'] and last['close']<prev['open']:
            return True, "🕯️ Engulfing Baissier"
        if meche_h > corps*2.0: return True, "📍 Pin Bar Baissier"
        if last['close']<last['open'] and corps>taille*0.4: return True, "📉 Rejet Baissier"
    else:
        if prev['close']<prev['open'] and last['close']>last['open'] and last['close']>prev['open']:
            return True, "🕯️ Engulfing Haussier"
        if meche_b > corps*2.0: return True, "📍 Pin Bar Haussier"
        if last['close']>last['open'] and corps>taille*0.4: return True, "📈 Rejet Haussier"
    return False, "Pas de réaction nette"

def calculer_score_confiance(symbole, tendance, force_ema, rr_ratio, reaction_type, volatilite):
    score = 50
    if "FORT"   in force_ema: score += 20
    elif "MODÉRÉ" in force_ema: score += 10
    else: score -= 15
    if rr_ratio >= 2.0: score += 15
    elif rr_ratio >= 1.5: score += 10
    else: score -= 10
    if "Engulfing" in reaction_type: score += 15
    elif "Pin Bar" in reaction_type: score += 12
    elif "Rejet"   in reaction_type: score += 8
    else: score -= 10
    if volatilite < 0.7: score += 5
    elif volatilite > 1.5: score -= 10
    return max(0, min(100, score))

# ==========================================
# STRATÉGIE 1: KASPER OTE STRICT
# ==========================================

def analyser_kasper_ote(symbole):
    c5  = obtenir_donnees_deriv(symbole, 300)
    c1h = obtenir_donnees_deriv(symbole, 3600)
    if not c5 or not c1h: return None
    try:
        df5 = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                              "high":float(c["high"]),"low":float(c["low"])} for c in c5])
        dfh = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                              "high":float(c["high"]),"low":float(c["low"])} for c in c1h])

        tendance, force = calculer_ema_cloud(dfh)
        if "FORT" not in force: return None

        sh, sl = trouver_dernier_swing(df5, tendance)
        if sh <= sl: return None

        zone = calculer_zone_ote(sh, sl, tendance)
        px   = df5["close"].iloc[-1]

        if tendance == "BEAR" and px > zone["sl"]: return None
        if tendance == "BULL" and px < zone["sl"]: return None

        react, msg_r = detecter_reaction_ote(df5, zone, tendance)
        if not react: return None

        risque  = abs(px - zone["sl"])
        recomp  = abs(zone["tp_15r"] - px)
        rr      = round(recomp / risque, 2) if risque > 0 else 0
        if rr < 1.5: return None

        atr        = (dfh["high"] - dfh["low"]).rolling(14).mean().iloc[-1]
        volatilite = atr / px if px > 0 else 1.0
        confiance  = calculer_score_confiance(symbole, tendance, force, rr, msg_r, volatilite)
        if confiance < 75: return None

        return {
            "action": "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force": force, "msg": msg_r,
            "sh": round(sh,5), "sl_swing": round(sl,5),
            "zone": zone, "sl": zone["sl"], "tp1": zone["tp_1r"],
            "tp": zone["tp_15r"], "rr": rr, "px": round(px,5),
            "strategie": 1, "confiance": confiance,
            "label": "KASPER OTE STRICT",
            "contexte_requis": "TENDANCE"
        }
    except Exception as e:
        print(f"[Kasper/{symbole}] {e}", flush=True)
    return None

# ==========================================
# STRATÉGIE 2: OTE SCALPING AGRESSIF
# ==========================================

def analyser_ote_scalping(symbole):
    c5  = obtenir_donnees_deriv(symbole, 300)
    c1h = obtenir_donnees_deriv(symbole, 3600)
    if not c5 or not c1h: return None
    try:
        df5 = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                              "high":float(c["high"]),"low":float(c["low"])} for c in c5])
        dfh = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                              "high":float(c["high"]),"low":float(c["low"])} for c in c1h])

        tendance, force = calculer_ema_cloud(dfh)

        sh, sl = trouver_dernier_swing(df5, tendance)
        if sh <= sl: return None

        zone = calculer_zone_ote(sh, sl, tendance)
        px   = df5["close"].iloc[-1]

        if tendance == "BEAR" and px > zone["sl"]: return None
        if tendance == "BULL" and px < zone["sl"]: return None

        react, msg_r = detecter_reaction_ote(df5, zone, tendance)
        if not react: return None

        risque  = abs(px - zone["sl"])
        recomp  = abs(zone["tp_15r"] - px)
        rr      = round(recomp / risque, 2) if risque > 0 else 0
        if rr < 1.3: return None

        atr        = (dfh["high"] - dfh["low"]).rolling(14).mean().iloc[-1]
        volatilite = atr / px if px > 0 else 1.0
        confiance  = calculer_score_confiance(symbole, tendance, force, rr, msg_r, volatilite)
        if confiance < 55: return None

        return {
            "action": "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force": force, "msg": msg_r,
            "sh": round(sh,5), "sl_swing": round(sl,5),
            "zone": zone, "sl": zone["sl"], "tp1": zone["tp_1r"],
            "tp": zone["tp_15r"], "rr": rr, "px": round(px,5),
            "strategie": 2, "confiance": confiance,
            "label": "OTE SCALPING",
            "contexte_requis": "SCALPING"
        }
    except Exception as e:
        print(f"[Scalping/{symbole}] {e}", flush=True)
    return None

# ==========================================
# STRATÉGIE 3: ZONE TRADING
# ==========================================

def identifier_zone_consolidation(df, lookback=50):
    df_r = df.iloc[-lookback:] if len(df) > lookback else df
    r_high = df_r["high"].max()
    r_low  = df_r["low"].min()
    zone   = {"resistance": r_high, "support": r_low, "width": r_high - r_low}
    rebonds_up, rebonds_dn = 0, 0
    for i in range(len(df_r)):
        l = df_r["low"].iloc[i]
        h = df_r["high"].iloc[i]
        c = df_r["close"].iloc[i]
        if l < zone["support"] * 1.002 and c > zone["support"] * 1.005:
            rebonds_up += 1
        if h > zone["resistance"] * 0.998 and c < zone["resistance"] * 0.995:
            rebonds_dn += 1
    zone["rebond_count"] = rebonds_up + rebonds_dn
    if zone["rebond_count"] < 3:
        return None
    return zone

def analyser_zone_trading(symbole):
    c4h = obtenir_donnees_h4(symbole)
    c1h = obtenir_donnees_deriv(symbole, 3600)
    if not c4h or not c1h: return None
    try:
        df4h = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                               "high":float(c["high"]),"low":float(c["low"])} for c in c4h])
        df1h = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                               "high":float(c["high"]),"low":float(c["low"])} for c in c1h])

        zone = identifier_zone_consolidation(df4h, lookback=50)
        if not zone: return None

        vol_zone    = (df4h.iloc[-50:]["high"] - df4h.iloc[-50:]["low"]).std()
        vol_general = (df4h["high"] - df4h["low"]).std()
        if vol_zone > vol_general * 0.75: return None

        px         = df4h["close"].iloc[-1]
        zone_width = zone["resistance"] - zone["support"]
        if zone_width <= 0: return None

        dist_sup = px - zone["support"]
        dist_res = zone["resistance"] - px

        signal    = None
        tendance  = None

        if dist_sup < zone_width * 0.2:
            last = df1h.iloc[-2]
            if last["low"] < zone["support"] * 1.002 and last["close"] > zone["support"]:
                signal   = "BUY"
                tendance = "BULL"
                sl       = zone["support"]  - zone_width * 0.05
                tp       = zone["resistance"]

        elif dist_res < zone_width * 0.2:
            last = df1h.iloc[-2]
            if last["high"] > zone["resistance"] * 0.998 and last["close"] < zone["resistance"]:
                signal   = "SELL"
                tendance = "BEAR"
                sl       = zone["resistance"] + zone_width * 0.05
                tp       = zone["support"]

        if not signal: return None

        risque  = abs(px - sl)
        recomp  = abs(tp  - px)
        rr      = round(recomp / risque, 2) if risque > 0 else 0
        if rr < 1.5: return None

        try:
            rsi_val = ta.momentum.RSIIndicator(close=df1h["close"], window=14).rsi().iloc[-1]
            rsi_ok  = (tendance == "BULL" and rsi_val < 65) or (tendance == "BEAR" and rsi_val > 35)
        except:
            rsi_ok = False

        confiance = 50
        if zone["rebond_count"] >= 5:  confiance += 15
        elif zone["rebond_count"] >= 3: confiance += 8
        ratio_vol = vol_zone / vol_general if vol_general > 0 else 1.0
        if ratio_vol < 0.5:  confiance += 15
        elif ratio_vol < 0.7: confiance += 8
        if rr >= 2.0:  confiance += 15
        elif rr >= 1.5: confiance += 10
        if rsi_ok:     confiance += 10
        if dans_killzone(): confiance += 5
        confiance = max(0, min(100, confiance))
        if confiance < 60: return None

        return {
            "action": "🟢 ACHAT (BUY)" if signal=="BUY" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force": "ZONE 📦",
            "msg": f"Rebond sur {'Support' if signal=='BUY' else 'Résistance'}",
            "zone_support":    round(zone["support"], 5),
            "zone_resistance": round(zone["resistance"], 5),
            "zone_rebonds":    zone["rebond_count"],
            "sl":  round(sl, 5), "tp1": round(tp, 5),
            "tp":  round(tp, 5), "rr":  rr,
            "px":  round(px, 5), "strategie": 3,
            "confiance": confiance,
            "label": "ZONE TRADING",
            "contexte_requis": "RANGE"
        }
    except Exception as e:
        print(f"[ZoneTrading/{symbole}] {e}", flush=True)
    return None

# ==========================================
# STRATÉGIE 4 — BOUGIE PIVOT SESSION
# ==========================================

def trouver_bougie_pivot(df, tendance, lookback=30):
    df_r = df.iloc[-lookback:] if len(df) > lookback else df
    if len(df_r) < 5:
        return None

    for i in range(len(df_r) - 3, 1, -1):
        bougie      = df_r.iloc[i]
        bougie_next = df_r.iloc[i+1] if i+1 < len(df_r) else None
        if bougie_next is None:
            continue

        corps_bougie = bougie["close"] - bougie["open"]
        corps_next   = bougie_next["close"] - bougie_next["open"]

        if tendance == "BULL":
            if corps_bougie <= 0 and corps_next > 0:
                taille_next = abs(corps_next)
                taille_pivot = bougie["high"] - bougie["low"]
                if taille_pivot > 0 and taille_next > taille_pivot * 0.8:
                    return {
                        "index": i,
                        "high": float(bougie["high"]), "low": float(bougie["low"]),
                        "open": float(bougie["open"]), "close": float(bougie["close"]),
                    }
        else:
            if corps_bougie >= 0 and corps_next < 0:
                taille_next = abs(corps_next)
                taille_pivot = bougie["high"] - bougie["low"]
                if taille_pivot > 0 and taille_next > taille_pivot * 0.8:
                    return {
                        "index": i,
                        "high": float(bougie["high"]), "low": float(bougie["low"]),
                        "open": float(bougie["open"]), "close": float(bougie["close"]),
                    }
    return None

def detecter_liquidity_sweep(df, pivot, tendance, lookback=15):
    df_r = df.iloc[-lookback:]
    if len(df_r) < 3:
        return False

    recent_low  = df_r["low"].min()
    recent_high = df_r["high"].max()
    last        = df_r.iloc[-2]

    if tendance == "BULL":
        return last["low"] <= pivot["low"] * 1.0015 and last["close"] > pivot["low"]
    else:
        return last["high"] >= pivot["high"] * 0.9985 and last["close"] < pivot["high"]

def analyser_bougie_pivot_session(symbole):
    c1h = obtenir_donnees_deriv(symbole, 3600)
    c5  = obtenir_donnees_deriv(symbole, 300)
    if not c1h or not c5:
        return None

    try:
        dfh = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                              "high":float(c["high"]),"low":float(c["low"])} for c in c1h])
        df5 = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                              "high":float(c["high"]),"low":float(c["low"])} for c in c5])

        asian_range = maj_asian_range(symbole)
        bos = detecter_bos_londres(symbole, asian_range) if asian_range else None

        if bos:
            tendance = bos
        else:
            tendance, _ = calculer_ema_cloud(dfh)

        pivot_h1 = trouver_bougie_pivot(dfh, tendance, lookback=30)
        if not pivot_h1:
            return None

        pivot_m5 = trouver_bougie_pivot(df5, tendance, lookback=40)
        if not pivot_m5:
            return None

        zone_h1_haut = max(pivot_h1["high"], pivot_h1["open"], pivot_h1["close"])
        zone_h1_bas  = min(pivot_h1["low"],  pivot_h1["open"], pivot_h1["close"])
        pivot_m5_mid = (pivot_m5["high"] + pivot_m5["low"]) / 2

        marge = (zone_h1_haut - zone_h1_bas) * 1.5
        if not (zone_h1_bas - marge <= pivot_m5_mid <= zone_h1_haut + marge):
            return None

        sweep_ok = detecter_liquidity_sweep(df5, pivot_m5, tendance)
        px = df5["close"].iloc[-1]

        if tendance == "BULL":
            entree = pivot_m5["high"]
            sl     = pivot_m5["low"] * 0.9985
            if px > entree * 1.01 or px < pivot_m5["low"]:
                return None
            distance_risque = entree - sl
            tp1      = entree + distance_risque * 2.0
            tp_final = entree + distance_risque * 6.0
        else:
            entree = pivot_m5["low"]
            sl     = pivot_m5["high"] * 1.0015
            if px < entree * 0.99 or px > pivot_m5["high"]:
                return None
            distance_risque = sl - entree
            tp1      = entree - distance_risque * 2.0
            tp_final = entree - distance_risque * 6.0

        if distance_risque <= 0:
            return None

        risque  = abs(entree - sl)
        recomp  = abs(tp_final - entree)
        rr      = round(recomp / risque, 2) if risque > 0 else 0
        if rr < 2.0:
            return None

        confiance = 50
        if bos:                       confiance += 20
        if sweep_ok:                  confiance += 20
        session_v43 = session_actuelle_v43()
        if session_v43 in ("LONDON_EXPANSION","NY_CONTINUATION"):
            confiance += 10
        if rr >= 4.0:                 confiance += 10
        elif rr >= 2.0:                confiance += 5
        confiance = max(0, min(100, confiance))

        if confiance < 65:
            return None

        return {
            "action": "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance,
            "force": "BOS ✅" if bos else "EMA",
            "msg": ("🎯 Sweep de liquidité confirmé" if sweep_ok else "Pivot sans sweep confirmé"),
            "px": round(px, 5),
            "entree": round(entree, 5),
            "sl": round(sl, 5),
            "tp1": round(tp1, 5),
            "tp": round(tp_final, 5),
            "rr": rr,
            "strategie": 4,
            "confiance": confiance,
            "label": "BOUGIE PIVOT SESSION",
            "contexte_requis": "SESSION_PIVOT",
            "session": session_v43,
            "bos_detecte": bool(bos),
            "sweep_detecte": sweep_ok,
            "asian_range": asian_range,
        }

    except Exception as e:
        print(f"[BougiePivot/{symbole}] {e}", flush=True)
    return None

# ==========================================
# DÉTECTION DU CONTEXTE MARCHÉ
# ==========================================

def detecter_contexte(symbole):
    cached = contexte_marche_cache.get(symbole)
    if cached and (time.time() - cached["ts"]) < 120:
        return cached["contexte"]

    try:
        c4h = obtenir_donnees_h4(symbole)
        c1h = obtenir_donnees_deriv(symbole, 3600)
        if not c4h or not c1h:
            return "INDECIS"

        df4h = pd.DataFrame([{
            "open":float(c["open"]),"close":float(c["close"]),
            "high":float(c["high"]),"low":float(c["low"])
        } for c in c4h[-100:]])

        df1h = pd.DataFrame([{
            "open":float(c["open"]),"close":float(c["close"]),
            "high":float(c["high"]),"low":float(c["low"])
        } for c in c1h[-50:]])

        e72  = ta.trend.EMAIndicator(close=df4h["close"], window=min(72, len(df4h)-1)).ema_indicator()
        e89  = ta.trend.EMAIndicator(close=df4h["close"], window=min(89, len(df4h)-1)).ema_indicator()
        e180 = ta.trend.EMAIndicator(close=df4h["close"], window=min(180,len(df4h)-1)).ema_indicator()
        e200 = ta.trend.EMAIndicator(close=df4h["close"], window=min(200,len(df4h)-1)).ema_indicator()
        rsi  = ta.momentum.RSIIndicator(close=df1h["close"], window=14).rsi()

        rapide_bull   = e72.iloc[-1]  > e89.iloc[-1]
        lent_bull     = e180.iloc[-1] > e200.iloc[-1]
        emas_alignees = rapide_bull == lent_bull
        vol           = (df4h["high"] - df4h["low"]).iloc[-20:].mean()
        px_moyen      = df4h["close"].iloc[-1]
        vol_pct       = (vol / px_moyen) if px_moyen > 0 else 0
        rsi_val       = rsi.iloc[-1] if not rsi.isna().iloc[-1] else 50

        session_v43 = session_actuelle_v43()

        if session_v43 in ("LONDON_EXPANSION", "NY_CONTINUATION"):
            asian_range = maj_asian_range(symbole)
            bos = detecter_bos_londres(symbole, asian_range) if asian_range else None
            if bos:
                contexte_marche_cache[symbole] = {"contexte": "SESSION_PIVOT", "ts": time.time()}
                return "SESSION_PIVOT"

        if emas_alignees and vol_pct > 0.005:
            contexte = "TENDANCE"
        elif rsi_val < 30 or rsi_val > 70:
            contexte = "SCALPING"
        elif not emas_alignees and vol_pct < 0.004:
            contexte = "RANGE"
        else:
            contexte = "INDECIS"

        contexte_marche_cache[symbole] = {"contexte": contexte, "ts": time.time()}
        return contexte

    except Exception as e:
        print(f"[Contexte/{symbole}] {e}", flush=True)
        return "INDECIS"

# ==========================================
# CERVEAU PRO TRADER V2 (4 stratégies)
# ==========================================

def cerveau_pro_trader(symbole):
    contexte = detecter_contexte(symbole)

    if contexte == "SESSION_PIVOT":
        res = analyser_bougie_pivot_session(symbole)
        emoji_ctx = "🎯 SESSION PIVOT (BOS + Liquidité)"
        if not res:
            res = analyser_kasper_ote(symbole)
            emoji_ctx = "📈 TENDANCE (fallback)"

    elif contexte == "TENDANCE":
        res = analyser_kasper_ote(symbole)
        emoji_ctx = "📈 TENDANCE FORTE"

    elif contexte == "SCALPING":
        res = analyser_ote_scalping(symbole)
        emoji_ctx = "⚡ MOMENTUM SCALPING"

    elif contexte == "RANGE":
        res = analyser_zone_trading(symbole)
        emoji_ctx = "📦 ZONE / RANGE"

    else:
        return None, contexte

    if res:
        res["contexte_detecte"] = emoji_ctx

    return res, contexte

# ==========================================
# /Volatility GRANULAIRE
# ==========================================

@bot.message_handler(commands=['Volatility'])
def gerer_volatility(message):
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "❌ Admin uniquement.")

    parts = message.text.strip().split()

    if len(parts) == 1:
        lignes = ["🔥 *STATUT VOLATILITY PAIRS:*\n━━━━━━━━━━━━━━━━━━"]
        for p, actif in volatility_pairs_active.items():
            lignes.append(f"  {'✅' if actif else '❌'} {p}")
        lignes.append("\n*Commandes:*")
        lignes.append("/Volatility V10 ON/OFF")
        lignes.append("/Volatility ALL ON/OFF")
        return bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")

    if len(parts) < 3:
        return bot.send_message(message.chat.id,
            "Usage: /Volatility V10 ON\n/Volatility ALL OFF", parse_mode="Markdown")

    paire  = parts[1].upper()
    action = parts[2].upper()

    if action not in ("ON","OFF"):
        return bot.send_message(message.chat.id, "Action invalide: ON ou OFF")

    etat = (action == "ON")

    if paire == "ALL":
        for p in volatility_pairs_active:
            volatility_pairs_active[p] = etat
        msg = ("✅ Toutes les paires Volatility *ACTIVÉES*"
               if etat else "⛔ Toutes les paires Volatility *DÉSACTIVÉES*")
        return bot.send_message(message.chat.id, msg, parse_mode="Markdown")

    if paire in volatility_pairs_active:
        volatility_pairs_active[paire] = etat
        msg = (f"✅ {paire} *ACTIVÉ*" if etat else f"⛔ {paire} *DÉSACTIVÉ*")
        return bot.send_message(message.chat.id, msg, parse_mode="Markdown")

    bot.send_message(message.chat.id,
        f"❌ Paire inconnue: {paire}\nValides: V10, V25, V50, V75, V100, ALL")

# ==========================================
# /risk — Configurer le risque par trade
# ==========================================

@bot.message_handler(commands=['risk'])
def gerer_risque(message):
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "❌ Admin uniquement.")

    parts = message.text.strip().split()
    if len(parts) == 1:
        txt = (
            f"⚙️ *PARAMÈTRES DE RISQUE ACTUELS*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Risque/trade : {RISK_CONFIG['risk_per_trade_pct']}%\n"
            f"Limite perte/jour : {RISK_CONFIG['daily_loss_limit_pct']}%\n"
            f"Pertes consécutives max : {RISK_CONFIG['max_consecutive_losses']}\n"
            f"Durée pause anti-tilt : {RISK_CONFIG['pause_duration_minutes']} min\n"
            f"Partial TP : {int(RISK_CONFIG['partial_tp_ratio']*100)}%\n"
            f"Trades max/jour : {RISK_CONFIG['max_trades_per_day']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Usage: /risk <param> <valeur>\n"
            f"Ex: /risk risk_per_trade_pct 1.5"
        )
        return bot.send_message(message.chat.id, txt, parse_mode="Markdown")

    if len(parts) >= 3 and parts[1] in RISK_CONFIG:
        try:
            valeur = float(parts[2])
            RISK_CONFIG[parts[1]] = valeur
            return bot.send_message(message.chat.id,
                f"✅ {parts[1]} = {valeur}", parse_mode="Markdown")
        except ValueError:
            return bot.send_message(message.chat.id, "❌ Valeur invalide.")

    bot.send_message(message.chat.id, "❌ Paramètre inconnu.")

# ==========================================
# /rapport — Rapport quotidien
# ==========================================

def generer_rapport_texte(uid):
    stats = init_daily_stats(uid)
    total = stats["trades"]
    winrate = (stats["wins"] / total * 100) if total > 0 else 0
    return (
        f"📊 *RAPPORT DU JOUR* ({stats['date']})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Trades exécutés : {total}/{RISK_CONFIG['max_trades_per_day']}\n"
        f"✅ Gagnés : {stats['wins']}  |  ❌ Perdus : {stats['losses']}\n"
        f"🎯 Win Rate : {winrate:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 P&L du jour : {stats['pnl']:+.2f} USD\n"
        f"🏆 Meilleur trade : {stats['best_trade']:+.2f} USD\n"
        f"💔 Pire trade : {stats['worst_trade']:+.2f} USD\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 P&L total cumulé : {pnl_total.get(uid,0):+.2f} USD\n"
        f"📈 Bilan global : {win_count.get(uid,0)}W / {loss_count.get(uid,0)}L"
    )

@bot.message_handler(commands=['rapport'])
def rapport_quotidien(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    bot.send_message(uid, generer_rapport_texte(uid), parse_mode="Markdown")

def envoyer_rapports_quotidiens_auto():
    dernier_envoi = None
    while True:
        try:
            time.sleep(60)
            now = datetime.datetime.utcnow()
            cle_jour = now.strftime("%Y-%m-%d")
            if now.hour == 22 and dernier_envoi != cle_jour:
                for uid in list(utilisateurs_actifs):
                    try:
                        bot.send_message(uid, "🌙 *Rapport de fin de journée*\n\n" +
                                         generer_rapport_texte(uid), parse_mode="Markdown")
                    except:
                        pass
                dernier_envoi = cle_jour
        except Exception as e:
            print(f"[Rapport Auto] {e}", flush=True)

# ==========================================
# /pause /resume — Circuit breaker manuel
# ==========================================

@bot.message_handler(commands=['pause'])
def pause_manuelle(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    stats = init_daily_stats(uid)
    stats["paused_until"] = time.time() + (12 * 3600)
    bot.send_message(uid, "⏸️ Trading mis en pause manuellement pour 12h.\n"
                          "Utilise /resume pour reprendre.", parse_mode="Markdown")

@bot.message_handler(commands=['resume'])
def resume_manuel(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    stats = init_daily_stats(uid)
    stats["paused_until"] = None
    stats["consecutive_losses"] = 0
    bot.send_message(uid, "▶️ Trading repris. Bonne chance!", parse_mode="Markdown")

# ==========================================
# SCANNER PRINCIPAL V43
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            libres = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not libres: continue

            for paire in (ELITE_PAIRS_MT5 + FOREX_PAIRS):
                statut, _ = est_symbole_autorise(paire)
                if statut != "AUTORISE": continue

                res, contexte = cerveau_pro_trader(paire)
                if not res: continue

                px = obtenir_prix_broker_realtime(paire) or res["px"]
                if not valider_prix_avant_signal(paire, px): continue

                cle = f"{paire}_PRO"
                signaux_cache[cle] = {
                    "time":    time.time(),
                    "action":  res["action"],
                    "mt5_sl":  res["sl"],
                    "mt5_tp1": res.get("tp1", res["tp"]),
                    "mt5_tp":  res["tp"],
                    "mt5_rr":  res["rr"],
                    "force":   res["force"],
                    "msg":     res["msg"],
                    "confiance": res["confiance"],
                    "strategie": res["strategie"],
                    "label":   res["label"],
                    "contexte":res.get("contexte_detecte",""),
                    "extra":   res,
                }
                derniere_alerte_auto[cle] = time.time()

                nom  = NOMS_AFFICHAGE.get(paire, f"{paire[:3]}/{paire[3:]}")
                dir_ = "🟢 BUY" if "BUY" in res["action"] else "🔴 SELL"

                for uid in libres:
                    if utilisateur_a_trade_actif(uid): continue

                    peut_trader, raison = utilisateur_peut_trader(uid)
                    if not peut_trader: continue

                    pf = plateforme_trading.get(uid, "MT5")
                    if pf == "MT5"    and paire not in ELITE_PAIRS_MT5: continue
                    if pf == "POCKET" and paire not in FOREX_PAIRS:    continue

                    markup = InlineKeyboardMarkup().add(
                        InlineKeyboardButton(f"⚡ Copier {nom}", callback_data=f"set_{paire}")
                    )

                    if res["strategie"] == 3:
                        ligne_extra = (f"📦 Zone : {res['zone_support']:.5f}"
                                       f" → {res['zone_resistance']:.5f}"
                                       f" ({res['zone_rebonds']} rebonds)\n")
                    elif res["strategie"] == 4:
                        bos_txt   = "✅ BOS confirmé" if res.get("bos_detecte") else "⚠️ Pas de BOS"
                        sweep_txt = "✅ Sweep liquidité" if res.get("sweep_detecte") else "⚠️ Pas de sweep"
                        ligne_extra = (f"🧱 Bougie Pivot — {bos_txt} | {sweep_txt}\n"
                                       f"🕒 Session : {res.get('session','')}\n")
                    else:
                        z = res.get("zone", {})
                        ligne_extra = (f"🔶 Zone OTE : {z.get('ote_bas',0):.5f}"
                                       f" → {z.get('ote_haut',0):.5f}\n")

                    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"],
                                                    px, res["sl"], paire)

                    txt = (
                        f"💼 *TERMINAL PRIME V43*\n"
                        f"{nom}  {dir_}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Stratégie : *{res['label']}*\n"
                        f"📊 Contexte  : {res.get('contexte_detecte','')}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"☁️ Structure : {res['force']}\n"
                        f"📍 {res['msg']}\n"
                        f"⏰ {nom_killzone()}\n"
                        f"{ligne_extra}"
                        f"⚖️ R/R : {res['rr']}R\n"
                        f"🎖️ Confiance : {res['confiance']}%\n"
                        f"💰 Prix réel : {px:.5f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💵 Risque calculé : ${sizing['montant_risque']} "
                        f"({RISK_CONFIG['risk_per_trade_pct']}% du capital)"
                    )
                    try:
                        bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                    except:
                        pass

        except Exception as e:
            print(f"[Scanner V43] {e}", flush=True)

# ==========================================
# MONITORING AVANCÉ DES TRADES
# ==========================================

def monitorer_trades_actifs():
    while True:
        try:
            time.sleep(5)
            for uid in list(trades_actifs.keys()):
                if uid not in trades_actifs: continue
                trade = trades_actifs[uid]

                symbole      = trade["symbol"]
                prix_current = obtenir_prix_broker_realtime(symbole)
                if not prix_current: continue

                direction = trade["direction"]

                if trade["state"] == TradeState.TRADE_OPEN:

                    hit_tp1 = (direction == "BUY"  and prix_current >= trade["tp1"]) or \
                              (direction == "SELL" and prix_current <= trade["tp1"])
                    hit_sl  = (direction == "BUY"  and prix_current <= trade["sl"]) or \
                              (direction == "SELL" and prix_current >= trade["sl"])

                    if hit_sl:
                        result = fermer_trade_complet(uid, prix_current, win=False)
                        if result:
                            envoyer_message_resultat(uid, trade, result, perte_totale=True)
                        continue

                    if hit_tp1:
                        partiel = fermer_trade_partiel(uid, prix_current)
                        if partiel:
                            envoyer_message_partiel(uid, trade, partiel, prix_current)
                        continue

                elif trade["state"] == TradeState.TRADE_PARTIAL:

                    appliquer_trailing_stop(uid, prix_current)

                    hit_tp_final = (direction == "BUY"  and prix_current >= trade["tp_final"]) or \
                                   (direction == "SELL" and prix_current <= trade["tp_final"])
                    hit_be_sl    = (direction == "BUY"  and prix_current <= trade["sl"]) or \
                                   (direction == "SELL" and prix_current >= trade["sl"])

                    if hit_tp_final:
                        result = fermer_trade_complet(uid, prix_current, win=True)
                        if result:
                            envoyer_message_resultat(uid, trade, result, perte_totale=False,
                                                     partiel_deja_pris=True)
                        continue

                    if hit_be_sl:
                        result = fermer_trade_complet(uid, prix_current, win=True)
                        if result:
                            envoyer_message_resultat(uid, trade, result, perte_totale=False,
                                                     partiel_deja_pris=True, sortie_be=True)
                        continue

        except Exception as e:
            print(f"[Monitor] {e}", flush=True)

def envoyer_message_partiel(uid, trade, partiel, prix_current):
    msg = (
        f"🟡 *TP1 ATTEINT — 85% SÉCURISÉ!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {trade['symbol']}\n"
        f"Entrée : {trade['entry_price']:.5f}\n"
        f"TP1    : {prix_current:.5f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Profit partiel : +{partiel['pnl_partiel']:.2f} USD* (85% fermé)\n"
        f"🛡️ SL déplacé en *Breakeven* : {partiel['nouveau_sl']:.5f}\n"
        f"🏃 15% restant continue vers le TP final, *sans risque*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Technique pro: sécuriser le gain, laisser courir le reste."
    )
    try: bot.send_message(uid, msg, parse_mode="Markdown")
    except: pass

def envoyer_message_resultat(uid, trade, result, perte_totale, partiel_deja_pris=False, sortie_be=False):
    stats = init_daily_stats(uid)

    if perte_totale:
        msg = (
            f"❌ *TRADE PERDU* 😔\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {trade['symbol']}\n"
            f"Entrée : {trade['entry_price']:.5f}\n"
            f"Sortie : {result['pnl']:+.2f} USD (Stop Loss)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💔 *Perte : {result['pnl']:.2f} USD*\n"
            f"⏱️ Durée : {int(result['duration']/60)} min\n"
            f"🎖️ {trade.get('label','')} (Confiance {trade['confiance']}%)\n"
        )
    elif sortie_be:
        msg = (
            f"🛡️ *SORTIE EN BREAKEVEN/TRAILING*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {trade['symbol']}\n"
            f"Le 15% restant est sorti au niveau sécurisé.\n"
            f"💰 Gain sécurisé sur cette portion : {result['pnl']:+.2f} USD\n"
            f"⏱️ Durée totale : {int(result['duration']/60)} min\n"
            f"🎖️ {trade.get('label','')}\n"
        )
    else:
        msg = (
            f"✅ *TP FINAL ATTEINT — TRADE GAGNÉ!* 🎉🎉\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {trade['symbol']}\n"
            f"Entrée : {trade['entry_price']:.5f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Profit (15% final) : +{result['pnl']:.2f} USD*\n"
            f"⏱️ Durée : {int(result['duration']/60)} min\n"
            f"🎖️ {trade.get('label','')} (Confiance {trade['confiance']}%)\n"
        )

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Bilan du jour : {stats['wins']}W / {stats['losses']}L "
        f"({stats['pnl']:+.2f} USD)\n"
        f"🏦 P&L total : {pnl_total.get(uid,0):+.2f} USD"
    )

    if daily_loss_limit_atteinte(uid):
        msg += (f"\n\n🛑 *LIMITE DE PERTE JOURNALIÈRE ATTEINTE.*\n"
                f"Trading suspendu jusqu'à demain — protection du capital.")
    else:
        en_pause, _ = utilisateur_en_pause(uid)
        if en_pause:
            msg += (f"\n\n⏸️ *PAUSE ANTI-TILT ACTIVÉE* "
                    f"({RISK_CONFIG['max_consecutive_losses']} pertes consécutives).\n"
                    f"Reprise dans {RISK_CONFIG['pause_duration_minutes']} minutes.")

    try: bot.send_message(uid, msg, parse_mode="Markdown")
    except: pass

# ==========================================
# GESTION DES CLÉS VIP
# ==========================================

DUREES_VALIDES = {
    "1s": (7,"1 Semaine"), "2s": (14,"2 Semaines"),
    "1m": (30,"1 Mois"),   "3m": (90,"3 Mois"),
    "6m": (180,"6 Mois"),  "1a": (365,"1 An"),
    "vie": ("LIFETIME","À VIE 👑"),
}

def est_autorise(uid):
    if uid == ADMIN_ID: return True
    if uid in utilisateurs_autorises:
        exp = utilisateurs_autorises[uid]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: return True
        del utilisateurs_autorises[uid]
        try: bot.send_message(uid, "⚠️ Abonnement expiré. Contacte l'admin.")
        except: pass
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(message.chat.id,
            "⚙️ *GÉNÉRATEUR DE CLÉS VIP*\nUsage : /keygen 1m\n"
            "1s / 2s / 1m / 3m / 6m / 1a / vie / <jours>", parse_mode="Markdown")
    arg = parts[1].lower().strip()
    if arg in DUREES_VALIDES:
        jours, label = DUREES_VALIDES[arg]
    else:
        try:
            jours = int(arg)
            label = f"{jours} jours"
        except:
            return bot.send_message(message.chat.id, "❌ Argument invalide.")
    cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    cles_generees[cle] = jours
    bot.send_message(message.chat.id,
        f"✅ *CLÉ VIP GÉNÉRÉE*\n🔑 `{cle}`\n⏳ Durée : {label}\n"
        f"Activation : `/vip {cle}`", parse_mode="Markdown")

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    cid   = message.chat.id
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(cid, "⚠️ Usage : /vip VOTRE-CLÉ")
    cle = parts[1].strip()
    if cle not in cles_generees:
        return bot.send_message(cid, "❌ Clé invalide ou déjà utilisée.")
    jours = cles_generees.pop(cle)
    if jours == "LIFETIME":
        utilisateurs_autorises[cid] = "LIFETIME"; txt = "À VIE 👑"
    else:
        exp = datetime.datetime.now() + datetime.timedelta(days=jours)
        utilisateurs_autorises[cid] = exp; txt = exp.strftime('%d/%m/%Y à %H:%M')
    bot.send_message(cid,
        f"🎉 *ACCÈS DÉVERROUILLÉ !*\n⏳ Expiration : {txt}\n/start pour commencer.",
        parse_mode="Markdown")

@bot.message_handler(commands=['abonnes'])
def lister_abonnes(message):
    if message.chat.id != ADMIN_ID: return
    now = datetime.datetime.now()
    lignes = ["👥 *ABONNÉS ACTIFS :*\n──────────────────"]
    for uid, exp in utilisateurs_autorises.items():
        if uid == ADMIN_ID: continue
        if exp == "LIFETIME":       statut = "👑 À vie"
        elif now < exp:             statut = f"✅ {(exp-now).days}j (exp: {exp.strftime('%d/%m/%Y')})"
        else:                       statut = "❌ Expiré"
        lignes.append(f"• {uid} → {statut}")
    bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(commands=['cles'])
def lister_cles(message):
    if message.chat.id != ADMIN_ID: return
    if not cles_generees:
        return bot.send_message(message.chat.id, "Aucune clé en attente.")
    lignes = ["🔑 *CLÉS EN ATTENTE :*\n──────────────────"]
    for cle, jours in cles_generees.items():
        lignes.append(f"`{cle}` → {'À VIE' if jours=='LIFETIME' else f'{jours}j'}")
    bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")

# ==========================================
# /historique — Derniers trades
# ==========================================

@bot.message_handler(commands=['historique'])
def historique_trades(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    hist = trades_historique.get(uid, [])
    if not hist:
        return bot.send_message(uid, "📭 Aucun trade dans l'historique.")

    lignes = ["📜 *HISTORIQUE (10 derniers trades)*\n━━━━━━━━━━━━━━━━━━━━━━"]
    for t in hist[-10:][::-1]:
        emoji = "✅" if t["win"] else "❌"
        date_str = datetime.datetime.fromtimestamp(t["timestamp"]).strftime("%d/%m %H:%M")
        lignes.append(f"{emoji} {t['symbol']} {t['direction']} | "
                      f"{t['pnl']:+.2f}$ | {date_str}")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

# ==========================================
# INTERFACE TELEGRAM PRINCIPALE
# ==========================================

def obtenir_clavier(uid):
    pf = plateforme_trading.get(uid, "MT5")
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"),
               KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("🏦 BROKER: POCKET" if pf=="POCKET" else "📈 BROKER: MT5"),
               KeyboardButton("⏰ HEURES DE TRADING"))
    markup.row(KeyboardButton("📊 RAPPORT DU JOUR"),
               KeyboardButton("📜 HISTORIQUE"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid):
        return bot.send_message(uid, "🔒 Accès restreint. /vip VOTRE-CLÉ pour activer.")
    utilisateurs_actifs.add(uid)
    plateforme_trading.setdefault(uid, "MT5")
    init_daily_stats(uid)

    kz  = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    vol = "\n".join([f"  {'✅' if v else '❌'} {p}"
                     for p, v in volatility_pairs_active.items()])
    trade_info = ""
    if uid in trades_actifs:
        t = trades_actifs[uid]
        trade_info = f"\n🟠 *TRADE ACTIF:* {t['symbol']} {t['direction']} @ {t['entry_price']}"

    bot.send_message(uid,
        f"💼 *TERMINAL PRIME V43* — THE WINNER'S BRAIN\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"4 stratégies, 1 cerveau, gestion de gagnant\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 TENDANCE       → Kasper OTE\n"
        f"⚡ SCALPING       → OTE Scalping\n"
        f"📦 RANGE          → Zone Trading\n"
        f"🎯 SESSION PIVOT  → Bougie Pivot (BOS+Liquidité)\n"
        f"🤷 INDÉCIS        → Patience\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ *Gestion pro intégrée :*\n"
        f"  • Position sizing réel ({RISK_CONFIG['risk_per_trade_pct']}%/trade)\n"
        f"  • TP partiel 85% + Breakeven auto\n"
        f"  • Trailing stop après breakeven\n"
        f"  • Limite perte/jour {RISK_CONFIG['daily_loss_limit_pct']}%\n"
        f"  • Pause anti-tilt après {RISK_CONFIG['max_consecutive_losses']} pertes\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Volatility Pairs :\n{vol}\n"
        f"⏰ Killzone : {kz}{trade_info}",
        reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or
                                    m.text.startswith("📈 BROKER:"))
def toggle_pf(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if plateforme_trading.get(uid,"MT5") == "POCKET":
        plateforme_trading[uid] = "MT5"
        bot.send_message(uid, "📈 *MT5 ACTIVÉ*\n🔥 Volatility | 🥇 Gold | 🥈 Argent",
                         reply_markup=obtenir_clavier(uid), parse_mode="Markdown")
    else:
        plateforme_trading[uid] = "POCKET"
        bot.send_message(uid, "🏦 *POCKET ACTIVÉ* — Forex Binaire",
                         reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires(message):
    kz  = "🟢 EN COURS" if dans_killzone() else "🔴 INACTIVE"
    vol = "\n".join([f"  {'✅' if v else '❌'} {p}"
                     for p, v in volatility_pairs_active.items()])
    bot.send_message(message.chat.id,
        f"🕒 *KILLZONES OTE*\n\n"
        f"🌏 Asie    : 00:00 – 07:00 GMT (accumulation)\n"
        f"🇬🇧 Londres : 08:00 – 11:00 GMT (expansion/BOS)\n"
        f"🇺🇸 New York: 14:00 – 17:00 GMT (continuation)\n\n"
        f"⏰ Statut : {kz}\n"
        f"🎯 Session V43 : {session_actuelle_v43()}\n\n"
        f"🔥 Volatility :\n{vol}\n\n"
        f"/Volatility V50 OFF → désactiver V50\n"
        f"/Volatility ALL ON  → tout activer",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 RAPPORT DU JOUR")
def rapport_bouton(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    bot.send_message(uid, generer_rapport_texte(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📜 HISTORIQUE")
def historique_bouton(message):
    historique_trades(message)

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE",
                                               "📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if uid in trades_actifs:
        return bot.send_message(uid,
            "🟠 *TRADE ACTIF EN COURS*\n"
            "Attendez la clôture avant d'ouvrir un autre.",
            parse_mode="Markdown")

    peut_trader, raison = utilisateur_peut_trader(uid)
    if not peut_trader:
        return bot.send_message(uid, raison, parse_mode="Markdown")

    pf = plateforme_trading.get(uid, "MT5")
    markup = InlineKeyboardMarkup(row_width=3)

    if pf == "MT5":
        btns_vol = [InlineKeyboardButton(
                        NOMS_AFFICHAGE.get(p, p),
                        callback_data=f"set_{p}")
                    for p, actif in volatility_pairs_active.items() if actif]
        if btns_vol:
            markup.add(*btns_vol)
        markup.add(InlineKeyboardButton("🥇 GOLD",   callback_data="set_XAUUSD"),
                   InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"))
        bot.send_message(uid, "🎯 Sélectionne ta cible MT5 :",
                         reply_markup=markup, parse_mode="Markdown")
    else:
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"),
            InlineKeyboardButton("🇬🇧 GBP/USD", callback_data="set_GBPUSD"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"),
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"),
            InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"),
        )
        bot.send_message(uid, "🎯 Sélectionne ta cible Pocket Forex :",
                         reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if uid in trades_actifs:
        return bot.send_message(uid, "⚠️ Trade actif en cours.")
    actif = user_prefs.get(uid)
    if not actif:
        return bot.send_message(uid, "⚠️ Choisis d'abord une cible !")
    fake = type("C", (), {
        "data": f"set_{actif}",
        "message": message,
        "from_user": message.from_user,
        "id": 0
    })()
    save_devise(fake)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): return

    if uid in trades_actifs:
        try: bot.answer_callback_query(call.id,
                                       "🟠 Trade actif! Attendez la clôture.", show_alert=True)
        except: pass
        return

    peut_trader, raison = utilisateur_peut_trader(uid)
    if not peut_trader:
        try: bot.answer_callback_query(call.id, raison, show_alert=True)
        except: pass
        return

    actif = call.data.replace("set_", "")
    user_prefs[uid] = actif

    try: bot.delete_message(uid, call.message.message_id)
    except: pass

    cle   = f"{actif}_PRO"
    cache = signaux_cache.get(cle)

    if not cache or (time.time() - cache["time"]) > 90:
        return bot.send_message(uid,
            f"⏱️ Signal expiré sur {NOMS_AFFICHAGE.get(actif, actif)}\n"
            f"Attends le prochain scan automatique.", parse_mode="Markdown")

    px  = obtenir_prix_broker_realtime(actif) or 0
    nom = NOMS_AFFICHAGE.get(actif, actif)
    fmt = ".0f" if actif in VOLATILE_PAIRS else ".5f"

    entry_direction = "BUY" if "BUY" in cache["action"] else "SELL"
    trade_id, sizing = ouvrir_trade(uid, actif, entry_direction, px,
                                    cache["mt5_sl"], cache["mt5_tp1"], cache["mt5_tp"],
                                    cache["strategie"], cache["confiance"],
                                    label=cache.get("label","SIGNAL"))

    signal = (
        f"💼 *{cache.get('label','SIGNAL')}* — {nom}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢 BUY MARKET' if 'BUY' in cache['action'] else '🔴 SELL MARKET'}\n"
        f"📊 Contexte : {cache.get('contexte','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entrée  : {px:{fmt}}\n"
        f"🛑 SL      : {cache['mt5_sl']:{fmt}}\n"
        f"🎯 TP1 (85%): {cache['mt5_tp1']:{fmt}}\n"
        f"🏁 TP Final (15%): {cache['mt5_tp']:{fmt}}\n"
        f"⚖️ R/R     : {cache['mt5_rr']:.2f}R\n"
        f"🎖️ Confiance : {cache.get('confiance',0)}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 *Risque réel calculé* : ${sizing['montant_risque']}\n"
        f"   ({RISK_CONFIG['risk_per_trade_pct']}% du capital ${CAPITAL_ACTUEL})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *TRADE OUVERT*\n"
        f"🆔 {trade_id}\n"
        f"📬 Au TP1: 85% fermé + SL→Breakeven automatique\n"
        f"🏃 Au TP Final: 15% restant sécurisé par trailing stop"
    )
    bot.send_message(uid, signal, parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto,            daemon=True).start()
    Thread(target=monitorer_trades_actifs,         daemon=True).start()
    Thread(target=envoyer_rapports_quotidiens_auto,daemon=True).start()
    print("💼 TERMINAL PRIME V43 — The Winner's Brain ACTIF "
          "(4 stratégies + gestion pro complète)", flush=True)
    bot.infinity_polling()
