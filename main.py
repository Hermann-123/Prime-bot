"""
╔════════════════════════════════════════════════════════════════════════════╗
║                   TERMINAL PRIME V37 — DUAL STRATEGY                       ║
║              Stratégie 1 (Kasper OTE Strict) + Stratégie 2 (OTE Agressif)  ║
║                   + Système de Confiance + Volatility Toggle               ║
╚════════════════════════════════════════════════════════════════════════════╝
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

# ==========================================
# CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAHcc-tRXHGjtJ_dGkNtiZoAhDVy0H-A98Q"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# LISTES DE PAIRES — V37 (VOLATILITY RÉACTIVÉ)
# ==========================================

VOLATILE_PAIRS = ["V10","V25","V50","V75","V100"]  # ✅ Réactivés en V37
COMMODITY_PAIRS = ["XAUUSD","XAGUSD"]  # ✅ Gold + Argent UNIQUEMENT (Pétrole supprimé)
FOREX_PAIRS = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD","AUDJPY",
               "EURAUD","EURUSD","AUDCAD","USDCHF","CADCHF","EURCHF",
               "USDJPY","GBPUSD"]

# MT5 Mode = Volatility + Gold + Argent UNIQUEMENT
ELITE_PAIRS_MT5 = VOLATILE_PAIRS + COMMODITY_PAIRS

ALL_PAIRS = VOLATILE_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS

NOMS_AFFICHAGE = {
    "XAUUSD":"🥇 GOLD", "XAGUSD":"🥈 ARGENT",
    "V10":"🔥 V10", "V25":"🔥 V25", "V50":"🔥 V50",
    "V75":"⚡ V75", "V100":"💥 V100",
}

# ==========================================
# VARIABLES D'ÉTAT
# ==========================================

user_prefs = {}
plateforme_trading = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
signaux_cache = {}

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0}

# ✅ V37: Toggle Volatility Scan
volatility_scan_active = True

# ✅ V37: Tracking des 2 stratégies
signaux_strategie_1 = {}  # Kasper OTE Strict
signaux_strategie_2 = {}  # OTE Scalping Agressif
confidence_scores = {}     # Score de confiance pour chaque signal

# ==========================================
# KEEP ALIVE
# ==========================================

app = Flask(__name__)
@app.route('/')
def home(): 
    return "Terminal Prime V37 (Dual Strategy + Volatility Toggle)"

def run(): 
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive(): 
    Thread(target=run, daemon=True).start()

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
# ✅ V37: COMMANDE /Volatility TOGGLE
# ==========================================

@bot.message_handler(commands=['Volatility'])
def toggle_volatility(message):
    global volatility_scan_active
    
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "❌ Admin only.", parse_mode="Markdown")
    
    if volatility_scan_active:
        # Désactiver
        volatility_scan_active = False
        response = "⛔ **Volatility restreinte.** Scan des indices Volatility désactivé."
    else:
        # Activer
        volatility_scan_active = True
        response = "✅ **Scan des indices Volatility activé.**"
    
    bot.send_message(message.chat.id, response, parse_mode="Markdown")

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
        paires_actives += PAIRES_SESSION_Londres
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

def est_gold_ou_index_actif():
    now = datetime.datetime.utcnow()
    j = now.weekday()
    h = now.hour + now.minute/60.0
    weekend = (j==4 and h>=21) or j==5 or (j==6 and h<21)
    return not weekend

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
    # ✅ V37: Volatility check
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
        if est_gold_ou_index_actif(): 
            return "AUTORISE", ""
        return "BLOCAGE_TOTAL", "Week-end"
    
    session, paires_session = get_session_active()
    if session is None: 
        return "HORS_SESSION", "🔒 Hors Killzone"
    if symbole in paires_session: 
        return "AUTORISE", ""
    
    return "HORS_SESSION", f"🔒 {symbole} inactif en session {session}"

# ==========================================
# HYBRID DATA ENGINE (FMP + DERIV FALLBACK)
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
    
    # Fallback Deriv
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

def obtenir_prix_actuel_deriv(symbole_brut):
    if symbole_brut in ALL_PAIRS:
        mapping_fmp = {
            "XAUUSD": "FOREX:XAUUSD",
            "XAGUSD": "FOREX:XAGUSD",
        }
        sym_fmp = mapping_fmp.get(symbole_brut, symbole_brut)
        
        try:
            url = f"https://financialmodelingprep.com/api/v3/quote/{sym_fmp}?apikey={FMP_API_KEY}"
            res = requests.get(url, timeout=5).json()
            if isinstance(res, list) and len(res) > 0:
                return float(res[0]["price"])
        except Exception as e:
            print(f"[FMP Quote Error - {symbole_brut}] {e}", flush=True)
    
    # Fallback Deriv
    sym = prefixer_symbole(symbole_brut)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history":sym,"end":"latest","count":1,"style":"ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]:
                return float(res["history"]["prices"][0])
        except:
            try: 
                ws.close()
            except: 
                pass
            time.sleep(0.3)
    return None

# ==========================================
# ✅ V37: SYSTÈME DE CONFIANCE (Confidence Score)
# ==========================================

def calculer_score_confiance(symbole, tendance, force_ema, rr_ratio, reaction_type, volatilite):
    """
    Calcule un score de confiance 0-100% basé sur plusieurs critères.
    """
    score = 50  # Base
    
    # Critère 1: Force EMA
    if "FORT" in force_ema:
        score += 20
    elif "MODÉRÉ" in force_ema:
        score += 10
    else:
        score -= 15
    
    # Critère 2: R/R Ratio
    if rr_ratio >= 2.0:
        score += 15
    elif rr_ratio >= 1.5:
        score += 10
    else:
        score -= 10
    
    # Critère 3: Type de réaction
    if "Engulfing" in reaction_type:
        score += 15
    elif "Pin Bar" in reaction_type:
        score += 12
    elif "Rejet" in reaction_type:
        score += 8
    else:
        score -= 10
    
    # Critère 4: Volatilité (adaptative)
    if volatilite < 0.7:
        score += 5  # Basse volatilité = plus stable
    elif volatilite > 1.5:
        score -= 10  # Haute volatilité = risqué
    
    # Critère 5: Session/Killzone
    if dans_killzone():
        score += 5
    else:
        score -= 10
    
    # Clamper entre 0 et 100
    score = max(0, min(100, score))
    
    return score

# ==========================================
# STRATÉGIE 1: KASPER OTE STRICT (Confiance >= 75%)
# ==========================================

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

def analyser_strategie_1_kasper(symbole):
    """
    STRATÉGIE 1: Kasper OTE STRICT
    Confiance requise: 75%+
    Critères: EMA FORT + Fibonacci 0.618/0.786 + Réaction claire
    """
    c5 = obtenir_donnees_deriv(symbole, 300)
    c1h= obtenir_donnees_deriv(symbole, 3600)
    if not c5 or not c1h: 
        return None
    try:
        df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
        dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
        tendance, force = calculer_ema_cloud(dfh)
        
        # FILTRE STRICT: Seulement tendance FORTE
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
        
        # FILTRE STRICT: R/R >= 1.5
        if rr < 1.5: 
            return None
        
        # Calculer volatilité (ATR simple)
        atr = (dfh['high'] - dfh['low']).rolling(14).mean().iloc[-1]
        volatilite = atr / px if px > 0 else 1.0
        
        # Calculer score de confiance
        confiance = calculer_score_confiance(symbole, tendance, force, rr, msg_r, volatilite)
        
        # RETOURNER SEULEMENT si confiance >= 75%
        if confiance < 75:
            return None
        
        return {
            "action": "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force":force, "msg":msg_r,
            "sh":round(sh,5), "sl_swing":round(sl,5),
            "zone":zone, "sl":zone["sl"], "tp1":zone["tp_1r"],
            "tp":zone["tp_15r"], "rr":rr, "px":round(px,5),
            "kz":nom_killzone(),
            "strategie": 1,
            "confiance": confiance
        }
    except Exception as e:
        print(f"[Kasper/{symbole}] {e}", flush=True)
    return None

# ==========================================
# STRATÉGIE 2: OTE SCALPING AGRESSIF (Confiance >= 55%)
# ==========================================

def analyser_strategie_2_scalp(symbole):
    """
    STRATÉGIE 2: OTE Scalping AGRESSIF
    Confiance requise: 55%+
    Critères: EMA MODÉRÉ acceptable + Fibonacci flexible + Réaction acceptable
    """
    c5 = obtenir_donnees_deriv(symbole, 300)
    c1h= obtenir_donnees_deriv(symbole, 3600)
    if not c5 or not c1h: 
        return None
    try:
        df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
        dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
        tendance, force = calculer_ema_cloud(dfh)
        
        # FILTRE AGRESSIF: Accepter MODÉRÉ aussi
        # (pas de restriction sur la force)
        
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
        
        # FILTRE AGRESSIF: R/R >= 1.3 (moins strict)
        if rr < 1.3: 
            return None
        
        # Calculer volatilité
        atr = (dfh['high'] - dfh['low']).rolling(14).mean().iloc[-1]
        volatilite = atr / px if px > 0 else 1.0
        
        # Calculer score de confiance
        confiance = calculer_score_confiance(symbole, tendance, force, rr, msg_r, volatilite)
        
        # RETOURNER SEULEMENT si confiance >= 55%
        if confiance < 55:
            return None
        
        return {
            "action": "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force":force, "msg":msg_r,
            "sh":round(sh,5), "sl_swing":round(sl,5),
            "zone":zone, "sl":zone["sl"], "tp1":zone["tp_1r"],
            "tp":zone["tp_15r"], "rr":rr, "px":round(px,5),
            "kz":nom_killzone(),
            "strategie": 2,
            "confiance": confiance
        }
    except Exception as e:
        print(f"[Scalping/{symbole}] {e}", flush=True)
    return None

def nettoyer_trades_bloques():
    now = time.time()
    for uid in list(trades_en_cours.keys()):
        t = trades_en_cours[uid]
        if now - t.get('ts', now) > t.get('dur',300)+120: 
            del trades_en_cours[uid]

# ==========================================
# ✅ V37: SCANNER AUTOMATIQUE (2 STRATÉGIES)
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            nettoyer_trades_bloques()
            libres = [u for u in utilisateurs_actifs if est_autorise(u) and u not in trades_en_cours]
            if not libres: 
                continue
            
            # Scanner toutes les paires
            paires_a_scanner = ELITE_PAIRS_MT5 + FOREX_PAIRS
            
            for paire in paires_a_scanner:
                statut,_ = est_symbole_autorise(paire)
                if statut != "AUTORISE": 
                    continue
                
                # ✅ STRATÉGIE 1: Kasper OTE STRICT
                res1 = analyser_strategie_1_kasper(paire)
                if res1:
                    cle = f"{paire}_STR1"
                    signaux_strategie_1[cle] = res1
                    derniere_alerte_auto[cle] = time.time()
                    
                    px = obtenir_prix_actuel_deriv(paire) or res1['px']
                    signaux_cache[cle] = {
                        'time':time.time(), 'action':res1['action'],
                        'mt5_sl':res1['sl'], 'mt5_tp':res1['tp'],
                        'mt5_tp1':res1['tp1'], 'mt5_rr':res1['rr'],
                        'zone':res1['zone'], 'sh':res1['sh'],
                        'sl_swing':res1['sl_swing'], 'force':res1['force'],
                        'msg':res1['msg'], 'kz':res1['kz'],
                        'dur':300, 'confiance':res1['confiance'],
                        'strategie':1
                    }
                    
                    nom = NOMS_AFFICHAGE.get(paire, f"{paire[:3]}/{paire[3:]}")
                    dir_ = "🟢 BUY" if "BUY" in res1['action'] else "🔴 SELL"
                    z = res1['zone']
                    
                    for uid in libres:
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
                            f"⏰ {res1['kz']}\n"
                            f"🔶 Zone OTE : {z['ote_bas']:.5f} → {z['ote_haut']:.5f}\n"
                            f"⚖️ R/R : {res1['rr']}R\n"
                            f"🎖️ Confiance : {res1['confiance']}%"
                        )
                        try: 
                            bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                        except: 
                            pass
                
                # ✅ STRATÉGIE 2: OTE Scalping AGRESSIF
                res2 = analyser_strategie_2_scalp(paire)
                if res2:
                    cle = f"{paire}_STR2"
                    signaux_strategie_2[cle] = res2
                    derniere_alerte_auto[cle] = time.time()
                    
                    px = obtenir_prix_actuel_deriv(paire) or res2['px']
                    signaux_cache[cle] = {
                        'time':time.time(), 'action':res2['action'],
                        'mt5_sl':res2['sl'], 'mt5_tp':res2['tp'],
                        'mt5_tp1':res2['tp1'], 'mt5_rr':res2['rr'],
                        'zone':res2['zone'], 'sh':res2['sh'],
                        'sl_swing':res2['sl_swing'], 'force':res2['force'],
                        'msg':res2['msg'], 'kz':res2['kz'],
                        'dur':300, 'confiance':res2['confiance'],
                        'strategie':2
                    }
                    
                    nom = NOMS_AFFICHAGE.get(paire, f"{paire[:3]}/{paire[3:]}")
                    dir_ = "🟢 BUY" if "BUY" in res2['action'] else "🔴 SELL"
                    z = res2['zone']
                    
                    for uid in libres:
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
                            f"⏰ {res2['kz']}\n"
                            f"🔶 Zone OTE : {z['ote_bas']:.5f} → {z['ote_haut']:.5f}\n"
                            f"⚖️ R/R : {res2['rr']}R\n"
                            f"🎖️ Confiance : {res2['confiance']}%"
                        )
                        try: 
                            bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                        except: 
                            pass
        
        except Exception as e:
            print(f"[Scanner] ⚠️ {e}", flush=True)

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
    bot.send_message(uid,
        f"🏴‍☠️ **TERMINAL PRIME V37** 🔥\n"
        f"──────────────────\n"
        f"✅ **Dual Strategy System:**\n"
        f"  🔹 Stratégie 1: Kasper OTE STRICT (75%+)\n"
        f"  🔹 Stratégie 2: OTE Scalping AGRESSIF (55%+)\n"
        f"──────────────────\n"
        f"📈 **Mode MT5 (Volatility + Metals):**\n"
        f"  ⚡ V10, V25, V50, V75, V100 | {volatility_status}\n"
        f"  🥇 Gold | 🥈 Argent\n"
        f"──────────────────\n"
        f"⏰ **Killzone:** {kz}",
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
        f"🔥 Volatility : {volatility_status}\n\n"
        f"💡 Utilisez /Volatility pour activer/désactiver",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE","📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): 
        return
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
        markup.add(InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"))
        bot.send_message(uid,"🎯 Sélectionne ta cible Pocket Forex :",reply_markup=markup,parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text=="🚀 LANCER L'ANALYSE")
def lancer(message):
    uid = message.chat.id
    if not est_autorise(uid): 
        return
    nettoyer_trades_bloques()
    if uid in trades_en_cours: 
        return bot.send_message(uid,"⚠️ Trade en cours.")
    actif = user_prefs.get(message.from_user.id)
    if not actif: 
        return bot.send_message(uid,"⚠️ Choisis d'abord une cible !")
    save_devise(type('obj',(object,),{'data':f"set_{actif}",'message':message,'from_user':message.from_user,'id':0})())

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): 
        return
    nettoyer_trades_bloques()
    if uid in trades_en_cours:
        try: 
            bot.answer_callback_query(call.id,"⚠️ Trade en cours !",show_alert=True)
        except: 
            pass
        return
    
    actif = call.data.replace("set_","")
    user_prefs[getattr(call,'from_user',type('o',(object,),{'id':uid})()).id] = actif
    
    try: 
        bot.delete_message(uid, call.message.message_id)
    except: 
        pass
    
    # Chercher le signal (de n'importe quelle stratégie)
    cle1 = f"{actif}_STR1"
    cle2 = f"{actif}_STR2"
    
    cache = signaux_cache.get(cle1) or signaux_cache.get(cle2)
    
    if not cache or (time.time()-cache['time']) > 90:
        return bot.send_message(uid, f"⏱️ Signal OTE expiré sur {NOMS_AFFICHAGE.get(actif,actif)}\nAttends le radar auto.", parse_mode="Markdown")
    
    px = obtenir_prix_actuel_deriv(actif) or 0
    nom = NOMS_AFFICHAGE.get(actif, actif)
    z = cache.get('zone',{})
    dir_ = "🟢 BUY MARKET" if "BUY" in cache['action'] else "🔴 SELL MARKET"
    
    fmt = ".0f" if actif in VOLATILE_PAIRS else ".5f"
    strat_label = "STRATÉGIE 1 (STRICT)" if cache['strategie'] == 1 else "STRATÉGIE 2 (AGRESSIF)"
    
    signal = (
        f"🎯 {strat_label} — {nom}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{dir_}\n"
        f"☁️ EMA Cloud : {cache.get('force','—')}\n"
        f"🔑 {cache.get('kz','—')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Swing H : {cache.get('sh',0):{fmt}}\n"
        f"📍 Swing L : {cache.get('sl_swing',0):{fmt}}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟡 Zone OTE (0.618–0.786) :\n"
        f" {z.get('ote_bas',0):{fmt}} → {z.get('ote_haut',0):{fmt}}\n"
        f"💰 Prix : {px:{fmt}}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 SL : {cache['mt5_sl']:{fmt}}\n"
        f"🎯 TP 1R : {cache['mt5_tp1']:{fmt}}\n"
        f"🚀 TP 1.5R : {cache['mt5_tp']:{fmt}}\n"
        f"⚖️ R/R : {cache['mt5_rr']:.2f}R\n"
        f"🎖️ Confiance : {cache.get('confiance',0)}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{cache.get('msg','—')}"
    )
    bot.send_message(uid, signal, parse_mode="Markdown")

@bot.message_handler(commands=['kasper'])
def cmd_kasper(message):
    uid = message.chat.id
    if not est_autorise(uid): 
        return
    parts = message.text.split()
    symbole = parts[1].upper() if len(parts)>1 else "XAUUSD"
    if symbole not in ALL_PAIRS:
        return bot.send_message(uid,"❌ Symbole non reconnu.\nEx: /kasper XAUUSD | /kasper V10")
    
    msg_obj = bot.send_message(uid,f"🔍 Analyse Kasper OTE sur {NOMS_AFFICHAGE.get(symbole,symbole)}...",parse_mode="Markdown")
    res = analyser_strategie_1_kasper(symbole)
    nom = NOMS_AFFICHAGE.get(symbole, symbole)
    
    fmt = ".0f" if symbole in VOLATILE_PAIRS else ".5f"
    
    if not res:
        c5 = obtenir_donnees_deriv(symbole,300)
        c1h = obtenir_donnees_deriv(symbole,3600)
        if c5 and c1h:
            df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
            dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
            t,f = calculer_ema_cloud(dfh)
            sh,sl = trouver_dernier_swing(df5,t)
            z = calculer_zone_ote(sh,sl,t)
            px = obtenir_prix_actuel_deriv(symbole) or df5['close'].iloc[-1]
            kz = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
            texte=(f"👁️ KASPER OTE — {nom}\n━━━━━━━━━━━━━━━━━━━━━━\n"
                   f"☁️ EMA Cloud H1 : {f} ({'🟢 BULL' if t=='BULL' else '🔴 BEAR'})\n"
                   f"📍 Swing H/L : {sh:{fmt}} / {sl:{fmt}}\n"
                   f"🟡 Zone OTE : {z['ote_bas']:{fmt}} → {z['ote_haut']:{fmt}}\n"
                   f"💰 Prix : {px:{fmt}}\n"
                   f"🛑 SL : {z['sl']:{fmt}} | 🚀 TP 1.5R : {z['tp_15r']:{fmt}}\n"
                   f"━━━━━━━━━━━━━━━━━━━━━━\n"
                   f"⏳ En attente de réaction.\n"
                   f"⏰ {kz}")
        else:
            texte = "⚠️ Données indisponibles. Réessaie."
        return bot.edit_message_text(texte,uid,msg_obj.message_id,parse_mode="Markdown")
    
    z = res['zone']
    kz_ = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    texte=(f"🎯 KASPER OTE — {nom}\n━━━━━━━━━━━━━━━━━━━━━━\n"
           f"{'🟢 BUY' if res['tendance']=='BULL' else '🔴 SELL'}\n"
           f"☁️ EMA : {res['force']} | ⏰ {kz_}\n"
           f"━━━━━━━━━━━━━━━━━━━━━━\n"
           f"📍 Swing H : {res['sh']:{fmt}} | L : {res['sl_swing']:{fmt}}\n"
           f"🟡 Zone OTE : {z['ote_bas']:{fmt}} → {z['ote_haut']:{fmt}}\n"
           f"💰 Prix : {res['px']:{fmt}}\n"
           f"━━━━━━━━━━━━━━━━━━━━━━\n"
           f"🛑 SL : {res['sl']:{fmt}}\n"
           f"🎯 TP 1R : {res['tp1']:{fmt}}\n"
           f"🚀 TP 1.5R : {res['tp']:{fmt}}\n"
           f"⚖️ R/R : {res['rr']}R\n"
           f"🎖️ Confiance : {res['confiance']}%\n"
           f"━━━━━━━━━━━━━━━━━━━━━━\n"
           f"{res['msg']}")
    bot.edit_message_text(texte,uid,msg_obj.message_id,parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================

if __name__=="__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    print("⬛ TERMINAL PRIME V37 — Dual Strategy + Volatility Toggle activé.", flush=True)
    bot.infinity_polling()

