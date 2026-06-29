"""
╔════════════════════════════════════════════════════════════════════════════╗
║              TERMINAL PRIME V42 — THE PRO TRADER BRAIN                    ║
║  Base: V38 (stable) + /Volatility granulaire + Stratégie 3 + Cerveau Pro  ║
╚════════════════════════════════════════════════════════════════════════════╝

NOUVEAUTÉS V42 (sur base V38 stable):
  ✅ /Volatility granulaire: V10/V25/V50/V75/V100 ON/OFF individuels
  ✅ Stratégie 3: Zone Trading (Support/Résistance + RSI)
  ✅ Cerveau Pro Trader: détecte le contexte → choisit la meilleure stratégie
     - TENDANCE forte  → Kasper OTE (S1)
     - MOMENTUM/RSI    → OTE Scalping (S2)
     - RANGE/consolid  → Zone Trading (S3)
     - INDÉCIS         → Attendre (patience = pro)
  ✅ Tous les fixes V38 conservés intacts
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

TELEGRAM_TOKEN = "8658287331:AAHlXPDHfNe93ryRtnHadW8VLP0aumOnRvc"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# ÉTATS DE TRADE (V38)
# ==========================================

class TradeState(Enum):
    SIGNAL_SENT = "SIGNAL_ENVOYÉ"
    TRADE_OPEN  = "TRADE_OUVERT"
    TRADE_WIN   = "GAGNÉ"
    TRADE_LOSS  = "PERDU"
    CANCELLED   = "ANNULÉ"

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
# VARIABLES D'ÉTAT
# ==========================================

user_prefs          = {}
plateforme_trading  = {}
utilisateurs_actifs = set()
derniere_alerte_auto= {}
signaux_cache       = {}

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees          = {}
stats_journee          = {'ITM': 0, 'OTM': 0}

# ✅ V42: Contrôle GRANULAIRE par paire (remplace l'ancien booléen unique)
volatility_pairs_active = {
    "V10":  True,
    "V25":  True,
    "V50":  True,
    "V75":  True,
    "V100": True,
}

# V38 — gestion des trades
trades_actifs    = {}
trades_historique= {}
prix_cache       = {}
prix_broker      = {}
pnl_total        = {}
win_count        = {}
loss_count       = {}

# V42 — contexte marché mémorisé
contexte_marche_cache = {}   # symbole -> {"contexte": "TENDANCE", "ts": timestamp}

# ==========================================
# KEEP ALIVE
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime V42 — Pro Trader Brain"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# UTILITAIRES PRIX (V38 inchangé)
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
        tf  = "5min" if granularite == 300 else "1hour"
        mapping_fmp = {"XAUUSD":"FOREX:XAUUSD","XAGUSD":"FOREX:XAGUSD"}
        sym_fmp = mapping_fmp.get(symbole_brut, symbole_brut)
        try:
            url = (f"https://financialmodelingprep.com/api/v3/historical-chart/"
                   f"{tf}/{sym_fmp}?apikey={FMP_API_KEY}")
            res = requests.get(url, timeout=5).json()
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
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history": sym, "end": "latest",
                                "count": 250, "style": "candles",
                                "granularity": granularite}))
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res and "error" not in res:
                return res["candles"]
        except:
            try: ws.close()
            except: pass
            time.sleep(0.3)
    return None

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
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
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
# GESTION TRADES (V38 inchangé)
# ==========================================

def create_trade_id():
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp, strategy, confiance):
    trade_id = create_trade_id()
    trades_actifs[uid] = {
        "trade_id": trade_id, "symbol": symbole,
        "direction": direction, "entry_price": entry_price,
        "sl": sl, "tp": tp, "strategy": strategy, "confiance": confiance,
        "state": TradeState.TRADE_OPEN, "timestamp_open": time.time(),
        "exit_price": None, "exit_time": None, "pnl": None
    }
    print(f"[Trade Opened] {uid}: {trade_id} {symbole} {direction} @ {entry_price}", flush=True)
    return trade_id

def fermer_trade(uid, exit_price, win=True):
    if uid not in trades_actifs:
        return None
    trade    = trades_actifs[uid]
    trade_id = trade["trade_id"]

    if trade["direction"] == "BUY":
        pnl = (exit_price - trade["entry_price"]) * 1000
    else:
        pnl = (trade["entry_price"] - exit_price) * 1000

    trade["state"] = TradeState.TRADE_WIN if win else TradeState.TRADE_LOSS
    if win:
        win_count[uid]  = win_count.get(uid, 0) + 1
    else:
        loss_count[uid] = loss_count.get(uid, 0) + 1

    trade["exit_price"] = exit_price
    trade["exit_time"]  = time.time()
    trade["pnl"]        = pnl
    duration_seconds    = trade["exit_time"] - trade["timestamp_open"]

    if uid not in trades_historique:
        trades_historique[uid] = []
    trades_historique[uid].append({
        "trade_id": trade_id, "symbol": trade["symbol"],
        "direction": trade["direction"], "entry": trade["entry_price"],
        "exit": exit_price, "pnl": pnl, "duration": duration_seconds,
        "win": win, "timestamp": trade["exit_time"]
    })
    pnl_total[uid] = pnl_total.get(uid, 0) + pnl
    del trades_actifs[uid]
    print(f"[Trade Closed] {uid}: {trade_id} PnL={pnl:.2f}", flush=True)
    return {"trade_id": trade_id, "pnl": pnl, "win": win, "duration": duration_seconds}

def utilisateur_a_trade_actif(uid):
    return uid in trades_actifs and trades_actifs[uid]["state"] == TradeState.TRADE_OPEN

# ==========================================
# KILLZONES (V38 inchangé)
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
    if 7.0 <= h < 8.0:  return "🌏🇬🇧 Asie+Londres (07h-08h)"
    if 0.0 <= h < 7.0:  return "🌏 Asian Killzone (00h-07h)"
    if 8.0 <= h <= 10.0: return "🇬🇧 London Killzone (08h-10h)"
    if 12.0 <= h <= 15.0: return "🇺🇸 New York Killzone (12h-15h)"
    return "⏳ Hors session"

def est_symbole_autorise(symbole):
    # ✅ V42: Vérification granulaire par paire Volatility
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
# INDICATEURS TECHNIQUES (V38 inchangé)
# ==========================================

def calculer_ema_cloud(df):
    e72  = ta.trend.EMAIndicator(close=df['close'], window=72).ema_indicator()
    e89  = ta.trend.EMAIndicator(close=df['close'], window=89).ema_indicator()
    e180 = ta.trend.EMAIndicator(close=df['close'], window=180).ema_indicator()
    e200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
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
# ✅ V42: DÉTECTION DU CONTEXTE MARCHÉ
# ==========================================

def detecter_contexte(symbole):
    """
    Analyse le marché et retourne le contexte:
      TENDANCE  → EMA bien alignées + volatilité élevée
      SCALPING  → RSI extrême (< 30 ou > 70)
      RANGE     → EMA divergentes + basse volatilité
      INDECIS   → Pas assez de signal clair
    """
    cached = contexte_marche_cache.get(symbole)
    if cached and (time.time() - cached["ts"]) < 120:   # cache 2 min
        return cached["contexte"]

    try:
        c4h = obtenir_donnees_deriv(symbole, 14400)
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

        # Décision contexte
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
# STRATÉGIE 1: KASPER OTE STRICT (V38)
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
# STRATÉGIE 2: OTE SCALPING (V38)
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
# ✅ V42 NEW: STRATÉGIE 3 — ZONE TRADING
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
    c4h = obtenir_donnees_deriv(symbole, 14400)
    c1h = obtenir_donnees_deriv(symbole, 3600)
    if not c4h or not c1h: return None
    try:
        df4h = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                               "high":float(c["high"]),"low":float(c["low"])} for c in c4h])
        df1h = pd.DataFrame([{"open":float(c["open"]),"close":float(c["close"]),
                               "high":float(c["high"]),"low":float(c["low"])} for c in c1h])

        zone = identifier_zone_consolidation(df4h, lookback=50)
        if not zone: return None

        # Volatilité doit être basse dans la zone
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

        # Prix dans les 20% inférieurs → BUY rebond support
        if dist_sup < zone_width * 0.2:
            last = df1h.iloc[-2]
            if last["low"] < zone["support"] * 1.002 and last["close"] > zone["support"]:
                signal   = "BUY"
                tendance = "BULL"
                sl       = zone["support"]  - zone_width * 0.05
                tp       = zone["resistance"]

        # Prix dans les 20% supérieurs → SELL rebond résistance
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

        # Confirmer RSI
        try:
            rsi_val = ta.momentum.RSIIndicator(close=df1h["close"], window=14).rsi().iloc[-1]
            rsi_ok  = (tendance == "BULL" and rsi_val < 65) or (tendance == "BEAR" and rsi_val > 35)
        except:
            rsi_ok = False

        # Score de confiance pour la zone
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
            "tendance": tendance, "force": "ZONE 📦", "msg": f"Rebond sur {'Support' if signal=='BUY' else 'Résistance'}",
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
# ✅ V42: CERVEAU PRO TRADER
# ==========================================

def cerveau_pro_trader(symbole):
    """
    Détecte le contexte, puis lance UNIQUEMENT la stratégie adaptée.
    Comme un vrai trader pro qui choisit son outil selon le marché.
    """
    contexte = detecter_contexte(symbole)

    if contexte == "TENDANCE":
        res = analyser_kasper_ote(symbole)
        emoji_ctx = "📈 TENDANCE FORTE"

    elif contexte == "SCALPING":
        res = analyser_ote_scalping(symbole)
        emoji_ctx = "⚡ MOMENTUM SCALPING"

    elif contexte == "RANGE":
        res = analyser_zone_trading(symbole)
        emoji_ctx = "📦 ZONE / RANGE"

    else:  # INDECIS → patience
        print(f"[Cerveau/{symbole}] Contexte INDÉCIS → skip", flush=True)
        return None, None

    if res:
        res["contexte_detecte"] = emoji_ctx

    return res, contexte

# ==========================================
# ✅ V42: /Volatility GRANULAIRE
# ==========================================

@bot.message_handler(commands=['Volatility'])
def gerer_volatility(message):
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "❌ Admin uniquement.")

    parts = message.text.strip().split()

    # /Volatility seul → afficher statut
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
# SCANNER PRINCIPAL V42
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

                # ── Cerveau Pro Trader ──────────────────────────────
                res, contexte = cerveau_pro_trader(paire)
                if not res: continue

                # Récupérer prix réel et valider
                px = obtenir_prix_broker_realtime(paire) or res["px"]
                if not valider_prix_avant_signal(paire, px): continue

                # Clé de cache unique
                cle = f"{paire}_PRO"
                signaux_cache[cle] = {
                    "time":    time.time(),
                    "action":  res["action"],
                    "mt5_sl":  res["sl"],
                    "mt5_tp":  res["tp"],
                    "mt5_tp1": res.get("tp1", res["tp"]),
                    "mt5_rr":  res["rr"],
                    "zone":    res.get("zone", {}),
                    "sh":      res.get("sh", 0),
                    "sl_swing":res.get("sl_swing", 0),
                    "force":   res["force"],
                    "msg":     res["msg"],
                    "dur":     300,
                    "confiance": res["confiance"],
                    "strategie": res["strategie"],
                    "label":   res["label"],
                    "contexte":res.get("contexte_detecte","")
                }
                derniere_alerte_auto[cle] = time.time()

                nom  = NOMS_AFFICHAGE.get(paire, f"{paire[:3]}/{paire[3:]}")
                dir_ = "🟢 BUY" if "BUY" in res["action"] else "🔴 SELL"

                for uid in libres:
                    if utilisateur_a_trade_actif(uid): continue
                    pf = plateforme_trading.get(uid, "MT5")
                    if pf == "MT5"    and paire not in ELITE_PAIRS_MT5: continue
                    if pf == "POCKET" and paire not in FOREX_PAIRS:    continue

                    markup = InlineKeyboardMarkup().add(
                        InlineKeyboardButton(f"⚡ Copier {nom}", callback_data=f"set_{paire}")
                    )

                    # Ligne zone pour Stratégie 3
                    if res["strategie"] == 3:
                        ligne_zone = (f"📦 Zone : {res['zone_support']:.5f}"
                                      f" → {res['zone_resistance']:.5f}"
                                      f" ({res['zone_rebonds']} rebonds)\n")
                    else:
                        z = res.get("zone", {})
                        ligne_zone = (f"🔶 Zone OTE : {z.get('ote_bas',0):.5f}"
                                      f" → {z.get('ote_haut',0):.5f}\n")

                    txt = (
                        f"💼 *TERMINAL PRIME V42*\n"
                        f"{nom}  {dir_}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Stratégie : *{res['label']}*\n"
                        f"📊 Contexte  : {res.get('contexte_detecte','')}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"☁️ EMA  : {res['force']}\n"
                        f"📍 {res['msg']}\n"
                        f"⏰ {nom_killzone()}\n"
                        f"{ligne_zone}"
                        f"⚖️ R/R : {res['rr']}R\n"
                        f"🎖️ Confiance : {res['confiance']}%\n"
                        f"💰 Prix réel : {px:.5f}"
                    )
                    try:
                        bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                    except:
                        pass

        except Exception as e:
            print(f"[Scanner V42] {e}", flush=True)

# ==========================================
# MONITORING TP/SL (V38 inchangé)
# ==========================================

def monitorer_trades_actifs():
    while True:
        try:
            time.sleep(5)
            for uid in list(trades_actifs.keys()):
                if uid not in trades_actifs: continue
                trade = trades_actifs[uid]
                if trade["state"] != TradeState.TRADE_OPEN: continue

                symbole      = trade["symbol"]
                prix_current = obtenir_prix_broker_realtime(symbole)
                if not prix_current: continue

                hit_tp = (trade["direction"]=="BUY"  and prix_current >= trade["tp"]) or \
                         (trade["direction"]=="SELL" and prix_current <= trade["tp"])
                hit_sl = (trade["direction"]=="BUY"  and prix_current <= trade["sl"]) or \
                         (trade["direction"]=="SELL" and prix_current >= trade["sl"])

                if hit_tp or hit_sl:
                    win    = hit_tp
                    result = fermer_trade(uid, prix_current, win=win)
                    if result:
                        if win:
                            msg = (
                                f"✅ *TRADE GAGNÉ!* 🎉\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 {symbole}\n"
                                f"Entrée : {trade['entry_price']:.5f}\n"
                                f"Sortie : {prix_current:.5f}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"💰 *Profit : +{result['pnl']:.2f} USD*\n"
                                f"⏱️ Durée : {int(result['duration']/60)} min\n"
                                f"🎖️ Stratégie {trade['strategy']} "
                                f"(Confiance {trade['confiance']}%)\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏦 P&L total : {pnl_total.get(uid,0):.2f} USD"
                            )
                        else:
                            msg = (
                                f"❌ *TRADE PERDU* 😔\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 {symbole}\n"
                                f"Entrée : {trade['entry_price']:.5f}\n"
                                f"Sortie : {prix_current:.5f}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"💔 *Perte : {result['pnl']:.2f} USD*\n"
                                f"⏱️ Durée : {int(result['duration']/60)} min\n"
                                f"🎖️ Stratégie {trade['strategy']} "
                                f"(Confiance {trade['confiance']}%)\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏦 P&L total : {pnl_total.get(uid,0):.2f} USD"
                            )
                        try: bot.send_message(uid, msg, parse_mode="Markdown")
                        except: pass

        except Exception as e:
            print(f"[Monitor] {e}", flush=True)

# ==========================================
# GESTION DES CLÉS VIP (V38 inchangé)
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
# INTERFACE PRINCIPALE
# ==========================================

def obtenir_clavier(uid):
    pf = plateforme_trading.get(uid, "MT5")
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"),
               KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("🏦 BROKER: POCKET" if pf=="POCKET" else "📈 BROKER: MT5"),
               KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid):
        return bot.send_message(uid, "🔒 Accès restreint. /vip VOTRE-CLÉ pour activer.")
    utilisateurs_actifs.add(uid)
    plateforme_trading.setdefault(uid, "MT5")

    kz  = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    vol = "\n".join([f"  {'✅' if v else '❌'} {p}"
                     for p, v in volatility_pairs_active.items()])
    trade_info = ""
    if uid in trades_actifs:
        t = trades_actifs[uid]
        trade_info = f"\n🟠 *TRADE ACTIF:* {t['symbol']} {t['direction']} @ {t['entry_price']}"

    bot.send_message(uid,
        f"💼 *TERMINAL PRIME V42* — THE PRO TRADER\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Cerveau Pro : 3 stratégies, 1 décision\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 TENDANCE → Kasper OTE\n"
        f"⚡ SCALPING → OTE Scalping\n"
        f"📦 RANGE    → Zone Trading\n"
        f"🤷 INDÉCIS  → Patience\n"
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
        f"🌏 Asie    : 00:00 – 07:00 GMT\n"
        f"🇬🇧 Londres : 08:00 – 10:00 GMT\n"
        f"🇺🇸 New York: 12:00 – 15:00 GMT\n\n"
        f"⏰ Statut : {kz}\n\n"
        f"🔥 Volatility :\n{vol}\n\n"
        f"/Volatility V50 OFF → désactiver V50\n"
        f"/Volatility ALL ON  → tout activer",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE",
                                               "📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if uid in trades_actifs:
        return bot.send_message(uid,
            "🟠 *TRADE ACTIF EN COURS*\n"
            "Attendez la clôture (TP ou SL) avant d'ouvrir un autre.",
            parse_mode="Markdown")

    pf = plateforme_trading.get(uid, "MT5")
    markup = InlineKeyboardMarkup(row_width=3)

    if pf == "MT5":
        # Afficher seulement les paires Volatility actives
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
    trade_id = ouvrir_trade(uid, actif, entry_direction, px,
                            cache["mt5_sl"], cache["mt5_tp"],
                            cache["strategie"], cache["confiance"])

    signal = (
        f"💼 *{cache.get('label','SIGNAL')}* — {nom}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢 BUY MARKET' if 'BUY' in cache['action'] else '🔴 SELL MARKET'}\n"
        f"📊 Contexte : {cache.get('contexte','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entrée : {px:{fmt}}\n"
        f"🛑 SL     : {cache['mt5_sl']:{fmt}}\n"
        f"🎯 TP     : {cache['mt5_tp']:{fmt}}\n"
        f"⚖️ R/R    : {cache['mt5_rr']:.2f}R\n"
        f"🎖️ Confiance : {cache.get('confiance',0)}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *TRADE OUVERT*\n"
        f"🆔 {trade_id}\n"
        f"📬 Vous recevrez un message à la clôture."
    )
    bot.send_message(uid, signal, parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto,     daemon=True).start()
    Thread(target=monitorer_trades_actifs, daemon=True).start()
    print("💼 TERMINAL PRIME V42 — Pro Trader Brain ACTIF", flush=True)
    bot.infinity_polling()
