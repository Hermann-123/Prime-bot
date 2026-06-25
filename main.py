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

TELEGRAM_TOKEN = "8658287331:AAGNP-p5FG1JNd5DE-lHjYxq7DJ4L_Z1p1w"
bot            = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID       = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY    = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# LISTES DE PAIRES — V35
# ==========================================

SYNTHETIC_PAIRS = ["V10","V25","V50","V75","V100"]

# ✅ NOUVEAU : Indices boursiers MT5 ajoutés
INDEX_PAIRS     = ["SP500","US100","DAX"]

COMMODITY_PAIRS = ["XAUUSD","XAGUSD","USOUSD"]  # Gold, Argent, Pétrole
CRYPTO_PAIRS    = ["BTCUSD","ETHUSD","LTCUSD"]
FOREX_PAIRS     = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD","AUDJPY",
                   "EURAUD","EURUSD","AUDCAD","USDCHF","CADCHF","EURCHF",
                   "USDJPY","GBPUSD"]

# ✅ V35 : MT5 = UNIQUEMENT Indices + Commodités (Synthétiques retirés)
ELITE_PAIRS_MT5 = INDEX_PAIRS + COMMODITY_PAIRS
ALL_PAIRS       = INDEX_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS + CRYPTO_PAIRS

# Noms affichés dans Telegram
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
def run():   app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

# ==========================================
# ✅ KEYGEN CORRIGÉ — Gestion d'erreur complète
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
    """
    ✅ FIX KEYGEN : Gestion d'erreur complète + message d'aide si mauvais argument.
    Usage : /keygen 1s | 2s | 1m | 3m | 6m | 1a | vie | <nombre_jours>
    """
    if message.chat.id != ADMIN_ID:
        return  # Silencieux pour les non-admins

    parts = message.text.strip().split()

    # ── Pas d'argument → afficher l'aide ─────────────────────────────────
    if len(parts) < 2:
        aide = (
            "⚙️ **GÉNÉRATEUR DE CLÉS VIP**\n"
            "──────────────────\n"
            "Usage : `/keygen <durée>`\n\n"
            "**Durées disponibles :**\n"
            "`/keygen 1s`  → 1 Semaine (7j)\n"
            "`/keygen 2s`  → 2 Semaines (14j)\n"
            "`/keygen 1m`  → 1 Mois (30j)\n"
            "`/keygen 3m`  → 3 Mois (90j)\n"
            "`/keygen 6m`  → 6 Mois (180j)\n"
            "`/keygen 1a`  → 1 An (365j)\n"
            "`/keygen vie` → À vie 👑\n"
            "`/keygen 45`  → Nombre de jours personnalisé"
        )
        return bot.send_message(message.chat.id, aide, parse_mode="Markdown")

    arg = parts[1].lower().strip()

    # ── Argument reconnu dans le dictionnaire ────────────────────────────
    if arg in DUREES_VALIDES:
        jours, label = DUREES_VALIDES[arg]
    else:
        # ── Essai comme nombre de jours ──────────────────────────────────
        try:
            jours = int(arg)
            if jours <= 0:
                return bot.send_message(
                    message.chat.id,
                    "❌ **Erreur :** Le nombre de jours doit être positif.\nEx: `/keygen 30`",
                    parse_mode="Markdown"
                )
            label = f"{jours} jours"
        except ValueError:
            # ── Argument complètement invalide ───────────────────────────
            valides = " | ".join(DUREES_VALIDES.keys())
            return bot.send_message(
                message.chat.id,
                f"❌ **Argument invalide :** `{arg}`\n\n"
                f"**Valeurs acceptées :** `{valides}`\n"
                f"Ou un nombre de jours : `/keygen 30`",
                parse_mode="Markdown"
            )

    # ── Génération de la clé ──────────────────────────────────────────────
    cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    cles_generees[cle] = jours

    msg = (
        f"✅ **CLÉ VIP GÉNÉRÉE**\n"
        f"──────────────────\n"
        f"🔑 **Clé :** `{cle}`\n"
        f"⏳ **Durée :** {label}\n"
        f"──────────────────\n"
        f"_L'abonné active avec :_ `/vip {cle}`"
    )
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")


@bot.message_handler(commands=['vip'])
def activer_vip(message):
    cid = message.chat.id
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(cid, "⚠️ Usage : `/vip VOTRE-CLÉ`", parse_mode="Markdown")
    cle = parts[1].strip()
    if cle not in cles_generees:
        return bot.send_message(cid,
            "❌ **Clé invalide, expirée ou déjà utilisée.**\n"
            "Contacte l'admin pour obtenir une nouvelle clé.",
            parse_mode="Markdown")
    jours = cles_generees.pop(cle)
    if jours == "LIFETIME":
        utilisateurs_autorises[cid] = "LIFETIME"
        txt = "À VIE 👑"
    else:
        exp = datetime.datetime.now() + datetime.timedelta(days=jours)
        utilisateurs_autorises[cid] = exp
        txt = exp.strftime('%d/%m/%Y à %H:%M')
    bot.send_message(cid,
        f"🎉 **ACCÈS TERMINAL PRIME DÉVERROUILLÉ !**\n"
        f"──────────────────\n"
        f"⏳ **Expiration :** {txt}\n\n"
        f"👉 Tape /start pour initialiser ton tableau de bord.",
        parse_mode="Markdown")


@bot.message_handler(commands=['abonnes'])
def lister_abonnes(message):
    """Commande admin : voir tous les abonnés actifs."""
    if message.chat.id != ADMIN_ID: return
    if not utilisateurs_autorises:
        return bot.send_message(message.chat.id, "Aucun abonné actif.")
    lignes = ["👥 **ABONNÉS ACTIFS :**\n──────────────────"]
    now = datetime.datetime.now()
    for uid, exp in utilisateurs_autorises.items():
        if uid == ADMIN_ID: continue
        if exp == "LIFETIME":
            statut = "👑 À vie"
        elif now < exp:
            reste = (exp - now).days
            statut = f"✅ {reste}j restants (exp: {exp.strftime('%d/%m/%Y')})"
        else:
            statut = "❌ Expiré"
        lignes.append(f"• `{uid}` → {statut}")
    bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")


@bot.message_handler(commands=['cles'])
def lister_cles(message):
    """Commande admin : voir les clés en attente d'activation."""
    if message.chat.id != ADMIN_ID: return
    if not cles_generees:
        return bot.send_message(message.chat.id, "Aucune clé en attente.")
    lignes = ["🔑 **CLÉS EN ATTENTE :**\n──────────────────"]
    for cle, jours in cles_generees.items():
        dur = "À VIE" if jours == "LIFETIME" else f"{jours}j"
        lignes.append(f"`{cle}` → {dur}")
    bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")


def est_autorise(uid):
    if uid == ADMIN_ID: return True
    if uid in utilisateurs_autorises:
        exp = utilisateurs_autorises[uid]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: return True
        del utilisateurs_autorises[uid]
        try: bot.send_message(uid, "⚠️ **Abonnement expiré.**\nContacte l'admin pour renouveler.", parse_mode="Markdown")
        except: pass
    return False

# ==========================================
# KILLZONES
# ==========================================

# Paires autorisées par session (ICT Killzones)
PAIRES_SESSION_ASIE    = ["AUDJPY","CADJPY","CHFJPY","USDJPY","EURJPY",
                           "AUDUSD","AUDCAD","XAUUSD","XAGUSD"]
PAIRES_SESSION_LONDRES = ["EURUSD","GBPUSD","EURCHF","USDCHF","CADCHF",
                           "EURJPY","EURAUD","XAUUSD","XAGUSD","USOUSD","DAX"]
PAIRES_SESSION_NY      = ["EURUSD","GBPUSD","USDCAD","USDCHF","AUDUSD",
                           "XAUUSD","XAGUSD","USOUSD","SP500","US100"]

def get_session_active():
    """Retourne la session active et les paires autorisées."""
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute/60.0
    # Session Asiatique : 00h00 → 08h00 GMT
    if 0.0 <= h < 8.0:
        return "ASIE", PAIRES_SESSION_ASIE
    # Chevauchement Asie/Londres : 07h-08h déjà inclus dans Asie et Londres
    # Session Londonienne : 07h00 → 10h00 GMT
    if 7.0 <= h <= 10.0:
        return "LONDRES", PAIRES_SESSION_LONDRES
    # Session New York : 12h00 → 15h00 GMT
    if 12.0 <= h <= 15.0:
        return "NEW_YORK", PAIRES_SESSION_NY
    return None, []

def dans_killzone():
    session, _ = get_session_active()
    return session is not None

def nom_killzone():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute/60.0
    if 0.0  <= h <  8.0:  return "🌏 Asian Killzone (00h-08h)"
    if 7.0  <= h <= 10.0: return "🇬🇧 London Killzone (07h-10h)"
    if 12.0 <= h <= 15.0: return "🇺🇸 New York Killzone (12h-15h)"
    return "⏳ Hors session"

def est_symbole_autorise(symbole):
    # Synthétiques : toujours actifs (24h/24, 7j/7)
    if symbole in SYNTHETIC_PAIRS: return "AUTORISE", ""

    now = datetime.datetime.utcnow()
    j   = now.weekday()
    h   = now.hour + now.minute/60.0
    weekend = (j==4 and h>=21) or j==5 or (j==6 and h<21)

    if weekend:
        return ("AUTORISE","") if symbole in CRYPTO_PAIRS else ("BLOCAGE_TOTAL","Week-end")
    if symbole in CRYPTO_PAIRS: return "BLOCAGE_TOTAL","Cryptos semaine"

    # Vérifier si le symbole est actif dans la session en cours
    session, paires_session = get_session_active()

    if session is None:
        return "HORS_SESSION", f"🔒 Hors Killzone (prochaine session à 00h ou 07h GMT)"

    if symbole in paires_session:
        return "AUTORISE", ""

    # Indices boursiers : seulement Londres et NY
    if symbole in INDEX_PAIRS:
        if session in ("LONDRES","NEW_YORK"): return "AUTORISE", ""
        return "HORS_SESSION", f"📊 Indices inactifs en session {session}"

    return "HORS_SESSION", f"🔒 {symbole} inactif en session {session}"

# ==========================================
# WEBSOCKET DERIV — PREFIXES
# ==========================================

def prefixer_symbole(s):
    """
    ✅ Préfixes Deriv corrects pour chaque type d'actif.
    Indices boursiers = frx + symbole sur Deriv.
    """
    if s in SYNTHETIC_PAIRS: return f"R_{s.replace('V','')}"
    if s in CRYPTO_PAIRS:    return f"cry{s}"
    # Indices et Commodités et Forex → préfixe frx
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
            if "candles" in res and "error" not in res: return res["candles"]
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
# MOTEUR KASPER OTE (EMA Cloud + Fibonacci)
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
    highs = df['high'].values
    lows  = df['low'].values
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
    return {"ote_bas":round(ob,5),"ote_haut":round(oh,5),"sl":round(sl_lvl,5),
            "tp_1r":round(tp1,5),"tp_15r":round(tp15,5)}

def detecter_reaction_ote(df, zone, tendance):
    last = df.iloc[-2]  # Bougie FERMÉE
    prev = df.iloc[-3]
    px   = last['close']
    dans = zone["ote_bas"] <= px <= zone["ote_haut"]
    pdans= zone["ote_bas"] <= prev['close'] <= zone["ote_haut"]
    if not (dans or pdans): return False, "Hors zone OTE"
    corps   = abs(last['close']-last['open'])
    taille  = last['high']-last['low']
    meche_h = last['high']-max(last['open'],last['close'])
    meche_b = min(last['open'],last['close'])-last['low']
    if taille == 0: return False, "Bougie doji"
    if tendance == "BEAR":
        if prev['close']>prev['open'] and last['close']<last['open'] and last['close']<prev['open']:
            return True,"🕯️ Engulfing Baissier (bougie fermée)"
        if meche_h > corps*2.0: return True,"📍 Pin Bar Baissier (bougie fermée)"
        if last['close']<last['open'] and corps>taille*0.4: return True,"📉 Rejet Baissier confirmé"
    else:
        if prev['close']<prev['open'] and last['close']>last['open'] and last['close']>prev['open']:
            return True,"🕯️ Engulfing Haussier (bougie fermée)"
        if meche_b > corps*2.0: return True,"📍 Pin Bar Haussier (bougie fermée)"
        if last['close']>last['open'] and corps>taille*0.4: return True,"📈 Rejet Haussier confirmé"
    return False,"Pas de réaction nette"

def analyser_kasper_complet(symbole):
    c5 = obtenir_donnees_deriv(symbole, 300)
    c1h= obtenir_donnees_deriv(symbole, 3600)
    if not c5 or not c1h: return None
    try:
        df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
        dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
        tendance, force = calculer_ema_cloud(dfh)
        sh, sl = trouver_dernier_swing(df5, tendance)
        if (sh-sl) < 0.3: return None
        zone = calculer_zone_ote(sh, sl, tendance)
        px = df5['close'].iloc[-1]
        # Invalidation si prix au-delà du SL
        if tendance=="BEAR" and px > zone["sl"]: return None
        if tendance=="BULL" and px < zone["sl"]: return None
        react, msg_r = detecter_reaction_ote(df5, zone, tendance)
        if not react: return None
        risque = abs(px-zone["sl"])
        recomp = abs(zone["tp_15r"]-px)
        rr = round(recomp/risque,2) if risque>0 else 0
        if rr < 1.5: return None
        return {
            "action":   "🟢 ACHAT (BUY)" if tendance=="BULL" else "🔴 VENTE (SELL)",
            "tendance": tendance, "force":force, "msg":msg_r,
            "sh":round(sh,3), "sl_swing":round(sl,3),
            "zone":zone, "sl":zone["sl"], "tp1":zone["tp_1r"],
            "tp":zone["tp_15r"], "rr":rr, "px":round(px,3),
            "kz":nom_killzone()
        }
    except Exception as e:
        print(f"[Kasper/{symbole}] {e}", flush=True)
    return None

# ==========================================
# NETTOYAGE TRADES BLOQUÉS
# ==========================================

def nettoyer_trades_bloques():
    now = time.time()
    for uid in list(trades_en_cours.keys()):
        t = trades_en_cours[uid]
        if now - t.get('ts', now) > t.get('dur',300)+120:
            del trades_en_cours[uid]

# ==========================================
# SCANNER AUTOMATIQUE — V35
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            nettoyer_trades_bloques()
            if not dans_killzone():
                time.sleep(60)
                continue

            libres = [u for u in utilisateurs_actifs if est_autorise(u) and u not in trades_en_cours]
            if not libres: continue

            # ── Scan MT5 : Indices + Commodités (Synthétiques retirés V35) ──
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

                nom  = NOMS_AFFICHAGE.get(paire, f"{paire[:3]}/{paire[3:]}")
                dir_ = "🟢 BUY" if "BUY" in res['action'] else "🔴 SELL"
                z    = res['zone']

                for uid in libres:
                    pf = plateforme_trading.get(uid,"MT5")
                    if pf=="MT5"    and paire not in ELITE_PAIRS_MT5: continue
                    if pf=="POCKET" and paire not in FOREX_PAIRS:     continue

                    markup = InlineKeyboardMarkup().add(
                        InlineKeyboardButton(f"⚡ Copier signal {nom}", callback_data=f"set_{paire}")
                    )
                    txt = (
                        f"🎯 **KASPER OTE — {nom}** {dir_}\n"
                        f"☁️ EMA Cloud : `{res['force']}`\n"
                        f"📍 {res['msg']}\n"
                        f"🔑 {res['kz']}\n"
                        f"🟡 Zone OTE : `{z['ote_bas']:.3f}` → `{z['ote_haut']:.3f}`\n"
                        f"⚖️ R/R : `{res['rr']}R`\n"
                        f"_90s pour agir_"
                    )
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
    markup.row(
        KeyboardButton("🏦 BROKER: POCKET" if pf=="POCKET" else "📈 BROKER: MT5"),
        KeyboardButton("⏰ HEURES DE TRADING")
    )
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid): return bot.send_message(uid,"🔒 Accès restreint. Utilise /vip VOTRE-CLÉ pour activer.")
    utilisateurs_actifs.add(uid)
    plateforme_trading.setdefault(uid,"MT5")
    kz = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    bot.send_message(uid,
        f"🏴‍☠️ **TERMINAL PRIME V35** 🔥\n"
        f"──────────────────\n"
        f"✅ **Actifs MT5 (Focus Élite) :**\n"
        f"   📈 S&P 500 | 💹 NASDAQ 100 | 🇩🇪 DAX\n"
        f"   🥇 Gold | 🥈 Argent | 🛢 Pétrole\n"
        f"──────────────────\n"
        f"✅ **Keygen corrigé** — /keygen 1s / 2s / 1m / 3m / 6m / 1a / vie\n"
        f"✅ **Moteur Kasper OTE** — EMA Cloud + Fibonacci 0.618-0.786\n"
        f"✅ **3 Killzones** — Asie (00h-08h) | Londres (07h-10h) | NY (12h-15h)\n"
        f"──────────────────\n"
        f"⏰ **Killzone actuelle :** {kz}",
        reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_pf(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if plateforme_trading.get(uid,"MT5")=="POCKET":
        plateforme_trading[uid]="MT5"
        bot.send_message(uid,"📈 **MT5 ACTIVÉ**\n🥇 Gold | 🥈 Argent | 🛢 Pétrole | 📈 S&P500 | 💹 NASDAQ | 🇩🇪 DAX",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")
    else:
        plateforme_trading[uid]="POCKET"
        bot.send_message(uid,"🏦 **POCKET ACTIVÉ**\nForex Binaire",reply_markup=obtenir_clavier(uid),parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text=="⏰ HEURES DE TRADING")
def horaires(message):
    kz = "🟢 EN COURS" if dans_killzone() else "🔴 INACTIVE"
    bot.send_message(message.chat.id,
        f"🕒 **KILLZONES KASPER OTE**\n\n"
        f"🌏 **Asie     :** 00:00 – 08:00 GMT\n"
        f"🇬🇧 **Londres  :** 07:00 – 10:00 GMT\n"
        f"🇺🇸 **New York :** 12:00 – 15:00 GMT\n\n"
        f"⏰ **Statut actuel :** {kz}\n\n"
        f"_Le scanner suit les paires adaptées à chaque session._",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE","📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    pf  = plateforme_trading.get(uid,"MT5")
    markup = InlineKeyboardMarkup(row_width=3)

    if pf == "MT5":
        # ── Indices boursiers (NOUVEAU) ───────────────────────────────────
        markup.add(
            InlineKeyboardButton("📈 S&P 500",    callback_data="set_SP500"),
            InlineKeyboardButton("💹 NASDAQ 100", callback_data="set_US100"),
            InlineKeyboardButton("🇩🇪 DAX",       callback_data="set_DAX")
        )
        # ── Commodités ────────────────────────────────────────────────────
        markup.add(
            InlineKeyboardButton("🥇 GOLD",    callback_data="set_XAUUSD"),
            InlineKeyboardButton("🥈 ARGENT",  callback_data="set_XAGUSD"),
            InlineKeyboardButton("🛢 PÉTROLE", callback_data="set_USOUSD")
        )
        bot.send_message(uid,"🎯 **Sélectionne ta cible MT5 :**",reply_markup=markup,parse_mode="Markdown")
    else:
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"),
            InlineKeyboardButton("🇬🇧 GBP/USD", callback_data="set_GBPUSD"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
        )
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"),
            InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY")
        )
        markup.add(
            InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"),
            InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"),
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD")
        )
        bot.send_message(uid,"🎯 **Sélectionne ta cible Pocket Forex :**",reply_markup=markup,parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text=="🚀 LANCER L'ANALYSE")
def lancer(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    nettoyer_trades_bloques()
    if uid in trades_en_cours: return bot.send_message(uid,"⚠️ Trade en cours.")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(uid,"⚠️ Choisis d'abord une cible !")
    save_devise(type('obj',(object,),{
        'data':f"set_{actif}",'message':message,'from_user':message.from_user,'id':0
    })())

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid   = call.message.chat.id
    if not est_autorise(uid): return
    nettoyer_trades_bloques()
    if uid in trades_en_cours:
        try: bot.answer_callback_query(call.id,"⚠️ Trade en cours !",show_alert=True)
        except: pass
        return

    actif = call.data.replace("set_","")
    user_prefs[getattr(call,'from_user',type('o',(object,),{'id':uid})()).id] = actif
    cle   = f"{actif}_KASPER"
    cache = signaux_cache.get(cle)

    try: bot.delete_message(uid, call.message.message_id)
    except: pass

    if not cache or (time.time()-cache['time']) > 90:
        return bot.send_message(uid,
            f"⏱️ **Signal OTE expiré sur {NOMS_AFFICHAGE.get(actif,actif)}**\n"
            "La zone a peut-être bougé. Attends la prochaine alerte du radar.",
            parse_mode="Markdown")

    px   = obtenir_prix_actuel_deriv(actif) or 0
    nom  = NOMS_AFFICHAGE.get(actif, f"{actif[:3]}/{actif[3:]}" if len(actif)>4 else actif)
    z    = cache.get('zone',{})
    dir_ = "🟢 BUY MARKET" if "BUY" in cache['action'] else "🔴 SELL MARKET"

    # Format prix adapté (indices = 0 décimales, forex = 5)
    if actif in INDEX_PAIRS:
        fmt = ".0f"
    elif actif in COMMODITY_PAIRS:
        fmt = ".2f"
    else:
        fmt = ".5f"

    signal = (
        f"🎯 **KASPER SNIPER — {nom}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{dir_}\n"
        f"☁️ EMA Cloud : `{cache.get('force','—')}`\n"
        f"🔑 {cache.get('kz','—')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Swing High : `{cache.get('sh',0):{fmt}}`\n"
        f"📍 Swing Low  : `{cache.get('sl_swing',0):{fmt}}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟡 **Zone OTE (0.618–0.786) :**\n"
        f"   `{z.get('ote_bas',0):{fmt}}` → `{z.get('ote_haut',0):{fmt}}`\n"
        f"💰 **Prix actuel :** `{px:{fmt}}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 **STOP LOSS :**  `{cache['mt5_sl']:{fmt}}`\n"
        f"🎯 **TP 1R     :**  `{cache['mt5_tp1']:{fmt}}`\n"
        f"🚀 **TP 1.5R   :**  `{cache['mt5_tp']:{fmt}}`\n"
        f"⚖️ **R/R       :**  `{cache['mt5_rr']:.2f}R`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{cache.get('msg','—')}\n"
        f"⚠️ _Gestion : 1% du capital max_"
    )
    bot.send_message(uid, signal, parse_mode="Markdown")

# ──────────────────────────────────────────
# COMMANDE /kasper — Analyse manuelle
# ──────────────────────────────────────────

@bot.message_handler(commands=['kasper'])
def cmd_kasper(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    parts   = message.text.split()
    symbole = parts[1].upper() if len(parts)>1 else "XAUUSD"
    if symbole not in ALL_PAIRS:
        return bot.send_message(uid,"❌ Symbole non reconnu.\nEx: /kasper XAUUSD | /kasper SP500 | /kasper EURUSD")
    msg_obj = bot.send_message(uid,f"🔍 _Analyse Kasper OTE sur {NOMS_AFFICHAGE.get(symbole,symbole)}..._",parse_mode="Markdown")
    res = analyser_kasper_complet(symbole)
    nom = NOMS_AFFICHAGE.get(symbole, symbole)
    if actif in INDEX_PAIRS:   fmt = ".0f"
    elif actif in COMMODITY_PAIRS: fmt = ".2f"
    else: fmt = ".5f"
    if not res:
        # Affichage de la zone sans signal confirmé
        c5  = obtenir_donnees_deriv(symbole,300)
        c1h = obtenir_donnees_deriv(symbole,3600)
        if c5 and c1h:
            df5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c5])
            dfh = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in c1h])
            t,f = calculer_ema_cloud(dfh)
            sh,sl = trouver_dernier_swing(df5,t)
            z = calculer_zone_ote(sh,sl,t)
            px = df5['close'].iloc[-1]
            kz = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
            texte=(f"👁️ **KASPER OTE — {nom}**\n━━━━━━━━━━━━━━━━━━━━━━\n"
                   f"☁️ EMA Cloud H1 : `{f}` ({'🟢 BULL' if t=='BULL' else '🔴 BEAR'})\n"
                   f"📍 Swing H/L : `{sh:{fmt}}` / `{sl:{fmt}}`\n"
                   f"🟡 Zone OTE : `{z['ote_bas']:{fmt}}` → `{z['ote_haut']:{fmt}}`\n"
                   f"💰 Prix actuel : `{px:{fmt}}`\n"
                   f"🛑 SL : `{z['sl']:{fmt}}` | 🚀 TP 1.5R : `{z['tp_15r']:{fmt}}`\n"
                   f"━━━━━━━━━━━━━━━━━━━━━━\n"
                   f"⏳ En attente d'une réaction dans la zone\n"
                   f"⏰ Killzone : {kz}")
        else:
            texte = "⚠️ Données indisponibles. Réessaie dans quelques secondes."
        return bot.edit_message_text(texte,uid,msg_obj.message_id,parse_mode="Markdown")

    z   = res['zone']
    kz_ = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    texte=(f"🎯 **KASPER OTE — {nom}**\n━━━━━━━━━━━━━━━━━━━━━━\n"
           f"{'🟢 BUY' if res['tendance']=='BULL' else '🔴 SELL'}\n"
           f"☁️ EMA Cloud : `{res['force']}`  | ⏰ {kz_}\n"
           f"━━━━━━━━━━━━━━━━━━━━━━\n"
           f"📍 Swing H : `{res['sh']:{fmt}}` | L : `{res['sl_swing']:{fmt}}`\n"
           f"🟡 Zone OTE : `{z['ote_bas']:{fmt}}` → `{z['ote_haut']:{fmt}}`\n"
           f"💰 Prix : `{res['px']:{fmt}}`\n"
           f"━━━━━━━━━━━━━━━━━━━━━━\n"
           f"🛑 SL : `{res['sl']:{fmt}}`\n"
           f"🎯 TP 1R : `{res['tp1']:{fmt}}`\n"
           f"🚀 TP 1.5R : `{res['tp']:{fmt}}`\n"
           f"⚖️ R/R : `{res['rr']}R`\n"
           f"━━━━━━━━━━━━━━━━━━━━━━\n"
           f"{res['msg']}")
    bot.edit_message_text(texte,uid,msg_obj.message_id,parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================

if __name__=="__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    print("⬛ TERMINAL PRIME V35 — Démarré.", flush=True)
    bot.infinity_polling()
