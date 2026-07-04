"""
╔════════════════════════════════════════════════════════════════════════════╗
║              TERMINAL PRIME V46 — MASTER CLASS + WINNER'S BRAIN           ║
║                                                                            ║
║  Fusion V44 (infrastructure éprouvée) + V45 (stratégies PDF), bugs fixés: ║
║                                                                            ║
║  📘 STRATÉGIES (nouvelles, remplacent Kasper/Scalping/Zone/Pivot):        ║
║   1. CPR Pullback & Rejection    (Vikram Prabhu — Price Action)           ║
║   2. Open Drive Breakout PDH/PDL (Vikram Prabhu — Cassure décisive)       ║
║   3. RSI Extremes & Exhaustion   (Dr Investors + gestion Smart Raja)      ║
║   🧠 Cerveau contextuel: priorise Breakout en jour CPR étroit (tendance), ║
║      CPR Rejection en jour CPR large (range), RSI en dernier recours.    ║
║                                                                            ║
║  🔧 BUGS CORRIGÉS lors de la fusion (présents dans le document fourni):   ║
║   • epoch FMP figé à l'heure actuelle pour toutes les bougies → cassait  ║
║     le regroupement par date du CPR (toujours 1 seul jour détecté)       ║
║   • granularité 900s (M15) silencieusement mappée sur 4h                 ║
║                                                                            ║
║  ✅ INFRASTRUCTURE V44 CONSERVÉE INTÉGRALEMENT:                          ║
║   Accès VIP (/keygen /vip /abonnes /cles), /Volatility granulaire,       ║
║   killzones + filtre week-end, watchdog anti-blocage, scanner parallèle, ║
║   revalidation prix/R:R au clic, TP partiel 85%+breakeven+trailing,      ║
║   rapports quotidiens automatiques, /risk /rapport /pause /resume        ║
║   /debloquer /status /historique, filet try/finally testé en conditions ║
║   réelles.                                                                ║
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

TELEGRAM_TOKEN = "8658287331:AAGxATaSmQmq3O-GL7fyDXoLCBbTf3_zwgE"
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
    return "Terminal Prime V46 — Master Class Edition"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# UTILITAIRES PRIX (base V38)
# ==========================================

def prefixer_symbole(s):
    mapping = {"XAUUSD":"frxXAUUSD","XAGUSD":"frxXAGUSD"}
    if s in mapping:
        return mapping[s]
    if s in VOLATILE_PAIRS:
        return f"R_{s.replace('V','')}"
    return f"frx{s}"

# ✅ V44 NEW: cache court (TTL) des bougies pour éviter de re-télécharger les
# mêmes données plusieurs fois pendant un même cycle de scan (detecter_contexte
# puis la stratégie choisie demandaient chacune les mêmes bougies H1/H4/M5,
# doublant inutilement la latence réseau — cause principale des signaux en retard).
_candles_cache = {}
_candles_cache_lock = Lock()
CANDLES_CACHE_TTL = 20  # secondes — assez court pour rester réactif, assez long pour dédupliquer

def _obtenir_donnees_deriv_reseau(symbole_brut, granularite=300):
    """
    Fonction réseau brute — timeouts réduits pour échouer plus vite vers le fallback.
    ✅ V46 FIX #1: chaque bougie FMP recevait auparavant epoch=int(time.time()),
       c'est-à-dire l'heure ACTUELLE pour TOUTES les bougies (peu importe leur
       vraie date). Résultat: tout regroupement par date (ex: calcul du CPR
       journalier) fusionnait 250 bougies en un seul jour et échouait
       silencieusement. On parse maintenant le vrai champ "date" renvoyé par FMP.
    ✅ V46 FIX #2: la granularité 900s (15 min) était silencieusement mappée
       sur "4hour"/14400s aussi bien côté FMP que côté fallback Deriv.
    """
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
                        # Fallback: approximation par index (préserve au moins un
                        # étalement réaliste des dates plutôt qu'un timestamp unique)
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
    """
    ✅ V44: version cachée (TTL courte). Même signature, même comportement
    logique, mais évite les appels réseau redondants dans un même cycle.
    """
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
    """Récupère des données 4H en agrégeant 4x les bougies H1 si l'API ne supporte pas 14400 directement"""
    data = obtenir_donnees_deriv(symbole, 14400)
    if data and len(data) > 20:
        return data
    # Fallback: agréger H1 par groupes de 4
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
# ✅ V43 NEW: GESTION DU RISQUE PROFESSIONNELLE
# ==========================================

def get_today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

def init_daily_stats(uid):
    """Initialise ou réinitialise les stats du jour si on a changé de date"""
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
    """Vérifie si l'utilisateur est en pause anti-tilt"""
    stats = init_daily_stats(uid)
    if stats["paused_until"] and time.time() < stats["paused_until"]:
        return True, stats["paused_until"]
    return False, None

def daily_loss_limit_atteinte(uid):
    """Vérifie si la limite de perte journalière est atteinte"""
    stats = init_daily_stats(uid)
    limite = -(CAPITAL_ACTUEL * RISK_CONFIG["daily_loss_limit_pct"] / 100.0)
    return stats["pnl"] <= limite

def max_trades_jour_atteint(uid):
    stats = init_daily_stats(uid)
    return stats["trades"] >= RISK_CONFIG["max_trades_per_day"]

def utilisateur_peut_trader(uid):
    """
    ✅ Circuit breaker complet:
    - Limite de perte journalière
    - Pause anti-tilt (pertes consécutives)
    - Nombre max de trades par jour
    Retourne (bool_peut_trader, raison_si_non)
    """
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
    """
    ✅ V43 NEW: Calcul RÉEL de la taille de position
    Au lieu d'un montant fixe arbitraire, calcule selon:
      - Le capital actuel
      - Le % de risque accepté
      - La distance réelle entre entrée et stop loss
    Retourne le montant en argent risqué + un "lot factor" relatif pour affichage
    """
    montant_risque = capital * (risk_pct / 100.0)
    distance_sl = abs(prix_entree - prix_sl)

    if distance_sl <= 0:
        return {"montant_risque": montant_risque, "lot_factor": 0, "distance_sl": 0}

    # Lot factor = combien d'unités on peut se permettre pour respecter le risque
    # (simplifié — sert de guide proportionnel, le lot exact dépend du broker/contract size)
    lot_factor = montant_risque / distance_sl

    return {
        "montant_risque": round(montant_risque, 2),
        "lot_factor": round(lot_factor, 4),
        "distance_sl": round(distance_sl, 5),
        "distance_sl_pct": round((distance_sl / prix_entree) * 100, 3) if prix_entree else 0
    }

def enregistrer_resultat_trade(uid, pnl, win, pnl_pour_bilan=None):
    """
    Met à jour les stats journalières + déclenche la pause anti-tilt si besoin.
    pnl: portion à ajouter à stats["pnl"] (évite double-comptage si un TP
         partiel avait déjà ajouté sa part avant cet appel).
    pnl_pour_bilan: P&L TOTAL du trade (portion partielle + finale), utilisé
         uniquement pour best_trade/worst_trade. Si absent, on utilise pnl.

    ✅ V44 FIX: cette fonction plantait auparavant (TypeError) car appelée
    avec pnl_pour_bilan= sans que le paramètre existe. Cela empêchait
    `del trades_actifs[uid]` de s'exécuter dans fermer_trade_complet(),
    bloquant l'utilisateur DÉFINITIVEMENT après son premier trade fermé
    (plus aucun signal, plus aucune notification gagné/perdu).
    """
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

    # Déclenchement pause anti-tilt
    if stats["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
        stats["paused_until"] = time.time() + (RISK_CONFIG["pause_duration_minutes"] * 60)
        print(f"[Risk] {uid} EN PAUSE anti-tilt ({stats['consecutive_losses']} pertes consécutives)", flush=True)

    return stats

# ==========================================
# ✅ V43 NEW: PARTIAL TP 85% + BREAKEVEN + TRAILING STOP
# (Technique exacte observée dans la Stratégie 4: "Prise de profits 85%
#  puis Break Even")
# ==========================================

def create_trade_id():
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance, label="SIGNAL"):
    """
    Ouvre un trade avec gestion complète:
      - Position sizing réel
      - TP1 (objectif intermédiaire, 85% de la position)
      - TP final (15% restant après passage en breakeven)
    """
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
    """
    Ferme totalement un trade et enregistre dans l'historique + stats journalières.

    ✅ V44 FIX (protection définitive): tout le corps est protégé par
    try/finally. Le retrait de trades_actifs[uid] est GARANTI même si une
    erreur imprévue survient pendant le calcul — plus jamais un utilisateur
    ne pourra rester bloqué "TRADE ACTIF EN COURS" pour toujours à cause
    d'une exception. En cas d'erreur, on notifie quand même l'utilisateur
    au lieu de rester silencieux.
    """
    with lock_trade:
        if uid not in trades_actifs:
            return None
        trade    = trades_actifs[uid]
        trade_id = trade["trade_id"]

        try:
            risque_initial = trade["sizing"]["montant_risque"]

            # Si une fermeture partielle (85%) a déjà eu lieu, ce closing ne porte
            # que sur les 15% restants — on proportionne le risque utilisé pour
            # le calcul du P&L de cette portion finale.
            portion_restante = (1 - RISK_CONFIG["partial_tp_ratio"]) if trade.get("partial_closed") else 1.0
            risque_portion    = risque_initial * portion_restante

            if win:
                gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
                pnl_final = risque_portion * gain_ratio
            else:
                pnl_final = -risque_portion

            # P&L TOTAL réel du trade = portion déjà sécurisée (85%) + portion finale
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
            # ✅ Filet de sécurité: même en cas de bug imprévu, on notifie
            # l'utilisateur au lieu de le laisser bloqué en silence.
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
            # GARANTIE ABSOLUE: l'utilisateur ne reste jamais bloqué, quoi qu'il arrive.
            trades_actifs.pop(uid, None)

def fermer_trade_partiel(uid, exit_price):
    """
    Ferme 85% de la position au TP1, déplace SL à breakeven pour les 15% restants
    (Technique exacte de la Stratégie 4).
    ✅ V44: protégé par lock + try/except (cohérence avec fermer_trade_complet).
    """
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

            # Déplacer le SL au point d'entrée (+ petit buffer pour couvrir les frais)
            buffer = trade["entry_price"] * RISK_CONFIG["breakeven_buffer_pct"]
            if trade["direction"] == "BUY":
                trade["sl"] = trade["entry_price"] + buffer
            else:
                trade["sl"] = trade["entry_price"] - buffer

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_partiel

            # Le profit partiel alimente aussi le P&L du jour (circuit breaker + rapport)
            stats = init_daily_stats(uid)
            stats["pnl"] += pnl_partiel

            print(f"[Partial TP] {uid}: {trade['trade_id']} 85% fermé (+{pnl_partiel:.2f}), "
                  f"SL → Breakeven {trade['sl']:.5f}", flush=True)

            return {"pnl_partiel": round(pnl_partiel, 2), "nouveau_sl": trade["sl"]}

        except Exception as e:
            print(f"[Partial TP] ⚠️ ERREUR pour {uid}: {e}", flush=True)
            return None

def appliquer_trailing_stop(uid, prix_current):
    """
    ✅ V43 NEW: Trailing stop actif uniquement APRÈS le passage en breakeven (15% restants)
    Sécurise les gains progressivement sur la portion qui continue de courir
    """
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
# ✅ V44 NEW: WATCHDOG ANTI-BLOCAGE
# Filet de sécurité ultime: si jamais un trade reste bloqué anormalement
# longtemps (bug futur, état incohérent, etc.), on le force-ferme et on
# prévient l'utilisateur — plus JAMAIS de blocage silencieux permanent.
# ==========================================

def watchdog_trades_bloques():
    while True:
        try:
            time.sleep(300)  # vérifie toutes les 5 minutes
            maintenant = time.time()
            for uid in list(trades_actifs.keys()):
                trade = trades_actifs.get(uid)
                if not trade:
                    continue

                age_heures = (maintenant - trade.get("timestamp_open", maintenant)) / 3600

                # Cas 1: trade dans un état incohérent (ni OPEN ni PARTIAL) → nettoyage immédiat
                if trade["state"] not in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL):
                    print(f"[Watchdog] {uid} état incohérent ({trade['state']}) → nettoyage forcé", flush=True)
                    trades_actifs.pop(uid, None)
                    try:
                        bot.send_message(uid,
                            "🔧 Un trade bloqué a été nettoyé automatiquement. "
                            "Tu peux recevoir de nouveaux signaux normalement.",
                            parse_mode="Markdown")
                    except Exception:
                        pass
                    continue

                # Cas 2: trade ouvert depuis trop longtemps → force-clôture au marché
                if age_heures >= RISK_CONFIG["max_trade_age_hours"]:
                    prix_current = obtenir_prix_broker_realtime(trade["symbol"])
                    if prix_current:
                        # Déterminer gagnant/perdant selon la position actuelle du prix
                        # par rapport au prix d'entrée (pas de raccourci arbitraire)
                        if trade["direction"] == "BUY":
                            win_watchdog = prix_current >= trade["entry_price"]
                        else:
                            win_watchdog = prix_current <= trade["entry_price"]

                        print(f"[Watchdog] {uid} trade {trade['trade_id']} ouvert depuis "
                              f"{age_heures:.1f}h → clôture forcée", flush=True)
                        fermer_trade_complet(uid, prix_current, win=win_watchdog)
                        try:
                            bot.send_message(uid,
                                f"⏱️ Trade {trade['symbol']} clôturé automatiquement après "
                                f"{RISK_CONFIG['max_trade_age_hours']}h (sécurité anti-blocage).\n"
                                f"Consulte /historique pour le détail.",
                                parse_mode="Markdown")
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Watchdog] {e}", flush=True)


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
    """Sessions spécifiques à la Stratégie 4 (heures observées dans la vidéo, en UTC approx)"""
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

# ==========================================
# 📘 V46 NEW: STRATÉGIES "MASTER CLASS" (basées sur PDF de trading)
# Remplace l'ancienne couche (Kasper OTE / OTE Scalping / Zone Trading /
# Bougie Pivot Session) par 3 stratégies documentées:
#   1. CPR Pullback & Rejection    (Vikram Prabhu — Price Action)
#   2. Open Drive Breakout PDH/PDL (Vikram Prabhu — Cassure décisive)
#   3. RSI Extremes & Exhaustion   (Dr Investors + gestion Smart Raja)
# ==========================================

def calculer_cpr_journalier(symbole):
    """
    Extrait le CPR (Central Pivot Range) de la veille — Pivot, BCPR, TCPR —
    ainsi que PDH/PDL (Plus Haut/Bas de la veille), à partir des bougies H1
    agrégées par date réelle (bug d'epoch corrigé en V46 — voir
    _obtenir_donnees_deriv_reseau).
    """
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

        prev_day = daily.iloc[-2]  # dernière journée COMPLÈTE (pas celle en cours)
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
    """
    Détecte Pin Bar, Engulfing et Marubozu selon les règles strictes du
    PDF Candlestick Patterns. Analyse la bougie fraîchement CLÔTURÉE (iloc[-2]),
    jamais la bougie en formation.
    """
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

        # Pin Bar — mèche > 2x le corps
        if lower_wick > body * 2.0 and upper_wick < body:
            return "PIN_BULL", lower_wick
        if upper_wick > body * 2.0 and lower_wick < body:
            return "PIN_BEAR", upper_wick

        # Engulfing — avalement complet du corps précédent
        if pc < po and c > o and c > po and o < pc:
            return "ENGULFING_BULL", body
        if pc > po and c < o and c < po and o > pc:
            return "ENGULFING_BEAR", body

        # Marubozu — corps > 85% du range, quasi aucune mèche
        if body > rng * 0.85:
            return ("MARUBOZU_BULL" if c > o else "MARUBOZU_BEAR"), body

        return "NONE", 0
    except Exception:
        return "NONE", 0

# ------------------------------------------
# STRATÉGIE 1 : CPR PULLBACK & REJECTION
# ------------------------------------------

def analyser_cpr_rejection(symbole):
    """
    Le prix revient tester le CPR (Pivot/BCPR/TCPR) et forme une bougie de
    rejet (Pin Bar ou Engulfing) → entrée dans le sens du biais journalier
    (prix vs Pivot), objectif = PDH/PDL.
    ✅ V46: utilise désormais du VRAI M15 (900s), corrigé du bug de mapping
    qui renvoyait auparavant du H4 mal étiqueté.
    """
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

# ------------------------------------------
# STRATÉGIE 2 : OPEN DRIVE BREAKOUT (PDH/PDL)
# ------------------------------------------

def analyser_open_drive(symbole):
    """
    Une bougie forte (Marubozu ou Pin Bar) casse décisivement le PDH ou le
    PDL sans hésitation — entrée dans le sens de la cassure.
    """
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

# ------------------------------------------
# STRATÉGIE 3 : RSI EXTREMES & EXHAUSTION
# ------------------------------------------

def analyser_rsi_exhaustion(symbole):
    """
    RSI en zone extrême (< 30 ou > 70) confirmé par une mèche d'épuisement
    (Pin Bar) → retournement probable.
    """
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

# ------------------------------------------
# ✅ V46 NEW: DÉTECTION DE CONTEXTE (léger, basé sur CPR + RSI)
# Restaure l'esprit du "cerveau" V44 (une stratégie adaptée au contexte du
# jour plutôt qu'une cascade aveugle) en s'appuyant sur les nouveaux
# indicateurs PDF au lieu de l'ancien système EMA/Zones.
# ------------------------------------------

def detecter_contexte_pdf(symbole):
    """
    Retourne un indice de contexte pour prioriser l'ordre des stratégies:
      "JOUR_TENDANCE" -> CPR étroit: privilégier Open Drive Breakout
      "JOUR_RANGE"    -> CPR large: privilégier CPR Rejection
      "INDECIS"       -> pas de CPR disponible, cascade par défaut
    Mis en cache 2 minutes (même logique que l'ancien V44).
    """
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

def cerveau_pro_trader(symbole):
    """
    ✅ V46: Cerveau Pro Trader — sélectionne l'ordre de priorité des 3
    stratégies PDF selon le contexte CPR du jour, comme un trader qui adapte
    son approche selon que la journée s'annonce directionnelle ou en range.
    RSI Exhaustion reste toujours vérifié en dernier recours (retournement
    possible quel que soit le contexte).
    """
    contexte = detecter_contexte_pdf(symbole)

    if contexte == "JOUR_TENDANCE":
        ordre = [
            (analyser_open_drive,     "🚀 BREAKOUT PDH/PDL (jour tendance)"),
            (analyser_cpr_rejection,  "🧱 REBOND CPR (Vikram)"),
            (analyser_rsi_exhaustion, "⚠️ EXTRÊME RSI (Dr Investors)"),
        ]
    elif contexte == "JOUR_RANGE":
        ordre = [
            (analyser_cpr_rejection,  "🧱 REBOND CPR (jour range)"),
            (analyser_open_drive,     "🚀 BREAKOUT PDH/PDL"),
            (analyser_rsi_exhaustion, "⚠️ EXTRÊME RSI (Dr Investors)"),
        ]
    else:  # INDECIS — pas de CPR dispo, cascade par défaut
        ordre = [
            (analyser_open_drive,     "🚀 BREAKOUT PDH/PDL"),
            (analyser_cpr_rejection,  "🧱 REBOND CPR (Vikram)"),
            (analyser_rsi_exhaustion, "⚠️ EXTRÊME RSI (Dr Investors)"),
        ]

    for fn, emoji_ctx in ordre:
        res = fn(symbole)
        if res:
            res["contexte_detecte"] = emoji_ctx
            return res, contexte

    return None, contexte


# ==========================================
# ✅ /Volatility GRANULAIRE
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
# ✅ V43 NEW: /risk — Configurer le risque par trade
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
# ✅ V43 NEW: /rapport — Rapport quotidien
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
    """Envoie automatiquement le rapport à 22h UTC chaque jour à tous les users actifs"""
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
# ✅ V43 NEW: /pause /resume — Circuit breaker manuel
# ==========================================

@bot.message_handler(commands=['pause'])
def pause_manuelle(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    stats = init_daily_stats(uid)
    stats["paused_until"] = time.time() + (12 * 3600)  # pause 12h
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
# ✅ V44 NEW: /debloquer — Déblocage manuel immédiat (admin ou soi-même)
# Complète le watchdog automatique (5 min) pour un déblocage instantané.
# ==========================================

@bot.message_handler(commands=['debloquer'])
def debloquer_manuel(message):
    uid = message.chat.id
    if not est_autorise(uid): return

    parts = message.text.strip().split()
    cible = uid
    if len(parts) > 1 and message.chat.id == ADMIN_ID:
        try:
            cible = int(parts[1])
        except ValueError:
            return bot.send_message(uid, "❌ ID invalide.")

    etait_bloque = cible in trades_actifs
    trades_actifs.pop(cible, None)

    stats = init_daily_stats(cible)
    stats["paused_until"] = None

    if etait_bloque:
        bot.send_message(uid, f"🔓 Utilisateur {cible} débloqué. Trade actif nettoyé.",
                         parse_mode="Markdown")
        if cible != uid:
            try:
                bot.send_message(cible, "🔓 Ton compte a été débloqué par l'admin. "
                                        "Tu peux à nouveau recevoir des signaux.",
                                 parse_mode="Markdown")
            except: pass
    else:
        bot.send_message(uid, f"✅ Aucun blocage détecté pour {cible} — tout est déjà normal.",
                         parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_technique(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    bloque = "🟠 OUI" if uid in trades_actifs else "🟢 NON"
    en_pause, jusqua = utilisateur_en_pause(uid)
    txt = (
        f"🔧 *STATUS TECHNIQUE*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Trade actif en cours : {bloque}\n"
        f"Pause anti-tilt : {'🟠 OUI' if en_pause else '🟢 NON'}\n"
        f"Cycle scanner : ~15s (parallélisé)\n"
        f"Validité signal : {RISK_CONFIG['signal_validity_seconds']}s\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Si tu ne reçois plus de signaux malgré tout, "
        f"utilise /debloquer pour te débloquer immédiatement."
    )
    bot.send_message(uid, txt, parse_mode="Markdown")

# ==========================================
# SCANNER PRINCIPAL V43
# ==========================================

def _analyser_une_paire(paire):
    """
    ✅ V44 NEW: Analyse UNE paire (extrait de scanner_marche_auto pour
    permettre l'exécution en parallèle sur toutes les paires).
    Retourne (paire, res, px) ou None si rien à signaler.
    """
    try:
        statut, _ = est_symbole_autorise(paire)
        if statut != "AUTORISE":
            return None

        res, contexte = cerveau_pro_trader(paire)
        if not res:
            return None

        px = obtenir_prix_broker_realtime(paire) or res["px"]
        if not valider_prix_avant_signal(paire, px):
            return None

        return (paire, res, px)
    except Exception as e:
        print(f"[Analyse/{paire}] {e}", flush=True)
        return None

def scanner_marche_auto():
    """
    ✅ V44 FIX: scanner parallélisé (ThreadPoolExecutor) au lieu de séquentiel.
    Auparavant, 21 paires étaient analysées une par une, chacune avec plusieurs
    appels réseau — un cycle complet pouvait prendre largement plus que les
    30s de pause prévue, rendant les signaux obsolètes à l'arrivée (TP1 déjà
    atteint sur les paires rapides comme les indices Volatility).
    Maintenant, toutes les paires sont analysées EN MÊME TEMPS.
    """
    # ✅ V44.1: Scanner restreint à Gold + Argent + Volatility uniquement
    # (ELITE_PAIRS_MT5 = VOLATILE_PAIRS + COMMODITY_PAIRS). Les 14 paires Forex
    # classiques ne sont plus scannées automatiquement.
    toutes_paires = ELITE_PAIRS_MT5

    while True:
        try:
            time.sleep(15)  # cycle plus rapide, rendu possible par la parallélisation
            libres = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not libres:
                continue

            resultats = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(_analyser_une_paire, p): p for p in toutes_paires}
                for future in as_completed(futures, timeout=25):
                    try:
                        r = future.result()
                        if r:
                            resultats.append(r)
                    except Exception as e:
                        print(f"[Scanner Parallel] {e}", flush=True)

            # ── Diffusion des signaux trouvés (rapide, pas de réseau lourd ici) ──
            for paire, res, px in resultats:
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

                    # ✅ V44.1: plus besoin de filtre broker — toutes les paires
                    # scannées (Gold/Argent/Volatility) sont désormais MT5 par défaut

                    markup = InlineKeyboardMarkup().add(
                        InlineKeyboardButton(f"⚡ Copier {nom}", callback_data=f"set_{paire}")
                    )

                    # ✅ V46: détails spécifiques aux 3 stratégies PDF
                    if res["strategie"] == 1:      # CPR Rejection
                        ligne_extra = (f"🧱 CPR : {res.get('cpr_bot',0):.5f} - {res.get('cpr_top',0):.5f} "
                                       f"({res.get('cpr_etat','')})\n"
                                       f"🎯 Objectif : {'PDH' if res['tendance']=='BULL' else 'PDL'} "
                                       f"{res.get('objectif_pdhl',0):.5f}\n")
                    elif res["strategie"] == 2:    # Open Drive Breakout
                        ligne_extra = (f"🚀 Cassure {'PDH' if 'BUY' in res['action'] else 'PDL'} : "
                                       f"{res.get('niveau_casse',0):.5f}\n")
                    elif res["strategie"] == 3:    # RSI Exhaustion
                        ligne_extra = f"📉 RSI (H1) : {res.get('rsi_value','?')}\n"
                    else:
                        ligne_extra = ""

                    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"],
                                                    px, res["sl"], paire)

                    txt = (
                        f"💼 *TERMINAL PRIME V46*\n"
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
                        f"({RISK_CONFIG['risk_per_trade_pct']}% du capital)\n"
                        f"⏳ Signal valide {RISK_CONFIG['signal_validity_seconds']}s"
                    )
                    try:
                        bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                    except:
                        pass

        except Exception as e:
            print(f"[Scanner V44] {e}", flush=True)

# ==========================================
# ✅ V43 NEW: MONITORING AVANCÉ DES TRADES
# Gère: TP1 partiel (85%) → Breakeven → Trailing Stop → TP final / SL
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

                # ── PHASE 1: Trade encore plein (avant TP1) ─────────────
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

                # ── PHASE 2: 85% fermé, 15% en breakeven + trailing ─────
                elif trade["state"] == TradeState.TRADE_PARTIAL:

                    # Appliquer le trailing stop (sécurise les gains progressivement)
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
                        # Sortie au breakeven ou en trailing stop — jamais une vraie perte
                        # car le SL ne peut être déplacé que dans le sens favorable après TP1
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

    # Alerte si circuit breaker se déclenche après ce trade
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
# ✅ V43 NEW: /historique — Derniers trades
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
    # ✅ V44.1: bouton BROKER retiré — mode MT5 (Gold/Argent/Volatility) unique
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"),
               KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"),
               KeyboardButton("📊 RAPPORT DU JOUR"))
    markup.row(KeyboardButton("📜 HISTORIQUE"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid):
        return bot.send_message(uid, "🔒 Accès restreint. /vip VOTRE-CLÉ pour activer.")
    utilisateurs_actifs.add(uid)
    init_daily_stats(uid)

    kz  = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    vol = "\n".join([f"  {'✅' if v else '❌'} {p}"
                     for p, v in volatility_pairs_active.items()])
    trade_info = ""
    if uid in trades_actifs:
        t = trades_actifs[uid]
        trade_info = f"\n🟠 *TRADE ACTIF:* {t['symbol']} {t['direction']} @ {t['entry_price']}"

    bot.send_message(uid,
        f"💼 *TERMINAL PRIME V46* — MASTER CLASS EDITION\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"4 stratégies, 1 cerveau, gestion de gagnant\n"
        f"🎯 Scan exclusif : 🥇 Gold · 🥈 Argent · 🔥 Volatility\n"
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

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires(message):
    kz  = "🟢 EN COURS" if dans_killzone() else "🔴 INACTIVE"
    vol = "\n".join([f"  {'✅' if v else '❌'} {p}"
                     for p, v in volatility_pairs_active.items()])
    bot.send_message(message.chat.id,
        f"🕒 *KILLZONES & CPR JOURNALIER*\n\n"
        f"🌏 Asie    : 00:00 – 07:00 GMT\n"
        f"🇬🇧 Londres : 08:00 – 11:00 GMT\n"
        f"🇺🇸 New York: 14:00 – 17:00 GMT\n\n"
        f"⏰ Statut : {kz}\n"
        f"🧱 Le CPR (Pivot/BCPR/TCPR) se recalcule chaque jour à partir\n"
        f"   de la clôture de la veille — actif 24/24 sur Volatility,\n"
        f"   soumis aux horaires de marché pour Gold/Argent.\n\n"
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

    # ✅ V44.1: mode unique — Gold, Argent, Volatility (plus de Forex/POCKET)
    markup = InlineKeyboardMarkup(row_width=3)
    btns_vol = [InlineKeyboardButton(
                    NOMS_AFFICHAGE.get(p, p),
                    callback_data=f"set_{p}")
                for p, actif in volatility_pairs_active.items() if actif]
    if btns_vol:
        markup.add(*btns_vol)
    markup.add(InlineKeyboardButton("🥇 GOLD",   callback_data="set_XAUUSD"),
               InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"))
    bot.send_message(uid, "🎯 Sélectionne ta cible :",
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

    # ✅ V44 FIX: fenêtre de validité réduite (45s au lieu de 90s) — un
    # signal vieux de 90s peut déjà être largement dépassé sur une paire
    # rapide (Volatility indices).
    if not cache or (time.time() - cache["time"]) > RISK_CONFIG["signal_validity_seconds"]:
        return bot.send_message(uid,
            f"⏱️ Signal expiré sur {NOMS_AFFICHAGE.get(actif, actif)}\n"
            f"Attends le prochain scan automatique.", parse_mode="Markdown")

    px  = obtenir_prix_broker_realtime(actif) or 0
    nom = NOMS_AFFICHAGE.get(actif, actif)
    fmt = ".0f" if actif in VOLATILE_PAIRS else ".5f"

    if px <= 0:
        return bot.send_message(uid,
            f"⚠️ Impossible de récupérer le prix actuel de {nom}. Réessaie dans un instant.",
            parse_mode="Markdown")

    entry_direction = "BUY" if "BUY" in cache["action"] else "SELL"
    sl_cache, tp1_cache, tp_final_cache = cache["mt5_sl"], cache["mt5_tp1"], cache["mt5_tp"]

    # ✅ V44 FIX NOUVEAU: revalider le marché AVANT d'ouvrir le trade.
    # Si le prix a déjà dépassé le SL ou le TP1 (ou TP final) prévu pendant
    # le délai entre le scan et le clic, on REFUSE d'ouvrir — c'est
    # exactement le scénario "j'entre et j'ai déjà atteint mon TP1".
    if entry_direction == "BUY":
        deja_sl  = px <= sl_cache
        deja_tp1 = px >= tp1_cache
    else:
        deja_sl  = px >= sl_cache
        deja_tp1 = px <= tp1_cache

    if deja_sl:
        return bot.send_message(uid,
            f"❌ *Signal annulé* — {nom}\n"
            f"Le marché a déjà atteint le niveau de Stop Loss prévu "
            f"({sl_cache:{fmt}}) pendant le délai d'exécution.\n"
            f"Aucun trade ouvert. Attends le prochain signal.",
            parse_mode="Markdown")

    if deja_tp1:
        return bot.send_message(uid,
            f"❌ *Signal annulé* — {nom}\n"
            f"Le marché a déjà atteint l'objectif TP1 prévu ({tp1_cache:{fmt}}) "
            f"avant que tu n'ouvres la position — entrer maintenant capturerait "
            f"un R/R trop dégradé.\n"
            f"Aucun trade ouvert. Attends le prochain signal.",
            parse_mode="Markdown")

    # Recalcul du R/R réellement disponible avec le prix FRAIS d'exécution
    risque_restant  = abs(px - sl_cache)
    recomp_restante = abs(tp_final_cache - px)
    rr_restant = (recomp_restante / risque_restant) if risque_restant > 0 else 0
    rr_original = cache["mt5_rr"]

    if rr_original > 0:
        degradation_pct = max(0, (1 - (rr_restant / rr_original)) * 100)
    else:
        degradation_pct = 0

    if degradation_pct > RISK_CONFIG["max_rr_degradation_pct"]:
        return bot.send_message(uid,
            f"❌ *Signal annulé* — {nom}\n"
            f"Le R/R restant s'est trop dégradé depuis la détection du signal "
            f"({rr_original:.2f}R → {rr_restant:.2f}R, -{degradation_pct:.0f}%).\n"
            f"Aucun trade ouvert pour protéger la qualité de l'entrée.",
            parse_mode="Markdown")

    trade_id, sizing = ouvrir_trade(uid, actif, entry_direction, px,
                                    sl_cache, tp1_cache, tp_final_cache,
                                    cache["strategie"], cache["confiance"],
                                    label=cache.get("label","SIGNAL"))

    signal = (
        f"💼 *{cache.get('label','SIGNAL')}* — {nom}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢 BUY MARKET' if 'BUY' in cache['action'] else '🔴 SELL MARKET'}\n"
        f"📊 Contexte : {cache.get('contexte','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entrée  : {px:{fmt}}\n"
        f"🛑 SL      : {sl_cache:{fmt}}\n"
        f"🎯 TP1 (85%): {tp1_cache:{fmt}}\n"
        f"🏁 TP Final (15%): {tp_final_cache:{fmt}}\n"
        f"⚖️ R/R actuel : {rr_restant:.2f}R (prévu {rr_original:.2f}R)\n"
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
    Thread(target=watchdog_trades_bloques,         daemon=True).start()
    print("💼 TERMINAL PRIME V46 — MASTER CLASS ACTIF "
          "(CPR + Open Drive + RSI Exhaustion, scanner parallèle, watchdog)", flush=True)
    bot.infinity_polling()
