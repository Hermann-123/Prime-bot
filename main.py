import os
import sys
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
# ✅ V20.1 — MOTEUR PRÉCIS, SÉLECTIF, SETUP-DRIVEN
# ==========================================
# Philosophie :
#   1. DATA ENGINE        — M5/M15/M30 (+M1 si SCALP)
#   2. MARKET REGIME      — TREND / RANGE / BREAKOUT / CHAOTIC
#   3. SETUPS V20         — 3 setups stricts uniquement :
#                           - TREND_PULLBACK_PRECIS    (10 MIN)
#                           - RANGE_REJECTION_PRECIS   (2 MIN)
#                           - BREAKOUT_RETEST_PRECIS   (5 MIN)
#   4. AI VALIDATOR       — APPROVE/REJECT seulement
#   5. RISK ENGINE        — pertes/jour, pause, cooldown, plafond de signaux
#
# Nouveautés V20.1 :
#   - Délai d'entrée porté à 50 secondes (le temps de placer le trade).
#   - Expirations mappées 2 / 5 / 10 minutes selon le setup.

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ==========================================
# ✅ FILET DE SÉCURITÉ TELEGRAM
# ==========================================

_original_send_message = bot.send_message
def _envoi_securise(chat_id, text, *args, **kwargs):
    try:
        return _original_send_message(chat_id, text, *args, **kwargs)
    except Exception as e:
        message_erreur = str(e).lower()
        if "can't parse entities" in message_erreur or "can't find end of the entity" in message_erreur:
            print(f"[TELEGRAM] Markdown invalide pour {chat_id}, renvoi en texte brut. Détail : {e}", flush=True)
            texte_brut = text.replace("**", "").replace("__", "").replace("`", "")
            kwargs.pop("parse_mode", None)
            try:
                return _original_send_message(chat_id, texte_brut, *args, **kwargs)
            except Exception as e2:
                print(f"[TELEGRAM] Échec définitif de l'envoi à {chat_id} : {e2}", flush=True)
                return None
        print(f"[TELEGRAM] Erreur d'envoi à {chat_id} : {e}", flush=True)
        return None
bot.send_message = _envoi_securise

_original_edit_message_text = bot.edit_message_text
def _edition_securisee(text, *args, **kwargs):
    try:
        return _original_edit_message_text(text, *args, **kwargs)
    except Exception as e:
        message_erreur = str(e).lower()
        if "can't parse entities" in message_erreur or "can't find end of the entity" in message_erreur:
            print(f"[TELEGRAM] Markdown invalide (édition), renvoi en texte brut. Détail : {e}", flush=True)
            texte_brut = text.replace("**", "").replace("__", "").replace("`", "")
            kwargs.pop("parse_mode", None)
            try:
                return _original_edit_message_text(texte_brut, *args, **kwargs)
            except Exception as e2:
                print(f"[TELEGRAM] Échec définitif de l'édition : {e2}", flush=True)
                return None
        print(f"[TELEGRAM] Erreur d'édition : {e}", flush=True)
        return None
bot.edit_message_text = _edition_securisee

ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

MISE_PCT_CAPITAL = 0.02

# ==========================================
# ⏱ DÉLAI D'ENTRÉE (V20.1 : 20s → 50s)
# ==========================================
DELAI_ENTREE_SECONDES = 50

# ==========================================
# RISK ENGINE — CONFIGURATION
# ==========================================

RISK_CONFIG = {
    "daily_loss_limit_pct": 5.0,
    "max_consecutive_losses": 3,
    "pause_duration_minutes": 60,
    "cooldown_win_minutes": 5,
    "cooldown_loss_minutes": 20,
    "payout_net": 0.80,
}

LIMITE_SIGNAUX_JOUR = 8

# ==========================================
# BARÈMES V20
# ==========================================

SEUIL_NO_TRADE = 75
SEUIL_OBSERVATION = 85
SEUIL_POTENTIEL = 92
SEUIL_MIN_STRATEGIE = 75
# <75 NO_TRADE | 75-84 OBSERVATION | 85-91 POTENTIEL | 92+ QUALIFIE

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
mode_trading = {}
filtre_vip_actif = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}

risk_state = {}
cooldown_paire_utilisateur = {}

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}

CRYPTO_PAIRS = []
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD",
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF",
    "CADCHF", "EURCHF", "USDJPY"
]

def nom_otc(symbole):
    return f"{symbole[:3]}/{symbole[3:]}"

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V20.1 — Setup-Driven Precision Engine"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# SYSTÈME DE GESTION DES ACCÈS VIP
# ==========================================

def est_autorise(user_id):
    if user_id == ADMIN_ID:
        return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME" or datetime.datetime.now() < expiration:
            return True
        else:
            del utilisateurs_autorises[user_id]
            try:
                bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
            except:
                pass
            return False
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        argument = message.text.split()[1].lower()
        if argument == '1s':
            jours = 7
        elif argument == '2s':
            jours = 14
        elif argument == '1m':
            jours = 30
        elif argument == '3m':
            jours = 90
        elif argument == 'vie':
            jours = "LIFETIME"
        else:
            jours = int(argument)

        cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cles_generees[cle] = jours

        texte = f"✅ **CLÉ GÉNÉRÉE AVEC SUCCÈS**\n\n🔑 **Clé :** `{cle}`\n"
        texte += f"⏳ **Durée :** À VIE 👑\n\n" if jours == "LIFETIME" else f"⏳ **Durée :** {jours} Jours\n\n"
        bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except:
        pass

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    chat_id = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle in cles_generees:
            jours = cles_generees[cle]
            if jours == "LIFETIME":
                utilisateurs_autorises[chat_id] = "LIFETIME"
                expiration_texte = "À VIE 👑"
            else:
                expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
                utilisateurs_autorises[chat_id] = expiration
                expiration_texte = expiration.strftime('%d/%m/%Y à %H:%M')
            del cles_generees[cle]
            texte = f"🎉 **ACCÈS TERMINAL PRIME DÉVERROUILLÉ !** 🎉\n\nBienvenue dans l'équipe.\n⏳ **Fin de l'abonnement :** {expiration_texte}\n\n👉 Tapez /start pour initialiser votre tableau de bord."
            bot.send_message(chat_id, texte, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")
    except:
        pass

# ==========================================
# VERROUILLAGE TEMPOREL
# ==========================================

def est_symbole_autorise(symbole):
    now = datetime.datetime.utcnow()
    jour = now.weekday()
    heure_dec = now.hour + (now.minute / 60.0)

    est_week_end = False
    if jour == 4 and heure_dec >= 21.0:
        est_week_end = True
    elif jour == 5:
        est_week_end = True
    elif jour == 6 and heure_dec < 21.0:
        est_week_end = True

    if est_week_end:
        if symbole in CRYPTO_PAIRS:
            return "AUTORISE", ""
        else:
            return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Le marché Forex réel (source de données) est fermé le week-end — les prix gelés produiraient des signaux trompeurs. Reprise dimanche soir (21h GMT)."

    if symbole in CRYPTO_PAIRS:
        return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Les Cryptomonnaies sont verrouillées la semaine. Réservées au week-end."

    if heure_dec >= 17.5:
        return "HORS_SESSION", "🛑 **REPLI TACTIQUE** : Couvre-feu en cours (17h30 - 00h00 GMT)."

    if heure_dec >= 0.0 and heure_dec < 8.0:
        if symbole in ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"]:
            return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Session Asiatique."

    if heure_dec >= 7.0 and heure_dec < 12.0:
        paires = ["EURUSD", "EURJPY", "EURAUD", "EURCHF", "USDCHF", "CADCHF"]
        if heure_dec < 8.0:
            paires.extend(["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"])
        if symbole in paires:
            return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Session Européenne."

    if heure_dec >= 12.0 and heure_dec < 17.5:
        if symbole in ["EURUSD", "USDCAD", "AUDUSD"]:
            return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Zone de Guerre US/CA."

    return "BLOCAGE_TOTAL", "🛑 Erreur temporelle."

# ==========================================
# FILTRE NEWS
# ==========================================

def est_heure_de_news_dynamique():
    if not FMP_API_KEY:
        return False
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today}&to={today}&apikey={FMP_API_KEY}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            events = response.json()
            maintenant = datetime.datetime.utcnow()
            for event in events:
                if event.get('impact') == 'High':
                    e_time = datetime.datetime.strptime(event['date'], "%Y-%m-%d %H:%M:%S")
                    diff = abs((maintenant - e_time).total_seconds() / 60)
                    if diff <= 30:
                        return True
    except:
        pass
    return False

# ==========================================
# 1. DATA ENGINE
# ==========================================

def prefixer_symbole(symbole_brut):
    if symbole_brut in CRYPTO_PAIRS:
        return f"cry{symbole_brut}"
    return f"frx{symbole_brut}"

DERIV_ENDPOINTS = [
    "wss://ws.derivws.com/websockets/v3?app_id=1089",
    "wss://api.derivws.com/trading/v1/options/ws/public",
]
_derniere_url_deriv_ok = {"url": None}
_derniere_raison_deriv = {}

def _connecter_deriv(timeout=5):
    derniere_erreur = None
    urls = DERIV_ENDPOINTS
    if _derniere_url_deriv_ok["url"] in urls:
        urls = [_derniere_url_deriv_ok["url"]] + [u for u in urls if u != _derniere_url_deriv_ok["url"]]
    for url in urls:
        try:
            ws = websocket.WebSocket()
            ws.connect(url, timeout=timeout)
            _derniere_url_deriv_ok["url"] = url
            return ws, url
        except Exception as e:
            derniere_erreur = e
            continue
    raise derniere_erreur if derniere_erreur else ConnectionError("Aucun endpoint Deriv disponible")

def _telecharger_bougies(ws, symbole, granularite, count, fin):
    toutes, end = [], fin
    for _ in range(8):
        restant = count - len(toutes)
        if restant <= 0:
            break
        ws.send(json.dumps({
            "ticks_history": symbole,
            "end": end,
            "count": restant,
            "style": "candles",
            "granularity": granularite
        }))
        rep = json.loads(ws.recv())
        if "error" in rep:
            raise RuntimeError(str(rep["error"].get("message", rep["error"]))[:120])
        page = rep.get("candles") or []
        if toutes:
            page = [c for c in page if c["epoch"] < toutes[0]["epoch"]]
        if not page:
            break
        toutes = page + toutes
        end = toutes[0]["epoch"] - 1
        time.sleep(0.2)
    return toutes

def obtenir_donnees_deriv(symbole_brut, granularite=300, count=250):
    symbole = prefixer_symbole(symbole_brut)
    for _ in range(3):
        try:
            ws, url = _connecter_deriv(timeout=5)
            for fin in (int(time.time()), "latest"):
                candles = _telecharger_bougies(ws, symbole, granularite, count, fin)
                if candles:
                    ws.close()
                    return candles
                _derniere_raison_deriv[symbole_brut] = f"M{granularite//60}: 0 bougie reçue (end={fin})"
                print(f"[DERIV] {symbole_brut} via {url} (end={fin}) — 0 bougie.", flush=True)
            ws.close()
        except Exception as e:
            _derniere_raison_deriv[symbole_brut] = f"M{granularite//60}: {type(e).__name__}: {str(e)[:90]}"
            print(f"[DERIV] {symbole_brut} — échec : {type(e).__name__}: {e}", flush=True)
        time.sleep(1)
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for _ in range(3):
        try:
            ws, url = _connecter_deriv(timeout=5)
            for fin in (int(time.time()), "latest"):
                req = {"ticks_history": symbole, "end": fin, "count": 1, "style": "ticks"}
                ws.send(json.dumps(req))
                res = json.loads(ws.recv())
                prices = res.get("history", {}).get("prices") if "error" not in res else None
                if prices:
                    ws.close()
                    return float(prices[-1])
                print(f"[DERIV] {symbole_brut} via {url} (end={fin}) — aucun prix. Réponse : {str(res)[:200]}", flush=True)
            ws.close()
        except Exception as e:
            print(f"[DERIV] {symbole_brut} — échec connexion (prix, tous endpoints) : {type(e).__name__}: {e}", flush=True)
        time.sleep(1)
    return None

def _candles_vers_df(candles):
    return pd.DataFrame([{
        'open': float(c['open']),
        'high': float(c['high']),
        'low': float(c['low']),
        'close': float(c['close'])
    } for c in candles])

def data_engine_fetch(symbole, avec_m1=False):
    c15 = obtenir_donnees_deriv(symbole, 900, 250)
    c30 = obtenir_donnees_deriv(symbole, 1800, 250)
    c5 = obtenir_donnees_deriv(symbole, 300, 250)
    if not c15 or len(c15) < 60 or not c5 or len(c5) < 30:
        _derniere_raison_deriv[symbole] = (
            f"M15={len(c15) if c15 else 0} bougies (min 60), M5={len(c5) if c5 else 0} (min 30). "
            + _derniere_raison_deriv.get(symbole, "")
        )
        return None

    dfs = {
        "M15": _candles_vers_df(c15),
        "M5": _candles_vers_df(c5),
        "M30": _candles_vers_df(c30) if c30 and len(c30) >= 40 else None,
    }
    if avec_m1:
        c1 = obtenir_donnees_deriv(symbole, 60, 250)
        dfs["M1"] = _candles_vers_df(c1) if c1 and len(c1) >= 30 else None
    return dfs

# ==========================================
# UTILITAIRES INDICATEURS
# ==========================================

def calculer_aroon(df, period=9):
    high_idx = df['high'].rolling(period + 1).apply(lambda x: period - x.values.argmax(), raw=True)
    low_idx  = df['low'].rolling(period + 1).apply(lambda x: period - x.values.argmin(), raw=True)
    return ((period - high_idx) / period) * 100, ((period - low_idx) / period) * 100

def calculer_stc(df, fast=14, slow=50, cycle=5, d1=3, d2=3):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    low_macd, high_macd = macd.rolling(cycle).min(), macd.rolling(cycle).max()
    k1 = 100 * (macd - low_macd) / (high_macd - low_macd).replace(0, 1e-9)
    d1_line = k1.ewm(span=d1, adjust=False).mean()
    low_d, high_d = d1_line.rolling(cycle).min(), d1_line.rolling(cycle).max()
    k2 = 100 * (d1_line - low_d) / (high_d - low_d).replace(0, 1e-9)
    return k2.ewm(span=d2, adjust=False).mean().clip(0, 100)

def calculer_donchian(df, period=20):
    return df['high'].rolling(period).max(), df['low'].rolling(period).min()

def evaluer_structure(df, lookback=20):
    try:
        highs = df['high'].iloc[-lookback:].values
        lows = df['low'].iloc[-lookback:].values
        if len(highs) < 2:
            return 50.0
        hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
        hl = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i-1])
        lh = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i-1])
        ll = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i-1])
        coherence_bull = (hh + hl) / (2 * (len(highs) - 1))
        coherence_bear = (lh + ll) / (2 * (len(lows) - 1))
        return round(max(coherence_bull, coherence_bear) * 100, 1)
    except Exception:
        return 50.0

def detecter_pattern_bougie(df):
    if len(df) < 3:
        return "NONE"
    try:
        last, prev = df.iloc[-2], df.iloc[-3]
        o, h, l, c = float(last['open']), float(last['high']), float(last['low']), float(last['close'])
        po, pc = float(prev['open']), float(prev['close'])
        body, rng = abs(c - o), h - l
        if rng == 0:
            return "NONE"
        upper_wick, lower_wick = h - max(o, c), min(o, c) - l

        if lower_wick > body * 1.8 and upper_wick < body:
            return "PIN_BULL"
        if upper_wick > body * 1.8 and lower_wick < body:
            return "PIN_BEAR"
        if pc < po and c > o and c > po and o < pc:
            return "ENGULFING_BULL"
        if pc > po and c < o and c < po and o > pc:
            return "ENGULFING_BEAR"
        if body > rng * 0.75:
            return "MARUBOZU_BULL" if c > o else "MARUBOZU_BEAR"
        return "NONE"
    except Exception:
        return "NONE"

PATTERNS_BULL = ("PIN_BULL", "ENGULFING_BULL", "MARUBOZU_BULL")
PATTERNS_BEAR = ("PIN_BEAR", "ENGULFING_BEAR", "MARUBOZU_BEAR")

# ==========================================
# UTILITAIRES V20 — PRÉCISION / TIMING / QUALITÉ
# ==========================================

def pente_ema(serie, bars=3):
    try:
        if len(serie) < bars + 2:
            return 0.0
        return float(serie.iloc[-2] - serie.iloc[-2-bars])
    except Exception:
        return 0.0

def taille_corps_bougie(candle):
    try:
        return abs(float(candle['close']) - float(candle['open']))
    except Exception:
        return 0.0

def taille_totale_bougie(candle):
    try:
        return float(candle['high']) - float(candle['low'])
    except Exception:
        return 0.0

def cloture_dans_le_sens(candle, direction):
    try:
        o, h, l, c = map(float, [candle['open'], candle['high'], candle['low'], candle['close']])
        rng = max(h - l, 1e-9)
        pos_close = (c - l) / rng
        if direction == "CALL":
            return c > o and pos_close >= 0.6
        return c < o and pos_close <= 0.4
    except Exception:
        return False

def wick_opposee_trop_grande(candle, direction):
    try:
        o, h, l, c = map(float, [candle['open'], candle['high'], candle['low'], candle['close']])
        body = max(abs(c - o), 1e-9)
        upper = h - max(o, c)
        lower = min(o, c) - l
        if direction == "CALL":
            return upper > body * 1.4
        return lower > body * 1.4
    except Exception:
        return True

def bougie_trop_grande(df, idx=-2, multiplicateur=1.8):
    try:
        tailles = (df['high'] - df['low'])
        derniere = float(tailles.iloc[idx])
        moyenne = float(tailles.iloc[-12:-2].mean())
        return moyenne > 0 and derniere > moyenne * multiplicateur
    except Exception:
        return False

def breakout_recent_trop_etendu(df15, direction, lookback=3, seuil_pct=0.0045):
    try:
        close_now = float(df15['close'].iloc[-2])
        if direction == "CALL":
            ref = float(df15['low'].iloc[-lookback:-2].min())
            extension = (close_now - ref) / close_now
        else:
            ref = float(df15['high'].iloc[-lookback:-2].max())
            extension = (ref - close_now) / close_now
        return extension > seuil_pct
    except Exception:
        return True

def distance_pct(a, b):
    try:
        if abs(b) < 1e-12:
            return 999.0
        return abs(a - b) / abs(b)
    except Exception:
        return 999.0

# ==========================================
# 2. MARKET REGIME ENGINE
# ==========================================

def detecter_regime_marche(df15):
    try:
        adx_ind = ta.trend.ADXIndicator(df15['high'], df15['low'], df15['close'], window=14)
        adx_val = float(adx_ind.adx().iloc[-2])

        ema20 = df15['close'].ewm(span=20, adjust=False).mean()
        ema50 = df15['close'].ewm(span=50, adjust=False).mean()
        direction_biais = "BULL" if ema20.iloc[-2] > ema50.iloc[-2] else "BEAR"

        atr = ta.volatility.AverageTrueRange(df15['high'], df15['low'], df15['close'], window=14).average_true_range()
        atr_val = float(atr.iloc[-2])
        atr_moy = float(atr.rolling(30).mean().iloc[-2]) if len(df15) >= 32 else atr_val
        atr_pct = (atr_val / atr_moy) if atr_moy > 0 else 1.0

        upper, lower = calculer_donchian(df15, 20)
        px = float(df15['close'].iloc[-2])
        largeur_canal_pct = ((upper.iloc[-2] - lower.iloc[-2]) / px) if px else 0

        structure_score = evaluer_structure(df15)

        corps = (df15['close'] - df15['open']).abs()
        taille = (df15['high'] - df15['low']).replace(0, 1e-9)
        ratio_corps_recent = (corps / taille).iloc[-4:-1].mean()

        chaos = bool((atr_pct > 2.2) or (ratio_corps_recent < 0.15))

        proche_haut = px >= float(upper.iloc[-4]) * 0.999
        proche_bas = px <= float(lower.iloc[-4]) * 1.001
        expansion = atr_pct > 1.3

        if chaos:
            regime = "CHAOTIC"
        elif (proche_haut or proche_bas) and expansion:
            regime = "BREAKOUT"
        elif adx_val >= 22 and structure_score >= 55:
            regime = "TREND"
        elif adx_val < 18 and largeur_canal_pct < 0.010:
            regime = "RANGE"
        else:
            regime = "TREND" if adx_val >= 20 else "RANGE"

        return {
            "regime": regime,
            "direction_biais": direction_biais,
            "adx": round(adx_val, 1),
            "atr_pct": round(atr_pct, 2),
            "structure_score": structure_score,
            "largeur_canal_pct": round(largeur_canal_pct * 100, 3),
            "chaos": chaos,
        }
    except Exception as e:
        return {
            "regime": "CHAOTIC",
            "direction_biais": "BULL",
            "adx": 0,
            "atr_pct": 1.0,
            "structure_score": 50.0,
            "largeur_canal_pct": 0,
            "chaos": True,
            "erreur": str(e)
        }

# ==========================================
# 3. STRATEGIES V20 — SETUPS PRÉCIS UNIQUEMENT
# ==========================================

def strategie_trend_pullback_precis(df15, df5, regime):
    if regime["regime"] != "TREND":
        return None

    try:
        ema20 = df15['close'].ewm(span=20, adjust=False).mean()
        ema50 = df15['close'].ewm(span=50, adjust=False).mean()
        adx_ind = ta.trend.ADXIndicator(df15['high'], df15['low'], df15['close'], window=14)
        adx = float(adx_ind.adx().iloc[-2])
        rsi5 = ta.momentum.RSIIndicator(df5['close'], window=7).rsi()

        px = float(df15['close'].iloc[-2])
        e20 = float(ema20.iloc[-2])
        e50 = float(ema50.iloc[-2])

        pente20 = pente_ema(ema20, bars=3)
        structure = regime.get("structure_score", 50)

        direction = None
        if e20 > e50 and pente20 > 0 and adx >= 20 and structure >= 60:
            direction = "CALL"
        elif e20 < e50 and pente20 < 0 and adx >= 20 and structure >= 60:
            direction = "PUT"
        else:
            return None

        dist_ema20 = distance_pct(px, e20)
        if dist_ema20 > 0.0035:
            return None

        last = df5.iloc[-2]
        pattern = detecter_pattern_bougie(df5)
        rsi_val = float(rsi5.iloc[-2])

        confirmation = False
        if direction == "CALL":
            confirmation = ((pattern in PATTERNS_BULL) or cloture_dans_le_sens(last, "CALL")) and rsi_val > 50
        else:
            confirmation = ((pattern in PATTERNS_BEAR) or cloture_dans_le_sens(last, "PUT")) and rsi_val < 50

        if not confirmation:
            return None

        if bougie_trop_grande(df5, -2, 1.8):
            return None

        if wick_opposee_trop_grande(last, direction):
            return None

        if breakout_recent_trop_etendu(df15, direction):
            return None

        score = 0
        raisons = []

        score += 25
        raisons.append(f"Régime tendance confirmé (ADX {adx:.1f})")

        if dist_ema20 <= 0.0015:
            zone_score = 25
        elif dist_ema20 <= 0.0025:
            zone_score = 20
        else:
            zone_score = 14
        score += zone_score
        raisons.append(f"Prix proche EMA20 ({dist_ema20*100:.2f}%)")

        score += 20
        raisons.append(f"Confirmation M5 ({pattern if pattern != 'NONE' else 'bougie validée'})")

        momentum_score = 15 if ((direction == "CALL" and rsi_val > 52) or (direction == "PUT" and rsi_val < 48)) else 8
        score += momentum_score
        raisons.append(f"RSI M5 {rsi_val:.1f}")

        timing_score = 15 if dist_ema20 <= 0.0025 else 8
        score += timing_score
        raisons.append("Entrée non tardive")

        score = min(100, score)

        # V20.1 : Trend Pullback = 10 MINUTES (600s)
        return {
            "nom": "TREND_PULLBACK_PRECIS",
            "label": "Trend Pullback Précis",
            "direction": direction,
            "score": round(score, 1),
            "raisons": raisons,
            "details_txt": f"EMA20 {e20:.5f} · dist {dist_ema20*100:.2f}% · RSI5 {rsi_val:.1f}",
            "regime_natif": "TREND",
            "expiration": 600,
            "exp_texte": "10 MINUTES",
        }
    except Exception:
        return None

def strategie_range_rejection_precis(df15, df5, regime):
    if regime["regime"] != "RANGE":
        return None

    try:
        upper, lower = calculer_donchian(df15, 20)
        rsi15 = ta.momentum.RSIIndicator(df15['close'], window=7).rsi()
        cci15 = ta.trend.CCIIndicator(df15['high'], df15['low'], df15['close'], window=14).cci()

        up = float(upper.iloc[-2])
        low = float(lower.iloc[-2])
        px = float(df15['close'].iloc[-2])
        largeur = max(up - low, 1e-9)
        pos = (px - low) / largeur

        rsi_val = float(rsi15.iloc[-2])
        cci_val = float(cci15.iloc[-2])
        pattern = detecter_pattern_bougie(df5)
        last = df5.iloc[-2]

        direction = None
        if pos <= 0.15 and (cci_val <= -100 or rsi_val <= 35):
            direction = "CALL"
        elif pos >= 0.85 and (cci_val >= 100 or rsi_val >= 65):
            direction = "PUT"
        else:
            return None

        if bougie_trop_grande(df5, -2, 1.7):
            return None

        if wick_opposee_trop_grande(last, direction):
            return None

        if regime.get("atr_pct", 1.0) > 1.25:
            return None

        confirmation = False
        if direction == "CALL":
            confirmation = (pattern in PATTERNS_BULL) or cloture_dans_le_sens(last, "CALL")
        else:
            confirmation = (pattern in PATTERNS_BEAR) or cloture_dans_le_sens(last, "PUT")

        if not confirmation:
            return None

        score = 0
        raisons = []

        score += 25
        raisons.append("Régime range confirmé")

        zone_score = 25 if (pos <= 0.08 or pos >= 0.92) else 20
        score += zone_score
        raisons.append(f"Extrême du range ({pos*100:.0f}%)")

        score += 20
        raisons.append(f"Rejet confirmé ({pattern if pattern != 'NONE' else 'bougie validée'})")

        momentum_score = 15 if abs(cci_val) >= 120 else 10
        score += momentum_score
        raisons.append(f"CCI {cci_val:.0f} · RSI {rsi_val:.1f}")

        score += 15
        raisons.append("Entrée au bord du range")

        score = min(100, score)

        # V20.1 : Range Rejection = 2 MINUTES (120s)
        return {
            "nom": "RANGE_REJECTION_PRECIS",
            "label": "Range Rejection Précis",
            "direction": direction,
            "score": round(score, 1),
            "raisons": raisons,
            "details_txt": f"Position {pos*100:.0f}% · CCI {cci_val:.0f} · RSI {rsi_val:.1f}",
            "regime_natif": "RANGE",
            "expiration": 120,
            "exp_texte": "2 MINUTES",
        }
    except Exception:
        return None

def strategie_breakout_retest_precis(df15, df5, regime):
    if regime["regime"] not in ("BREAKOUT", "TREND"):
        return None

    try:
        upper, lower = calculer_donchian(df15, 20)
        adx_ind = ta.trend.ADXIndicator(df15['high'], df15['low'], df15['close'], window=14)
        adx = float(adx_ind.adx().iloc[-2])

        close_prev_break = float(df15['close'].iloc[-3])
        high_ref = float(upper.iloc[-4])
        low_ref = float(lower.iloc[-4])
        px = float(df15['close'].iloc[-2])

        direction = None
        niveau = None

        if close_prev_break > high_ref and px >= high_ref:
            direction = "CALL"
            niveau = high_ref
        elif close_prev_break < low_ref and px <= low_ref:
            direction = "PUT"
            niveau = low_ref
        else:
            return None

        dist_retest = distance_pct(px, niveau)
        if dist_retest > 0.004:
            return None

        pattern = detecter_pattern_bougie(df5)
        last = df5.iloc[-2]

        if bougie_trop_grande(df5, -2, 1.9):
            return None

        if wick_opposee_trop_grande(last, direction):
            return None

        if regime.get("atr_pct", 1.0) > 1.8:
            return None

        confirmation = False
        if direction == "CALL":
            confirmation = (pattern in PATTERNS_BULL) or cloture_dans_le_sens(last, "CALL")
        else:
            confirmation = (pattern in PATTERNS_BEAR) or cloture_dans_le_sens(last, "PUT")

        if not confirmation:
            return None

        score = 0
        raisons = []

        score += 25
        raisons.append(f"Breakout cohérent (ADX {adx:.1f})")

        zone_score = 25 if dist_retest <= 0.002 else 18
        score += zone_score
        raisons.append(f"Retest propre ({dist_retest*100:.2f}%)")

        score += 20
        raisons.append(f"Reprise confirmée ({pattern if pattern != 'NONE' else 'bougie validée'})")

        momentum_score = 15 if regime.get("atr_pct", 1.0) >= 1.1 else 10
        score += momentum_score
        raisons.append(f"ATR x{regime.get('atr_pct', 1.0)}")

        score += 15
        raisons.append("Entrée non tardive")

        score = min(100, score)

        # V20.1 : Breakout Retest = 5 MINUTES (300s) — fixe
        expiration = 300
        exp_texte = "5 MINUTES"

        return {
            "nom": "BREAKOUT_RETEST_PRECIS",
            "label": "Breakout Retest Précis",
            "direction": direction,
            "score": round(score, 1),
            "raisons": raisons,
            "details_txt": f"Niveau {niveau:.5f} · retest {dist_retest*100:.2f}%",
            "regime_natif": "BREAKOUT",
            "expiration": expiration,
            "exp_texte": exp_texte,
        }
    except Exception:
        return None

# ==========================================
# 4. AI VALIDATOR (Groq — APPROVE/REJECT uniquement)
# ==========================================

def ai_validator(symbole, regime, setup, score_confluence, bande):
    if not GROQ_API_KEY:
        return {
            "disponible": False,
            "decision": "APPROVE",
            "avis": "Groq désactivé — décision déterministe seule."
        }

    dossier = {
        "asset": symbole,
        "regime": regime["regime"],
        "direction": setup["direction"],
        "strategy": setup["nom"],
        "adx": float(regime["adx"]),
        "atr_pct": float(regime["atr_pct"]),
        "structure_score": float(regime["structure_score"]),
        "setup_score": float(setup["score"]),
        "confluence_score": float(score_confluence),
        "bande": bande,
        "chaos": bool(regime["chaos"]),
    }

    prompt = (
        "Tu es un validateur de risque pour un bot d'options binaires. Tu NE génères "
        "JAMAIS de signal — un signal a déjà été produit par un moteur déterministe. "
        "Ton seul rôle : répondre APPROVE ou REJECT.\n\n"
        "Réponds UNIQUEMENT en JSON strict:\n"
        '{"decision": "APPROVE ou REJECT", "avis": "<1-2 phrases>"}\n\n'
        f"DOSSIER: {json.dumps(dossier, ensure_ascii=False)}\n\n"
        "Sois sévère si le contexte te semble incohérent, marginal, trop étendu, "
        "ou si l'entrée paraît tardive."
    )

    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 150
            },
            timeout=8,
        )
        if resp.status_code != 200:
            return {
                "disponible": False,
                "decision": "APPROVE",
                "avis": "Groq indisponible (HTTP) — décision déterministe seule."
            }
        texte = resp.json()["choices"][0]["message"]["content"].strip().replace("```json", "").replace("```", "")
        parsed = json.loads(texte)
        decision = str(parsed.get("decision", "APPROVE")).upper()
        if decision not in ("APPROVE", "REJECT"):
            decision = "APPROVE"
        return {
            "disponible": True,
            "decision": decision,
            "avis": str(parsed.get("avis", ""))[:250]
        }
    except Exception:
        return {
            "disponible": False,
            "decision": "APPROVE",
            "avis": "Groq indisponible (erreur) — décision déterministe seule."
        }

# ==========================================
# RISK ENGINE
# ==========================================

def _init_risk_state(chat_id):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    if chat_id not in risk_state or risk_state[chat_id]["date"] != today:
        risk_state[chat_id] = {
            "date": today,
            "pnl_pct": 0.0,
            "consecutive_losses": 0,
            "paused_until": None,
            "wins": 0,
            "losses": 0,
            "total_mise": 0.0,
            "total_gain": 0.0,
            "signaux_recus": 0,
        }
    return risk_state[chat_id]

def risk_engine_verifier(chat_id, symbole):
    etat = _init_risk_state(chat_id)

    if etat["pnl_pct"] <= -RISK_CONFIG["daily_loss_limit_pct"]:
        return False, (
            f"🛑 **BOT STOP** — limite de perte journalière atteinte "
            f"({RISK_CONFIG['daily_loss_limit_pct']}%). Trading suspendu jusqu'à demain."
        )

    if etat["signaux_recus"] >= LIMITE_SIGNAUX_JOUR:
        return False, (
            f"📵 **PLAFOND ATTEINT** — {LIMITE_SIGNAUX_JOUR} signaux déjà envoyés aujourd'hui. "
            f"Reprise demain."
        )

    if etat["paused_until"] and time.time() < etat["paused_until"]:
        minutes_restantes = int((etat["paused_until"] - time.time()) / 60) + 1
        return False, (
            f"⏸️ **PAUSE ACTIVE** — {RISK_CONFIG['max_consecutive_losses']} pertes consécutives. "
            f"Reprise dans {minutes_restantes} min."
        )

    cle = (chat_id, symbole)
    cd = cooldown_paire_utilisateur.get(cle)
    if cd and time.time() < cd["until"]:
        minutes_restantes = int((cd["until"] - time.time()) / 60) + 1
        return False, f"⏳ **COOLDOWN {cd['raison']}** sur {nom_otc(symbole)} — {minutes_restantes} min restantes."

    return True, None

def risk_engine_enregistrer_resultat(chat_id, symbole, win, mise, gain):
    etat = _init_risk_state(chat_id)
    etat["total_mise"] += mise
    etat["total_gain"] += gain
    etat["pnl_pct"] += (gain / CAPITAL_ACTUEL) * 100

    if win:
        etat["wins"] += 1
        etat["consecutive_losses"] = 0
        cooldown_paire_utilisateur[(chat_id, symbole)] = {
            "until": time.time() + RISK_CONFIG["cooldown_win_minutes"] * 60,
            "raison": "WIN"
        }
    else:
        etat["losses"] += 1
        etat["consecutive_losses"] += 1
        cooldown_paire_utilisateur[(chat_id, symbole)] = {
            "until": time.time() + RISK_CONFIG["cooldown_loss_minutes"] * 60,
            "raison": "LOSS"
        }
        if etat["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
            etat["paused_until"] = time.time() + RISK_CONFIG["pause_duration_minutes"] * 60

def marche_choc_detecte(df5):
    try:
        corps = (df5['close'] - df5['open']).abs()
        taille = df5['high'] - df5['low']
        avg_taille = taille.iloc[-4:-1].mean()
        avg_corps = corps.iloc[-4:-1].mean()
        if avg_corps > 0 and (avg_taille > avg_corps * 3.5):
            return True
        derniere = taille.iloc[-2]
        moyenne_taille = taille.iloc[-15:-2].mean()
        if moyenne_taille > 0 and derniere > moyenne_taille * 4:
            return True
        return False
    except Exception:
        return False

def calculer_expectancy(wins, losses, payout_net):
    total = wins + losses
    if total == 0:
        return None, None, None
    winrate = wins / total
    expectancy_pct = (winrate * payout_net) - ((1 - winrate) * 1.0)
    seuil_equilibre = 1 / (1 + payout_net)
    return round(winrate * 100, 1), round(expectancy_pct * 100, 2), round(seuil_equilibre * 100, 2)

# ==========================================
# ORCHESTRATEUR — PIPELINE V20
# ==========================================

def _analyser_binaire_pro_interne(symbole, mode="STANDARD"):
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return {"decision": "NO_TRADE", "raison_no_trade": "⚠️ ALERTE NEWS : Marché manipulé."}

    avec_m1 = (mode == "SCALP")
    dfs = data_engine_fetch(symbole, avec_m1=avec_m1)
    if not dfs:
        return {
            "decision": "NO_TRADE",
            "raison_no_trade": "⚠️ Données insuffisantes.\n" + _derniere_raison_deriv.get(symbole, "")[:250]
        }

    df15, df5 = dfs["M15"], dfs["M5"]

    if marche_choc_detecte(df5):
        return {
            "decision": "NO_TRADE",
            "raison_no_trade": "🛑 **FILTRE CHOC DE MARCHÉ** — mouvement anormal détecté. NO TRADE."
        }

    regime = detecter_regime_marche(df15)
    if regime["regime"] == "CHAOTIC":
        return {
            "decision": "NO_TRADE",
            "raison_no_trade": "🌪️ **RÉGIME CHAOTIQUE** — marché non exploitable actuellement. NO TRADE.",
            "regime": regime
        }

    candidats = []

    s1 = strategie_trend_pullback_precis(df15, df5, regime)
    s2 = strategie_range_rejection_precis(df15, df5, regime)
    s3 = strategie_breakout_retest_precis(df15, df5, regime)

    for s in (s1, s2, s3):
        if s:
            candidats.append(s)

    if not candidats:
        return {
            "decision": "NO_TRADE",
            "raison_no_trade": f"⚠️ Aucun setup V20 propre détecté sur {symbole} ({regime['regime']}).",
            "regime": regime
        }

    directions = list(set(c["direction"] for c in candidats))
    if len(directions) > 1:
        return {
            "decision": "NO_TRADE",
            "raison_no_trade": "⚠️ Conflit de direction entre setups — NO TRADE.",
            "regime": regime,
            "setups": candidats
        }

    setup = max(candidats, key=lambda c: c["score"])

    if setup["score"] < SEUIL_MIN_STRATEGIE:
        return {
            "decision": "NO_TRADE",
            "raison_no_trade": f"⚠️ Setup détecté mais qualité insuffisante ({setup['score']}/100).",
            "regime": regime,
            "setup": setup
        }

    if setup["score"] < SEUIL_OBSERVATION:
        bande = "OBSERVATION"
    elif setup["score"] < SEUIL_POTENTIEL:
        bande = "POTENTIEL"
    else:
        bande = "QUALIFIE"

    score_confluence = setup["score"]

    if bande == "OBSERVATION":
        return {
            "decision": "NO_TRADE",
            "raison_no_trade": (
                f"👁️ **OBSERVATION** (score {score_confluence}/100, sous le seuil d'envoi de {SEUIL_OBSERVATION}) "
                f"— {setup['label']} sur {symbole}, pas assez propre pour trader."
            ),
            "regime": regime,
            "setup": setup,
            "score_confluence": score_confluence,
            "bande": bande
        }

    ai = ai_validator(symbole, regime, setup, score_confluence, bande)
    if ai["decision"] == "REJECT":
        return {
            "decision": "NO_TRADE",
            "raison_no_trade": f"🤖 **AI VALIDATOR — REJET** : {ai['avis']}",
            "regime": regime,
            "setup": setup,
            "score_confluence": score_confluence,
            "bande": bande,
            "ai": ai
        }

    duree_secondes = setup.get("expiration", 300)
    exp_texte = setup.get("exp_texte", "5 MINUTES")

    action = "🟢 ACHAT (CALL)" if setup["direction"] == "CALL" else "🔴 VENTE (PUT)"

    return {
        "decision": "SIGNAL",
        "action": action,
        "direction": setup["direction"],
        "duree_secondes": duree_secondes,
        "exp_texte": exp_texte,
        "regime": regime,
        "setup": setup,
        "score_confluence": score_confluence,
        "bande": bande,
        "ai": ai,
        "raisons": setup["raisons"][:5],
    }

def analyser_binaire_pro(symbole, mode="STANDARD"):
    try:
        return _analyser_binaire_pro_interne(symbole, mode)
    except Exception as e:
        import traceback
        trace = traceback.format_exc()
        derniere_ligne = [l for l in trace.strip().split("\n") if l.strip()][-1]
        ligne_code = [l for l in trace.strip().split("\n") if "bot_v20" in l or "main.py" in l]
        print(f"[ANALYSE] {symbole}/{mode} — ERREUR :\n{trace}", flush=True)
        detail = (ligne_code[-1].strip() if ligne_code else "") + " | " + derniere_ligne
        return {"decision": "NO_TRADE", "raison_no_trade": f"⚠️ Erreur interne ({type(e).__name__}) : {detail[:300]}"}

# ==========================================
# EXÉCUTION DU SIGNAL — SANS MARTINGALE
# ==========================================

def relever_prix_entree(chat_id, trade_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trade_id in trades_en_cours[chat_id]:
        trades_en_cours[chat_id][trade_id]['prix_entree'] = prix

def executer_trade(chat_id, symbole, direction, duree_secondes, resultat_analyse):
    action_affichage = "🟢 ACHAT (CALL)" if direction == "CALL" else "🔴 VENTE (PUT)"
    nom_paire = nom_otc(symbole)

    maintenant = datetime.datetime.utcnow()
    heure_entree = maintenant + datetime.timedelta(seconds=DELAI_ENTREE_SECONDES)
    heure_texte = heure_entree.strftime("%H:%M:%S") + " GMT"

    mise = int(CAPITAL_ACTUEL * MISE_PCT_CAPITAL)
    regime, setup = resultat_analyse["regime"], resultat_analyse["setup"]
    bande = resultat_analyse["bande"]
    badge = "💎 QUALIFIÉ" if bande == "QUALIFIE" else "✅ POTENTIEL"
    raisons_txt = " · ".join(resultat_analyse.get("raisons", [])[:3])
    ai_txt = f"\n🤖 IA : {resultat_analyse['ai']['avis']}" if resultat_analyse.get("ai", {}).get("disponible") else ""

    texte = (
        f"🚨 **SIGNAL {badge}** 🚨\n"
        f"──────────────────\n"
        f"🌐 **ACTIF :** {nom_paire}\n"
        f"⏱ **ENTRÉE EXACTE :** `{heure_texte}`\n"
        f"👉 **ACTION :** {action_affichage}\n"
        f"⏳ **DURÉE :** {resultat_analyse['exp_texte']}\n"
        f"💵 **MISE :** `{mise}$` (fixe, {int(MISE_PCT_CAPITAL*100)}% — pas de Martingale)\n"
        f"──────────────────\n"
        f"🧭 **Régime :** {regime['regime']} (ADX {regime['adx']}, structure {regime['structure_score']}%)\n"
        f"🧩 **Setup :** {setup['label']}\n"
        f"📊 **Score :** {resultat_analyse['score_confluence']}/100\n"
        f"📍 {raisons_txt}{ai_txt}\n"
        f"──────────────────\n"
        f"⏳ *Entrée dans {DELAI_ENTREE_SECONDES} secondes — tu as le temps d'ouvrir Deriv et de placer ton trade.*"
    )
    try:
        bot.send_message(chat_id, texte, parse_mode="Markdown")
    except:
        pass

    _init_risk_state(chat_id)["signaux_recus"] += 1

    trade_id = f"{symbole}_{int(time.time()*1000)}"
    trades_en_cours.setdefault(chat_id, {})[trade_id] = {
        'symbole': symbole,
        'action': direction,
        'prix_entree': None,
        'mise': mise,
    }
    Timer(DELAI_ENTREE_SECONDES, relever_prix_entree, args=[chat_id, trade_id, symbole]).start()
    Timer(DELAI_ENTREE_SECONDES + duree_secondes + 3, verifier_resultat, args=[chat_id, trade_id]).start()

def verifier_resultat(chat_id, trade_id):
    if chat_id not in trades_en_cours or trade_id not in trades_en_cours[chat_id]:
        return
    trade = trades_en_cours[chat_id][trade_id]
    if not trade.get('prix_entree'):
        trades_en_cours[chat_id].pop(trade_id, None)
        return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie:
        return

    prix_entree, action, mise = trade['prix_entree'], trade['action'], trade['mise']
    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = nom_otc(symbole)

    gain = mise * RISK_CONFIG["payout_net"] if gagne else -mise
    risk_engine_enregistrer_resultat(chat_id, symbole, gagne, mise, gain)
    etat = _init_risk_state(chat_id)
    wr, expectancy, seuil_eq = calculer_expectancy(etat["wins"], etat["losses"], RISK_CONFIG["payout_net"])

    if gagne:
        texte = (f"✅ **CIBLE ABATTUE (WIN)**\n🚀 {nom_paire} ({action})\n"
                 f"📈 Entrée : `{prix_entree}` · 📉 Sortie : `{prix_sortie}`\n"
                 f"💰 Gain : `+{gain:.0f}$`")
    else:
        texte = (f"❌ **PERTE (LOSS)**\n⚠️ {nom_paire} ({action})\n"
                 f"📈 Entrée : `{prix_entree}` · 📉 Sortie : `{prix_sortie}`\n"
                 f"💸 Perte : `{gain:.0f}$`")

    if wr is not None:
        texte += (f"\n──────────────────\n📊 Aujourd'hui : {etat['wins']}W/{etat['losses']}L "
                   f"({wr}%) · Expectancy : {expectancy:+.1f}% (seuil équilibre {seuil_eq}% pour ce payout) "
                   f"· Signaux reçus : {etat['signaux_recus']}/{LIMITE_SIGNAUX_JOUR}")

    if etat["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
        texte += f"\n\n⏸️ **PAUSE ACTIVÉE** ({RISK_CONFIG['pause_duration_minutes']} min) — {RISK_CONFIG['max_consecutive_losses']} pertes consécutives."
    elif etat["pnl_pct"] <= -RISK_CONFIG["daily_loss_limit_pct"]:
        texte += f"\n\n🛑 **BOT STOP** — limite de perte journalière atteinte."

    try:
        bot.send_message(chat_id, texte, parse_mode="Markdown")
    except:
        pass

    trades_en_cours[chat_id].pop(trade_id, None)
    if not trades_en_cours[chat_id]:
        trades_en_cours.pop(chat_id, None)

# ==========================================
# INTERFACE TELEGRAM
# ==========================================

def obtenir_clavier(user_id):
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    btn_mode = "🛡️ MODE: STANDARD (5min)" if mode_actuel == "STANDARD" else "🔥 MODE: SCALP (1min)"
    vip_actif = filtre_vip_actif.get(user_id, False)
    btn_vip = "💎 SIGNAUX: QUALIFIÉ SEUL ✅" if vip_actif else "💎 SIGNAUX: POTENTIEL+QUALIFIÉ"
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton(btn_mode), KeyboardButton("⏰ HEURES DE TRADING"))
    markup.row(KeyboardButton(btn_vip), KeyboardButton("📊 MON BILAN"))
    return markup

@bot.message_handler(func=lambda m: m.text.startswith("💎 SIGNAUX"))
def toggle_vip(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        return
    filtre_vip_actif[user_id] = not filtre_vip_actif.get(user_id, False)
    if filtre_vip_actif[user_id]:
        bot.send_message(user_id, "💎 **QUALIFIÉ UNIQUEMENT** — tu ne recevras que les signaux ≥ 92/100 (bande QUALIFIÉ).",
                          reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        bot.send_message(user_id, "🔓 **POTENTIEL + QUALIFIÉ** — tu reçois tous les signaux validés (≥ 85/100).",
                          reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        return
    if user_id in trades_en_cours and trades_en_cours[user_id]:
        return bot.send_message(user_id, "⚠️ Trade en cours.")
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    mode_trading[user_id] = "SCALP" if mode_actuel == "STANDARD" else "STANDARD"
    if mode_trading[user_id] == "STANDARD":
        texte_mode = "✅ Mode STANDARD activé — setups V20 en 2, 5 ou 10 minutes."
    else:
        texte_mode = "✅ Mode SCALP activé — usage plus agressif, moins recommandé en V20."
    bot.send_message(user_id, texte_mode, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 MON BILAN")
def mon_bilan(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        return
    etat = _init_risk_state(user_id)
    wr, expectancy, seuil_eq = calculer_expectancy(etat["wins"], etat["losses"], RISK_CONFIG["payout_net"])
    if wr is None:
        return bot.send_message(user_id, "📭 Aucun trade enregistré aujourd'hui.", parse_mode="Markdown")
    texte = (
        f"📊 **BILAN DU JOUR**\n──────────────────\n"
        f"✅ Wins : {etat['wins']} · ❌ Losses : {etat['losses']}\n"
        f"🎯 Win Rate : {wr}%\n"
        f"💰 P&L : {etat['pnl_pct']:+.2f}% du capital\n"
        f"📈 Expectancy/trade : {expectancy:+.1f}%\n"
        f"⚖️ Seuil de rentabilité (payout {int(RISK_CONFIG['payout_net']*100)}%) : {seuil_eq}%\n"
        f"📩 Signaux : {etat['signaux_recus']}/{LIMITE_SIGNAUX_JOUR}\n"
        f"──────────────────\n"
        f"{'🟢 Au-dessus du seuil de rentabilité' if wr > seuil_eq else '🔴 En dessous du seuil de rentabilité'}"
    )
    bot.send_message(user_id, texte, parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    filtre_vip_actif[user_id] = filtre_vip_actif.get(user_id, False)
    _init_risk_state(user_id)
    texte = """🏴‍☠️ **TERMINAL PRIME - V20.1** 🔥

Moteur Setup-Driven — précision avant volume.

🧭 **Market Regime** — TREND / RANGE / BREAKOUT / CHAOTIC
🧩 **3 Setups précis** :
   • Trend Pullback Précis  → **10 MIN**
   • Range Rejection Précis → **2 MIN**
   • Breakout Retest Précis → **5 MIN**
📊 **Scoring strict** — 0-74 NO TRADE · 75-84 OBSERVATION · 85-91 POTENTIEL · 92+ QUALIFIÉ
🤖 **AI Validator** — Groq en APPROVE/REJECT, jamais générateur
🛡️ **Risk Engine** — perte/jour, pause après pertes, cooldown, plafond de signaux
⏱ **Entrée :** 50 secondes après le signal (le temps de placer ton trade).

❌ **Martingale supprimée** — mise fixe, un signal = une exécution.
Le meilleur signal peut être l'absence de signal."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id):
        return

    actif = call.data.replace("set_", "")
    if actif not in FOREX_PAIRS:
        bot.send_message(chat_id, "⚠️ Cette paire n'est plus disponible. Ouvre à nouveau 📊 CHOISIR UNE DEVISE.")
        return
    statut, msg_erreur = est_symbole_autorise(actif)
    if statut == "BLOCAGE_TOTAL":
        bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
        return

    user_prefs[call.from_user.id] = actif
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    nom_affiche = nom_otc(actif)

    ok, raison = risk_engine_verifier(chat_id, actif)
    if not ok:
        bot.send_message(chat_id, raison, parse_mode="Markdown")
        return

    try:
        msg = bot.send_message(chat_id, "⏳ *Pipeline V20.1 en cours...*", parse_mode="Markdown")
    except:
        return

    resultat = analyser_binaire_pro(actif, mode_actuel)

    if resultat["decision"] == "NO_TRADE":
        try:
            bot.edit_message_text(resultat["raison_no_trade"], chat_id, msg.message_id, parse_mode="Markdown")
        except:
            pass
        return

    if filtre_vip_actif.get(chat_id, False) and resultat["bande"] != "QUALIFIE":
        try:
            bot.edit_message_text(
                f"💎 **MODE QUALIFIÉ SEUL** — signal trouvé (score {resultat['score_confluence']}/100) "
                f"mais sous le seuil QUALIFIÉ (92). Ignoré. Désactive le filtre pour le recevoir.",
                chat_id, msg.message_id, parse_mode="Markdown")
        except:
            pass
        return

    try:
        bot.delete_message(chat_id, msg.message_id)
    except:
        pass

    executer_trade(chat_id, actif, resultat["direction"], resultat["duree_secondes"], resultat)

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id):
        return
    texte = """🕒 **GUIDE DES HORAIRES** 🕒

✅ **Session Asiatique (00h00-08h00) :** JPY, AUD, CAD, CHF
🇪🇺 **Session Europe (07h00-12h00) :** EUR, USD, CHF
🔥 **Zone US/CA (12h00-17h30) :** EUR/USD, AUD/USD, USD/CAD
🛑 **Repli Tactique (17h30-00h00) :** Forex bloqué.
🌙 **Week-end :** marché fermé, reprise dimanche 21h GMT.

*(Bilan Automatique à 18h00 GMT)*"""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id):
        return
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
        InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
        InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
        InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"),
        InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
    )
    bot.send_message(message.chat.id, "Sélectionne ta cible :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id):
        return
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    statut, msg_erreur = est_symbole_autorise(actif)
    if statut == "BLOCAGE_TOTAL":
        return bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

def scanner_marche_auto():
    while True:
        try:
            time.sleep(45)
            utilisateurs_libres = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_libres:
                print("[SCANNER] aucun utilisateur actif — envoie /start au bot.", flush=True)
                continue
            _analysees, _signaux = 0, 0

            for paire in CRYPTO_PAIRS + FOREX_PAIRS:
                statut, _ = est_symbole_autorise(paire)
                if statut != "AUTORISE":
                    continue

                for mode in ["STANDARD"]:
                    cle_memoire = f"{paire}_{mode}"
                    delai_repos = 300
                    if cle_memoire in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle_memoire] < delai_repos):
                        continue

                    resultat = analyser_binaire_pro(paire, mode)
                    _analysees += 1
                    if resultat["decision"] != "SIGNAL":
                        continue
                    _signaux += 1

                    derniere_alerte_auto[cle_memoire] = time.time()
                    nom_affiche = nom_otc(paire)
                    badge = "💎" if resultat["bande"] == "QUALIFIE" else "✅"

                    for uid in utilisateurs_libres:
                        if mode_trading.get(uid, "STANDARD") != mode:
                            continue
                        if filtre_vip_actif.get(uid, False) and resultat["bande"] != "QUALIFIE":
                            continue
                        ok, _ = risk_engine_verifier(uid, paire)
                        if not ok:
                            continue
                        if uid in trades_en_cours and trades_en_cours[uid]:
                            continue

                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"📊 Analyser {nom_affiche}", callback_data=f"set_{paire}"))
                        msg = f"{badge} **SIGNAL {resultat['setup']['label']} : {nom_affiche}**\nRégime {resultat['regime']['regime']} · Score {resultat['score_confluence']}/100 · Durée {resultat['exp_texte']}"
                        try:
                            bot.send_message(uid, msg, reply_markup=markup)
                        except:
                            pass
            print(f"[SCANNER] cycle terminé : {_analysees} analyses, {_signaux} signaux, {len(utilisateurs_libres)} utilisateur(s).", flush=True)
        except Exception as e:
            print(f"[SCANNER] ERREUR : {type(e).__name__}: {e}", flush=True)

def gestionnaire_bilan():
    bilan_envoye_aujourdhui = False
    while True:
        try:
            now = datetime.datetime.utcnow()
            if now.hour == 18 and now.minute == 0 and not bilan_envoye_aujourdhui:
                for uid in list(utilisateurs_actifs):
                    if not est_autorise(uid):
                        continue
                    etat = _init_risk_state(uid)
                    wr, expectancy, seuil_eq = calculer_expectancy(etat["wins"], etat["losses"], RISK_CONFIG["payout_net"])
                    if wr is None:
                        continue
                    texte = (f"📊 **BILAN JOURNALIER (18h GMT)**\n──────────────────\n"
                              f"✅ {etat['wins']}W · ❌ {etat['losses']}L · {wr}%\n"
                              f"💰 P&L : {etat['pnl_pct']:+.2f}% · Expectancy : {expectancy:+.1f}%\n"
                              f"⚖️ Seuil équilibre : {seuil_eq}% · Signaux : {etat['signaux_recus']}/{LIMITE_SIGNAUX_JOUR}")
                    try:
                        bot.send_message(uid, texte, parse_mode="Markdown")
                    except:
                        pass
                bilan_envoye_aujourdhui = True
            elif now.hour == 18 and now.minute > 5:
                bilan_envoye_aujourdhui = False
        except Exception:
            pass
        time.sleep(30)

# ==========================================
# COMMANDE /backtest
# ==========================================

def _decouper_texte(texte, taille_max=3500):
    morceaux = []
    while texte:
        morceaux.append(texte[:taille_max])
        texte = texte[taille_max:]
    return morceaux

@bot.message_handler(commands=['backtest'])
def commande_backtest(message):
    if message.chat.id != ADMIN_ID:
        return

    parts = message.text.split()
    pairs = parts[1] if len(parts) > 1 else "EURUSD,USDJPY,AUDJPY"
    jours = int(parts[2]) if len(parts) > 2 else 14
    mode = parts[3] if len(parts) > 3 else "STANDARD"
    limite = int(parts[4]) if len(parts) > 4 else LIMITE_SIGNAUX_JOUR
    strategie_isolee = parts[5].upper() if len(parts) > 5 else None
    if strategie_isolee in ("-", "TOUS", "TOUTES", "NONE", "ALL", "MIX", "AUCUNE"):
        strategie_isolee = None
    duree_override = int(parts[6]) if len(parts) > 6 else None
    inverser = len(parts) > 7 and parts[7].upper() == "INVERSE"

    bot.send_message(
        message.chat.id,
        f"⏳ **Backtest lancé** — {pairs} · {jours}j · {mode}"
        + (f" · stratégie isolée : {strategie_isolee}" if strategie_isolee else "")
        + (f" · expiration : {duree_override}s" if duree_override else "")
        + (" · MODE INVERSÉ" if inverser else "") + "\n"
        f"Ça peut prendre plusieurs minutes. Suis la progression dans Render > Logs, "
        f"ou attends le résumé ici.",
        parse_mode="Markdown",
    )
    print(f"[BACKTEST] Commande reçue de {message.chat.id} : {pairs} / {jours}j / {mode} / isolee={strategie_isolee} / duree={duree_override}", flush=True)

    def tache():
        try:
            import backtest_engine
            rapport = backtest_engine.lancer_backtest_texte(pairs, jours, mode, limite, strategie_isolee=strategie_isolee, duree_override=duree_override, inverser=inverser)
        except Exception as e:
            rapport = f"❌ Erreur pendant le backtest : {e}"
            print(f"[BACKTEST] ERREUR : {e}", flush=True)
        for morceau in _decouper_texte(rapport):
            try:
                bot.send_message(message.chat.id, f"```\n{morceau}\n```", parse_mode="Markdown")
            except Exception:
                try:
                    bot.send_message(message.chat.id, morceau)
                except:
                    pass

    Thread(target=tache, daemon=True).start()

# ==========================================
# COMMANDE /diagnostic
# ==========================================

@bot.message_handler(commands=['diagnostic'])
def commande_diagnostic(message):
    if message.chat.id != ADMIN_ID:
        return
    bot.send_message(message.chat.id, "🔍 Diagnostic réseau en cours (quelques secondes)...")

    def tache():
        import socket
        resultats = []

        try:
            s = socket.create_connection(("ws.derivws.com", 443), timeout=8)
            s.close()
            resultats.append("✅ TCP brut vers ws.derivws.com:443 — OK")
        except Exception as e:
            resultats.append(f"❌ TCP brut vers ws.derivws.com:443 — ÉCHEC : {type(e).__name__}: {e}")

        try:
            r = requests.get("https://deriv.com", timeout=8)
            resultats.append(f"✅ HTTPS vers deriv.com — statut {r.status_code}")
        except Exception as e:
            resultats.append(f"❌ HTTPS vers deriv.com — ÉCHEC : {type(e).__name__}: {e}")

        try:
            r2 = requests.get("https://www.google.com", timeout=8)
            resultats.append(f"✅ HTTPS vers google.com (test réseau général) — statut {r2.status_code}")
        except Exception as e:
            resultats.append(f"❌ HTTPS vers google.com — ÉCHEC : {type(e).__name__}: {e}")

        ok_paires, ko_paires = [], []
        for paire in FOREX_PAIRS:
            c = obtenir_donnees_deriv(paire, 900, 250)
            nb = len(c) if c else 0
            if nb >= 60:
                ok_paires.append(f"✅ {paire}: {nb}")
            else:
                ko_paires.append(f"⚠️ {paire}: {nb} — {_derniere_raison_deriv.get(paire, '')[:60]}")
            time.sleep(0.3)
        resultats.append("— Bougies M15 obtenues par le bot (250 demandées) —")
        resultats.extend(ko_paires + ok_paires)

        texte = "🔍 RÉSULTAT DIAGNOSTIC RÉSEAU\n" + "\n".join(resultats)
        print("[DIAGNOSTIC] " + texte.replace("\n", " | "), flush=True)
        try:
            bot.send_message(message.chat.id, texte)
        except Exception:
            pass

    Thread(target=tache, daemon=True).start()

@bot.message_handler(commands=['scan'])
def commande_scan(message):
    if message.chat.id != ADMIN_ID:
        return
    mode = mode_trading.get(message.chat.id, "STANDARD")
    bot.send_message(message.chat.id, f"🔎 Scan en direct ({mode}) — compte 1 à 2 minutes...")

    def tache():
        lignes = []
        try:
            news = est_heure_de_news_dynamique()
        except Exception:
            news = False
        lignes.append(f"📰 News majeures (±30 min) : {'OUI — analyses bloquées' if news else 'non'}")
        lignes.append(f"🤖 IA Groq : {'active' if GROQ_API_KEY else 'désactivée'}")
        lignes.append(f"👥 Utilisateurs suivis par le scanner : {len(utilisateurs_actifs)}")
        for paire in FOREX_PAIRS:
            statut, _ = est_symbole_autorise(paire)
            if statut != "AUTORISE":
                lignes.append(f"⏸ {nom_otc(paire)} : hors session")
                continue
            try:
                r = analyser_binaire_pro(paire, mode)
            except Exception as e:
                import traceback
                print(f"[SCAN] {paire} — ERREUR : {type(e).__name__}: {e}", flush=True)
                traceback.print_exc()
                lignes.append(f"❌ {nom_otc(paire)} : erreur {type(e).__name__} (détail dans Render > Logs)")
                continue
            if r["decision"] == "SIGNAL":
                lignes.append(f"🟢 {nom_otc(paire)} : SIGNAL {r['direction']} ({r['bande']}, {r['score_confluence']}/100, {r['exp_texte']})")
            else:
                raison = str(r.get("raison_no_trade", "")).replace("**", "").replace("\n", " ")[:85]
                regime = r.get("regime", {}).get("regime", "")
                lignes.append(f"⚪ {nom_otc(paire)} : {(regime + ' · ') if regime else ''}{raison}")
        bot.send_message(message.chat.id, "\n".join(lignes))

    Thread(target=tache, daemon=True).start()

if __name__ == "__main__":
    utilisateurs_actifs.add(ADMIN_ID)
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestionnaire_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition V20.1 — Setup-Driven Precision Engine Démarrée.", flush=True)
    bot.infinity_polling()
