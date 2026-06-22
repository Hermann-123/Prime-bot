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
from threading import Thread, Timer

# ==========================================
# CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAGWGSnc4ExpiiK1Vvdt2Xcb0O-0013GuCg"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")
COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3

# ==========================================
# VARIABLES D'ÉTAT
# ==========================================

user_prefs = {}
mode_trading = {}
plateforme_trading = {}
filtre_special = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
signaux_cache = {}
cooldown_actifs = {}
niveaux_martingale = {}
historique_signaux = {}
gold_trades_actifs = {}
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}

SYNTHETIC_PAIRS = ["V10", "V25", "V50", "V75", "V100"]
COMMODITY_PAIRS = ["XAUUSD", "XAGUSD", "USOUSD"]
CRYPTO_PAIRS    = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS     = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD",
                   "AUDJPY","EURAUD","EURUSD","AUDCAD","USDCHF",
                   "CADCHF","EURCHF","USDJPY"]
ELITE_PAIRS_MT5  = SYNTHETIC_PAIRS + COMMODITY_PAIRS
ALL_PAIRS_POCKET = SYNTHETIC_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS + CRYPTO_PAIRS

# ==========================================
# PROFILS — VERSION FINALE CORRIGÉE
# ==========================================

def obtenir_profil_actif(symbole):
    if symbole in SYNTHETIC_PAIRS:
        return {"stoch_achat":30,"rsi_achat":35,"stoch_vente":70,"rsi_vente":65,
                "vol_multiplier":2.5,"rr_min":1.8,"cooldown_otm":900,"nom":"SMC Synthétiques"}
    elif symbole in COMMODITY_PAIRS:
        return {"stoch_achat":25,"rsi_achat":40,"stoch_vente":75,"rsi_vente":60,
                "vol_multiplier":2.0,"rr_min":2.0,"cooldown_otm":1200,"nom":"SMC Métaux/Énergie"}
    else:
        # ✅ FIX : seuils Forex réalistes pour générer des signaux
        return {"stoch_achat":42,"rsi_achat":48,"stoch_vente":58,"rsi_vente":52,
                "vol_multiplier":2.5,"rr_min":1.2,"cooldown_otm":600,"nom":"SMC Forex"}

# ==========================================
# KEEP ALIVE
# ==========================================

app = Flask(__name__)
@app.route('/') 
def home(): return "Terminal Prime V29 FINAL"
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

# ==========================================
# ACCÈS VIP
# ==========================================

def est_autorise(user_id):
    if user_id == ADMIN_ID: return True
    if user_id in utilisateurs_autorises:
        exp = utilisateurs_autorises[user_id]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: return True
        del utilisateurs_autorises[user_id]
        try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ**", parse_mode="Markdown")
        except: pass
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    try:
        arg = message.text.split()[1].lower()
        jours = {"1s":7,"2s":14,"1m":30,"3m":90,"vie":"LIFETIME"}.get(arg, int(arg))
        cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cles_generees[cle] = jours
        dur = "À VIE 👑" if jours == "LIFETIME" else f"{jours} jours"
        bot.send_message(message.chat.id, f"✅ **CLÉ :** `{cle}`\n⏳ **Durée :** {dur}", parse_mode="Markdown")
    except: pass

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    chat_id = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle not in cles_generees:
            return bot.send_message(chat_id, "❌ Clé invalide.", parse_mode="Markdown")
        jours = cles_generees.pop(cle)
        if jours == "LIFETIME":
            utilisateurs_autorises[chat_id] = "LIFETIME"
            exp_txt = "À VIE 👑"
        else:
            exp = datetime.datetime.now() + datetime.timedelta(days=jours)
            utilisateurs_autorises[chat_id] = exp
            exp_txt = exp.strftime('%d/%m/%Y %H:%M')
        bot.send_message(chat_id, f"🎉 **ACCÈS DÉVERROUILLÉ !**\n⏳ Fin : {exp_txt}\n\n👉 /start", parse_mode="Markdown")
    except: pass

# ==========================================
# VERROUILLAGE TEMPOREL
# ==========================================

def est_symbole_autorise(symbole):
    if symbole in SYNTHETIC_PAIRS: return "AUTORISE", ""
    now = datetime.datetime.utcnow()
    j = now.weekday()
    h = now.hour + now.minute/60.0
    weekend = (j==4 and h>=21) or j==5 or (j==6 and h<21)
    if weekend:
        return ("AUTORISE","") if symbole in CRYPTO_PAIRS else ("BLOCAGE_TOTAL","Weekend")
    if symbole in CRYPTO_PAIRS: return "BLOCAGE_TOTAL", "Cryptos semaine"
    if h >= 17.5: return "HORS_SESSION", "Couvre-feu 17h30"
    if 0 <= h < 8:
        ok = ["AUDJPY","CADJPY","CHFJPY","USDJPY","AUDCAD","XAUUSD","XAGUSD","USOUSD"]
        return ("AUTORISE","") if symbole in ok else ("HORS_SESSION","Hors Asie")
    if 7 <= h < 12:
        ok = ["EURUSD","EURJPY","EURAUD","EURCHF","USDCHF","CADCHF","XAUUSD","XAGUSD","USOUSD"]
        if h < 8: ok += ["AUDJPY","CADJPY","CHFJPY","USDJPY","AUDCAD"]
        return ("AUTORISE","") if symbole in ok else ("HORS_SESSION","Hors Europe")
    if 12 <= h < 17.5:
        ok = ["EURUSD","USDCAD","AUDUSD","XAUUSD","XAGUSD","USOUSD"]
        return ("AUTORISE","") if symbole in ok else ("HORS_SESSION","Hors US")
    return "BLOCAGE_TOTAL", "Erreur heure"

# ==========================================
# WEBSOCKET
# ==========================================

def prefixer_symbole(s):
    if s in SYNTHETIC_PAIRS: return f"R_{s.replace('V','')}"
    if s in CRYPTO_PAIRS: return f"cry{s}"
    return f"frx{s}"

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    sym = prefixer_symbole(symbole_brut)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history":sym,"end":"latest","count":250,
                                "style":"candles","granularity":granularite}))
            ws.settimeout(7)
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res and "error" not in res:
                return res["candles"]
        except:
            try: ws.close()
            except: pass
            time.sleep(0.3)
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    sym = prefixer_symbole(symbole_brut)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history":sym,"end":"latest","count":1,"style":"ticks"}))
            ws.settimeout(7)
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]:
                return float(res["history"]["prices"][0])
        except:
            try: ws.close()
            except: pass
            time.sleep(0.3)
    return None

# ==========================================
# NEWS FILTER
# ==========================================

def est_heure_de_news_dynamique():
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today}&to={today}&apikey={FMP_API_KEY}"
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            now = datetime.datetime.utcnow()
            for e in r.json():
                if e.get('impact') == 'High':
                    et = datetime.datetime.strptime(e['date'], "%Y-%m-%d %H:%M:%S")
                    if abs((now-et).total_seconds()/60) <= 30: return True
    except: pass
    return False

# ==========================================
# DIVERGENCE RSI
# ==========================================

def detecter_divergence(df, action):
    try:
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        p = df['close']
        if "ACHAT" in action and p.iloc[-1] < p.iloc[-5] and rsi.iloc[-1] > rsi.iloc[-5]:
            return True, "🔄 Divergence RSI Haussière"
        if "VENTE" in action and p.iloc[-1] > p.iloc[-5] and rsi.iloc[-1] < rsi.iloc[-5]:
            return True, "🔄 Divergence RSI Baissière"
    except: pass
    return False, ""

# ==========================================
# HISTORIQUE QUALITÉ
# ==========================================

def obtenir_qualite_paire(symbole, action):
    cle = f"{symbole}_{action}"
    hist = historique_signaux.get(cle, [])
    if len(hist) >= 2 and all(r=="OTM" for r in hist[-2:]):
        return False, f"⚠️ {symbole} : 2 OTM consécutifs, paire évitée."
    return True, ""

def enregistrer_resultat(symbole, action, resultat):
    cle = f"{symbole}_{action}"
    historique_signaux.setdefault(cle, []).append(resultat)
    historique_signaux[cle] = historique_signaux[cle][-5:]

# ==========================================
# CORRÉLATION FOREX
# ==========================================

def verifier_correlation(symbole, action):
    if symbole in SYNTHETIC_PAIRS or symbole in COMMODITY_PAIRS: return True
    correlations = {"EURUSD":("USDCHF","INV"),"AUDUSD":("USDCAD","INV"),
                    "USDCHF":("EURUSD","INV"),"USDCAD":("AUDUSD","INV")}
    if symbole not in correlations: return True
    sym_c, typ = correlations[symbole]
    candles = obtenir_donnees_deriv(sym_c, 300)
    if not candles: return True
    try:
        df = pd.DataFrame([{'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in candles])
        hi = df['high'].iloc[-20:-1].max(); lo = df['low'].iloc[-20:-1].min(); px = df['close'].iloc[-1]
        trend = "H" if (px-lo)>(hi-px) else "B"
        act = "CALL" if "ACHAT" in action else "PUT"
        if typ=="INV":
            if act=="CALL" and trend=="H": return False
            if act=="PUT"  and trend=="B": return False
    except: pass
    return True

# ==========================================
# MOTEUR D'ANALYSE PRINCIPAL V29
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):

    # News filter
    if est_heure_de_news_dynamique() and symbole not in SYNTHETIC_PAIRS:
        return "⚠️ NEWS en cours.", None, None, None, None, None, None, None

    # ── GOLD : Méthode Ahmad FX ───────────────────────────────────────────
    if symbole == "XAUUSD":
        candles = obtenir_donnees_deriv("XAUUSD", 300)
        if not candles:
            return "⚠️ Gold données indisponibles.", None, None, None, None, None, None, None
        try:
            df = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),
                                 'high':float(c['high']),'low':float(c['low'])} for c in candles])
            ema8  = ta.trend.EMAIndicator(close=df['close'], window=8).ema_indicator()
            ema21 = ta.trend.EMAIndicator(close=df['close'], window=21).ema_indicator()
            rsi9  = ta.momentum.RSIIndicator(close=df['close'], window=9).rsi()
            macd  = ta.trend.MACD(close=df['close']).macd_diff()

            last = df.iloc[-1]
            corps = abs(last['close']-last['open'])
            taille = last['high']-last['low']
            meche_h = last['high'] - max(last['open'],last['close'])
            meche_b = min(last['open'],last['close']) - last['low']
            rejet_haut = meche_h > corps*2.0
            rejet_bas  = meche_b > corps*2.0

            bull = (ema8.iloc[-1]>ema21.iloc[-1]) and (rsi9.iloc[-1]>50) and (macd.iloc[-1]>0)
            bear = (ema8.iloc[-1]<ema21.iloc[-1]) and (rsi9.iloc[-1]<50) and (macd.iloc[-1]<0)

            if rejet_bas:  bull = True
            if rejet_haut: bear = True

            if bull and not bear:
                bb = "🏆 Ahmad FX : Gold Sniper Haussier"
                if rejet_bas: bb += " + Mèche Rejet"
                return "🟢 ACHAT (CALL)", 97, "5 MIN", 300, round(rsi9.iloc[-1],1), 80, bb, 9.5
            elif bear and not bull:
                bb = "🏆 Ahmad FX : Gold Sniper Baissier"
                if rejet_haut: bb += " + Mèche Rejet"
                return "🔴 VENTE (PUT)", 97, "5 MIN", 300, round(rsi9.iloc[-1],1), 20, bb, 9.5
        except: pass
        return "⚠️ Gold : En observation.", None, None, None, None, None, None, None

    # ── SMC V29 FOREX & SYNTHÉTIQUES ─────────────────────────────────────
    profil = obtenir_profil_actif(symbole)

    if symbole in FOREX_PAIRS or symbole in CRYPTO_PAIRS:
        timeframes = [300, 120] if mode == "STANDARD" else [60]
    else:
        timeframes = [600, 300, 120] if mode == "STANDARD" else [60]

    for tf in timeframes:
        candles = obtenir_donnees_deriv(symbole, tf)
        if not candles: continue
        try:
            df = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),
                                 'high':float(c['high']),'low':float(c['low'])} for c in candles])
            df['corps']    = abs(df['close']-df['open'])
            df['taille']   = df['high']-df['low']
            df['meche_h']  = df['high'] - df[['open','close']].max(axis=1)
            df['meche_b']  = df[['open','close']].min(axis=1) - df['low']
            df['vol']      = df['taille']
            df['vol_moy']  = df['vol'].rolling(14).mean()

            atr    = ta.volatility.AverageTrueRange(high=df['high'],low=df['low'],close=df['close'],window=14).average_true_range()
            atr_v  = atr.iloc[-1]
            atr_m  = atr.iloc[-20:].mean()
            if atr_v < atr_m*0.5: continue
            if atr_v > atr_m*3.0: continue

            vol_ok = df['vol'].iloc[-1] > df['vol_moy'].iloc[-1] and \
                     df['vol'].iloc[-1] < df['vol_moy'].iloc[-1]*profil["vol_multiplier"]

            avg_t = df['taille'].iloc[-4:-1].mean()
            avg_c = df['corps'].iloc[-4:-1].mean()
            if avg_c>0 and avg_t>avg_c*3.5: continue

            df['rsi']    = ta.momentum.RSIIndicator(close=df['close'],window=14).rsi()
            df['stoch']  = ta.momentum.StochasticOscillator(high=df['high'],low=df['low'],close=df['close']).stoch()
            df['macd_d']  = ta.trend.MACD(close=df['close']).macd_diff()

            last,prev,p2 = df.iloc[-1],df.iloc[-2],df.iloc[-3]
            px    = last['close']
            rsi_v = round(last['rsi'],1)
            st_v  = round(last['stoch'],1)
            macd_v= last['macdd']

            action,conf,bb,score = None,0,"En attente",5.0

            vrai_corps   = last['corps'] > last['taille']*0.25
            is_green     = last['close']>last['open']
            is_red       = last['close']<last['open']
            prev_green   = prev['close']>prev['open']
            prev_red     = prev['close']<prev['open']
            p2_green     = p2['close']>p2['open']
            p2_red       = p2['close']<p2['open']

            rejet_h      = last['meche_b'] > last['corps']*1.5
            rejet_b      = last['meche_h'] > last['corps']*1.5
            aval_h       = prev_red and is_green and last['close']>prev['open'] and last['open']<=prev['close']
            aval_b       = prev_green and is_red and last['close']<prev['open'] and last['open']>=prev['close']
            harami_h     = prev_red and is_green and last['open']>prev['close'] and last['close']<prev['open']
            harami_b     = prev_green and is_red and last['open']<prev['close'] and last['close']>prev['open']

            pc           = prev['corps']
            danger_h     = prev['meche_h']>(pc*1.5) if pc>0 else False
            danger_b     = prev['meche_b']>(pc*1.5) if pc>0 else False
            fusee_h      = is_green and prev_green and p2_green and vrai_corps
            fusee_b      = is_red   and prev_red   and p2_red   and vrai_corps

            sh1 = df['high'].iloc[-20:-10].max(); sl1 = df['low'].iloc[-20:-10].min()
            sh2 = df['high'].iloc[-10:-1].max();  sl2 = df['low'].iloc[-10:-1].min()
            struct_h = sh2>sh1 and sl2>=sl1
            struct_b = sl2<sl1 and sh2<=sh1

            px_moy   = df['close'].iloc[-6:-1].mean()
            discount = px < px_moy
            premium  = px > px_moy

            # ✅ GESTION COMPLÈTE DES DÉLAIS ET EXPIRATIONS (Évite les variables manquantes)
            duree = 180 if tf == 300 else tf
            if tf == 300:
                exp = "3 MIN ⚡"
            elif tf in [60, 120, 600]:
                exp = f"{int(tf/60)} MIN"
            else:
                exp = f"{int(duree/60)} MIN"

            if mode=="STANDARD":
                # ── Signal ACHAT ──
                if struct_h and discount and vol_ok and vrai_corps \
                        and not danger_h and not fusee_b and macd_v>0:
                    if st_v < profil["stoch_achat"] and rsi_v < profil["rsi_achat"]:
                        action,conf,score = "🟢 ACHAT (CALL)",80,7.0
                        bb = f"🎯 {profil['nom']} : Zone Discount"
                    if aval_h or rejet_h or harami_h:
                        action,conf,score = "🟢 ACHAT (CALL)",94,9.0
                        bb = f"👑 {profil['nom']} : Prise Liquidité 🚀"

                # ── Signal VENTE ──
                elif struct_b and premium and vol_ok and vrai_corps \
                        and not danger_b and not fusee_h and macd_v<0:
                    if st_v > profil["stoch_vente"] and rsi_v > profil["rsi_vente"]:
                        action,conf,score = "🔴 VENTE (PUT)",80,7.0
                        bb = f"🎯 {profil['nom']} : Zone Premium"
                    if aval_b or rejet_b or harami_b:
                        action,conf,score = "🔴 VENTE (PUT)",94,9.0
                        bb = f"👑 {profil['nom']} : Prise Liquidité ☄️"

            elif mode=="SCALP":
                boll = ta.volatility.BollingerBands(close=df['close'],window=20,window_dev=2.2)
                bb_h = boll.bollinger_hband().iloc[-1]
                bb_b = boll.bollinger_lband().iloc[-1]
                df['bbw'] = boll.bollinger_wband()
                squeeze = df['bbw'].iloc[-1] < df['bbw'].rolling(20).mean().iloc[-1]*0.8
                duree,exp = 60,"1 MIN SCALP 🛡️"
                if not squeeze and vol_ok and vrai_corps:
                    if last['low']<=bb_b and rejet_h and not danger_h and not fusee_b and macd_v>0:
                        action,conf,score = "🟢 ACHAT (CALL)",90,9.0
                        bb = f"🛡️ Scalp {profil['nom']} : BB Bas"
                    elif last['high']>=bb_h and rejet_b and not danger_b and not fusee_h and macd_v<0:
                        action,conf,score = "🔴 VENTE (PUT)",90,9.0
                        bb = f"🛡️ Scalp {profil['nom']} : BB Haut"

            if action:
                div_ok,div_msg = detecter_divergence(df,action)
                if div_ok:
                    score = min(score+1.5,10.0)
                    bb += f"\n{div_msg}"

                if symbole in SYNTHETIC_PAIRS:
                    candles_m5 = obtenir_donnees_deriv(symbole,300)
                    if candles_m5:
                        try:
                            df5 = pd.DataFrame([{'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in candles_m5])
                            macd5 = ta.trend.MACD(close=df5['close']).macd_diff().iloc[-1]
                            act_s = "CALL" if "ACHAT" in action else "PUT"
                            if act_s=="CALL" and macd5<0: return "⚠️ MTF Synth divergent.",None,None,None,None,None,None,None
                            if act_s=="PUT"  and macd5>0: return "⚠️ MTF Synth divergent.",None,None,None,None,None,None,None
                        except: pass

                act_s = "CALL" if "ACHAT" in action else "PUT"
                ok,msg = obtenir_qualite_paire(symbole,act_s)
                if not ok: return f"⚠️ {msg}",None,None,None,None,None,None,None

                if not verifier_correlation(symbole,action):
                    return "⚠️ Corrélation adverse.",None,None,None,None,None,None,None

                cd = profil.get("cooldown_otm",600)
                if symbole in cooldown_actifs and time.time()-cooldown_actifs[symbole]['time']<cd:
                    if act_s==cooldown_actifs[symbole]['action']:
                        return "⚠️ Cooldown actif.",None,None,None,None,None,None,None

                return action,min(conf,99),exp,duree,rsi_v,st_v,bb,score
        except: continue

    return f"⚠️ En attente ({mode}).",None,None,None,None,None,None,None

# ==========================================
# INTERFACE
# ==========================================

def obtenir_clavier(uid):
    mode = mode_trading.get(uid,"STANDARD")
    pf   = plateforme_trading.get(uid,"MT5")
    fil  = filtre_special.get(uid,"TOUS")
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"),KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("🛡️ MODE: SMC STANDARD" if mode=="STANDARD" else "🔥 MODE: SMC SCALP"),
               KeyboardButton("🏦 BROKER: POCKET" if pf=="POCKET" else "📈 BROKER: MT5"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"),
               KeyboardButton("💎 SIGNAUX: TOUS" if fil=="TOUS" else "💎 SIGNAUX: SPÉCIAUX"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid): return bot.send_message(uid,"🔒 Accès restreint.")
    utilisateurs_actifs.add(uid)
    mode_trading.setdefault(uid,"STANDARD")
    plateforme_trading.setdefault(uid,"MT5")
    filtre_special.setdefault(uid,"TOUS")
    niveaux_martingale.setdefault(uid,0)
    texte = """🏴‍☠️ **TERMINAL PRIME V29 — EDITION FINALE** 🔥
──────────────────
✅ SMC Forex V29 : Profils corrigés, signaux fluides
✅ Gold Sniper Ahmad FX : Mèches + EMA + MACD
✅ WS stable : une connexion par appel (0 deadlock)
✅ Cooldown dynamique par marché
✅ Martingale adaptative avec pivot IA"""
    bot.send_message(uid, texte, reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if uid in trades_en_cours: return bot.send_message(uid,"⚠️ Trade en cours.")
    if mode_trading.get(uid,"STANDARD")=="STANDARD":
        mode_trading[uid]="SCALP"
        bot.send_message(uid,"🔥 **SCALP ACTIVÉ**",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")
    else:
        mode_trading[uid]="STANDARD"
        bot.send_message(uid,"🛡️ **STANDARD ACTIVÉ**",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_pf(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if uid in trades_en_cours: return bot.send_message(uid,"⚠️ Trade en cours.")
    if plateforme_trading.get(uid,"MT5")=="POCKET":
        plateforme_trading[uid]="MT5"
        bot.send_message(uid,"📈 **MT5 ACTIVÉ** — Synthétiques + Gold",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")
    else:
        plateforme_trading[uid]="POCKET"
        bot.send_message(uid,"🏦 **POCKET ACTIVÉ** — Forex Binaire",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("💎 SIGNAUX:"))
def toggle_filtre(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if filtre_special.get(uid,"TOUS")=="TOUS":
        filtre_special[uid]="SPECIAUX"
        bot.send_message(uid,"💎 **SIGNAUX SPÉCIAUX UNIQUEMENT (≥9.5)**",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")
    else:
        filtre_special[uid]="TOUS"
        bot.send_message(uid,"📡 **TOUS SIGNAUX (≥7.0)**",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text=="⏰ HEURES DE TRADING")
def horaires(message):
    if not est_autorise(message.chat.id): return
    bot.send_message(message.chat.id,
        "🕒 **Forex/Matières premières :** Lun–Ven, Sessions Londres & New York\n💥 **Synthétiques :** 24h/24 7j/7",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE","📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    if not est_autorise(message.chat.id): return
    pf = plateforme_trading.get(message.chat.id,"MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    if pf=="MT5":
        markup.add(InlineKeyboardButton("🔥 V10",callback_data="set_V10"),
                   InlineKeyboardButton("🔥 V25",callback_data="set_V25"),
                   InlineKeyboardButton("🔥 V50",callback_data="set_V50"))
        markup.add(InlineKeyboardButton("⚡ V75",callback_data="set_V75"),
                   InlineKeyboardButton("💥 V100",callback_data="set_V100"))
        markup.add(InlineKeyboardButton("🥇 GOLD",callback_data="set_XAUUSD"),
                   InlineKeyboardButton("🥈 ARGENT",callback_data="set_XAGUSD"),
                   InlineKeyboardButton("🛢 PÉTROLE",callback_data="set_USOUSD"))
        bot.send_message(message.chat.id,"Sélectionne ta cible MT5 :",reply_markup=markup)
    else:
        markup.add(InlineKeyboardButton("🇦🇺 AUD/USD",callback_data="set_AUDUSD"),
                   InlineKeyboardButton("🇨🇦 CAD/JPY",callback_data="set_CADJPY"),
                   InlineKeyboardButton("🇨🇭 CHF/JPY",callback_data="set_CHFJPY"))
        markup.add(InlineKeyboardButton("🇪🇺 EUR/JPY",callback_data="set_EURJPY"),
                   InlineKeyboardButton("🇺🇸 USD/CAD",callback_data="set_USDCAD"),
                   InlineKeyboardButton("🇦🇺 AUD/JPY",callback_data="set_AUDJPY"))
        markup.add(InlineKeyboardButton("🇪🇺 EUR/AUD",callback_data="set_EURAUD"),
                   InlineKeyboardButton("🇪🇺 EUR/USD",callback_data="set_EURUSD"),
                   InlineKeyboardButton("🇦🇺 AUD/CAD",callback_data="set_AUDCAD"))
        markup.add(InlineKeyboardButton("🇺🇸 USD/CHF",callback_data="set_USDCHF"),
                   InlineKeyboardButton("🇨🇦 CAD/CHF",callback_data="set_CADCHF"),
                   InlineKeyboardButton("🇪🇺 EUR/CHF",callback_data="set_EURCHF"))
        markup.add(InlineKeyboardButton("🇯🇵 USD/JPY",callback_data="set_USDJPY"))
        bot.send_message(message.chat.id,"Sélectionne ta cible Pocket Forex :",reply_markup=markup)

@bot.message_handler(func=lambda m: m.text=="🚀 LANCER L'ANALYSE")
def lancer(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if uid in trades_en_cours: return bot.send_message(uid,"⚠️ Trade en cours.")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(uid,"⚠️ Choisis d'abord une cible !")
    save_devise(type('obj',(object,),{'data':f"set_{actif}",'message':message,
                                      'from_user':message.from_user,'id':0})())

# ==========================================
# TIMING D'ENTRÉE
# ==========================================

def calculer_entree_precise(duree=60):
    now = datetime.datetime.now()
    sec = now.second
    if sec>=45:   delai = (60-sec)+5
    elif sec<=10: delai = (60-sec)+5
    else:         delai = (60-sec)+5
    return delai, (now+datetime.timedelta(seconds=delai)).strftime("%H:%M:%S")

# ==========================================
# CALLBACK PRINCIPAL
# ==========================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): return
    if uid in trades_en_cours:
        try: bot.answer_callback_query(call.id,"⚠️ Trade en cours !",show_alert=True)
        except: pass
        return

    actif = call.data.replace("set_","")
    user_prefs[getattr(call,'from_user',type('o',(object,),{'id':uid})()).id] = actif
    pf     = plateforme_trading.get(uid,"MT5")
    mode   = mode_trading.get(uid,"STANDARD")
    cle    = f"{actif}_{mode}"

    cache  = signaux_cache.get(cle)
    valid  = cache and (time.time()-cache['time']) <= 90

    try: bot.delete_message(uid,call.message.message_id)
    except: pass

    nom_map = {"XAUUSD":"🥇 GOLD","XAGUSD":"🥈 ARGENT","USOUSD":"🛢 PÉTROLE"}
    if actif in SYNTHETIC_PAIRS: nom = f"💥 {actif}"
    elif actif in nom_map: nom = nom_map[actif]
    elif actif in CRYPTO_PAIRS: nom = f"🪙 {actif[:3]}/{actif[3:]}"
    else: nom = f"💱 {actif[:3]}/{actif[3:]}"

    if not valid:
        bot.send_message(uid,f"⏱️ **Signal expiré sur {actif}**\nMerci d'attendre la prochaine alerte du radar.",parse_mode="Markdown")
        return

    delai,heure = calculer_entree_precise(cache.get('dur',60))
    px = obtenir_prix_actuel_deriv(actif) or 0.0

    if actif=="XAUUSD":
        dir_aff = "🟢 BUY" if "ACHAT" in cache['action'] else "🔴 SELL"
        sl  = cache.get('mt5_sl',0.0)
        tp1 = cache.get('mt5_tp',0.0)
        tp2 = cache.get('mt5_tp2',0.0)
        bot.send_message(uid,f"""⚡ **GOLD SNIPER V29 (AHMAD FX) 💎**
──────────────────
🌐 **ACTIF :** {nom}
👉 **ORDRE :** {dir_aff}
💰 **Prix :** `{px:.2f}`
🛑 **SL :** `{sl:.2f}`
🎯 **TP1 :** `{tp1:.2f}`
🎯 **TP2 :** `{tp2:.2f}`
⏱ **Entrée :** `{heure}`
──────────────────
📡 Surveillance automatique active (15s)""",parse_mode="Markdown")

        gold_trades_actifs[uid] = {
            'prix_entree':px,'action':"BUY" if "ACHAT" in cache['action'] else "SELL",
            'sl':sl,'tp1':tp1,'tp2':tp2,'sl_orig':sl,'tp1_orig':tp1,'tp2_orig':tp2,
            'be_atteint':False,'tp1_atteint':False,'palier':0
        }
        trades_en_cours[uid] = {'symbole':'XAUUSD','action':cache['action'],'duree':300,'nom_affiche':nom}
        return

    if pf=="MT5":
        dir_aff = "🟢 BUY" if "ACHAT" in cache['action'] else "🔴 SELL"
        bot.send_message(uid,f"""⚡ **SIGNAL MT5 SNIPER V29 💎**
──────────────────
🌐 **ACTIF :** {nom}
👉 **ORDRE :** {dir_aff}
🎯 **R/R :** {cache.get('mt5_rr',0):.2f}
💰 **Prix :** `{px:.5f}`
🛑 **SL :** `{cache.get('mt5_sl',0):.5f}`
✅ **TP :** `{cache.get('mt5_tp',0):.5f}`
⏱ **Entrée :** `{heure}`
⚠️ Lot 0.001 pour indices""",parse_mode="Markdown")
        return

    palier = niveaux_martingale.get(uid,0)
    score  = cache.get('sc',5.0)
    mise   = int((CAPITAL_ACTUEL*0.02)*(COEF_MARTINGALE**palier))

    if palier==0 and score<9.0:
        sig = f"""👻 **FANTÔME PALIER 0**
──────────────────
🌐 **ACTIF :** {nom}
⏱ **Entrée :** `{heure}`
👉 **Action :** {cache['action']}
⏳ **Durée :** {cache['exp']}
📊 **Score :** `{score}/10`
*L'IA observe. NE RENTREZ PAS.*"""
    elif palier==0 and score>=9.0:
        palier=1; niveaux_martingale[uid]=1
        sig = f"""🚨 **SIGNAL RÉEL VIP 💎 (Score {score}/10)**
──────────────────
🌐 **ACTIF :** {nom}
⏱ **Entrée :** `{heure}`
⏳ **Expiration :** {cache['exp']}
👉 **Action :** {cache['action']}
🛡️ {cache['bb']}
💵 **Mise :** `{mise}$` (Palier 1)"""
    else:
        sig = f"""🚨 **SIGNAL PALIER {palier}**
──────────────────
🌐 **ACTIF :** {nom}
⏱ **Entrée :** `{heure}`
👉 **Action :** {cache['action']}
⏳ **Durée :** {cache['exp']}
💵 **Mise :** `{mise}$`"""

    bot.send_message(uid,sig,parse_mode="Markdown")
    brut = "CALL" if "ACHAT" in cache['action'] else "PUT"
    Timer(delai,executer_tir_flash,args=[uid,actif,brut,cache['dur'],palier,nom]).start()

# ==========================================
# SCANNER AUTOMATIQUE V29
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            libres = [u for u in utilisateurs_actifs if est_autorise(u) and u not in trades_en_cours]
            if not libres: continue

            for paire in ALL_PAIRS_POCKET:
                statut,_ = est_symbole_autorise(paire)
                if statut=="BLOCAGE_TOTAL": continue

                if paire=="XAUUSD":
                    cle = "XAUUSD_STANDARD"
                    if cle in derniere_alerte_auto and time.time()-derniere_alerte_auto[cle]<300: continue
                    action,conf,exp,dur,rsi,st,bb,sc = analyser_binaire_pro("XAUUSD","STANDARD")
                    if action and "⚠️" not in action:
                        px = obtenir_prix_actuel_deriv("XAUUSD") or 0.0
                        atr_c = obtenir_donnees_deriv("XAUUSD",300)
                        atr_v = 3.0
                        if atr_c:
                            try:
                                dfa = pd.DataFrame([{'high':float(c['high']),'low':float(c['low']),'close':float(c['close'])} for c in atr_c])
                                atr_v = ta.volatility.AverageTrueRange(high=dfa['high'],low=dfa['low'],close=dfa['close'],window=14).average_true_range().iloc[-1]
                            except: pass
                        if "ACHAT" in action:
                            sl=round(px-atr_v*2,2); tp1=round(px+atr_v*1.5,2); tp2=round(px+atr_v*3,2)
                        else:
                            sl=round(px+atr_v*2,2); tp1=round(px-atr_v*1.5,2); tp2=round(px-atr_v*3,2)

                        signaux_cache[cle] = {'time':time.time(),'action':action,'conf':conf,
                            'exp':exp,'dur':dur,'rsi':rsi,'stoch':st,'bb':bb,'sc':sc,
                            'mt5_sl':sl,'mt5_tp':tp1,'mt5_tp2':tp2,'mt5_rr':2.0}
                        derniere_alerte_auto[cle] = time.time()

                        for uid in libres:
                            if plateforme_trading.get(uid,"MT5")!="MT5": continue
                            dir_txt = "🟢 BUY" if "ACHAT" in action else "🔴 SELL"
                            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("⚡ Frapper GOLD",callback_data="set_XAUUSD"))
                            try: bot.send_message(uid,f"🔔 **GOLD SNIPER V29 : XAUUSD**\n🏆 Ahmad FX — {dir_txt}\n🎯 TP1:{tp1:.2f} | TP2:{tp2:.2f} | SL:{sl:.2f}\n90s pour agir.",reply_markup=markup,parse_mode="Markdown")
                            except: pass
                    continue

                for mode in ["STANDARD","SCALP"]:
                    repos = 300 if mode=="STANDARD" else 120
                    cle = f"{paire}_{mode}"
                    if cle in derniere_alerte_auto and time.time()-derniere_alerte_auto[cle]<repos: continue

                    action,conf,exp,dur,rsi,st,bb,sc = analyser_binaire_pro(paire,mode)
                    if not action or "⚠️" in action: continue
                    if statut=="HORS_SESSION" and (sc is None or sc<9.0): continue

                    act_s = "CALL" if "ACHAT" in action else "PUT"
                    valide = True
                    sl=tp=rr=0
                    profil = obtenir_profil_actif(paire)

                    c15 = obtenir_donnees_deriv(paire,900)
                    if c15:
                        try:
                            d15 = pd.DataFrame([{'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c15])
                            e50 = ta.trend.EMAIndicator(close=d15['close'],window=50).ema_indicator()
                            e20 = ta.trend.EMAIndicator(close=d15['close'],window=20).ema_indicator()
                            tx  = "H" if d15['close'].iloc[-1]>e50.iloc[-1] else "B"
                            if act_s=="CALL" and tx=="B": valide=False
                            if act_s=="PUT"  and tx=="H": valide=False
                            if valide and paire in ELITE_PAIRS_MT5:
                                ema_ok = e20.iloc[-1]>e50.iloc[-1] if tx=="H" else e20.iloc[-1]<e50.iloc[-1]
                                if not ema_ok: valide=False
                        except: pass

                    if valide and paire in ELITE_PAIRS_MT5:
                        c5 = obtenir_donnees_deriv(paire,300)
                        px = obtenir_prix_actuel_deriv(paire)
                        if c5 and px:
                            try:
                                d5 = pd.DataFrame([{'high':float(c['high']),'low':float(c['low']),'close':float(c['close'])} for c in c5])
                                atr_v = ta.volatility.AverageTrueRange(high=d5['high'],low=d5['low'],close=d5['close'],window=14).average_true_range().iloc[-1]
                                if act_s=="CALL":
                                    sl=d15['low'].iloc[-30:-1].min()-atr_v*1.5
                                    tp=d15['high'].iloc[-40:-1].max()
                                    if tp<=px: tp=px+abs(px-sl)*2
                                else:
                                    sl=d15['high'].iloc[-30:-1].max()+atr_v*1.5
                                    tp=d15['low'].iloc[-40:-1].min()
                                    if tp>=px: tp=px-abs(sl-px)*2
                                risque=abs(px-sl); recomp=abs(tp-px)
                                rr=recomp/risque if risque>0 else 0
                                if rr<profil["rr_min"]: valide=False
                            except: pass

                    if not valide: continue

                    signaux_cache[cle] = {'time':time.time(),'action':action,'conf':conf,
                        'exp':exp,'dur':dur,'rsi':rsi,'stoch':st,'bb':bb,'sc':sc,
                        'mt5_sl':sl,'mt5_tp':tp,'mt5_rr':rr}
                    derniere_alerte_auto[cle] = time.time()

                    for uid in libres:
                        pf = plateforme_trading.get(uid,"MT5")
                        if pf=="MT5"   and paire not in ELITE_PAIRS_MT5: continue
                        if pf=="POCKET" and paire not in FOREX_PAIRS: continue
                        if mode_trading.get(uid,"STANDARD")!=mode: continue
                        if filtre_special.get(uid)=="SPECIAUX" and (sc is None or sc<9.5): continue

                        if paire in SYNTHETIC_PAIRS: nom_a=f"V{paire.replace('V','')}"
                        elif paire=="XAGUSD": nom_a="ARGENT"
                        elif paire=="USOUSD": nom_a="PÉTROLE"
                        else: nom_a=f"{paire[:3]}/{paire[3:]}"

                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Frapper {nom_a}",callback_data=f"set_{paire}"))
                        msg = f"🔔 **{'SIGNAL PREMIUM' if sc>=9.5 else 'RADAR'} {profil['nom']} : {nom_a}**\n{'✅ Score '+str(sc)+'/10 — ' if sc>=9.5 else ''}Structure validée. 90s pour agir."
                        try: bot.send_message(uid,msg,reply_markup=markup,parse_mode="Markdown")
                        except: pass

        except Exception as e:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] ⚠️ Scanner : {e}", flush=True)

# ==========================================
# RÉSULTATS TRADES
# ==========================================

def executer_tir_flash(uid,sym,action,dur,palier,nom):
    aff = "🟢 ACHAT (CALL)" if action=="CALL" else "🔴 VENTE (PUT)"
    if palier==0:
        txt = f"👻 **FANTÔME ({nom})**\nIA observe virtuellement..."; mk=None
    else:
        txt = f"🔥 **TIR PALIER {palier} ({nom})**\n👉 **{aff} MAINTENANT !**"
        mk = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ",callback_data="force_win"))
    try: bot.send_message(uid,txt,parse_mode="Markdown",reply_markup=mk)
    except: pass
    trades_en_cours[uid] = {'symbole':sym,'action':action,'duree':dur,'nom_affiche':nom}
    Timer(2,relever_entree,args=[uid,sym]).start()
    Timer(dur,verifier_resultat,args=[uid]).start()

def relever_entree(uid,sym):
    px = obtenir_prix_actuel_deriv(sym)
    if px and uid in trades_en_cours and trades_en_cours[uid]['symbole']==sym:
        trades_en_cours[uid]['prix_entree'] = px

def verifier_resultat(uid):
    global stats_journee, cooldown_actifs, niveaux_martingale
    time.sleep(3)
    trade = trades_en_cours.get(uid)
    if not trade or not trade.get('prix_entree'): return
    sym=trade['symbole']; px_s=obtenir_prix_actuel_deriv(sym)
    if not px_s: return
    px_e=trade['prix_entree']; act=trade['action']; nom=trade['nom_affiche']
    palier=niveaux_martingale.get(uid,0)
    gagne=(act=="CALL" and px_s>px_e) or (act=="PUT" and px_s<px_e)

    if gagne:
        niveaux_martingale[uid]=0; enregistrer_resultat(sym,act,"ITM")
        txt = f"👻 **FANTÔME ITM** — {nom}" if palier==0 else f"✅ **ITM** — {nom}\n📈 Entrée:{px_e:.5f} → Sortie:{px_s:.5f}\n🔓 Radar libre."
        if palier>0: stats_journee['ITM']+=1
        if sym in cooldown_actifs: del cooldown_actifs[sym]
        if uid in trades_en_cours: del trades_en_cours[uid]
        try: bot.send_message(uid,txt,parse_mode="Markdown")
        except: pass
    else:
        enregistrer_resultat(sym,act,"OTM")
        profil=obtenir_profil_actif(sym)
        if palier<MAX_MARTINGALE:
            niveaux_martingale[uid]=palier+1
            if uid in trades_en_cours: del trades_en_cours[uid]
            act_m=act; commentaire="🔍 Structure valide. Persistence."
            c1 = obtenir_donnees_deriv(sym,60)
            if c1:
                try:
                    da=pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1])
                    der=da.iloc[-1]; corps=abs(der['close']-der['open']); tot=der['high']-der['low']
                    rec3=da.iloc[-3:]; cm=rec3.apply(lambda r:abs(r['close']-r['open']),axis=1).mean()
                    fd=sum(1 if r['close']>r['open'] else -1 for _,r in rec3.iterrows())
                    if tot>0:
                        if act=="CALL" and der['close']<der['open'] and corps>tot*0.75 and fd<=-2 and corps>cm*1.2:
                            act_m="PUT"; commentaire="🔄 **BREAKER BLOCK** — Pivot PUT"
                        elif act=="PUT" and der['close']>der['open'] and corps>tot*0.75 and fd>=2 and corps>cm*1.2:
                            act_m="CALL"; commentaire="🔄 **BREAKER BLOCK** — Pivot CALL"
                except: pass
            
            # ✅ Sécurité try/except pour éviter tout crash de messagerie sur OTM
            try:
                bot.send_message(uid,f"⚠️ **OTM Palier {palier}**\n{commentaire}\n⚡ Palier {palier+1} en cours...",parse_mode="Markdown")
            except: 
                pass
                
            cle=f"{sym}_{mode_trading.get(uid,'STANDARD')}"
            signaux_cache[cle]={'time':time.time(),'action':"🟢 ACHAT (CALL)" if act_m=="CALL" else "🔴 VENTE (PUT)",
                'conf':99,'exp':f"{trade['duree']//60} MIN",'dur':trade['duree'],'rsi':50,'stoch':50,
                'bb':f"Martingale V29",'sc':5.0}
            class CF:
                def __init__(s,c,d): s.message=type('o',(object,),{'chat':type('o',(object,),{'id':c}),'message_id':0}); s.data=d; s.id=0; s.from_user=type('o',(object,),{'id':c})
            save_devise(CF(uid,f"set_{sym}"))
        else:
            cd=profil.get("cooldown_otm",600); niveaux_martingale[uid]=0
            if palier>0: stats_journee['OTM']+=1
            cooldown_actifs[sym]={'time':time.time(),'action':act,'duree':cd}
            if uid in trades_en_cours: del trades_en_cours[uid]
            try: bot.send_message(uid,f"🛑 **SÉQUENCE ARRÊTÉE**\nCooldown {cd//60} min sur {nom}.",parse_mode="Markdown")
            except: pass

@bot.callback_query_handler(func=lambda c: c.data=="force_win")
def win_manuel(call):
    uid=call.message.chat.id
    if uid in trades_en_cours:
        t=trades_en_cours[uid]; stats_journee['ITM']+=1
        enregistrer_resultat(t['symbole'],t['action'],"ITM")
        bot.send_message(uid,f"✅ **ITM MANUEL — {t['nom_affiche']}**\n🔓 Radar libre.",parse_mode="Markdown")
        del trades_en_cours[uid]
    niveaux_martingale[uid]=0
    try: bot.answer_callback_query(call.id,"Victoire enregistrée.",show_alert=True)
    except: pass
    try: bot.edit_message_reply_markup(uid,call.message.message_id,reply_markup=None)
    except: pass

# ==========================================
# SURVEILLANCE GOLD AUTONOME
# ==========================================

def surveiller_gold():
    while True:
        try:
            time.sleep(15)
            if not gold_trades_actifs: continue
            px = obtenir_prix_actuel_deriv("XAUUSD")
            if not px: continue
            for uid,t in list(gold_trades_actifs.items()):
                pe=t['prix_entree']; act=t['action']; tp1=t['tp1']; tp2=t['tp2']
                chemin=abs(tp1-pe); be=t.get('be_atteint',False); t1done=t.get('tp1_atteint',False)
                if act=="BUY":
                    if not be and px>=pe+chemin*0.5:
                        t['be_atteint']=True; t['sl']=pe
                        bot.send_message(uid,f"🛡️ **Gold BE** — SL relevé à `{pe:.2f}`",parse_mode="Markdown")
                    if not t1done and px>=tp1:
                        t['tp1_atteint']=True
                        bot.send_message(uid,f"🎯 **Gold TP1 atteint à {tp1:.2f}**\nSuivi vers TP2:{tp2:.2f}",parse_mode="Markdown")
                    if px>=tp2:
                        bot.send_message(uid,f"👑 **Gold TP2 atteint ! MAX PROFIT** 🎉",parse_mode="Markdown")
                        del gold_trades_actifs[uid]
                        if uid in trades_en_cours: del trades_en_cours[uid]
                    elif px<=t['sl']:
                        msg="🚪 **Gold BE : sortie neutre**" if t['sl']==pe else "🛑 **Gold SL touché — Re-entry...**"
                        bot.send_message(uid,msg,parse_mode="Markdown")
                        del gold_trades_actifs[uid]
                        if t['sl']!=pe: gerer_reentry_gold(uid,t)
                        elif uid in trades_en_cours: del trades_en_cours[uid]
                else:  # SELL
                    if not be and px<=pe-chemin*0.5:
                        t['be_atteint']=True; t['sl']=pe
                        bot.send_message(uid,f"🛡️ **Gold BE** — SL abaissé à `{pe:.2f}`",parse_mode="Markdown")
                    if not t1done and px<=tp1:
                        t['tp1_atteint']=True
                        bot.send_message(uid,f"🎯 **Gold TP1 atteint à {tp1:.2f}**\nSuivi vers TP2:{tp2:.2f}",parse_mode="Markdown")
                    if px<=tp2:
                        bot.send_message(uid,f"👑 **Gold TP2 atteint ! MAX PROFIT** 🎉",parse_mode="Markdown")
                        del gold_trades_actifs[uid]
                        if uid in trades_en_cours: del trades_en_cours[uid]
                    elif px>=t['sl']:
                        msg="🚪 **Gold BE : sortie neutre**" if t['sl']==pe else "🛑 **Gold SL touché — Re-entry...**"
                        bot.send_message(uid,msg,parse_mode="Markdown")
                        del gold_trades_actifs[uid]
                        if t['sl']!=pe: gerer_reentry_gold(uid,t)
                        elif uid in trades_en_cours: del trades_en_cours[uid]
        except Exception as e:
            print(f"[Gold] ⚠️ {e}",flush=True)

def gerer_reentry_gold(uid,ancien):
    palier=ancien.get('palier',0)+1
    if palier>4:
        bot.send_message(uid,"🛑 **Gold R4 max atteint.** Arrêt du protocole.",parse_mode="Markdown")
        if uid in trades_en_cours: del trades_en_cours[uid]
        return
    mise=int(CAPITAL_ACTUEL*0.02*(1.5**palier))
    act=ancien['action']; px=obtenir_prix_actuel_deriv("XAUUSD") or ancien['prix_entree']
    dec=2.0*palier
    if act=="BUY":   sl=ancien['sl_orig']-dec; tp1=ancien['tp1_orig']-dec; tp2=ancien['tp2_orig']-dec; pe=px-dec
    else:            sl=ancien['sl_orig']+dec; tp1=ancien['tp1_orig']+dec; tp2=ancien['tp2_orig']+dec; pe=px+dec
    bot.send_message(uid,f"""⚠️ **GOLD RE-ENTRY R{palier}**
👉 {'🟢 BUY' if act=='BUY' else '🔴 SELL'}  💵 Mise: `{mise}$`
🛑 SL:`{sl:.2f}` | 🎯 TP1:`{tp1:.2f}` | 🎯 TP2:`{tp2:.2f}`""",parse_mode="Markdown")
    gold_trades_actifs[uid]={'prix_entree':pe,'action':act,'sl':sl,'tp1':tp1,'tp2':tp2,
        'sl_orig':sl,'tp1_orig':tp1,'tp2_orig':tp2,'be_atteint':False,'tp1_atteint':False,'palier':palier}
    trades_en_cours[uid]={'symbole':'XAUUSD','action':act,'duree':300,'nom_affiche':'🥇 GOLD'}

# ==========================================
# BILAN QUOTIDIEN
# ==========================================

def gestionnaire_bilan():
    global stats_journee
    sent = False
    while True:
        try:
            now=datetime.datetime.utcnow()
            if now.hour==18 and now.minute==0 and not sent:
                tot=stats_journee['ITM']+stats_journee['OTM']
                wr=(stats_journee['ITM']/tot*100) if tot>0 else 0
                txt=f"📊 **BILAN V29**\n✅ ITM:{stats_journee['ITM']} | ❌ OTM:{stats_journee['OTM']} | 🎯 WR:{wr:.1f}%"
                for u in utilisateurs_actifs:
                    if est_autorise(u):
                        try: bot.send_message(u,txt,parse_mode="Markdown")
                        except: pass
                stats_journee={'ITM':0,'OTM':0,'details':[]}; sent=True
            elif now.minute>5: sent=False
        except: pass
        time.sleep(30)

# ==========================================
# LANCEMENT
# ==========================================

if __name__=="__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestionnaire_bilan,  daemon=True).start()
    Thread(target=surveiller_gold,     daemon=True).start()
    print("⬛ TERMINAL PRIME V29 FINAL — Démarré.", flush=True)
    bot.infinity_polling()
