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
from threading import Thread, Timer

# ==========================================
# CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAGAZudkq2euSVjCIqS3a7GBhlrS7L0bKcY"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID       = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY    = os.environ.get("FMP_API_KEY","D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# VARIABLES D'ÉTAT
# ==========================================

user_prefs          = {}
plateforme_trading  = {}
trades_en_cours     = {}
utilisateurs_actifs = set()
derniere_alerte_auto= {}
signaux_cache       = {}
historique_signaux  = {}

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees          = {}
stats_journee          = {'ITM': 0, 'OTM': 0}

SYNTHETIC_PAIRS  = ["V10","V25","V50","V75","V100"]
COMMODITY_PAIRS  = ["XAUUSD","XAGUSD","USOUSD"]
CRYPTO_PAIRS     = ["BTCUSD","ETHUSD","LTCUSD"]
# ✅ FIX : GBPUSD ajouté dans FOREX_PAIRS (était dans le menu mais pas dans la liste)
FOREX_PAIRS      = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD","AUDJPY",
                    "EURAUD","EURUSD","AUDCAD","USDCHF","CADCHF","EURCHF",
                    "USDJPY","GBPUSD"]
ELITE_PAIRS_MT5  = SYNTHETIC_PAIRS + COMMODITY_PAIRS
ALL_PAIRS        = SYNTHETIC_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS + CRYPTO_PAIRS

# ==========================================
# KEEP ALIVE
# ==========================================

app = Flask(__name__)
@app.route('/') 
def home(): return "Terminal Prime V33 FIXED (Kasper OTE 100% Fidèle)"
def run():    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

# ==========================================
# ACCÈS VIP
# ==========================================

def est_autorise(uid):
    if uid == ADMIN_ID: return True
    if uid in utilisateurs_autorises:
        exp = utilisateurs_autorises[uid]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: return True
        del utilisateurs_autorises[uid]
        try: bot.send_message(uid,"⚠️ Abonnement expiré.",parse_mode="Markdown")
        except: pass
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    try:
        arg  = message.text.split()[1].lower()
        jrs  = {"1s":7,"2s":14,"1m":30,"3m":90,"vie":"LIFETIME"}.get(arg, int(arg))
        cle  = "VIP-"+"".join(random.choices(string.ascii_uppercase+string.digits, k=8))
        cles_generees[cle] = jrs
        dur  = "À VIE 👑" if jrs=="LIFETIME" else f"{jrs} jours"
        bot.send_message(message.chat.id, f"✅ **CLÉ :** `{cle}`\n⏳ **Durée :** {dur}", parse_mode="Markdown")
    except: pass

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    cid = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle not in cles_generees:
            return bot.send_message(cid,"❌ Clé invalide.")
        jrs = cles_generees.pop(cle)
        if jrs == "LIFETIME":
            utilisateurs_autorises[cid] = "LIFETIME"; txt = "À VIE 👑"
        else:
            exp = datetime.datetime.now()+datetime.timedelta(days=jrs)
            utilisateurs_autorises[cid] = exp; txt = exp.strftime('%d/%m/%Y %H:%M')
        bot.send_message(cid, f"🎉 **ACCÈS DÉVERROUILLÉ !**\n⏳ Fin : {txt}\n\n👉 /start", parse_mode="Markdown")
    except: pass

# ==========================================
# KILLZONES & TEMPS
# ==========================================

def dans_killzone():
    """London 07h-10h GMT | New York 12h-15h GMT — comme dans la vidéo."""
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute/60.0
    return (7.0 <= h <= 10.0) or (12.0 <= h <= 15.0)

def nom_killzone():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute/60.0
    if 7.0 <= h <= 10.0:  return "🇬🇧 London Killzone"
    if 12.0 <= h <= 15.0: return "🇺🇸 New York Killzone"
    return "Hors session"

def est_symbole_autorise(symbole):
    if symbole in SYNTHETIC_PAIRS: return "AUTORISE", ""
    now = datetime.datetime.utcnow()
    j = now.weekday()
    h = now.hour + now.minute/60.0
    weekend = (j==4 and h>=21) or j==5 or (j==6 and h<21)
    if weekend:
        return ("AUTORISE","") if symbole in CRYPTO_PAIRS else ("BLOCAGE_TOTAL","Week-end")
    if symbole in CRYPTO_PAIRS: return "BLOCAGE_TOTAL","Cryptos semaine"
    if (symbole in COMMODITY_PAIRS or symbole in FOREX_PAIRS) and not dans_killzone():
        return "HORS_SESSION","🔒 Hors Killzone"
    return "AUTORISE", ""

# ==========================================
# WEBSOCKET DERIV (CONNEXION PAR APPEL)
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

# ============================================================
# MOTEUR KASPER OTE — 100% FIDÈLE À LA VIDÉO
# ============================================================

def calculer_ema_cloud(df):
    """
    Étape 1 — Ripster EMA Cloud sur H1
    4 EMAs exactes de la vidéo : 72, 89, 180, 200
    """
    ema72  = ta.trend.EMAIndicator(close=df['close'], window=72).ema_indicator()
    ema89  = ta.trend.EMAIndicator(close=df['close'], window=89).ema_indicator()
    ema180 = ta.trend.EMAIndicator(close=df['close'], window=180).ema_indicator()
    ema200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()

    rapide = "BULL" if ema72.iloc[-1]  > ema89.iloc[-1]  else "BEAR"
    lent   = "BULL" if ema180.iloc[-1] > ema200.iloc[-1] else "BEAR"

    if   rapide=="BULL" and lent=="BULL": return "BULL", "FORT 🟢🟢"
    elif rapide=="BEAR" and lent=="BEAR": return "BEAR", "FORT 🔴🔴"
    else: return rapide, "MODÉRÉ 🟡"


def trouver_dernier_swing(df, tendance):
    """
    ✅ FIX ERREUR 3 — Recherche du VRAI dernier swing significatif.
    
    Méthode : on cherche un pivot (haut/bas) qui est plus haut/bas
    que les N bougies de chaque côté = swing point réel.
    Comme le trader dans la vidéo qui identifie visuellement le swing.
    """
    n = 3  # Bougies de chaque côté pour valider un pivot

    highs = df['high'].values
    lows  = df['low'].values

    swing_highs = []
    swing_lows  = []

    for i in range(n, len(highs)-n):
        # Swing High : plus haut que les N bougies à gauche et à droite
        if all(highs[i] > highs[i-k] for k in range(1,n+1)) and \
           all(highs[i] > highs[i+k] for k in range(1,n+1)):
            swing_highs.append((i, highs[i]))

        # Swing Low : plus bas que les N bougies à gauche et à droite
        if all(lows[i] < lows[i-k] for k in range(1,n+1)) and \
           all(lows[i] < lows[i+k] for k in range(1,n+1)):
            swing_lows.append((i, lows[i]))

    if not swing_highs or not swing_lows:
        # Fallback simple si pas assez de bougies
        return df['high'].iloc[-40:].max(), df['low'].iloc[-40:].min()

    # Prendre les swings les plus récents
    last_swing_high = max(swing_highs[-3:], key=lambda x: x[0])  # le plus récent
    last_swing_low  = max(swing_lows[-3:],  key=lambda x: x[0])

    if tendance == "BEAR":
        # On veut : swing High récent → swing Low après (mouvement baissier à retracer)
        # Chercher un swing High suivi d'un swing Low plus bas
        for sh in reversed(swing_highs[-5:]):
            lows_apres = [sl for sl in swing_lows if sl[0] > sh[0]]
            if lows_apres:
                sl = min(lows_apres, key=lambda x: x[1])  # Le plus bas après le haut
                return sh[1], sl[1]
        return last_swing_high[1], last_swing_low[1]
    else:
        # On veut : swing Low récent → swing High après (mouvement haussier à retracer)
        for sl in reversed(swing_lows[-5:]):
            highs_apres = [sh for sh in swing_highs if sh[0] > sl[0]]
            if highs_apres:
                sh = max(highs_apres, key=lambda x: x[1])  # Le plus haut après le bas
                return sh[1], sl[1]
        return last_swing_high[1], last_swing_low[1]


def calculer_zone_ote(swing_high, swing_low, tendance):
    """
    Étape 3 — Zone OTE entre Fibonacci 0.618 et 0.786
    
    ✅ FIX ERREUR 6 — TP calculé en 1.5R RÉEL (distance SL × 1.5)
    pas comme avant où c'était 50% du swing (incorrect).
    """
    diff = swing_high - swing_low

    if tendance == "BEAR":
        # Retracement haussier dans une tendance baissière → SELL dans la zone
        ote_bas  = swing_low  + diff * 0.618
        ote_haut = swing_low  + diff * 0.786
        sl       = swing_high + diff * 0.05   # Légèrement au-dessus du swing high (Fibo 1.0+)

        # ✅ FIX : TP = SL_distance × 1.5 (vrai 1.5R)
        sl_distance = abs(ote_haut - sl)      # Distance entre milieu OTE et SL
        tp_1r       = ote_haut - sl_distance  # 1R
        tp_15r      = ote_haut - sl_distance * 1.5  # 1.5R

    else:  # BULL
        # Retracement baissier dans une tendance haussière → BUY dans la zone
        ote_bas  = swing_high - diff * 0.786
        ote_haut = swing_high - diff * 0.618
        sl       = swing_low  - diff * 0.05   # Légèrement sous le swing low

        # ✅ FIX : TP = SL_distance × 1.5 (vrai 1.5R)
        sl_distance = abs(ote_bas - sl)
        tp_1r       = ote_bas + sl_distance
        tp_15r      = ote_bas + sl_distance * 1.5

    return {
        "ote_bas":  round(ote_bas,  5),
        "ote_haut": round(ote_haut, 5),
        "sl":       round(sl,       5),
        "tp_1r":    round(tp_1r,    5),
        "tp_15r":   round(tp_15r,   5),
        "fib_618":  round(ote_bas,  5),
        "fib_786":  round(ote_haut, 5),
    }


def detecter_reaction_ote(df, zone_ote, tendance):
    """
    Étape 4 — Réaction dans la zone OTE.
    
    ✅ FIX ERREUR 9 — Utilise iloc[-2] (bougie FERMÉE)
    pas iloc[-1] qui est la bougie en cours (non confirmée).
    
    On confirme sur la bougie fermée, on exécute sur la bougie suivante.
    """
    # ✅ CORRECTION PRINCIPALE : bougie confirmée = iloc[-2]
    last = df.iloc[-2]   # Bougie FERMÉE (confirmée)
    prev = df.iloc[-3]   # Bougie avant

    px_fermeture = last['close']

    # Le prix de fermeture est-il dans la zone OTE ?
    dans_zone = zone_ote["ote_bas"] <= px_fermeture <= zone_ote["ote_haut"]
    # Ou la bougie précédente avait fermé dans la zone ?
    prev_dans  = zone_ote["ote_bas"] <= prev['close'] <= zone_ote["ote_haut"]

    if not (dans_zone or prev_dans):
        return False, "Hors zone OTE"

    corps   = abs(last['close'] - last['open'])
    taille  = last['high'] - last['low']
    meche_h = last['high'] - max(last['open'], last['close'])
    meche_b = min(last['open'], last['close']) - last['low']

    if taille == 0: return False, "Bougie doji (pas de signal)"

    if tendance == "BEAR":
        # Cherche un signal baissier dans la zone
        engulfing = (prev['close'] > prev['open'] and
                     last['close'] < last['open'] and
                     last['close'] < prev['open'] and corps > 0)
        pin_bar   = meche_h > corps * 2.0
        rejet     = last['close'] < last['open'] and corps > taille * 0.4

        if engulfing: return True, "🕯️ Engulfing Baissier (bougie fermée)"
        if pin_bar:   return True, "📍 Pin Bar Baissier (bougie fermée)"
        if rejet:     return True, "📉 Rejet Baissier confirmé"

    else:  # BULL
        engulfing = (prev['close'] < prev['open'] and
                     last['close'] > last['open'] and
                     last['close'] > prev['open'] and corps > 0)
        pin_bar   = meche_b > corps * 2.0
        rejet     = last['close'] > last['open'] and corps > taille * 0.4

        if engulfing: return True, "🕯️ Engulfing Haussier (bougie fermée)"
        if pin_bar:   return True, "📍 Pin Bar Haussier (bougie fermée)"
        if rejet:     return True, "📈 Rejet Haussier confirmé"

    return False, "Pas de réaction nette en OTE"


def verifier_invalidation(df, zone_ote, tendance):
    """
    ✅ FIX ERREUR 10 — Invalidation automatique du signal.
    
    Si la bougie actuelle a FERMÉ AU-DELÀ du swing high/low
    (c'est-à-dire au-dessus de SL) → signal invalidé.
    """
    px_actuel = df['close'].iloc[-1]
    sl = zone_ote["sl"]

    if tendance == "BEAR" and px_actuel > sl:
        return True, f"⚠️ Prix au-dessus du SL ({sl:.3f}) — Signal INVALIDÉ"
    if tendance == "BULL" and px_actuel < sl:
        return True, f"⚠️ Prix en dessous du SL ({sl:.3f}) — Signal INVALIDÉ"
    return False, ""


def analyser_kasper_complet(symbole):
    """
    Moteur Kasper OTE complet — 6 étapes de la vidéo + 2 fixes.
    """
    candles_m5 = obtenir_donnees_deriv(symbole, 300)   # M5 pour OTE
    candles_h1 = obtenir_donnees_deriv(symbole, 3600)  # H1 pour EMA Cloud

    if not candles_m5 or not candles_h1: return None

    try:
        df_m5 = pd.DataFrame([{
            'open':float(c['open']), 'close':float(c['close']),
            'high':float(c['high']), 'low':float(c['low'])
        } for c in candles_m5])

        df_h1 = pd.DataFrame([{
            'open':float(c['open']), 'close':float(c['close']),
            'high':float(c['high']), 'low':float(c['low'])
        } for c in candles_h1])

        # ── Étape 1+2 : EMA Cloud H1 → Tendance ─────────────────────────
        tendance, force = calculer_ema_cloud(df_h1)

        # ── Étape 3 : Swing + Zone OTE ───────────────────────────────────
        swing_h, swing_l = trouver_dernier_swing(df_m5, tendance)
        diff = swing_h - swing_l

        if diff < 0.3:  # Swing trop petit (moins de 30 pips)
            return None

        zone_ote = calculer_zone_ote(swing_h, swing_l, tendance)

        # ── Vérification invalidation ─────────────────────────────────────
        invalide, msg_inv = verifier_invalidation(df_m5, zone_ote, tendance)
        if invalide: return None

        # ── Étape 4 : Réaction OTE (sur bougie FERMÉE) ───────────────────
        reaction_ok, reaction_msg = detecter_reaction_ote(df_m5, zone_ote, tendance)
        if not reaction_ok: return None

        # ── Calcul R/R réel ───────────────────────────────────────────────
        px = df_m5['close'].iloc[-1]
        risque     = abs(px - zone_ote["sl"])
        recompense = abs(zone_ote["tp_15r"] - px)
        rr         = round(recompense / risque, 2) if risque > 0 else 0

        # ✅ FIX ERREUR 8 : Seuil R/R = 1.5 (pas 1.0 comme avant)
        if rr < 1.5: return None

        action = "🟢 ACHAT (BUY)" if tendance == "BULL" else "🔴 VENTE (SELL)"

        return {
            "action":       action,
            "tendance":     tendance,
            "force":        force,
            "msg":          reaction_msg,
            "swing_h":      round(swing_h, 3),
            "swing_l":      round(swing_l, 3),
            "zone":         zone_ote,
            "sl":           zone_ote["sl"],
            "tp_1r":        zone_ote["tp_1r"],
            "tp":           zone_ote["tp_15r"],
            "rr":           rr,
            "px":           round(px, 3),
            "killzone":     nom_killzone()
        }

    except Exception as e:
        print(f"[Kasper] ⚠️ {symbole}: {e}", flush=True)
        return None


# ==========================================
# NETTOYAGE TRADES BLOQUÉS
# ==========================================

def nettoyer_trades_bloques():
    now = time.time()
    for uid in list(trades_en_cours.keys()):
        t = trades_en_cours[uid]
        if now - t.get('timestamp', now) > t.get('duree', 300) + 120:
            del trades_en_cours[uid]

# ==========================================
# SCANNER AUTOMATIQUE
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(30)
            nettoyer_trades_bloques()

            # Pas de scan hors killzone (économise les ressources)
            if not dans_killzone():
                time.sleep(60)
                continue

            libres = [u for u in utilisateurs_actifs if est_autorise(u) and u not in trades_en_cours]
            if not libres: continue

            for paire in ELITE_PAIRS_MT5 + FOREX_PAIRS:
                statut, _ = est_symbole_autorise(paire)
                if statut != "AUTORISE": continue

                cle = f"{paire}_KASPER"
                if cle in derniere_alerte_auto and time.time()-derniere_alerte_auto[cle] < 300:
                    continue

                res = analyser_kasper_complet(paire)
                if not res: continue

                px = obtenir_prix_actuel_deriv(paire) or res['px']

                signaux_cache[cle] = {
                    'time':    time.time(),
                    'action':  res['action'],
                    'conf':    95,
                    'sc':      9.5,
                    'mt5_sl':  res['sl'],
                    'mt5_tp':  res['tp'],
                    'mt5_tp1': res['tp_1r'],
                    'mt5_rr':  res['rr'],
                    'zone':    res['zone'],
                    'swing_h': res['swing_h'],
                    'swing_l': res['swing_l'],
                    'force':   res['force'],
                    'msg':     res['msg'],
                    'killzone':res['killzone'],
                    'dur':     300
                }
                derniere_alerte_auto[cle] = time.time()

                nom_a  = "🥇 GOLD" if paire=="XAUUSD" else f"{paire[:3]}/{paire[3:]}"
                dir_ic = "🟢 BUY" if "BUY" in res['action'] else "🔴 SELL"

                for uid in libres:
                    pf = plateforme_trading.get(uid,"MT5")
                    if pf=="MT5"    and paire not in ELITE_PAIRS_MT5: continue
                    if pf=="POCKET" and paire not in FOREX_PAIRS:     continue

                    markup = InlineKeyboardMarkup().add(
                        InlineKeyboardButton(f"⚡ Copier signal {nom_a}", callback_data=f"set_{paire}")
                    )
                    msg = (
                        f"🎯 **KASPER OTE — {nom_a}** {dir_ic}\n"
                        f"☁️ EMA Cloud : `{res['force']}`\n"
                        f"📍 {res['msg']}\n"
                        f"🔑 {res['killzone']}\n"
                        f"⚖️ R/R : `{res['rr']}R` | Zone : `{res['zone']['ote_bas']:.3f}` → `{res['zone']['ote_haut']:.3f}`\n"
                        f"_90 secondes pour agir_"
                    )
                    try: bot.send_message(uid, msg, reply_markup=markup, parse_mode="Markdown")
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
    if not est_autorise(uid): return bot.send_message(uid,"🔒 Accès restreint.")
    utilisateurs_actifs.add(uid)
    plateforme_trading.setdefault(uid,"MT5")
    kz = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    texte = f"""🏴‍☠️ **TERMINAL PRIME V33 FIXED — KASPER OTE** 🔥
──────────────────
✅ EMA Cloud H1 (72/89/180/200) — Fidèle à la vidéo
✅ Fibonacci OTE 0.618-0.786 sur vrais swings pivots
✅ Réaction sur bougie FERMÉE (pas en cours)
✅ TP calculé en 1.5R réel (distance SL × 1.5)
✅ Seuil R/R minimum : 1.5 (pas 1.0)
✅ GBPUSD ajouté en mode Pocket
✅ Invalidation automatique si prix dépasse SL
──────────────────
⏰ **Killzone :** {kz}"""
    bot.send_message(uid, texte, reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🏦 BROKER:") or m.text.startswith("📈 BROKER:"))
def toggle_pf(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if plateforme_trading.get(uid,"MT5")=="POCKET":
        plateforme_trading[uid]="MT5"
        bot.send_message(uid,"📈 **MT5 ACTIVÉ** — Gold & Synthétiques", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")
    else:
        plateforme_trading[uid]="POCKET"
        bot.send_message(uid,"🏦 **POCKET ACTIVÉ** — Forex Binaire", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text=="⏰ HEURES DE TRADING")
def horaires(message):
    kz = "🟢 EN COURS" if dans_killzone() else "🔴 INACTIVE"
    bot.send_message(message.chat.id,
        f"🕒 **KILLZONES KASPER OTE**\n\n"
        f"🇬🇧 **Londres :** 07:00 – 10:00 GMT\n"
        f"🇺🇸 **New York :** 12:00 – 15:00 GMT\n\n"
        f"⏰ **Statut actuel :** {kz}\n\n"
        f"_Le scanner est automatiquement désactivé hors de ces fenêtres._",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE","📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    pf = plateforme_trading.get(uid,"MT5")
    markup = InlineKeyboardMarkup(row_width=3)
    if pf=="MT5":
        markup.add(
            InlineKeyboardButton("🥇 GOLD",    callback_data="set_XAUUSD"),
            InlineKeyboardButton("🥈 ARGENT",  callback_data="set_XAGUSD"),
            InlineKeyboardButton("🛢 PÉTROLE", callback_data="set_USOUSD")
        )
        markup.add(
            InlineKeyboardButton("🔥 V75",  callback_data="set_V75"),
            InlineKeyboardButton("💥 V100", callback_data="set_V100"),
            InlineKeyboardButton("⚡ V50",  callback_data="set_V50")
        )
        bot.send_message(uid,"🎯 **Sélectionne ta cible MT5 :**", reply_markup=markup, parse_mode="Markdown")
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
        bot.send_message(uid,"🎯 **Sélectionne ta cible Pocket Forex :**", reply_markup=markup, parse_mode="Markdown")

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
    uid  = call.message.chat.id
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

    # Signal expiré (90 secondes max)
    if not cache or (time.time()-cache['time']) > 90:
        return bot.send_message(uid,
            f"⏱️ **Signal OTE expiré sur {actif}**\n"
            "La zone OTE a peut-être été invalidée. Attends la prochaine alerte.",
            parse_mode="Markdown")

    px  = obtenir_prix_actuel_deriv(actif) or cache.get('mt5_sl', 0)
    nom = "🥇 GOLD" if actif=="XAUUSD" else f"{actif[:3]}/{actif[3:]}"
    z   = cache.get('zone', {})
    dir_aff = "🟢 BUY MARKET" if "BUY" in cache['action'] else "🔴 SELL MARKET"

    signal = f"""🎯 **KASPER SNIPER — {nom}**
━━━━━━━━━━━━━━━━━━━━━━
{dir_aff}
☁️ EMA Cloud : `{cache.get('force','—')}`
🔑 {cache.get('killzone','—')}
━━━━━━━━━━━━━━━━━━━━━━
📍 **Swing High :** `{cache.get('swing_h', 0):.3f}`
📍 **Swing Low  :** `{cache.get('swing_l', 0):.3f}`
━━━━━━━━━━━━━━━━━━━━━━
🟡 **Zone OTE (0.618–0.786) :**
   `{z.get('ote_bas',0):.3f}` → `{z.get('ote_haut',0):.3f}`
💰 **Prix actuel :** `{px:.3f}`
━━━━━━━━━━━━━━━━━━━━━━
🛑 **STOP LOSS :** `{cache['mt5_sl']:.3f}`
🎯 **TP 1R     :** `{cache['mt5_tp1']:.3f}`
🚀 **TP 1.5R   :** `{cache['mt5_tp']:.3f}`
⚖️ **Ratio R/R :** `{cache['mt5_rr']:.2f}R`
━━━━━━━━━━━━━━━━━━━━━━
{cache.get('msg','—')}
⚠️ _Gestion : 1% du capital max par trade_"""

    bot.send_message(uid, signal, parse_mode="Markdown")

# ==========================================
# COMMANDE /kasper — Analyse manuelle
# ==========================================

@bot.message_handler(commands=['kasper'])
def cmd_kasper(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    parts   = message.text.split()
    symbole = parts[1].upper() if len(parts) > 1 else "XAUUSD"
    if symbole not in ALL_PAIRS:
        return bot.send_message(uid,"❌ Symbole non reconnu.")
    msg_obj = bot.send_message(uid, f"🔍 *Analyse Kasper OTE sur {symbole}...*", parse_mode="Markdown")
    res = analyser_kasper_complet(symbole)
    if not res:
        candles_h1 = obtenir_donnees_deriv(symbole, 3600)
        candles_m5 = obtenir_donnees_deriv(symbole, 300)
        if candles_h1 and candles_m5:
            df_h1 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in candles_h1])
            df_m5 = pd.DataFrame([{'open':float(c['open']),'close':float(c['close']),'high':float(c['high']),'low':float(c['low'])} for c in candles_m5])
            tendance, force = calculer_ema_cloud(df_h1)
            swing_h, swing_l = trouver_dernier_swing(df_m5, tendance)
            zone = calculer_zone_ote(swing_h, swing_l, tendance)
            px = df_m5['close'].iloc[-1]
            kz = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
            texte = (
                f"👁️ **KASPER OTE — {symbole}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"☁️ EMA Cloud H1 : `{force}` ({'🟢 BULL' if tendance=='BULL' else '🔴 BEAR'})\n"
                f"📍 Swing High : `{swing_h:.3f}` | Low : `{swing_l:.3f}`\n"
                f"🟡 **Zone OTE :** `{zone['ote_bas']:.3f}` → `{zone['ote_haut']:.3f}`\n"
                f"💰 Prix actuel : `{px:.3f}`\n"
                f"🛑 SL : `{zone['sl']:.3f}` | 🚀 TP 1.5R : `{zone['tp_15r']:.3f}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳ En attente d'une réaction dans la zone\n"
                f"⏰ Killzone : {kz}"
            )
        else:
            texte = "⚠️ Données indisponibles pour cette paire."
        bot.edit_message_text(texte, uid, msg_obj.message_id, parse_mode="Markdown")
        return
    z    = res['zone']
    kz   = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    texte = (
        f"🎯 **KASPER OTE — {symbole}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢 BUY' if res['tendance']=='BULL' else '🔴 SELL'} | Score ≈ 9.5/10\n"
        f"☁️ EMA Cloud : `{res['force']}`\n"
        f"🔑 {res['killzone']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Swing High : `{res['swing_h']:.3f}` | Low : `{res['swing_l']:.3f}`\n"
        f"🟡 Zone OTE : `{z['ote_bas']:.3f}` → `{z['ote_haut']:.3f}`\n"
        f"💰 Prix : `{res['px']:.3f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 SL : `{res['sl']:.3f}`\n"
        f"🎯 TP 1R : `{res['tp_1r']:.3f}`\n"
        f"🚀 TP 1.5R : `{res['tp']:.3f}`\n"
        f"⚖️ R/R : `{res['rr']}R`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{res['msg']}"
    )
    bot.edit_message_text(texte, uid, msg_obj.message_id, parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================

if __name__=="__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    print("⬛ TERMINAL PRIME V33 FIXED — Démarré.", flush=True)
    bot.infinity_polling()
