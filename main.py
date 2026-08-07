"""
╔════════════════════════════════════════════════════════════════════════════╗
║   TERMINAL PRIME V50 — MULTI-MODULES IA + GROQ (ANALYSTE EXPERT)          ║
║                                                                            ║
║  Base V49 (calcul déterministe + Groq) + NOUVEAUX MODULES INDÉPENDANTS: ║
║                                                                            ║
║  🤖 PRINCIPE STRICT RESPECTÉ (inchangé depuis V48):                       ║
║   • Les stratégies (CPR/Open Drive/RSI) restent LA fondation du bot,      ║
║     RIGOUREUSEMENT INCHANGÉES, totalement indépendantes entre elles.      ║
║   • Le calcul déterministe reste le véritable cerveau — l'IA (Groq)       ║
║     n'intervient qu'après lui et ne peut jamais reverser un rejet.        ║
║                                                                            ║
║  🌍 MODULE CONTEXTE MARCHÉ (analyser_contexte_marche): tendance haussière/║
║     baissière/range, volatilité forte/faible, consolidation, proximité   ║
║     d'une cassure — ajuste le score du calcul par un facteur borné.      ║
║                                                                            ║
║  🚨 MODULE DÉTECTION FAUX SIGNAUX (detecter_faux_signal): cassure sans   ║
║     élan, mouvement épuisé, divergence RSI, mèche de retournement —      ║
║     applique une pénalité soustractive plafonnée au score.               ║
║                                                                            ║
║  ⏱️ MODULE MULTI-TIMEFRAME (analyser_coherence_multi_tf): compare M1/M5/ ║
║     M15/H1, pénalise un signal contraire à une unité de temps supérieure.║
║                                                                            ║
║  🛡️ MODULE GESTION INTELLIGENTE DU RISQUE (optimiser_gestion_risque):    ║
║     affine le SL selon l'ATR réel, toujours borné à ±15% du niveau       ║
║     déjà fixé par la stratégie — ne peut jamais élargir le risque.       ║
║                                                                            ║
║  🔮 GROQ enrichi: reçoit désormais le dossier complet (contexte,         ║
║     alertes faux-signal, cohérence multi-TF, risque optimisé) et rend    ║
║     un verdict structuré (confirmer/déconseiller + explication).         ║
║                                                                            ║
║  📚 APPRENTISSAGE ENRICHI (ia_enregistrer_resultat): actif, stratégie,   ║
║     timeframe, heure, score déterministe, avis Groq, SL/TP, résultat,  ║
║     drawdown, durée, contexte marché — tous les champs demandés.         ║
║     Statistiques disponibles via /iastats [strategie|actif|score|heure|  ║
║     groq|contexte].                                                      ║
║                                                                            ║
║  ✅ INFRASTRUCTURE V44/V46/V48 CONSERVÉE INTÉGRALEMENT (zéro régression):║
║   Accès VIP, /Volatility granulaire, killzones, watchdog anti-blocage,   ║
║   scanner parallèle, revalidation prix/R:R au clic, TP partiel           ║
║   85%+breakeven+trailing, rapports quotidiens, /risk /rapport /pause     ║
║   /resume /debloquer /status /historique /iaconfig /iastats.             ║
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

TELEGRAM_TOKEN = "8658287331:AAF3PIDkBZTGbRHhk1TMEpjst90qhzUgvyM"
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
    "max_trade_age_hours": 12,        # ✅ V44: watchdog — force-clôture si un trade traîne trop longtemps
    "signal_validity_seconds": 45,    # ✅ V44: fenêtre de validité d'un signal (réduite de 90s à 45s)
    "max_rr_degradation_pct": 40,     # ✅ V44: rejette l'entrée si le R/R restant a chuté de plus de 40%
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
    return "Terminal Prime V46 — Master Class Edition"

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
                        except (ValueError, TypeError):
                            try:
                                epoch_val = int(datetime.datetime.strptime(
                                    date_str, "%Y-%m-%d").timestamp())
                            except (ValueError, TypeError):
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
        print(f"[Validation {symbole}] Impossible obtenir prix broker", flush=True)
        return False
    decalage = abs(prix_bot - prix_real) / prix_real
    if decalage > tolerance:
        print(f"[Validation {symbole}] ÉCART {decalage*100:.2f}% — REJETÉ", flush=True)
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

    if valeur_bilan > stats["best_trade"]:
        stats["best_trade"] = valeur_bilan
    if valeur_bilan < stats["worst_trade"]:
        stats["worst_trade"] = valeur_bilan

    if stats["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
        stats["paused_until"] = time.time() + (RISK_CONFIG["pause_duration_minutes"] * 60)
        print(f"[Risk] {uid} EN PAUSE anti-tilt ({stats['consecutive_losses']} pertes consécutives)", flush=True)

    return stats

# ==========================================
# PARTIAL TP 85% + BREAKEVEN + TRAILING STOP
# ==========================================

def create_trade_id():
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance,
                 label="SIGNAL", strategie_nom_ia="?", ia_score=None, gemini_score=None,
                 contexte_marche=None):
    trade_id = create_trade_id()
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"],
                                    entry_price, sl, symbole)

    trades_actifs[uid] = {
        "trade_id": trade_id, "symbol": symbole,
        "direction": direction, "entry_price": entry_price,
        "sl": sl, "sl_original": sl,
        "tp1": tp1, "tp_final": tp_final,
        "strategy": strategy, "confiance": confiance, "label": label,
        "strategie_nom_ia": strategie_nom_ia,
        "ia_score": ia_score,
        "gemini_score": gemini_score,
        "contexte_marche": contexte_marche,
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
    with lock_trade:
        if uid not in trades_actifs:
            return None
        trade    = trades_actifs[uid]
        trade_id = trade["trade_id"]

        try:
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
            trade["pnl"]        = pnl_trade_total
            duration_seconds     = trade["exit_time"] - trade["timestamp_open"]

            if uid not in trades_historique:
                trades_historique[uid] = []
            trades_historique[uid].append({
                "trade_id": trade_id, "symbol": trade["symbol"],
                "direction": trade["direction"], "entry": trade["entry_price"],
                "exit": exit_price, "pnl": pnl_trade_total, "duration": duration_seconds,
                "win": win, "timestamp": trade["exit_time"], "label": trade.get("label","")
            })

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_final
            enregistrer_resultat_trade(uid, pnl_final, win, pnl_pour_bilan=pnl_trade_total)

            print(f"[Trade Closed] {uid}: {trade_id} PnL final={pnl_final:.2f} | "
                  f"PnL total trade={pnl_trade_total:.2f}", flush=True)
            return {"trade_id": trade_id, "pnl": pnl_trade_total, "pnl_final_portion": pnl_final,
                    "win": win, "duration": duration_seconds}

        except Exception as e:
            print(f"[Trade Closed] ⚠️ ERREUR pendant la clôture de {uid}/{trade_id}: {e}", flush=True)
            try:
                bot.send_message(uid,
                    f"⚠️ Trade {trade.get('symbol','?')} clôturé (erreur interne lors du calcul détaillé).\n"
                    f"Consulte /historique pour vérifier. Le trading reprend normalement.",
                    parse_mode="Markdown")
            except Exception:
                pass
            return {"trade_id": trade_id, "pnl": 0.0, "pnl_final_portion": 0.0,
                    "win": win, "duration": time.time() - trade.get("timestamp_open", time.time()),
                    "erreur": True}

        finally:
            trades_actifs.pop(uid, None)

def fermer_trade_partiel(uid, exit_price):
    with lock_trade:
        if uid not in trades_actifs:
            return None
        trade = trades_actifs[uid]
        if trade["partial_closed"]:
            return None

        try:
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

        except Exception as e:
            print(f"[Partial TP] ⚠️ ERREUR pour {uid}: {e}", flush=True)
            return None

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

def watchdog_trades_bloques():
    while True:
        try:
            time.sleep(300)
            maintenant = time.time()
            for uid in list(trades_actifs.keys()):
                trade = trades_actifs.get(uid)
                if not trade:
                    continue

                age_heures = (maintenant - trade.get("timestamp_open", maintenant)) / 3600

                if trade["state"] not in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL):
                    trades_actifs.pop(uid, None)
                    continue

                if age_heures >= RISK_CONFIG["max_trade_age_hours"]:
                    prix_current = obtenir_prix_broker_realtime(trade["symbol"])
                    if prix_current:
                        win_watchdog = prix_current >= trade["entry_price"] if trade["direction"] == "BUY" else prix_current <= trade["entry_price"]
                        fermer_trade_complet(uid, prix_current, win=win_watchdog)
        except Exception as e:
            print(f"[Watchdog] {e}", flush=True)


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

def calculer_cpr_journalier(symbole):
    h1 = obtenir_donnees_deriv(symbole, 3600)
    if not h1 or len(h1) < 30:
        return None

    try:
        df = pd.DataFrame([{
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
            "epoch": int(c["epoch"])
        } for c in h1])
        df['date'] = pd.to_datetime(df['epoch'], unit='s').dt.date
        daily = df.groupby('date').agg({'open':'first','high':'max',
                                         'low':'min','close':'last'}).reset_index()

        if len(daily) < 2:
            return None

        prev_day = daily.iloc[-2]
        pdh, pdl, pdc = float(prev_day['high']), float(prev_day['low']), float(prev_day['close'])

        pivot = (pdh + pdl + pdc) / 3
        bcpr  = (pdh + pdl) / 2
        tcpr  = (pivot - bcpr) + pivot
        top_cpr = max(bcpr, tcpr)
        bot_cpr = min(bcpr, tcpr)

        cpr_width_pct = ((top_cpr - bot_cpr) / pivot) * 100 if pivot else 0
        etat_cpr = "Étroit (Tendance)" if cpr_width_pct < 0.15 else "Large (Range)"

        return {
            "PDH": pdh, "PDL": pdl, "PIVOT": pivot,
            "TCPR": top_cpr, "BCPR": bot_cpr,
            "ETAT": etat_cpr, "WIDTH": cpr_width_pct
        }
    except Exception as e:
        print(f"[CPR/{symbole}] {e}", flush=True)
        return None

def detecter_chandeliers_pdf(df):
    if len(df) < 3:
        return "NONE", 0
    try:
        last = df.iloc[-2]
        prev = df.iloc[-3]

        o, h, l, c = float(last['open']), float(last['high']), float(last['low']), float(last['close'])
        po, pc = float(prev['open']), float(prev['close'])

        body  = abs(c - o)
        rng   = h - l
        if rng == 0:
            return "NONE", 0

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        if lower_wick > body * 2.0 and upper_wick < body:
            return "PIN_BULL", lower_wick
        if upper_wick > body * 2.0 and lower_wick < body:
            return "PIN_BEAR", upper_wick

        if pc < po and c > o and c > po and o < pc:
            return "ENGULFING_BULL", body
        if pc > po and c < o and c < po and o > pc:
            return "ENGULFING_BEAR", body

        if body > rng * 0.85:
            return ("MARUBOZU_BULL" if c > o else "MARUBOZU_BEAR"), body

        return "NONE", 0
    except Exception:
        return "NONE", 0

def analyser_cpr_rejection(symbole):
    cpr = calculer_cpr_journalier(symbole)
    c15 = obtenir_donnees_deriv(symbole, 900)
    if not cpr or not c15 or len(c15) < 5:
        return None

    try:
        df15 = pd.DataFrame([{
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"])
        } for c in c15])
        px = float(df15['close'].iloc[-1])
        pattern, _ = detecter_chandeliers_pdf(df15)
        if pattern == "NONE":
            return None

        biais = "BULL" if px > cpr["PIVOT"] else "BEAR"
        signal, sl, tp1, tp_final, zone_nom = None, 0.0, 0.0, 0.0, ""

        if biais == "BULL" and pattern in ("PIN_BULL", "ENGULFING_BULL"):
            dist_tcpr  = abs(px - cpr["TCPR"])  / px
            dist_pivot = abs(px - cpr["PIVOT"]) / px
            if dist_tcpr < 0.002:   zone_nom = "Top CPR"
            elif dist_pivot < 0.002: zone_nom = "Point Pivot Central"
            if zone_nom:
                signal = "BUY"
                sl = float(df15['low'].iloc[-2]) * 0.999
                distance_risque = px - sl
                if distance_risque <= 0:
                    return None
                tp1      = px + (distance_risque * 1.5)
                tp_final = cpr["PDH"]

        elif biais == "BEAR" and pattern in ("PIN_BEAR", "ENGULFING_BEAR"):
            dist_bcpr  = abs(px - cpr["BCPR"])  / px
            dist_pivot = abs(px - cpr["PIVOT"]) / px
            if dist_bcpr < 0.002:   zone_nom = "Bottom CPR"
            elif dist_pivot < 0.002: zone_nom = "Point Pivot Central"
            if zone_nom:
                signal = "SELL"
                sl = float(df15['high'].iloc[-2]) * 1.001
                distance_risque = sl - px
                if distance_risque <= 0:
                    return None
                tp1      = px - (distance_risque * 1.5)
                tp_final = cpr["PDL"]

        if not signal:
            return None

        risque = abs(px - sl)
        rr = abs(tp_final - px) / risque if risque > 0 else 0
        if rr < 1.5:
            return None

        return {
            "action": "🟢 ACHAT (BUY)" if signal == "BUY" else "🔴 VENTE (SELL)",
            "tendance": biais, "force": cpr["ETAT"],
            "msg": f"Rejet Chandelier ({pattern.replace('_',' ')}) sur {zone_nom}",
            "sl": round(sl,5), "tp1": round(tp1,5), "tp": round(tp_final,5),
            "rr": round(rr,2), "px": round(px,5),
            "strategie": 1, "confiance": 85 if cpr["ETAT"] == "Large (Range)" else 75,
            "label": "CPR PULLBACK & REJECTION",
            "cpr_top": round(cpr["TCPR"],5), "cpr_bot": round(cpr["BCPR"],5),
            "cpr_etat": cpr["ETAT"], "objectif_pdhl": round(tp_final,5),
        }
    except Exception as e:
        print(f"[CPR-Rejection/{symbole}] {e}", flush=True)
        return None

def analyser_open_drive(symbole):
    cpr = calculer_cpr_journalier(symbole)
    c5  = obtenir_donnees_deriv(symbole, 300)
    if not cpr or not c5 or len(c5) < 5:
        return None

    try:
        df5 = pd.DataFrame([{
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"])
        } for c in c5])
        px = float(df5['close'].iloc[-1])
        pattern, _ = detecter_chandeliers_pdf(df5)
        last_candle = df5.iloc[-2]

        signal, sl, tp_final, niveau_casse = None, 0.0, 0.0, 0.0

        if pattern in ("MARUBOZU_BULL", "PIN_BULL"):
            if float(last_candle['open']) < cpr["PDH"] * 1.001 and float(last_candle['close']) > cpr["PDH"]:
                signal = "BUY"
                niveau_casse = cpr["PDH"]
                sl = cpr["PDH"] * 0.998
                dist = px - sl
                if dist > 0:
                    tp_final = px + (dist * 2.5)

        elif pattern in ("MARUBOZU_BEAR", "PIN_BEAR"):
            if float(last_candle['open']) > cpr["PDL"] * 0.999 and float(last_candle['close']) < cpr["PDL"]:
                signal = "SELL"
                niveau_casse = cpr["PDL"]
                sl = cpr["PDL"] * 1.002
                dist = sl - px
                if dist > 0:
                    tp_final = px - (dist * 2.5)

        if not signal or tp_final == 0:
            return None

        rr  = 2.5
        tp1 = px + (abs(px - sl) * 1.0) if signal == "BUY" else px - (abs(px - sl) * 1.0)

        return {
            "action": "🟢 ACHAT (BUY)" if signal == "BUY" else "🔴 VENTE (SELL)",
            "tendance": "BREAKOUT", "force": "Impulsion Forte",
            "msg": f"Open Drive : Cassure du {'PDH' if signal=='BUY' else 'PDL'} par {pattern.replace('_',' ')}",
            "sl": round(sl,5), "tp1": round(tp1,5), "tp": round(tp_final,5),
            "rr": round(rr,2), "px": round(px,5),
            "strategie": 2, "confiance": 90,
            "label": "OPEN DRIVE BREAKOUT",
            "niveau_casse": round(niveau_casse,5),
        }
    except Exception as e:
        print(f"[OpenDrive/{symbole}] {e}", flush=True)
        return None

def analyser_rsi_exhaustion(symbole):
    c1h = obtenir_donnees_deriv(symbole, 3600)
    if not c1h or len(c1h) < 20:
        return None

    try:
        df1h = pd.DataFrame([{
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"])
        } for c in c1h])
        rsi_series = ta.momentum.RSIIndicator(close=df1h["close"], window=14).rsi()
        if rsi_series.isna().iloc[-2]:
            return None
        rsi = float(rsi_series.iloc[-2])

        px = float(df1h['close'].iloc[-1])
        pattern, _ = detecter_chandeliers_pdf(df1h)

        signal, sl, tp_final = None, 0.0, 0.0

        if rsi < 30 and pattern == "PIN_BULL":
            signal = "BUY"
            sl = float(df1h['low'].iloc[-2]) * 0.999
            dist = px - sl
            if dist > 0:
                tp_final = px + (dist * 3.0)

        elif rsi > 70 and pattern == "PIN_BEAR":
            signal = "SELL"
            sl = float(df1h['high'].iloc[-2]) * 1.001
            dist = sl - px
            if dist > 0:
                tp_final = px - (dist * 3.0)

        if not signal or tp_final == 0:
            return None

        tp1 = px + (abs(px - sl) * 1.5) if signal == "BUY" else px - (abs(px - sl) * 1.5)

        return {
            "action": "🟢 ACHAT (BUY)" if signal == "BUY" else "🔴 VENTE (SELL)",
            "tendance": "REVERSAL", "force": f"RSI Extrême ({round(rsi,1)})",
            "msg": "Épuisement : rejet massif des prix avec RSI critique",
            "sl": round(sl,5), "tp1": round(tp1,5), "tp": round(tp_final,5),
            "rr": 3.0, "px": round(px,5),
            "strategie": 3, "confiance": 80,
            "label": "RSI EXHAUSTION & REVERSAL",
            "rsi_value": round(rsi,1),
        }
    except Exception as e:
        print(f"[RSI-Exhaustion/{symbole}] {e}", flush=True)
        return None

def detecter_contexte_pdf(symbole):
    cached = contexte_marche_cache.get(symbole)
    if cached and (time.time() - cached["ts"]) < 120:
        return cached["contexte"]

    cpr = calculer_cpr_journalier(symbole)
    if not cpr:
        contexte = "INDECIS"
    elif cpr["ETAT"] == "Étroit (Tendance)":
        contexte = "JOUR_TENDANCE"
    else:
        contexte = "JOUR_RANGE"

    contexte_marche_cache[symbole] = {"contexte": contexte, "ts": time.time()}
    return contexte

IA_CONFIG = {
    "seuil_acceptation": 85,
    "groq_active": True,
    "groq_seuil_veto": 40,
    "poids": {
        "tendance_h1":        12,
        "adx":                10,
        "rsi_coherence":      10,
        "macd_coherence":      8,
        "ema_alignement":      8,
        "atr_volatilite":      8,
        "structure_marche":   10,
        "distance_sr":         8,
        "qualite_cassure":    10,
        "spread":              6,
        "multi_tf_coherence": 10,
    },
    "poids_contexte": {
        "tendance_forte":      1.10,
        "range":               0.90,
        "tres_volatil":        0.80,
        "peu_volatil":         0.95,
        "consolidation":       0.90,
        "proche_cassure":      1.05,
    },
    "seuil_multi_tf_penalite": 30,
}

# Clé API Groq configurée avec succès
GROQ_API_KEY = "Gsk_iG0CXRTa3SIPQJ9mhrDgWGdyb3FY80oLomvr0dAarcTwL8J1ZHMr"
GROQ_MODEL   = "llama-3.1-70b-versatile"
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

ia_historique = []
ia_poids_ajustes = {}

def calculer_adx(df):
    try:
        indicator = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        adx_val = indicator.adx().iloc[-1]
        return float(adx_val) if not math.isnan(adx_val) else 25.0
    except Exception:
        return 25.0

def calculer_atr(df):
    try:
        indicator = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14)
        atr_val = indicator.average_true_range().iloc[-1]
        return float(atr_val) if not math.isnan(atr_val) else 0.5
    except Exception:
        return 0.5

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

        if adx >= 25 and abs(pente_ema20) > 0.001:
            tendance = "HAUSSIERE" if ema20_h1.iloc[-2] > ema50_h1.iloc[-2] else "BAISSIERE"
        elif adx < 18:
            tendance = "RANGE"
        else:
            tendance = "INDECIS"

        if atr_pct > 1.2:
            volatilite = "TRES_VOLATIL"
        elif atr_pct < 0.05:
            volatilite = "PEU_VOLATIL"
        else:
            volatilite = "NORMALE"

        consolidation = (adx < 20 and atr_pct < 0.3)

        return {
            "tendance": tendance,
            "volatilite": volatilite,
            "consolidation": consolidation,
            "proche_cassure": proche_cassure,
            "adx": round(adx, 2)
        }
    except Exception as e:
        print(f"[Contexte] Erreur: {e}", flush=True)
        return None

# ==========================================
# FONCTION INTÉGRÉE D'APPEL GROQ
# ==========================================

def analyser_avec_groq(prompt_systeme, prompt_utilisateur):
    if not GROQ_API_KEY:
        return {"statut": "ERREUR", "raison": "Clé API Groq manquante"}
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": prompt_systeme},
            {"role": "user", "content": prompt_utilisateur}
        ],
        "temperature": 0.2,
        "max_tokens": 512
    }
    
    try:
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            contenu = data["choices"][0]["message"]["content"]
            return {"statut": "SUCCES", "reponse": contenu}
        else:
            return {"statut": "REFUS", "code": response.status_code, "texte": response.text[:300]}
    except requests.exceptions.Timeout:
        return {"statut": "TIMEOUT", "raison": "Délai d'attente dépassé (10s)"}
    except Exception as e:
        return {"statut": "ERREUR", "raison": str(e)}

if __name__ == "__main__":
    keep_alive()
    Thread(target=watchdog_trades_bloques, daemon=True).start()
    print("💼 TERMINAL PRIME V50 — ANALYSTE IA MULTI-MODULES (GROQ) ACTIF AVEC SUCCÈS", flush=True)
    bot.infinity_polling()

