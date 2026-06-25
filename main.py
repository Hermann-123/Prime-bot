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

TELEGRAM_TOKEN = "8658287331:AAE2m8uJFYbVqQ-5TX2v6r22FBfvGRjHnas"
bot            = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID       = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY    = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# LISTES DE PAIRES — V35
# ==========================================

SYNTHETIC_PAIRS = ["V10","V25","V50","V75","V100"]
INDEX_PAIRS     = ["SP500","US100","DAX"]
COMMODITY_PAIRS = ["XAUUSD","XAGUSD","USOUSD"] 
CRYPTO_PAIRS    = ["BTCUSD","ETHUSD","LTCUSD"]
FOREX_PAIRS     = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD","AUDJPY",
                   "EURAUD","EURUSD","AUDCAD","USDCHF","CADCHF","EURCHF",
                   "USDJPY","GBPUSD"]

ELITE_PAIRS_MT5 = INDEX_PAIRS + COMMODITY_PAIRS
ALL_PAIRS       = INDEX_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS + CRYPTO_PAIRS

NOMS_AFFICHAGE = {
    "XAUUSD":"🥇 GOLD",   "XAGUSD":"🥈 ARGENT",  "USOUSD":"🛢 PÉTROLE",
    "SP500":"📈 S&P 500", "US100":"💹 NASDAQ 100","DAX":"🇩🇪 DAX",
    "V10":"🔥 V10",       "V25":"🔥 V25",         "V50":"🔥 V50",
    "V75":"⚡ V75",       "V100":"💥 V100",
}

# ==========================================
# VARIABLES D'ÉTAT
# ==========================================

user_prefs           = {}
plateforme_trading   = {}
trades_en_cours      = {}
utilisateurs_actifs  = set()
derniere_alerte_auto = {}
signaux_cache        = {}

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees          = {}
stats_journee          = {'ITM': 0, 'OTM': 0}

# ==========================================
# KEEP ALIVE
# ==========================================

app = Flask(__name__)
@app.route('/')
def home(): return "Terminal Prime V35 (Elite MT5 : Gold/Argent/Petrole/SP500/NASDAQ/DAX)"
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

# ==========================================
# KEYGEN VIP
# ==========================================

DUREES_VALIDES = {
    "1s":  (7,   "1 Semaine"),
    "2s":  (14,  "2 Semaines"),
    "1m":  (30,  "1 Mois"),
    "3m":  (90,  "3 Mois"),
    "6m":  (180, "6 Mois"),
    "1a":  (365, "1 An"),
    "vie": ("LIFETIME", "À VIE 👑"),
}

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    parts = message.text.strip().split()

    if len(parts) < 2:
        aide = (
            "⚙️ **GÉNÉRATEUR DE CLÉS VIP**\n"
            "──────────────────\n"
            "Usage : `/keygen <durée>`\n\n"
            "**Durées :** 1s | 2s | 1m | 3m | 6m | 1a | vie | <jours>"
        )
        return bot.send_message(message.chat.id, aide, parse_mode="Markdown")

    arg = parts[1].lower().strip()
    if arg in DUREES_VALIDES:
        jours, label = DUREES_VALIDES[arg]
    else:
        try:
            jours = int(arg)
            if jours <= 0: raise ValueError
            label = f"{jours} jours"
        except ValueError:
            return bot.send_message(message.chat.id, f"❌ **Argument invalide :** `{arg}`", parse_mode="Markdown")

    cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    cles_generees[cle] = jours

    msg = (f"✅ **CLÉ VIP GÉNÉRÉE**\n──────────────────\n🔑 **Clé :** `{cle}`\n⏳ **Durée :** {label}\n"
           f"──────────────────\n_L'abonné active avec :_ `/vip {cle}`")
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    cid = message.chat.id
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(cid, "⚠️ Usage : `/vip VOTRE-CLÉ`", parse_mode="Markdown")
    cle = parts[1].strip()
    if cle not in cles_generees:
        return bot.send_message(cid, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")
    
    jours = cles_generees.pop(cle)
    if jours == "LIFETIME":
        utilisateurs_autorises[cid] = "LIFETIME"
        txt = "À VIE 👑"
    else:
        exp = datetime.datetime.now() + datetime.timedelta(days=jours)
        utilisateurs_autorises[cid] = exp
        txt = exp.strftime('%d/%m/%Y à %H:%M')
    
    bot.send_message(cid, f"🎉 **ACCÈS DÉVERROUILLÉ !**\n⏳ **Expiration :** {txt}\n👉 Tape /start", parse_mode="Markdown")

def est_autorise(uid):
    if uid == ADMIN_ID: return True
    if uid in utilisateurs_autorises:
        exp = utilisateurs_autorises[uid]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: return True
        del utilisateurs_autorises[uid]
        try: bot.send_message(uid, "⚠️ **Abonnement expiré.**", parse_mode="Markdown")
        except: pass
    return False

# ==========================================
# KILLZONES
# ==========================================

PAIRES_SESSION_ASIE    = ["AUDJPY","CADJPY","CHFJPY","USDJPY","EURJPY","AUDUSD","AUDCAD","XAUUSD","XAGUSD"]
PAIRES_SESSION_LONDRES = ["EURUSD","GBPUSD","EURCHF","USDCHF","CADCHF","EURJPY","EURAUD","XAUUSD","XAGUSD","USOUSD","DAX"]
PAIRES_SESSION_NY      = ["EURUSD","GBPUSD","USDCAD","USDCHF","AUDUSD","XAUUSD","XAGUSD","USOUSD","SP500","US100"]

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
        paires_actives += PAIRES_SESSION_LONDRES
        sessions_actives.append("LONDRES")
    if 12.0 <= h <= 15.0:
        paires_actives += PAIRES_SESSION_NY
        sessions_actives.append("NEW_YORK")

    if not sessions_actives: return None, []
    return "+".join(sessions_actives), list(dict.fromkeys(paires_actives))

def dans_killzone():
    return get_session_active()[0] is not None

def est_gold_ou_index_actif():
    now = datetime.datetime.utcnow()
    j, h = now.weekday(), now.hour + now.minute/60.0
    weekend = (j==4 and h>=21) or j==5 or (j==6 and h<21)
    return not weekend 

def nom_killzone():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute/60.0
    if 7.0  <= h <  8.0:  return "🌏🇬🇧 Asie+Londres (07h-08h)"
    if 0.0  <= h <  7.0:  return "🌏 Asian Killzone (00h-07h)"
    if 8.0  <= h <= 10.0: return "🇬🇧 London Killzone (08h-10h)"
    if 12.0 <= h <= 15.0: return "🇺🇸 New York Killzone (12h-15h)"
    return "⏳ Hors session"

def est_symbole_autorise(symbole):
    if symbole in SYNTHETIC_PAIRS: return "BLOCAGE_TOTAL", "Synthétiques désactivés"
    
    now = datetime.datetime.utcnow()
    j, h = now.weekday(), now.hour + now.minute/60.0
    weekend = (j==4 and h>=21) or j==5 or (j==6 and h<21)

    if weekend:
        return ("AUTORISE","") if symbole in CRYPTO_PAIRS else ("BLOCAGE_TOTAL","Week-end")
    if symbole in CRYPTO_PAIRS: return "BLOCAGE_TOTAL","Cryptos semaine"

    session, paires_session = get_session_active()
    if symbole in COMMODITY_PAIRS or symbole in INDEX_PAIRS:
        return ("AUTORISE", "") if est_gold_ou_index_actif() else ("BLOCAGE_TOTAL", "Week-end")
    
    if session is None: return "HORS_SESSION", "🔒 Hors Killzone"
    if symbole in paires_session: return "AUTORISE", ""
    return "HORS_SESSION", f"🔒 Inactif en session {session}"

# ==========================================
# WEBSOCKET DERIV — OPTIMISÉ
# ==========================================

def prefixer_symbole(s):
    if s in SYNTHETIC_PAIRS: return f"R_{s.replace('V','')}"
    if s in CRYPTO_PAIRS:    return f"cry{s}"
    return f"frx{s}"

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    sym = prefixer_symbole(symbole_brut)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history":sym,"end":"latest","count":250,"style":"candles","granularity":granularite}))
            res = json.loads(ws.recv())
            if "candles" in res: return res["candles"]
        except Exception:
            time.sleep(0.5)
        finally:
            if ws: ws.close()
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    sym = prefixer_symbole(symbole_brut)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=7)
            ws.send(json.dumps({"ticks_history":sym,"end":"latest","count":1,"style":"ticks"}))
            res = json.loads(ws.recv())
            if "history" in res and "prices" in res["history"]:
                return float(res["history"]["prices"][0])
        except Exception:
            time.sleep(0.5)
        finally:
            if ws: ws.close()
    return None

# ==========================================
# MOTEUR KASPER OTE 
# ==========================================

def calculer_ema_cloud(df):
    e72  = ta.trend.EMAIndicator(close=df['close'], window=72).ema_indicator()
    e89  = ta.trend.EMAIndicator(close=df['close'], window=89).ema_indicator()
    e180 = ta.trend.EMAIndicator(close=df['close'], window=180).ema_indicator()
    e200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
    r = "BULL" if e72.iloc[-1]  > e89.iloc[-1]  else "BEAR"
    l = "BULL" if e180.iloc[-1] > e200.iloc[-1] else "BEAR"
    if r=="BULL" and l=="BULL": return "BULL","FORT 🟢🟢"
    if r=="BEAR" and l=="BEAR": return "BEAR","FORT 🔴🔴"
    return r, "MODÉRÉ 🟡"

def trouver_dernier_swing(df, tendance):
    n = 3
    highs, lows = df['high'].values, df['low'].values
    swing_highs, swing_lows = [], []
    for i in range(n, len(highs)-n):
        if all(highs[i]>highs[i-k] for k in range(1,n+1)) and all(highs[i]>highs[i+k] for k in range(1,n+1)):
            swing_highs.append((i, highs[i]))
        if all(lows[i]<lows[i-k]  for k in range(1,n+1)) and all(lows[i]<lows[i+k]  for k in range(1,n+1)):
            swing_lows.append((i, lows[i]))
            
    if not swing_highs or not swing_lows:
        return df['high'].iloc[-40:].max(), df['low'].iloc[-40:].min()
        
    if tendance == "BEAR":
        for sh in reversed(swing_highs[-5:]):
            after = [sl for sl in swing_lows if sl[0]>sh[0]]
            if after: return sh[1], min(after, key=lambda x: x[1])[1]
    else:
        for sl in reversed(swing_lows[-5:]):
            after = [sh for sh in swing_highs if sh[0]>sl[0]]
            if after: return max(after, key=lambda x: x[1])[1], sl[1]
            
    return max(swing_highs[-3:], key=lambda x:x[0])[1], max(swing_lows[-3:], key=lambda x:x[0])[1]

def calculer_zone_ote(sh, sl, tendance):
    diff = sh - sl
    if tendance == "BEAR":
        ob, oh = sl+diff*0.618, sl+diff*0.786
        sl_lvl = sh + diff*0.05
        dist   = abs(oh - sl_lvl)
        tp1, tp15 = oh - dist, oh - dist*1.5
    else:
        ob, oh = sh-diff*0.786, sh-diff*0.618
        sl_lvl = sl - diff*0.05
        dist   = abs(ob - sl_lvl)
        tp1, tp15 = ob + dist, ob + dist*1.5
    return {"ote_bas":round(ob,5),"ote_haut":round(oh,5),"sl":round(sl_lvl,5),"tp_1r":round(tp1,5),"tp_15r":round(tp15,5)}

def detecter_reaction_ote(df, zone, tendance):
    last, prev = df.iloc[-2], df.iloc[-3]
    px = last['close']
    dans  = zone["ote_bas"] <= px <= zone["ote_haut"]
    pdans = zone["ote_bas"] <= prev['close'] <= zone["ote_haut"]
    if not (dans or pdans): return False, "Hors zone OTE"
    
    corps, taille = abs(last['close']-last['open']), last['high']-last['low']
    meche_h = last['high']-max(last['open'],last['close'])
    meche_b = min(last['open'],last['close'])-last['low']
    if taille == 0: return False, "Bougie doji"
    
    if tendance == "BEAR":
        if prev['close']>prev['open'] and last['close']<last['open'] and last['close']<prev['open']: return True,"🕯️ Engulfing Baissier"
        if meche_h > corps*2.0: return True,"📍 Pin Bar Baissier"
        if last['close']<last['open'] and corps>taille*0.4: return True,"📉 Rejet Baissier"
    else:
        if prev['close']<prev['open'] and last['close']>last['open'] and last['close']>prev['open']: return True,"🕯️ Engulfing Haussier"
        if meche_b > corps*2.0: return True,"📍 Pin Bar Haussier"
        if last['close']>last['open'] and corps>taille*0.4: return True,"📈 Rejet Haussier"
    return False,"Pas de réaction nette"

def analyser_kasper_complet(symbole):
    c5, c1h = obtenir_donnees_deriv(symbole, 300), obtenir_donnees_deriv(symbole, 3600)
    if not c5 or not c1h: return None
    try:
        df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
        dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
        tendance, force = calculer_ema_cloud(dfh)
        sh, sl = trouver_dernier_swing(df5, tendance)
        if (sh-sl) < 0.3: return None
        
        zone = calculer_zone_ote(sh, sl, tendance)
        px = df5['close'].iloc[-1]
        
        if tendance=="BEAR" and px > zone["sl"]: return None
        if tendance=="BULL" and px < zone["sl"]: return None
        
        react, msg_r = detecter_reaction_ote(df5, zone, tendance)
        if not react: return None
        
        risque, recomp = abs(px-zone["sl"]), abs(zone["tp_15r"]-px)
        rr = round(recomp/risque,2) if risque>0 else 0
        if rr < 1.5: return None
        
        return {
            "action": "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force":force, "msg":msg_r, "sh":round(sh,3), "sl_swing":round(sl,3),
            "zone":zone, "sl":zone["sl"], "tp1":zone["tp_1r"], "tp":zone["tp_15r"], "rr":rr, "px":round(px,3), "kz":nom_killzone()
        }
    except Exception as e:
        print(f"[Kasper/{symbole}] {e}", flush=True)
    return None

# ==========================================
# GESTION DES TRADES & SCANNER
# ==========================================

def nettoyer_trades_bloques():
    now = time.time()
    for uid in list(trades_en_cours.keys()):
        t = trades_en_cours[uid]
        if now - t.get('ts', now) > t.get('dur',300)+120:
            del trades_en_cours[uid]

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            nettoyer_trades_bloques()
            libres = [u for u in utilisateurs_actifs if est_autorise(u) and u not in trades_en_cours]
            if not libres: continue

            for paire in ELITE_PAIRS_MT5 + FOREX_PAIRS:
                statut,_ = est_symbole_autorise(paire)
                if statut != "AUTORISE": continue

                cle = f"{paire}_KASPER"
                if cle in derniere_alerte_auto and time.time()-derniere_alerte_auto[cle]<300: continue

                res = analyser_kasper_complet(paire)
                if not res: continue

                px = obtenir_prix_actuel_deriv(paire) or res['px']
                signaux_cache[cle] = {
                    'time':time.time(), 'action':res['action'], 'sc':9.5,
                    'mt5_sl':res['sl'], 'mt5_tp':res['tp'], 'mt5_tp1':res['tp1'],
                    'mt5_rr':res['rr'], 'zone':res['zone'],
                    'sh':res['sh'], 'sl_swing':res['sl_swing'],
                    'force':res['force'], 'msg':res['msg'], 'kz':res['kz'], 'dur':300
                }
                derniere_alerte_auto[cle] = time.time()

                nom = NOMS_AFFICHAGE.get(paire, paire)
                dir_ = "🟢 BUY" if "BUY" in res['action'] else "🔴 SELL"
                z = res['zone']

                for uid in libres:
                    pf = plateforme_trading.get(uid,"MT5")
                    if pf=="MT5" and paire not in ELITE_PAIRS_MT5: continue
                    if pf=="POCKET" and paire not in FOREX_PAIRS: continue

                    markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚡ Copier signal {nom}", callback_data=f"set_{paire}"))
                    txt = (f"🎯 **KASPER OTE — {nom}** {dir_}\n☁️ EMA Cloud : `{res['force']}`\n📍 {res['msg']}\n"
                           f"🔑 {res['kz']}\n🟡 Zone OTE : `{z['ote_bas']:.3f}` → `{z['ote_haut']:.3f}`\n⚖️ R/R : `{res['rr']}R`\n_90s pour agir_")
                    try: bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                    except: pass
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
    if not est_autorise(uid): return bot.send_message(uid,"🔒 Accès restreint. Utilise /vip VOTRE-CLÉ")
    utilisateurs_actifs.add(uid)
    plateforme_trading.setdefault(uid,"MT5")
    kz = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    bot.send_message(uid, f"🏴‍☠️ **TERMINAL PRIME V35** 🔥\n⏰ **Killzone actuelle :** {kz}", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_pf(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if plateforme_trading.get(uid,"MT5")=="POCKET":
        plateforme_trading[uid]="MT5"
        bot.send_message(uid,"📈 **MT5 ACTIVÉ**",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")
    else:
        plateforme_trading[uid]="POCKET"
        bot.send_message(uid,"🏦 **POCKET ACTIVÉ**",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE","📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    pf = plateforme_trading.get(uid,"MT5")
    markup = InlineKeyboardMarkup(row_width=3)

    if pf == "MT5":
        markup.add(InlineKeyboardButton("📈 S&P 500", callback_data="set_SP500"), InlineKeyboardButton("💹 NASDAQ", callback_data="set_US100"), InlineKeyboardButton("🇩🇪 DAX", callback_data="set_DAX"))
        markup.add(InlineKeyboardButton("🥇 GOLD", callback_data="set_XAUUSD"), InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"), InlineKeyboardButton("🛢 PÉTROLE", callback_data="set_USOUSD"))
    else:
        markup.add(InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇬🇧 GBP/USD", callback_data="set_GBPUSD"), InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"))
    bot.send_message(uid,"🎯 **Sélectionne ta cible :**",reply_markup=markup,parse_mode="Markdown")

def envoyer_signal_manuel(uid, actif):
    """Logique extraite pour éviter les faux objets callbacks."""
    cle = f"{actif}_KASPER"
    cache = signaux_cache.get(cle)
    
    if not cache or (time.time()-cache['time']) > 90:
        return bot.send_message(uid, f"⏱️ **Signal OTE expiré sur {NOMS_AFFICHAGE.get(actif,actif)}**\nLa zone a peut-être bougé.", parse_mode="Markdown")

    px = obtenir_prix_actuel_deriv(actif) or 0
    nom = NOMS_AFFICHAGE.get(actif, actif)
    z = cache.get('zone',{})
    dir_ = "🟢 BUY MARKET" if "BUY" in cache['action'] else "🔴 SELL MARKET"

    fmt = ".0f" if actif in INDEX_PAIRS else ".2f" if actif in COMMODITY_PAIRS else ".5f"

    signal = (
        f"🎯 **KASPER SNIPER — {nom}**\n━━━━━━━━━━━━━━━━━━━━━━\n{dir_}\n"
        f"☁️ EMA Cloud : `{cache.get('force','—')}`\n🔑 {cache.get('kz','—')}\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟡 **Zone OTE :** `{z.get('ote_bas',0):{fmt}}` → `{z.get('ote_haut',0):{fmt}}`\n"
        f"💰 **Prix actuel :** `{px:{fmt}}`\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 **SL :** `{cache['mt5_sl']:{fmt}}` | 🚀 **TP 1.5R :** `{cache['mt5_tp']:{fmt}}`\n"
        f"⚖️ **R/R :** `{cache['mt5_rr']:.2f}R`\n━━━━━━━━━━━━━━━━━━━━━━\n{cache.get('msg','—')}"
    )
    bot.send_message(uid, signal, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text=="🚀 LANCER L'ANALYSE")
def lancer(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    nettoyer_trades_bloques()
    if uid in trades_en_cours: return bot.send_message(uid,"⚠️ Trade en cours.")
    
    actif = user_prefs.get(uid)
    if not actif: return bot.send_message(uid,"⚠️ Choisis d'abord une cible !")
    envoyer_signal_manuel(uid, actif)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): return
    nettoyer_trades_bloques()
    
    if uid in trades_en_cours:
        try: bot.answer_callback_query(call.id,"⚠️ Trade en cours !",show_alert=True)
        except: pass
        return

    actif = call.data.replace("set_","")
    user_prefs[uid] = actif
    try: bot.delete_message(uid, call.message.message_id)
    except: pass
    
    envoyer_signal_manuel(uid, actif)

@bot.message_handler(commands=['kasper'])
def cmd_kasper(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    parts = message.text.split()
    symbole = parts[1].upper() if len(parts)>1 else "XAUUSD"
    
    if symbole not in ALL_PAIRS:
        return bot.send_message(uid,"❌ Symbole non reconnu.\nEx: /kasper XAUUSD")
        
    msg_obj = bot.send_message(uid,f"🔍 _Analyse Kasper OTE sur {NOMS_AFFICHAGE.get(symbole,symbole)}..._",parse_mode="Markdown")
    res = analyser_kasper_complet(symbole)
    nom = NOMS_AFFICHAGE.get(symbole, symbole)
    
    fmt = ".0f" if symbole in INDEX_PAIRS else ".2f" if symbole in COMMODITY_PAIRS else ".5f"

    if not res:
        c5, c1h = obtenir_donnees_deriv(symbole,300), obtenir_donnees_deriv(symbole,3600)
        if c5 and c1h:
            df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
            dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
            t, f = calculer_ema_cloud(dfh)
            sh, sl = trouver_dernier_swing(df5, t)
            z = calculer_zone_ote(sh, sl, t)
            px = df5['close'].iloc[-1]
            kz = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
            texte = (f"👁️ **KASPER OTE — {nom}**\n☁️ EMA Cloud H1 : `{f}`\n"
                     f"🟡 Zone OTE : `{z['ote_bas']:{fmt}}` → `{z['ote_haut']:{fmt}}`\n"
                     f"💰 Prix actuel : `{px:{fmt}}`\n🛑 SL : `{z['sl']:{fmt}}` | 🚀 TP 1.5R : `{z['tp_15r']:{fmt}}`\n"
                     f"⏳ En attente d'une réaction dans la zone")
        else:
            texte = "⚠️ Données indisponibles. Réessaie."
        return bot.edit_message_text(texte,uid,msg_obj.message_id,parse_mode="Markdown")

    z = res['zone']
    kz_ = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    texte=(f"🎯 **KASPER OTE — {nom}**\n━━━━━━━━━━━━━━━━━━━━━━\n{'🟢 BUY' if res['tendance']=='BULL' else '🔴 SELL'}\n"
           f"☁️ EMA Cloud : `{res['force']}` | ⏰ {kz_}\n━━━━━━━━━━━━━━━━━━━━━━\n"
           f"🟡 Zone OTE : `{z['ote_bas']:{fmt}}` → `{z['ote_haut']:{fmt}}`\n💰 Prix : `{res['px']:{fmt}}`\n"
           f"🛑 SL : `{res['sl']:{fmt}}` | 🚀 TP 1.5R : `{res['tp']:{fmt}}`\n"
           f"⚖️ R/R : `{res['rr']}R`\n━━━━━━━━━━━━━━━━━━━━━━\n{res['msg']}")
    bot.edit_message_text(texte,uid,msg_obj.message_id,parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================

if __name__=="__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    print("⬛ TERMINAL PRIME V35 — Démarré.", flush=True)
    bot.infinity_polling()
