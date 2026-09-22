import os
import json
import time
import sqlite3
import datetime as dt
import threading
from pathlib import Path
import websocket
import pandas as pd
import ta
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# ============================================================
# TERMINAL PRIME — V19 LABORATOIRE
# Objectif : mesurer l'avantage statistique avant tout Martingale.
# MODE RECHERCHE : les signaux sont PAPER uniquement.
# ============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
CAPITAL_ACTUEL = float(os.environ.get("CAPITAL_ACTUEL", "40650"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN manquant dans les variables d'environnement.")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID manquant dans les variables d'environnement.")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Le laboratoire ne mise jamais d'argent et ne lance aucune Martingale.
LAB_MODE = True
COOLDOWN_SECONDS = 180
SEUIL_SIGNAL_PILIER = 45.0
SEUIL_VIP_SCORE_ALGO = 9.0
DB_PATH = os.environ.get("LAB_DB_PATH", "v19_laboratoire.sqlite3")
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "1089")

CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD", "AUDJPY", "EURAUD",
    "EURUSD", "AUDCAD", "USDCHF", "CADCHF", "EURCHF", "USDJPY"
]
ALL_PAIRS = CRYPTO_PAIRS + FOREX_PAIRS

user_prefs = {}
mode_trading = {}
filtre_vip_actif = {}
utilisateurs_actifs = set()
trades_en_cours = {}
cooldown_actifs = {}
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
derneire_alerte_auto = {}

# ============================================================
# V20 — NOUVEAUX RÉGLAGES
# ============================================================
MIN_QUALITY_SCORE = float(os.environ.get("MIN_QUALITY_SCORE", "62"))
MAX_TRADES_PER_DAY = int(os.environ.get("MAX_TRADES_PER_DAY", "20"))
MAX_CONSECUTIVE_LOSSES = int(os.environ.get("MAX_CONSECUTIVE_LOSSES", "3"))
MAX_DAILY_LOSSES = int(os.environ.get("MAX_DAILY_LOSSES", "5"))
COOLDOWN_AFTER_WIN = int(os.environ.get("COOLDOWN_AFTER_WIN", "30"))
COOLDOWN_AFTER_LOSS = int(os.environ.get("COOLDOWN_AFTER_LOSS", "180"))
SHOCK_ATR_RATIO = float(os.environ.get("SHOCK_ATR_RATIO", "2.5"))
AI_VALIDATOR_ENABLED = os.environ.get("AI_VALIDATOR_ENABLED", "0").lower() in ("1", "true", "yes")
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")


# ============================================================
# SQLITE — JOURNAL PERSISTANT
# ============================================================

def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            signal_at TEXT,
            expiry_at TEXT,
            user_id INTEGER,
            asset TEXT NOT NULL,
            mode TEXT NOT NULL,
            timeframe INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            direction TEXT NOT NULL,
            score REAL,
            score_algo REAL,
            entry_price REAL,
            exit_price REAL,
            result TEXT,
            source TEXT DEFAULT 'LIVE_PAPER',
            manual_override INTEGER DEFAULT 0,
            details TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_asset ON trades(asset)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at)")
    con.commit()
    con.close()

init_db()


def insert_trade(**kw):
    con = db()
    cols = [
        "created_at", "signal_at", "expiry_at", "user_id", "asset", "mode",
        "timeframe", "strategy", "direction", "score", "score_algo",
        "entry_price", "exit_price", "result", "source", "manual_override", "details"
    ]
    vals = [kw.get(c) for c in cols]
    con.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
    rowid = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    con.commit()
    con.close()
    return rowid


def update_trade(trade_id, **kw):
    if not kw:
        return
    con = db()
    sets = ", ".join(f"{k}=?" for k in kw)
    con.execute(f"UPDATE trades SET {sets} WHERE id=?", [*kw.values(), trade_id])
    con.commit()
    con.close()


def stats_db(asset=None, strategy=None, since_days=None):
    con = db()
    where, params = ["result IN ('WIN','LOSS') AND manual_override=0"], []
    if asset:
        where.append("asset=?"); params.append(asset)
    if strategy:
        where.append("strategy=?"); params.append(strategy)
    if since_days:
        where.append("datetime(created_at) >= datetime('now', ?)"); params.append(f"-{int(since_days)} days")
    sql = f"SELECT COUNT(*) n, SUM(result='WIN') wins, SUM(result='LOSS') losses FROM trades WHERE {' AND '.join(where)}"
    r = con.execute(sql, params).fetchone()
    con.close()
    n = int(r["n"] or 0); wins = int(r["wins"] or 0); losses = int(r["losses"] or 0)
    return {"trades": n, "wins": wins, "losses": losses, "winrate": wins/n*100 if n else 0.0}

# ============================================================
# ACCÈS VIP
# ============================================================

def est_autorise(user_id):
    if user_id == ADMIN_ID:
        return True
    exp = utilisateurs_autorises.get(user_id)
    if exp is None:
        return False
    if exp == "LIFETIME" or dt.datetime.now() < exp:
        return True
    utilisateurs_autorises.pop(user_id, None)
    return False

@bot.message_handler(commands=["keygen"])
def keygen(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        arg = message.text.split()[1].lower()
        duree = {"1s":7, "2s":14, "1m":30, "3m":90, "vie":"LIFETIME"}.get(arg, int(arg))
        key = "VIP-" + os.urandom(4).hex().upper()
        cles_generees[key] = duree
        bot.send_message(message.chat.id, f"🔑 `{key}` — {('À VIE' if duree == 'LIFETIME' else str(duree)+' jours')}", parse_mode="Markdown")
    except Exception:
        bot.send_message(message.chat.id, "Usage : /keygen 1s | 2s | 1m | 3m | vie")

@bot.message_handler(commands=["vip"])
def vip(message):
    try:
        key = message.text.split()[1]
        if key not in cles_generees:
            return bot.send_message(message.chat.id, "❌ Clé invalide ou déjà utilisée.")
        duree = cles_generees.pop(key)
        utilisateurs_autorises[message.chat.id] = "LIFETIME" if duree == "LIFETIME" else dt.datetime.now() + dt.timedelta(days=duree)
        bot.send_message(message.chat.id, "✅ Accès laboratoire activé. Tape /start.")
    except Exception:
        bot.send_message(message.chat.id, "Usage : /vip TA-CLE")

# ============================================================
# DERIV
# ============================================================

def prefixer_symbole(symbole):
    return f"cry{symbole}" if symbole in CRYPTO_PAIRS else f"frx{symbole}"


def obtenir_donnees_deriv(symbole, granularite=300, count=250):
    ws = None
    try:
        ws = websocket.create_connection(
            f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}", timeout=8
        )
        ws.send(json.dumps({
            "ticks_history": prefixer_symbole(symbole), "end": "latest",
            "count": count, "style": "candles", "granularity": granularite
        }))
        res = json.loads(ws.recv())
        if "error" in res or "candles" not in res:
            return None
        return res["candles"]
    except Exception:
        return None
    finally:
        try:
            if ws: ws.close()
        except Exception:
            pass


def obtenir_prix_actuel_deriv(symbole):
    ws = None
    try:
        ws = websocket.create_connection(
            f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}", timeout=8
        )
        ws.send(json.dumps({
            "ticks_history": prefixer_symbole(symbole), "end": "latest", "count": 1, "style": "ticks"
        }))
        res = json.loads(ws.recv())
        return float(res["history"]["prices"][0]) if "history" in res else None
    except Exception:
        return None
    finally:
        try:
            if ws: ws.close()
        except Exception:
            pass


def candles_df(candles):
    df = pd.DataFrame([{
        "epoch": int(c.get("epoch", 0)), "open": float(c["open"]), "high": float(c["high"]),
        "low": float(c["low"]), "close": float(c["close"])
    } for c in candles])
    return df.sort_values("epoch").drop_duplicates("epoch").reset_index(drop=True)


def closed_only(df):
    # Deriv renvoie potentiellement la bougie en formation. Le laboratoire
    # prend toujours la dernière bougie terminée comme référence.
    if len(df) < 5:
        return df.iloc[0:0].copy()
    return df.iloc[:-1].copy()

# ============================================================
# FILTRES — CORRÉLATION CORRIGÉE ET FAIL-CLOSED
# ============================================================

def verifier_correlation(symbole, direction):
    correlations = {
        "EURUSD": ("USDCHF", "INVERSE"), "GBPUSD": ("USDCHF", "INVERSE"),
        "AUDUSD": ("USDCAD", "INVERSE"), "USDCHF": ("EURUSD", "INVERSE"),
        "USDCAD": ("AUDUSD", "INVERSE")
    }
    if symbole not in correlations:
        return True, "N/A"
    corr, typ = correlations[symbole]
    candles = obtenir_donnees_deriv(corr, 300)
    if not candles:
        return False, "Données corrélées indisponibles"
    try:
        d = closed_only(candles_df(candles))
        if len(d) < 25:
            return False, "Historique corrélé insuffisant"
        hi, lo, px = d.high.iloc[-20:].max(), d.low.iloc[-20:].min(), d.close.iloc[-1]
        tendance = "HAUSSE" if (px-lo) > (hi-px) else "BAISSE"
        if typ == "INVERSE":
            if direction == "CALL" and tendance == "HAUSSE": return False, f"Corrélation inverse défavorable ({corr})"
            if direction == "PUT" and tendance == "BAISSE": return False, f"Corrélation inverse défavorable ({corr})"
        return True, f"Corrélation OK ({corr})"
    except Exception:
        return False, "Erreur filtre corrélation"

# ============================================================
# LES 4 PILIERS — LOGIQUE V18 CONSERVÉE
# ============================================================

def calculer_aroon(df, period=9):
    up = df.high.rolling(period+1).apply(lambda x: period-x.values.argmax(), raw=True)
    down = df.low.rolling(period+1).apply(lambda x: period-x.values.argmin(), raw=True)
    return ((period-up)/period)*100, ((period-down)/period)*100


def calculer_stc(df, fast=14, slow=50, cycle=5, d1=3, d2=3):
    ef=df.close.ewm(span=fast, adjust=False).mean(); es=df.close.ewm(span=slow, adjust=False).mean(); macd=ef-es
    lo=macd.rolling(cycle).min(); hi=macd.rolling(cycle).max(); k1=100*(macd-lo)/(hi-lo).replace(0,1e-9)
    d=k1.ewm(span=d1,adjust=False).mean(); lo2=d.rolling(cycle).min(); hi2=d.rolling(cycle).max()
    k2=100*(d-lo2)/(hi2-lo2).replace(0,1e-9)
    return k2.ewm(span=d2,adjust=False).mean().clip(0,100)


def calculer_donchian(df, period=20):
    return df.high.rolling(period).max(), df.low.rolling(period).min()


def analyser_aroon_rsi(df):
    try:
        au,ad=calculer_aroon(df,9); r=ta.momentum.RSIIndicator(df.close,window=6).rsi()
        a,b=float(au.iloc[-1]),float(ad.iloc[-1]); ap,bp=float(au.iloc[-2]),float(ad.iloc[-2]); rv=float(r.iloc[-1])
        def s(d):
            x=[]; z=0
            if d=="CALL":
                z+=min(35,max(0,(a-b)*.5));
                if ap<=bp and a>b: z+=20;x.append("Croisement Aroon")
                if 40<=rv<=68:z+=20;x.append(f"RSI {rv:.1f}")
                if a>=70:z+=15;x.append("Aroon Up fort")
            else:
                z+=min(35,max(0,(b-a)*.5));
                if bp<=ap and b>a:z+=20;x.append("Croisement Aroon")
                if 32<=rv<=60:z+=20;x.append(f"RSI {rv:.1f}")
                if b>=70:z+=15;x.append("Aroon Down fort")
            return min(100,round(z,1)),x
        c,p=s("CALL"),s("PUT")
        return {"nom":"AROON_RSI","label":"Aroon + RSI","score_call":c[0],"score_put":p[0],"raisons_call":c[1],"raisons_put":p[1],"details_txt":f"Aroon {a:.0f}/{b:.0f} RSI {rv:.1f}"}
    except Exception:return None


def analyser_adx_stc(df):
    try:
        adx_i=ta.trend.ADXIndicator(df.high,df.low,df.close,window=14); adx=adx_i.adx(); dip=adx_i.adx_pos(); din=adx_i.adx_neg(); st=calculer_stc(df)
        av=float(adx.iloc[-1]); pp,nn=float(dip.iloc[-1]),float(din.iloc[-1]); sv,sp=float(st.iloc[-1]),float(st.iloc[-2])
        def s(d):
            z=0;x=[]
            if d=="CALL":
                if sp<=25 and sv>sp:z+=35;x.append("STC sortie basse")
                elif sv<40:z+=15
                if pp>nn:z+=20;x.append("+DI > -DI")
            else:
                if sp>=75 and sv<sp:z+=35;x.append("STC sortie haute")
                elif sv>60:z+=15
                if nn>pp:z+=20;x.append("-DI > +DI")
            if av>=15:z+=min(20,(av-15)*1.2);x.append(f"ADX {av:.0f}")
            return min(100,round(z,1)),x
        c,p=s("CALL"),s("PUT")
        return {"nom":"ADX_STC","label":"ADX + STC","score_call":c[0],"score_put":p[0],"raisons_call":c[1],"raisons_put":p[1],"details_txt":f"STC {sv:.0f} ADX {av:.0f}"}
    except Exception:return None


def analyser_cci_macd(df):
    try:
        cci=ta.trend.CCIIndicator(df.high,df.low,df.close,window=10).cci(); mac=ta.trend.MACD(df.close,window_slow=25,window_fast=10,window_sign=5).macd_diff()
        cv,cp=float(cci.iloc[-1]),float(cci.iloc[-2]); hv,hp=float(mac.iloc[-1]),float(mac.iloc[-2])
        def s(d):
            z=0;x=[]
            if d=="CALL":
                if cp<=-100 and cv>cp:z+=30;x.append("CCI remonte")
                elif cv<-50:z+=12
                if hv>0:z+=20;x.append("MACD +")
                if hv>hp:z+=15;x.append("MACD monte")
            else:
                if cp>=100 and cv<cp:z+=30;x.append("CCI descend")
                elif cv>50:z+=12
                if hv<0:z+=20;x.append("MACD -")
                if hv<hp:z+=15;x.append("MACD baisse")
            return min(100,round(z,1)),x
        c,p=s("CALL"),s("PUT")
        return {"nom":"CCI_MACD","label":"CCI + MACD","score_call":c[0],"score_put":p[0],"raisons_call":c[1],"raisons_put":p[1],"details_txt":f"CCI {cv:.0f} MACD {hv:.5f}"}
    except Exception:return None


def analyser_donchian_cci(df):
    try:
        up,lo=calculer_donchian(df,20); cci=ta.trend.CCIIndicator(df.high,df.low,df.close,window=11).cci()
        px=float(df.close.iloc[-1]); u,l=float(up.iloc[-1]),float(lo.iloc[-1]); w=max(u-l,1e-9); pos=(px-l)/w; cv,cp=float(cci.iloc[-1]),float(cci.iloc[-2])
        def s(d):
            z=0;x=[]
            if d=="CALL":
                pr=max(0,1-pos*2.5);z+=pr*35
                if pr>.5:x.append(f"Bas canal {pos*100:.0f}%")
                if cp<=-100 and cv>cp:z+=30;x.append("CCI remonte")
                elif cv<-30:z+=12
            else:
                pr=max(0,(pos-.6)*2.5);z+=pr*35
                if pr>.5:x.append(f"Haut canal {pos*100:.0f}%")
                if cp>=100 and cv<cp:z+=30;x.append("CCI descend")
                elif cv>30:z+=12
            return min(100,round(z,1)),x
        c,p=s("CALL"),s("PUT")
        return {"nom":"DONCHIAN_CCI","label":"Donchian + CCI","score_call":c[0],"score_put":p[0],"raisons_call":c[1],"raisons_put":p[1],"details_txt":f"Canal {pos*100:.0f}% CCI {cv:.0f}"}
    except Exception:return None

STRATEGIES = {
    "AROON_RSI": analyser_aroon_rsi,
    "ADX_STC": analyser_adx_stc,
    "CCI_MACD": analyser_cci_macd,
    "DONCHIAN_CCI": analyser_donchian_cci,
}

# ============================================================
# V20 — MARKET REGIME ENGINE
# ============================================================

def moteur_regime_marche(df):
    """Détermine le contexte sans transformer le régime en consensus obligatoire."""
    try:
        ema20 = ta.trend.EMAIndicator(df.close, window=20).ema_indicator()
        ema50 = ta.trend.EMAIndicator(df.close, window=50).ema_indicator()
        adx = ta.trend.ADXIndicator(df.high, df.low, df.close, window=14).adx()
        atr = ta.volatility.AverageTrueRange(df.high, df.low, df.close, window=14).average_true_range()
        rsi = ta.momentum.RSIIndicator(df.close, window=14).rsi()
        up, lo = calculer_donchian(df, 20)
        av=float(adx.iloc[-1]); at=float(atr.iloc[-1]); px=float(df.close.iloc[-1])
        e20=float(ema20.iloc[-1]); e50=float(ema50.iloc[-1]); rv=float(rsi.iloc[-1])
        prev_up=float(up.iloc[-2]); prev_lo=float(lo.iloc[-2])
        candle_range=float(df.high.iloc[-1]-df.low.iloc[-1])
        atr_ratio=candle_range/max(at,1e-12)
        series=atr.dropna().tail(80)
        atr_rank=float((series<=at).mean()*100) if len(series) else 50.0
        if atr_ratio>=SHOCK_ATR_RATIO or atr_rank>=97:
            regime="SHOCK"
        elif px>prev_up and atr_rank>=55:
            regime="BREAKOUT_UP"
        elif px<prev_lo and atr_rank>=55:
            regime="BREAKOUT_DOWN"
        elif av>=20 and e20>e50:
            regime="TREND_UP"
        elif av>=20 and e20<e50:
            regime="TREND_DOWN"
        elif av<18 and atr_rank<=60:
            regime="RANGE"
        else:
            regime="NEUTRAL"
        return {"regime":regime,"adx":av,"atr":at,"atr_ratio":atr_ratio,"atr_rank":atr_rank,"rsi":rv,"ema20":e20,"ema50":e50}
    except Exception:
        return None


def quality_filter(df, direction, strategy, regime_info, raw_score):
    """Score qualité 0-100: stratégie, structure, momentum, volatilité, contexte."""
    try:
        score=min(40.0, raw_score*0.40); reasons=[]
        last=df.iloc[-1]; prev=df.iloc[-2]
        body=abs(float(last.close-last.open)); rng=max(float(last.high-last.low),1e-12)
        body_ratio=body/rng
        if direction=="CALL": structure=last.close>=last.open and last.close>prev.close
        else: structure=last.close<=last.open and last.close<prev.close
        if structure: score+=18; reasons.append("structure alignée")
        else: score+=3
        rv=regime_info["rsi"]
        if (direction=="CALL" and 50<=rv<=70) or (direction=="PUT" and 30<=rv<=50):
            score+=12; reasons.append("momentum aligné")
        elif (direction=="CALL" and rv>78) or (direction=="PUT" and rv<22):
            score-=10; reasons.append("momentum trop étiré")
        else: score+=4
        if body_ratio>=.35: score+=8; reasons.append("bougie exploitable")
        else: score+=2
        regime=regime_info["regime"]
        if regime=="SHOCK": score-=30; reasons.append("marché en choc")
        elif regime=="TREND_UP": score += 12 if direction=="CALL" else -8; reasons.append("tendance haussière")
        elif regime=="TREND_DOWN": score += 12 if direction=="PUT" else -8; reasons.append("tendance baissière")
        elif regime=="BREAKOUT_UP": score += 10 if direction=="CALL" else -10; reasons.append("breakout haussier")
        elif regime=="BREAKOUT_DOWN": score += 10 if direction=="PUT" else -10; reasons.append("breakout baissier")
        elif regime=="RANGE" and strategy=="DONCHIAN_CCI": score+=7; reasons.append("range compatible")
        if regime_info["atr_ratio"]>=SHOCK_ATR_RATIO: score-=25
        elif regime_info["atr_rank"]>85: score-=8; reasons.append("volatilité élevée")
        else: score+=5
        return max(0,min(100,round(score,1))),reasons
    except Exception:
        return 0,["erreur filtre qualité"]


def risk_gate(user_id):
    n,w,l=stats_user(user_id)
    if n>=MAX_TRADES_PER_DAY: return False,f"limite quotidienne {MAX_TRADES_PER_DAY} trades"
    if l>=MAX_DAILY_LOSSES: return False,f"limite de pertes du jour {MAX_DAILY_LOSSES}"
    streak=consecutive_losses_user(user_id)
    if streak>=MAX_CONSECUTIVE_LOSSES: return False,f"pause après {streak} pertes consécutives"
    if user_id in trades_en_cours: return False,"un trade papier est déjà actif"
    return True,"OK"


def stats_user(user_id):
    con=db(); r=con.execute("SELECT COUNT(*) n,SUM(result='WIN') w,SUM(result='LOSS') l FROM trades WHERE user_id=? AND result IN ('WIN','LOSS') AND manual_override=0",(user_id,)).fetchone(); con.close()
    return int(r['n'] or 0),int(r['w'] or 0),int(r['l'] or 0)


def consecutive_losses_user(user_id):
    con=db(); rows=con.execute("SELECT result FROM trades WHERE user_id=? AND result IN ('WIN','LOSS') AND manual_override=0 ORDER BY id DESC LIMIT 20",(user_id,)).fetchall(); con.close()
    n=0
    for r in rows:
        if r['result']=='LOSS': n+=1
        else: break
    return n


def cooldown_ok_v20(user_id):
    con=db(); row=con.execute("SELECT result,created_at FROM trades WHERE user_id=? AND result IN ('WIN','LOSS') ORDER BY id DESC LIMIT 1",(user_id,)).fetchone(); con.close()
    if not row:return True,""
    try: when=dt.datetime.fromisoformat(row['created_at'])
    except Exception:return True,""
    elapsed=(dt.datetime.utcnow()-when).total_seconds(); limit=COOLDOWN_AFTER_WIN if row['result']=='WIN' else COOLDOWN_AFTER_LOSS
    if elapsed<limit:return False,f"cooldown {int(limit-elapsed)}s"
    return True,"OK"


def ai_validate_signal(payload):
    """Validateur optionnel OpenAI-compatible. Il ne génère jamais le signal."""
    if not AI_VALIDATOR_ENABLED or not AI_API_KEY:return True,"IA désactivée"
    try:
        prompt=("Tu es un validateur de signal binaire. Tu ne dois pas inventer de direction. "
                "Réponds uniquement PASS ou REJECT. Rejette les contextes incohérents, choc, conflit ou score faible.\n"+json.dumps(payload,ensure_ascii=False))
        r=requests.post(f"{AI_BASE_URL}/chat/completions",headers={"Authorization":f"Bearer {AI_API_KEY}","Content-Type":"application/json"},json={"model":AI_MODEL,"temperature":0,"max_tokens":10,"messages":[{"role":"user","content":prompt}]},timeout=12)
        if r.status_code!=200:return True,"IA indisponible — signal non bloqué"
        txt=r.json()["choices"][0]["message"]["content"].strip().upper()
        return (txt.startswith("PASS"),f"IA={txt[:20]}")
    except Exception:
        return True,"IA indisponible — signal non bloqué"

# ============================================================
# ANALYSE V19 — BOUGIES FERMÉES + LOG COMPLET
# ============================================================

def analyser_binaire_v19(symbole, mode="STANDARD"):
    """V20: conserve les 4 piliers V19 mais ajoute régime, qualité, risque et IA optionnelle."""
    tfs=[600,300,120] if mode=="STANDARD" else [60]
    for tf in tfs:
        raw=obtenir_donnees_deriv(symbole,tf)
        if not raw: continue
        df=closed_only(candles_df(raw))
        if len(df)<70: continue
        try:
            regime=moteur_regime_marche(df)
            if not regime or regime["regime"]=="SHOCK": continue
            resultats=[]
            for name,fn in STRATEGIES.items():
                r=fn(df)
                if not r: continue
                best=max(r["score_call"],r["score_put"])
                if best<SEUIL_SIGNAL_PILIER: continue
                direction="CALL" if r["score_call"]>=r["score_put"] else "PUT"
                quality,reasons_q=quality_filter(df,direction,name,regime,best)
                reasons=(r["raisons_call"] if direction=="CALL" else r["raisons_put"])+reasons_q
                resultats.append({"strategy":name,"label":r["label"],"direction":direction,"score":best,"quality":quality,"raisons":reasons,"details":r["details_txt"]})
            if not resultats: continue
            best=max(resultats,key=lambda x:(x["quality"],x["score"]))
            if best["quality"]<MIN_QUALITY_SCORE: continue
            ok_corr,corr_msg=verifier_correlation(symbole,best["direction"])
            if not ok_corr: continue
            # Confirmation MTF légère: on vérifie la tendance sur le TF suivant sans exiger consensus.
            mtf_note="MTF non vérifié"
            if mode=="STANDARD":
                higher_tf=900 if tf!=600 else 1800
                hraw=obtenir_donnees_deriv(symbole,higher_tf,180)
                hdf=closed_only(candles_df(hraw)) if hraw else pd.DataFrame()
                if len(hdf)>=60:
                    hreg=moteur_regime_marche(hdf)
                    if hreg:
                        aligned=(best["direction"]=="CALL" and hreg["regime"] in ("TREND_UP","BREAKOUT_UP")) or (best["direction"]=="PUT" and hreg["regime"] in ("TREND_DOWN","BREAKOUT_DOWN"))
                        if aligned: best["quality"]=min(100,best["quality"]+6); mtf_note=f"MTF aligné {hreg['regime']}"
                        elif hreg["regime"] in ("TREND_UP","TREND_DOWN"):
                            best["quality"]=max(0,best["quality"]-8); mtf_note=f"MTF contraire {hreg['regime']}"
            if best["quality"]<MIN_QUALITY_SCORE: continue
            ai_ok,ai_note=ai_validate_signal({"asset":symbole,"tf":tf,"direction":best["direction"],"strategy":best["strategy"],"raw_score":best["score"],"quality":best["quality"],"regime":regime["regime"],"rsi":regime["rsi"]})
            if not ai_ok: continue
            duration=180 if (mode=="STANDARD" and tf==300) else (tf if mode=="STANDARD" else 60)
            return {"action":best["direction"],"strategy":best["strategy"],"label":best["label"],"score":best["score"],"quality":best["quality"],"score_algo":round(5+(best["quality"]/100)*5,1),"tf":tf,"duration":duration,"details":best["details"],"reasons":best["raisons"],"correlation":corr_msg,"regime":regime["regime"],"regime_data":regime,"mtf":mtf_note,"ai":ai_note,"candidates":resultats,"signal_epoch":int(df.epoch.iloc[-1])}
        except Exception as e:
            print(f"[ANALYSE V20] {symbole}/{tf}: {e}",flush=True)
            continue
    return None

# ============================================================
# PAPER TRADING LIVE
# ============================================================

def planifier_paper(chat_id,symbole,analysis):
    ok,why=risk_gate(chat_id)
    if not ok:
        bot.send_message(chat_id,f"🛑 Risk Engine: {why}")
        return
    ok,why=cooldown_ok_v20(chat_id)
    if not ok:
        bot.send_message(chat_id,f"⏳ {why}")
        return
    now=dt.datetime.utcnow(); wait=60-now.second
    if wait<10: wait+=60
    entry_at=now+dt.timedelta(seconds=wait)
    exp_at=entry_at+dt.timedelta(seconds=analysis["duration"])
    trade_id=insert_trade(
        created_at=now.isoformat(), signal_at=entry_at.isoformat(), expiry_at=exp_at.isoformat(),
        user_id=chat_id, asset=symbole, mode=mode_trading.get(chat_id,"STANDARD"),
        timeframe=analysis["tf"], strategy=analysis["strategy"], direction=analysis["action"],
        score=analysis["score"], score_algo=analysis["score_algo"], source="LIVE_PAPER",
        details=json.dumps(analysis,ensure_ascii=False)
    )
    trades_en_cours[chat_id]={"trade_id":trade_id,"symbole":symbole,"analysis":analysis,"entry_at":entry_at,"duration":analysis["duration"]}
    msg=(f"🧪 **PAPER SIGNAL V19**\n──────────────\n"
         f"🌐 {symbole}\n👉 **{analysis['action']}**\n"
         f"🧩 {analysis['label']}\n📊 Score technique : {analysis['score']:.1f}/100\n"
         f"📐 Timeframe : {analysis['tf']}s\n⏱ Entrée test : `{entry_at.strftime('%H:%M:%S')} UTC`\n"
         f"⏳ Expiration : `{exp_at.strftime('%H:%M:%S')} UTC`\n"
         f"🛑 **Aucune mise réelle — laboratoire uniquement.**")
    bot.send_message(chat_id,msg,parse_mode="Markdown")
    Timer(wait,executer_paper,args=[chat_id]).start()


def executer_paper(chat_id):
    trade=trades_en_cours.get(chat_id)
    if not trade:return
    px=obtenir_prix_actuel_deriv(trade["symbole"])
    if px is None:
        return
    trade["entry_price"]=px
    update_trade(trade["trade_id"],entry_price=px)
    Timer(trade["duration"],terminer_paper,args=[chat_id]).start()


def terminer_paper(chat_id):
    trade=trades_en_cours.pop(chat_id,None)
    if not trade:return
    px=obtenir_prix_actuel_deriv(trade["symbole"])
    entry=trade.get("entry_price")
    if px is None or entry is None:
        update_trade(trade["trade_id"],result="DATA_ERROR")
        bot.send_message(chat_id,"⚠️ V19 : résultat non enregistré — donnée de sortie indisponible.")
        return
    direction=trade["analysis"]["action"]
    win=(direction=="CALL" and px>entry) or (direction=="PUT" and px<entry)
    result="WIN" if win else "LOSS"
    update_trade(trade["trade_id"],exit_price=px,result=result)
    emoji="✅" if win else "❌"
    bot.send_message(chat_id,f"{emoji} **PAPER {result}**\nEntrée : `{entry}`\nSortie : `{px}`\nStratégie : `{trade['analysis']['strategy']}`",parse_mode="Markdown")

# ============================================================
# BACKTEST — CHAQUE PILIER INDÉPENDAMMENT
# ============================================================

def backtest_strategy(df,strategy_name,duration_bars):
    fn=STRATEGIES[strategy_name]
    rows=[]
    # i est le dernier point connu. Le signal utilise i-1 (bougie clôturée),
    # puis le prix de sortie est pris dans les bougies futures.
    for i in range(70,len(df)-duration_bars-1):
        sample=df.iloc[:i].copy()
        try:r=fn(sample)
        except Exception:r=None
        if not r:continue
        score=max(r["score_call"],r["score_put"])
        if score<SEUIL_SIGNAL_PILIER:continue
        direction="CALL" if r["score_call"]>=r["score_put"] else "PUT"
        entry=float(df.close.iloc[i-1])
        exit_px=float(df.close.iloc[i-1+duration_bars])
        win=(direction=="CALL" and exit_px>entry) or (direction=="PUT" and exit_px<entry)
        rows.append({"epoch":int(df.epoch.iloc[i-1]),"direction":direction,"score":score,"entry":entry,"exit":exit_px,"result":"WIN" if win else "LOSS"})
    return rows


def run_backtest(asset,tf,only_strategy=None):
    raw=obtenir_donnees_deriv(asset,tf,count=250)
    if not raw:return None
    df=closed_only(candles_df(raw))
    # 1 barre d'expiration pour 60s, 3 pour 180s, etc.
    duration_bars=max(1,round(180/tf)) if tf>=60 else 1
    names=[only_strategy] if only_strategy else list(STRATEGIES)
    output={}
    for name in names:
        if name not in STRATEGIES:continue
        rows=backtest_strategy(df,name,duration_bars)
        n=len(rows);w=sum(x["result"]=="WIN" for x in rows)
        output[name]={"trades":n,"wins":w,"losses":n-w,"winrate":w/n*100 if n else 0.0}
    return output

# ============================================================
# RAPPORTS
# ============================================================

def stats_message(asset=None,strategy=None):
    s=stats_db(asset,strategy)
    return (f"🧪 **V19 — STATISTIQUES RÉELLES DU LAB**\n──────────────\n"
            f"Trades : **{s['trades']}**\nWIN : **{s['wins']}**\nLOSS : **{s['losses']}**\n"
            f"Winrate directionnel : **{s['winrate']:.2f}%**\n\n"
            f"⚠️ Ce chiffre mesure seulement la direction prix. Le payout réel du broker n'est pas inclus.")

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    if not est_autorise(message.chat.id):return
    bot.send_message(message.chat.id,stats_message(),parse_mode="Markdown")

@bot.message_handler(commands=["backtest"])
def cmd_backtest(message):
    if not est_autorise(message.chat.id):return
    p=message.text.split(); asset=p[1].upper() if len(p)>1 else "EURUSD"; tf=int(p[2]) if len(p)>2 else 300; strat=p[3].upper() if len(p)>3 else None
    if asset not in ALL_PAIRS or tf not in (60,120,300,600):
        return bot.send_message(message.chat.id,"Usage : /backtest EURUSD 300 [AROON_RSI]")
    bot.send_message(message.chat.id,"🧪 Backtest en cours...")
    res=run_backtest(asset,tf,strat)
    if res is None:return bot.send_message(message.chat.id,"❌ Données Deriv indisponibles.")
    txt=f"🧪 **BACKTEST V19 — {asset} / {tf}s**\n──────────────\n"
    for name,s in res.items():
        txt+=f"**{name}**\nTrades: {s['trades']} · WIN: {s['wins']} · LOSS: {s['losses']} · Winrate: {s['winrate']:.2f}%\n\n"
    txt+="⚠️ Résultat historique sur l'échantillon téléchargé, pas une garantie future."
    bot.send_message(message.chat.id,txt,parse_mode="Markdown")

# ============================================================
# INTERFACE
# ============================================================

def nom_otc(s):return f"{s[:3]}/{s[3:]} OTC"

def clavier(uid):
    m=ReplyKeyboardMarkup(resize_keyboard=True)
    m.row(KeyboardButton("📊 CHOISIR UNE DEVISE"),KeyboardButton("🚀 LANCER L'ANALYSE"))
    m.row(KeyboardButton("🧪 STATS LAB"),KeyboardButton("🧪 BACKTEST"))
    m.row(KeyboardButton("💎 SIGNAUX VIP"))
    return m

@bot.message_handler(commands=["start"])
def start(message):
    if not est_autorise(message.chat.id):return bot.send_message(message.chat.id,"🔒 Accès restreint.")
    utilisateurs_actifs.add(message.chat.id); mode_trading.setdefault(message.chat.id,"STANDARD"); filtre_vip_actif.setdefault(message.chat.id,False)
    bot.send_message(message.chat.id,
        "🧠 **TERMINAL PRIME V20 — BINARY ENGINE**\n\n"
        "4 piliers conservés + Market Regime + Quality + MTF + Risk Engine.\n\n"
        "✅ Bougies clôturées uniquement\n✅ Journal SQLite persistant\n✅ Backtest individuel\n✅ Paper trading\n🚫 Martingale désactivée\n🚫 WIN manuel désactivé\n🚫 Fausse confiance en %\n\n"
        "Commandes : `/stats`, `/risk` et `/backtest EURUSD 300 AROON_RSI`",
        reply_markup=clavier(message.chat.id),parse_mode="Markdown")

@bot.message_handler(func=lambda m:m.text=="📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id):return
    mk=InlineKeyboardMarkup(row_width=3)
    for p in ALL_PAIRS:mk.add(InlineKeyboardButton(nom_otc(p),callback_data=f"set_{p}"))
    bot.send_message(message.chat.id,"Choisis l'actif à tester :",reply_markup=mk)

@bot.callback_query_handler(func=lambda c:c.data.startswith("set_"))
def set_asset(call):
    uid=call.message.chat.id
    if not est_autorise(uid):return
    asset=call.data[4:];user_prefs[uid]=asset
    bot.answer_callback_query(call.id,"Actif sélectionné")
    bot.send_message(uid,f"✅ Actif : **{nom_otc(asset)}**\nLance l'analyse pour créer un PAPER SIGNAL.",parse_mode="Markdown",reply_markup=clavier(uid))

@bot.message_handler(func=lambda m:m.text=="🚀 LANCER L'ANALYSE")
def lancer(message):
    uid=message.chat.id
    if not est_autorise(uid):return
    if uid in trades_en_cours:return bot.send_message(uid,"⚠️ Un paper trade est déjà en cours.")
    asset=user_prefs.get(uid)
    if not asset:return bot.send_message(uid,"Choisis d'abord une devise.")
    bot.send_message(uid,"🔬 Analyse V19 des bougies clôturées...")
    a=analyser_binaire_v19(asset,mode_trading.get(uid,"STANDARD"))
    if not a:return bot.send_message(uid,"⏳ Aucun setup validé par le laboratoire actuellement.")
    if filtre_vip_actif.get(uid,False) and a["score_algo"]<SEUIL_VIP_SCORE_ALGO:
        return bot.send_message(uid,f"💎 Filtre VIP : score {a['score_algo']}/10 < {SEUIL_VIP_SCORE_ALGO}/10.")
    planifier_paper(uid,asset,a)

@bot.message_handler(func=lambda m:m.text=="🧪 STATS LAB")
def stats_btn(message):
    if est_autorise(message.chat.id):bot.send_message(message.chat.id,stats_message(),parse_mode="Markdown")

@bot.message_handler(func=lambda m:m.text=="🧪 BACKTEST")
def backtest_btn(message):
    if est_autorise(message.chat.id):bot.send_message(message.chat.id,"Exemple : `/backtest EURUSD 300` ou `/backtest EURUSD 300 ADX_STC`",parse_mode="Markdown")

@bot.message_handler(func=lambda m:m.text.startswith("💎 SIGNAUX VIP"))
def toggle_vip(message):
    if not est_autorise(message.chat.id):return
    uid=message.chat.id;filtre_vip_actif[uid]=not filtre_vip_actif.get(uid,False)
    bot.send_message(uid,"💎 VIP ON — score ≥ 9/10" if filtre_vip_actif[uid] else "🔓 VIP OFF — seuil standard",reply_markup=clavier(uid))

# ============================================================
# V20 — STATUS RISQUE
# ============================================================

@bot.message_handler(commands=["risk"])
def cmd_risk(message):
    if not est_autorise(message.chat.id): return
    n,w,l=stats_user(message.chat.id); streak=consecutive_losses_user(message.chat.id)
    bot.send_message(message.chat.id,
        f"🛡️ <b>RISK ENGINE V20</b>\n\nTrades jour: <b>{n}/{MAX_TRADES_PER_DAY}</b>\n"
        f"Wins: <b>{w}</b> | Loss: <b>{l}</b>\nSérie pertes: <b>{streak}/{MAX_CONSECUTIVE_LOSSES}</b>\n"
        f"Limite pertes jour: <b>{MAX_DAILY_LOSSES}</b>\nCooldown loss: <b>{COOLDOWN_AFTER_LOSS}s</b>\n"
        f"Qualité minimale: <b>{MIN_QUALITY_SCORE}/100</b>\nIA: <b>{'ON' if AI_VALIDATOR_ENABLED and AI_API_KEY else 'OFF'}</b>")

# ============================================================
# SCANNER AUTO — PAPER UNIQUEMENT
# ============================================================

def scanner_auto():
    while True:
        try:
            time.sleep(60)
            for uid in list(utilisateurs_actifs):
                if not est_autorise(uid) or uid in trades_en_cours:continue
                asset=user_prefs.get(uid)
                if not asset:continue
                mode=mode_trading.get(uid,"STANDARD")
                ok,_=risk_gate(uid)
                if not ok: continue
                ok,_=cooldown_ok_v20(uid)
                if not ok: continue
                a=analyser_binaire_v19(asset,mode)
                if not a:continue
                if filtre_vip_actif.get(uid,False) and a["score_algo"]<SEUIL_VIP_SCORE_ALGO:continue
                planifier_paper(uid,asset,a)
        except Exception:
            continue

# ============================================================
# SERVEUR RENDER
# ============================================================
app=Flask(__name__)
@app.route('/')
def home():return "Terminal Prime V19 Laboratoire — PAPER ONLY"

def keep_alive():
    Thread(target=lambda:app.run(host='0.0.0.0',port=int(os.environ.get('PORT',8080))),daemon=True).start()

if __name__=="__main__":
    keep_alive()
    Thread(target=scanner_auto,daemon=True).start()
    print("V19 LABORATOIRE démarrée — PAPER ONLY",flush=True)
    bot.infinity_polling(skip_pending=True)
