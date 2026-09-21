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
# ANALYSE V19 — BOUGIES FERMÉES + LOG COMPLET
# ============================================================

def analyser_binaire_v19(symbole, mode="STANDARD"):
    tfs=[600,300,120] if mode=="STANDARD" else [60]
    for tf in tfs:
        raw=obtenir_donnees_deriv(symbole,tf)
        if not raw: continue
        df=closed_only(candles_df(raw))
        if len(df)<70: continue
        try:
            taille=(df.high-df.low); corps=(df.close-df.open).abs()
            avg_taille=taille.iloc[-4:-1].mean(); avg_corps=corps.iloc[-4:-1].mean()
            if avg_corps>0 and avg_taille>avg_corps*3.5:
                continue
            # Le signal est évalué sur la dernière bougie clôturée uniquement.
            resultats=[]
            for name,fn in STRATEGIES.items():
                r=fn(df)
                if not r: continue
                best=max(r["score_call"],r["score_put"])
                if best<SEUIL_SIGNAL_PILIER: continue
                direction="CALL" if r["score_call"]>=r["score_put"] else "PUT"
                resultats.append({
                    "strategy":name,"label":r["label"],"direction":direction,"score":best,
                    "raisons":r["raisons_call"] if direction=="CALL" else r["raisons_put"],
                    "details":r["details_txt"]
                })
            if not resultats: continue
            # Baseline V19 = meilleur pilier, mais TOUS les candidats sont journalisés.
            best=max(resultats,key=lambda x:x["score"])
            ok_corr, corr_msg=verifier_correlation(symbole,best["direction"])
            if not ok_corr: continue
            score_algo=round(5+(best["score"]/100)*5,1)
            duration=180 if (mode=="STANDARD" and tf==300) else (tf if mode=="STANDARD" else 60)
            return {
                "action":best["direction"],"strategy":best["strategy"],"label":best["label"],
                "score":best["score"],"score_algo":score_algo,"tf":tf,"duration":duration,
                "details":best["details"],"reasons":best["raisons"],"correlation":corr_msg,
                "candidates":resultats,"signal_epoch":int(df.epoch.iloc[-1])
            }
        except Exception:
            continue
    return None

# ============================================================
# PAPER TRADING LIVE
# ============================================================

def planifier_paper(chat_id,symbole,analysis):
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
# BACKTEST LAB V19.2 — SIMULATION HISTORIQUE MASSIVE
# ============================================================

LAB_CANDLES = int(os.environ.get("LAB_CANDLES", "5000"))
LAB_MIN_TRADES = int(os.environ.get("LAB_MIN_TRADES", "30"))
LAB_TF_LIST = (60, 120, 300, 600)
LAB_EXPIRY_SECONDS = {60: 60, 120: 120, 300: 180, 600: 600}


def lab_duration_bars(tf):
    """Approximation propre de l'expiration en bougies du timeframe testé."""
    sec = LAB_EXPIRY_SECONDS.get(tf, tf)
    # Pour 300s/180s, une sortie exacte nécessite des données 60s.
    # Ici on conserve une simulation conservatrice d'au moins 1 bougie.
    return max(1, round(sec / tf))


def backtest_strategy(df, strategy_name, duration_bars=1):
    """Backtest sans look-ahead : le signal ne voit que les bougies déjà closes."""
    fn = STRATEGIES[strategy_name]
    rows = []
    min_history = 70
    last_i = len(df) - duration_bars
    for i in range(min_history, last_i):
        sample = df.iloc[:i].copy()
        try:
            r = fn(sample)
        except Exception:
            r = None
        if not r:
            continue
        score_call = float(r.get("score_call", 0))
        score_put = float(r.get("score_put", 0))
        score = max(score_call, score_put)
        if score < SEUIL_SIGNAL_PILIER:
            continue
        direction = "CALL" if score_call >= score_put else "PUT"
        entry = float(df.close.iloc[i-1])
        exit_px = float(df.close.iloc[i-1+duration_bars])
        win = (direction == "CALL" and exit_px > entry) or (direction == "PUT" and exit_px < entry)
        result = "WIN" if win else "LOSS"
        rows.append({
            "epoch": int(df.epoch.iloc[i-1]),
            "direction": direction,
            "score": score,
            "score_bucket": int(min(100, max(0, score)) // 5 * 5),
            "entry": entry,
            "exit": exit_px,
            "result": result,
        })
    return rows


def summarize_rows(rows):
    n = len(rows)
    wins = sum(x["result"] == "WIN" for x in rows)
    losses = n - wins
    calls = [x for x in rows if x["direction"] == "CALL"]
    puts = [x for x in rows if x["direction"] == "PUT"]
    def wr(a):
        return (sum(x["result"] == "WIN" for x in a) / len(a) * 100) if a else 0.0
    # Séries de pertes/gains pour repérer les phases instables.
    max_loss_streak = max_win_streak = cur_loss = cur_win = 0
    for x in rows:
        if x["result"] == "LOSS":
            cur_loss += 1; cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)
        else:
            cur_win += 1; cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
    buckets = {}
    for x in rows:
        b = int(min(100, max(0, x.get("score", 0))) // 5 * 5)
        buckets.setdefault(b, []).append(x)
    score_buckets = {b: {"trades": len(v), "winrate": wr(v)} for b, v in sorted(buckets.items())}
    return {
        "trades": n, "wins": wins, "losses": losses,
        "winrate": wr(rows), "call_trades": len(calls), "call_wr": wr(calls),
        "put_trades": len(puts), "put_wr": wr(puts),
        "max_loss_streak": max_loss_streak, "max_win_streak": max_win_streak,
        "score_buckets": score_buckets,
        "rows": rows,
    }


def print_lab_report(asset, tf, name, summary, split_name="ALL"):
    print("\n" + "="*78)
    print(f"V19.2 LAB | {asset} | TF={tf}s | {name} | {split_name}")
    print("-"*78)
    print(f"Trades={summary['trades']} | WIN={summary['wins']} | LOSS={summary['losses']} | Winrate={summary['winrate']:.2f}%")
    print(f"CALL={summary['call_trades']} ({summary['call_wr']:.2f}%) | PUT={summary['put_trades']} ({summary['put_wr']:.2f}%)")
    print(f"Max loss streak={summary['max_loss_streak']} | Max win streak={summary['max_win_streak']}")
    print("="*78)


def run_backtest(asset, tf, only_strategy=None, count=LAB_CANDLES):
    raw = obtenir_donnees_deriv(asset, tf, count=count)
    if not raw:
        return None
    df = closed_only(candles_df(raw))
    duration_bars = lab_duration_bars(tf)
    names = [only_strategy] if only_strategy else list(STRATEGIES)
    output = {}
    for name in names:
        if name not in STRATEGIES:
            continue
        rows = backtest_strategy(df, name, duration_bars)
        output[name] = summarize_rows(rows)
    return output


def _telegram_chunks(text, limit=3900):
    chunks=[]
    current=""
    for line in text.splitlines(True):
        if len(current)+len(line) > limit and current:
            chunks.append(current.rstrip())
            current=""
        current += line
    if current.strip(): chunks.append(current.rstrip())
    return chunks

def send_lab_report(chat_id, text):
    for chunk in _telegram_chunks(text):
        bot.send_message(chat_id, chunk)

def format_lab_telegram(global_report):
    lines=["🧪 V19.2 — RAPPORT DÉTAILLÉ", "", f"Simulations stratégie/TF : {len(global_report)}", ""]
    for asset, tf, name, s in global_report:
        lines += [
            f"📌 {asset} | TF {tf}s | {name}",
            f"Trades: {s['trades']} | WIN: {s['wins']} | LOSS: {s['losses']}",
            f"Winrate: {s['winrate']:.2f}%",
            f"CALL: {s['call_trades']} ({s['call_wr']:.2f}%) | PUT: {s['put_trades']} ({s['put_wr']:.2f}%)",
            f"Max pertes consécutives: {s['max_loss_streak']} | Max gains: {s['max_win_streak']}",
        ]
        rows=s.get('rows',[])
        cut=int(len(rows)*0.70)
        if len(rows)>=LAB_MIN_TRADES and len(rows)-cut:
            test=summarize_rows(rows[cut:])
            lines.append(f"OOS 30%: {test['trades']} trades | {test['winrate']:.2f}%")
        if s.get('score_buckets'):
            bucket_txt=[]
            for b,v in s['score_buckets'].items():
                bucket_txt.append(f"{b}-{min(100,b+4)}: {v['trades']} / {v['winrate']:.1f}%")
            lines.append("Scores: " + " · ".join(bucket_txt))
        lines.append("")
    lines += ["⚠️ Simulation historique directionnelle. Payout, spread, slippage et exécution réelle non inclus.", "⚠️ Aucun ordre réel / Martingale."]
    return "\n".join(lines)

def run_mass_lab(assets, tfs=LAB_TF_LIST, count=LAB_CANDLES):
    """Lance toutes les simulations et écrit un rapport exploitable dans Render."""
    print("\n" + "#"*90)
    print("V19.2 MASSIVE BACKTEST LAB — START")
    print(f"Assets={len(assets)} | Timeframes={list(tfs)} | Candles/request={count}")
    print("Martingale=OFF | Manual WIN=OFF | Look-ahead=OFF")
    print("#"*90)
    global_report = []
    for asset in assets:
        for tf in tfs:
            try:
                res = run_backtest(asset, tf, None, count=count)
            except Exception as e:
                print(f"[LAB ERROR] {asset} TF={tf}: {type(e).__name__}: {e}")
                continue
            if not res:
                print(f"[LAB DATA] {asset} TF={tf}: données indisponibles")
                continue
            for name, summary in res.items():
                print_lab_report(asset, tf, name, summary)
                global_report.append((asset, tf, name, summary))
                # Découpage chronologique 70/30 pour vérifier la stabilité temporelle.
                rows = summary["rows"]
                cut = int(len(rows) * 0.70)
                if len(rows) >= LAB_MIN_TRADES and cut > 0 and len(rows)-cut > 0:
                    train = summarize_rows(rows[:cut])
                    test = summarize_rows(rows[cut:])
                    print_lab_report(asset, tf, name, train, "TRAIN 70%")
                    print_lab_report(asset, tf, name, test, "OOS 30%")
    print("\n" + "#"*90)
    print("V19.2 MASSIVE BACKTEST LAB — FINAL SUMMARY")
    print("#"*90)
    for asset, tf, name, s in global_report:
        flag = "OK_SAMPLE" if s["trades"] >= LAB_MIN_TRADES else "SMALL_SAMPLE"
        print(f"{asset:8} TF={tf:3}s {name:14} trades={s['trades']:4} winrate={s['winrate']:6.2f}% {flag} maxLS={s['max_loss_streak']:2}")
    print("#"*90 + "\n")
    return global_report


@bot.message_handler(commands=["lab"])
def cmd_lab(message):
    if not est_autorise(message.chat.id):
        return
    p = message.text.split()
    asset_arg = p[1].upper() if len(p) > 1 else "EURUSD"
    if asset_arg == "ALL":
        assets = ALL_PAIRS
    elif asset_arg in ALL_PAIRS:
        assets = [asset_arg]
    else:
        return bot.send_message(message.chat.id, "Usage : /lab EURUSD ou /lab ALL")
    bot.send_message(message.chat.id,
                     f"🧪 V19.2 LAB lancé.\nAssets : {len(assets)}\nTF : 60/120/300/600s\nBougies : {LAB_CANDLES}\n\nLes résultats détaillés vont apparaître dans les logs Render.")
    def worker():
        try:
            report = run_mass_lab(assets)
            bot.send_message(message.chat.id, f"✅ V19.2 LAB terminé. {len(report)} simulations stratégie/TF analysées.\n\n📋 Je t'envoie maintenant le rapport détaillé en plusieurs messages si nécessaire.")
            send_lab_report(message.chat.id, format_lab_telegram(report))
        except Exception as e:
            print(f"[LAB FATAL] {type(e).__name__}: {e}")
            bot.send_message(message.chat.id, f"❌ LAB interrompu : {type(e).__name__}: {e}")
    threading.Thread(target=worker, daemon=True).start()

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
    if asset not in ALL_PAIRS or tf not in LAB_TF_LIST:
        return bot.send_message(message.chat.id,"Usage : /backtest EURUSD 300 [AROON_RSI]")
    bot.send_message(message.chat.id,"🧪 Backtest V19.2 en cours...")
    res=run_backtest(asset,tf,strat,count=LAB_CANDLES)
    if res is None:return bot.send_message(message.chat.id,"❌ Données Deriv indisponibles.")
    txt=f"🧪 **BACKTEST V19.2 — {asset} / {tf}s**\n──────────────\n"
    for name,s in res.items():
        txt+=f"**{name}**\nTrades: {s['trades']} · WIN: {s['wins']} · LOSS: {s['losses']} · Winrate: {s['winrate']:.2f}% · Max LS: {s['max_loss_streak']}\n"
        if s['trades'] >= LAB_MIN_TRADES:
            rows=s['rows']; cut=int(len(rows)*0.70); test=summarize_rows(rows[cut:]) if len(rows)-cut else None
            if test: txt+=f"OOS 30%: {test['trades']} trades · {test['winrate']:.2f}%\n"
        txt+="\n"
    txt+="⚠️ Historique simulé, pas une garantie future. Le payout broker n'est pas inclus."
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
        "🧪 **TERMINAL PRIME V19 — LABORATOIRE**\n\n"
        "4 piliers conservés : Aroon+RSI, ADX+STC, CCI+MACD, Donchian+CCI.\n\n"
        "✅ Bougies clôturées uniquement\n✅ Journal SQLite persistant\n✅ Backtest individuel\n✅ Paper trading\n🚫 Martingale désactivée\n🚫 WIN manuel désactivé\n🚫 Fausse confiance en %\n\n"
        "Commandes : `/stats`, `/backtest EURUSD 300 AROON_RSI`, `/lab EURUSD` ou `/lab ALL`",
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
    if est_autorise(message.chat.id):bot.send_message(message.chat.id,"Exemple : `/lab EURUSD`, `/lab ALL` ou `/backtest EURUSD 300 ADX_STC`",parse_mode="Markdown")

@bot.message_handler(func=lambda m:m.text.startswith("💎 SIGNAUX VIP"))
def toggle_vip(message):
    if not est_autorise(message.chat.id):return
    uid=message.chat.id;filtre_vip_actif[uid]=not filtre_vip_actif.get(uid,False)
    bot.send_message(uid,"💎 VIP ON — score ≥ 9/10" if filtre_vip_actif[uid] else "🔓 VIP OFF — seuil standard",reply_markup=clavier(uid))

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
