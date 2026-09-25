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
# ✅ V19 — REFONTE ARCHITECTURALE COMPLÈTE
# ==========================================
# Remplace le pipeline "score et go" par 5 couches indépendantes :
#   1. DATA ENGINE        — M5/M15/M30 (+M1 en mode SCALP)
#   2. MARKET REGIME       — TREND / RANGE / BREAKOUT / CHAOTIC
#   3. STRATEGIES          — 4 piliers (baseline) + 4 nouvelles, verrouillées
#                             par régime compatible
#   4. CONFLUENCE ENGINE   — barème additif, bandes NO_TRADE/OBSERVATION/
#                             POTENTIEL/QUALIFIÉ
#   5. AI VALIDATOR        — Groq, APPROVE/REJECT uniquement, jamais
#                             générateur de signal
#   + RISK ENGINE          — limite perte/jour, pause pertes consécutives,
#                             cooldown gagné/perdu, filtre choc de marché,
#                             PAS DE MARTINGALE (supprimée).
#
# Le principe central : "le meilleur signal peut être l'absence de signal."
# Chaque étage peut voter NO_TRADE ; aucun étage ne peut forcer un trade
# que l'étage précédent a refusé.

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ==========================================
# ✅ FILET DE SÉCURITÉ TELEGRAM — évite les réponses "silencieuses" perdues
# ==========================================
# Certains emoji composés (drapeaux, ZWJ...) cassent le parseur Markdown
# "legacy" de Telegram quand ils précèdent du texte en gras/italique
# ("Can't find end of the entity..."). Sans ce filet, l'exception est
# levée par bot.send_message, remonte jusqu'au handler, et l'utilisateur
# ne reçoit RIEN, sans aucun message d'erreur visible côté Telegram (on
# ne voit l'erreur que dans les logs Render). Ce wrapper retente en texte
# brut si le Markdown échoue, pour ce message précis et pour tous ceux à
# venir.

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

# ✅ V19: mise unique fixe (% du capital) — la Martingale est supprimée,
# plus de mise croissante par palier. Simple, prévisible, protège le capital.
MISE_PCT_CAPITAL = 0.02

# ==========================================
# RISK ENGINE — CONFIGURATION
# ==========================================

RISK_CONFIG = {
    "daily_loss_limit_pct": 5.0,       # % du capital — perte journalière max avant BOT STOP
    "max_consecutive_losses": 3,       # pertes d'affilée avant PAUSE
    "pause_duration_minutes": 60,      # durée de la pause après pertes consécutives
    "cooldown_win_minutes": 5,         # cooldown sur une paire après un WIN (évite la sur-confiance)
    "cooldown_loss_minutes": 20,       # cooldown sur une paire après une LOSS (évite la revanche)
    "payout_net": 0.80,                # payout net typique Pocket Option (80%) — ajustable
}

# ✅ NOUVEAU — plafond de signaux envoyés par jour, par utilisateur.
LIMITE_SIGNAUX_JOUR = 15

# ==========================================
# CONFLUENCE ENGINE — BARÈME (repris tel quel de l'architecture proposée)
# ==========================================

BAREME_CONFLUENCE = {
    "regime_compatible": 25,
    "structure_max": 20,
    "momentum": 20,
    "volatilite": 15,
    "setup_max": 15,
    "contexte_defavorable": -30,
}
SEUIL_NO_TRADE = 40
SEUIL_OBSERVATION = 55
SEUIL_POTENTIEL = 80
# < 40 NO_TRADE | 40-54 OBSERVATION (jamais envoyé) | 55-79 POTENTIEL | 80+ QUALIFIÉ

# ✅ NOUVEAU — seuil minimum qu'une stratégie individuelle doit atteindre
# (sur 100) pour même être proposée au Confluence Engine. C'était le vrai
# goulot d'étranglement : à 45, presque aucune stratégie ne l'atteignait,
# donc baisser SEUIL_OBSERVATION seul n'avait aucun effet (le Confluence
# Engine n'était jamais consulté, faute de candidat). Baissé à 25 par
# défaut — remonte-le si tu reçois trop de signaux de mauvaise qualité,
# baisse-le encore (ex. 15) si tu es toujours à 0.
SEUIL_MIN_STRATEGIE = 25
# < 55 NO_TRADE | 55-69 OBSERVATION (jamais envoyé) | 70-79 POTENTIEL | 80+ QUALIFIÉ

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
mode_trading = {}
filtre_vip_actif = {}   # VIP = QUALIFIÉ uniquement ; standard = POTENTIEL + QUALIFIÉ
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}

# Risk Engine — état par utilisateur
risk_state = {}   # chat_id -> {date, pnl_pct, consecutive_losses, paused_until,
                   #             wins, losses, total_mise, total_gain, signaux_recus}
# Cooldown par paire ET par utilisateur (win/loss différenciés)
cooldown_paire_utilisateur = {}  # (chat_id, symbole) -> {"until": ts, "raison": "WIN"/"LOSS"/"CHOC"}

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}

CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD",
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF",
    "CADCHF", "EURCHF", "USDJPY"
]

def nom_otc(symbole):
    return f"{symbole[:3]}/{symbole[3:]} OTC"

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V19 — Architecture 5 Couches (Regime/Strategies/Confluence/AI/Risk)"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# SYSTÈME DE GESTION DES ACCÈS VIP (inchangé)
# ==========================================

def est_autorise(user_id):
    if user_id == ADMIN_ID: return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME" or datetime.datetime.now() < expiration: return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
            except: pass
            return False
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    try:
        argument = message.text.split()[1].lower()
        if argument == '1s': jours = 7
        elif argument == '2s': jours = 14
        elif argument == '1m': jours = 30
        elif argument == '3m': jours = 90
        elif argument == 'vie': jours = "LIFETIME"
        else: jours = int(argument)

        cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cles_generees[cle] = jours

        texte = f"✅ **CLÉ GÉNÉRÉE AVEC SUCCÈS**\n\n🔑 **Clé :** `{cle}`\n"
        texte += f"⏳ **Durée :** À VIE 👑\n\n" if jours == "LIFETIME" else f"⏳ **Durée :** {jours} Jours\n\n"
        bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: pass

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
        else: bot.send_message(chat_id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")
    except: pass

# ==========================================
# VERROUILLAGE TEMPOREL (inchangé)
# ==========================================

def est_symbole_autorise(symbole):
    now = datetime.datetime.utcnow()
    jour = now.weekday()
    heure_dec = now.hour + (now.minute / 60.0)

    est_week_end = False
    if jour == 4 and heure_dec >= 21.0: est_week_end = True
    elif jour == 5: est_week_end = True
    elif jour == 6 and heure_dec < 21.0: est_week_end = True

    if est_week_end:
        if symbole in CRYPTO_PAIRS: return "AUTORISE", ""
        else: return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Le marché Forex réel (source de données) est fermé le week-end — les prix gelés produiraient des signaux trompeurs. Seules les cryptos sont autorisées."

    if symbole in CRYPTO_PAIRS:
        return "BLOCAGE_TOTAL", "🔒 **ACCÈS REFUSÉ** : Les Cryptomonnaies sont verrouillées la semaine. Réservées au week-end."

    if heure_dec >= 17.5: return "HORS_SESSION", "🛑 **REPLI TACTIQUE** : Couvre-feu en cours (17h30 - 00h00 GMT)."

    if heure_dec >= 0.0 and heure_dec < 8.0:
        if symbole in ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"]: return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Session Asiatique."

    if heure_dec >= 7.0 and heure_dec < 12.0:
        paires = ["EURUSD", "EURJPY", "EURAUD", "EURCHF", "USDCHF", "CADCHF"]
        if heure_dec < 8.0: paires.extend(["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"])
        if symbole in paires: return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Session Européenne."

    if heure_dec >= 12.0 and heure_dec < 17.5:
        if symbole in ["EURUSD", "USDCAD", "AUDUSD"]: return "AUTORISE", ""
        return "HORS_SESSION", "🔒 **ACCÈS REFUSÉ** : Hors Zone de Guerre US/CA."

    return "BLOCAGE_TOTAL", "🛑 Erreur temporelle."

# ==========================================
# FILTRE NEWS (inchangé)
# ==========================================

def est_heure_de_news_dynamique():
    if not FMP_API_KEY: return False
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
                    if diff <= 30: return True
    except: pass
    return False

# ==========================================
# 1. DATA ENGINE
# ==========================================

def prefixer_symbole(symbole_brut):
    if symbole_brut in CRYPTO_PAIRS: return f"cry{symbole_brut}"
    return f"frx{symbole_brut}"

# ✅ Deriv a introduit un nouvel endpoint WebSocket public en 2026
# (wss://api.derivws.com/trading/v1/options/ws/public), en plus de
# l'ancien endpoint historique (wss://ws.derivws.com/websockets/v3?app_id=...).
# On ne sait pas encore lequel accepte ticks_history de façon fiable dans la
# durée, donc on essaie les deux dans l'ordre et on garde celui qui marche
# — le bot s'auto-adapte au lieu de dépendre d'une seule URL figée.
DERIV_ENDPOINTS = [
    "wss://ws.derivws.com/websockets/v3?app_id=1089",
    "wss://api.derivws.com/trading/v1/options/ws/public",
]
_derniere_url_deriv_ok = {"url": None}

def _connecter_deriv(timeout=5):
    """Essaie chaque endpoint Deriv connu dans l'ordre, retourne (ws, url)
    du premier qui accepte la connexion WebSocket. Lève la dernière
    exception si aucun ne fonctionne."""
    derniere_erreur = None
    urls = DERIV_ENDPOINTS
    # Si un endpoint a déjà marché récemment, on l'essaie en premier.
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

def obtenir_donnees_deriv(symbole_brut, granularite=300, count=250):
    symbole = prefixer_symbole(symbole_brut)
    for _ in range(3):
        try:
            ws, url = _connecter_deriv(timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": count, "style": "candles", "granularity": granularite}
            ws.send(json.dumps(req))
            history = json.loads(ws.recv())
            ws.close()
            if "error" not in history and "candles" in history: return history['candles']
            print(f"[DERIV] {symbole_brut} via {url} — réponse sans 'candles' : {str(history)[:300]}", flush=True)
        except Exception as e:
            print(f"[DERIV] {symbole_brut} — échec connexion (tous endpoints) : {type(e).__name__}: {e}", flush=True)
            time.sleep(1)
            continue
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for _ in range(3):
        try:
            ws, url = _connecter_deriv(timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 1, "style": "ticks"}
            ws.send(json.dumps(req))
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]: return float(res["history"]["prices"][0])
            print(f"[DERIV] {symbole_brut} via {url} — réponse sans prix : {str(res)[:300]}", flush=True)
        except Exception as e:
            print(f"[DERIV] {symbole_brut} — échec connexion (prix, tous endpoints) : {type(e).__name__}: {e}", flush=True)
            time.sleep(1)
            continue
    return None

def _candles_vers_df(candles):
    return pd.DataFrame([{
        'open': float(c['open']), 'high': float(c['high']),
        'low': float(c['low']), 'close': float(c['close'])
    } for c in candles])

def data_engine_fetch(symbole, avec_m1=False):
    """
    ✅ V19 DATA ENGINE — récupère M5/M15/M30 (toujours), M1 en plus si
    demandé (mode SCALP). Retourne un dict de DataFrames ou None si des
    données essentielles manquent (M15 obligatoire pour le Regime Engine).
    """
    c15 = obtenir_donnees_deriv(symbole, 900, 250)
    c30 = obtenir_donnees_deriv(symbole, 1800, 250)
    c5 = obtenir_donnees_deriv(symbole, 300, 250)
    if not c15 or len(c15) < 60 or not c5 or len(c5) < 30:
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
    """Score 0-100 : clarté de la structure (higher-highs/lows cohérents)."""
    try:
        highs = df['high'].iloc[-lookback:].values
        lows = df['low'].iloc[-lookback:].values
        if len(highs) < 2: return 50.0
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
    """Pin Bar / Engulfing / Marubozu sur la dernière bougie clôturée (iloc[-2])."""
    if len(df) < 3: return "NONE"
    try:
        last, prev = df.iloc[-2], df.iloc[-3]
        o, h, l, c = float(last['open']), float(last['high']), float(last['low']), float(last['close'])
        po, pc = float(prev['open']), float(prev['close'])
        body, rng = abs(c - o), h - l
        if rng == 0: return "NONE"
        upper_wick, lower_wick = h - max(o, c), min(o, c) - l

        if lower_wick > body * 1.8 and upper_wick < body: return "PIN_BULL"
        if upper_wick > body * 1.8 and lower_wick < body: return "PIN_BEAR"
        if pc < po and c > o and c > po and o < pc: return "ENGULFING_BULL"
        if pc > po and c < o and c < po and o > pc: return "ENGULFING_BEAR"
        if body > rng * 0.75: return "MARUBOZU_BULL" if c > o else "MARUBOZU_BEAR"
        return "NONE"
    except Exception:
        return "NONE"

PATTERNS_BULL = ("PIN_BULL", "ENGULFING_BULL", "MARUBOZU_BULL")
PATTERNS_BEAR = ("PIN_BEAR", "ENGULFING_BEAR", "MARUBOZU_BEAR")

# ==========================================
# 2. MARKET REGIME ENGINE
# ==========================================

def detecter_regime_marche(df15):
    """
    ✅ V19 — classe le marché en TREND / RANGE / BREAKOUT / CHAOTIC sur M15.
    Ne génère AUCUN signal — sert uniquement de contexte pour verrouiller
    quelles stratégies ont le droit de tourner.
    """
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

        # 🔴 CHAOTIC : volatilité en pic anormal OU bougies dominées par les mèches
        chaos = (atr_pct > 2.2) or (ratio_corps_recent < 0.15)

        # 🔵 BREAKOUT : prix vient de dépasser le canal Donchian PRÉCÉDENT (pas
        # celui recalculé avec la bougie courante — évite l'auto-référence) et
        # la volatilité est en expansion
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
            "regime": regime, "direction_biais": direction_biais,
            "adx": round(adx_val, 1), "atr_pct": round(atr_pct, 2),
            "structure_score": structure_score,
            "largeur_canal_pct": round(largeur_canal_pct * 100, 3),
            "chaos": chaos,
        }
    except Exception as e:
        return {"regime": "CHAOTIC", "direction_biais": "BULL", "adx": 0, "atr_pct": 1.0,
                "structure_score": 50.0, "largeur_canal_pct": 0, "chaos": True, "erreur": str(e)}

# ==========================================
# 3. STRATEGIES — 4 PILIERS BASELINE (inchangés du V18.5)
# ==========================================

def analyser_aroon_rsi(df15):
    """BASELINE 1 — 'Show The Direction' (Aroon 9 + RSI 6). Régime-agnostique."""
    try:
        aroon_up, aroon_down = calculer_aroon(df15, 9)
        rsi6 = ta.momentum.RSIIndicator(close=df15['close'], window=6).rsi()
        au, ad = float(aroon_up.iloc[-2]), float(aroon_down.iloc[-2])
        au_p, ad_p = float(aroon_up.iloc[-3]), float(aroon_down.iloc[-3])
        rsi_val = float(rsi6.iloc[-2])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                s += min(35, max(0, (au - ad) * 0.5))
                if au_p <= ad_p and au > ad: s += 20; raisons.append("Croisement Aroon Up/Down")
                if 40 <= rsi_val <= 68: s += 20; raisons.append(f"RSI sain ({rsi_val:.1f})")
                if au >= 70: s += 15; raisons.append(f"Aroon Up fort ({au:.0f})")
            else:
                s += min(35, max(0, (ad - au) * 0.5))
                if ad_p <= au_p and ad > au: s += 20; raisons.append("Croisement Aroon Down/Up")
                if 32 <= rsi_val <= 60: s += 20; raisons.append(f"RSI sain ({rsi_val:.1f})")
                if ad >= 70: s += 15; raisons.append(f"Aroon Down fort ({ad:.0f})")
            return round(min(100, s), 1), raisons

        sc, rc = score("CALL"); sp, rp = score("PUT")
        direction = "CALL" if sc >= sp else "PUT"
        meilleur, raisons = (sc, rc) if direction == "CALL" else (sp, rp)
        if meilleur < SEUIL_MIN_STRATEGIE: return None
        return {"nom": "AROON_RSI", "label": "Show The Direction", "direction": direction,
                "score": meilleur, "raisons": raisons,
                "details_txt": f"Aroon Up {au:.0f}/Down {ad:.0f} · RSI(6) {rsi_val:.1f}",
                "regime_natif": None}
    except Exception:
        return None

def analyser_adx_stc(df15):
    """BASELINE 2 — 'Identifies Reversal Points' (ADX 14 + Schaff Trend Cycle). Régime-agnostique."""
    try:
        adx_ind = ta.trend.ADXIndicator(df15['high'], df15['low'], df15['close'], window=14)
        adx = adx_ind.adx(); di_pos = adx_ind.adx_pos(); di_neg = adx_ind.adx_neg()
        stc = calculer_stc(df15)
        adx_val = float(adx.iloc[-2]); dip, din = float(di_pos.iloc[-2]), float(di_neg.iloc[-2])
        stc_val, stc_prev = float(stc.iloc[-2]), float(stc.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                if stc_prev <= 25 and stc_val > stc_prev: s += 35; raisons.append(f"STC remonte ({stc_val:.0f})")
                elif stc_val < 40: s += 15
                if dip > din: s += 20; raisons.append("+DI > -DI")
                if adx_val >= 15: s += min(20, (adx_val - 15) * 1.2); raisons.append(f"ADX {adx_val:.0f}")
            else:
                if stc_prev >= 75 and stc_val < stc_prev: s += 35; raisons.append(f"STC redescend ({stc_val:.0f})")
                elif stc_val > 60: s += 15
                if din > dip: s += 20; raisons.append("-DI > +DI")
                if adx_val >= 15: s += min(20, (adx_val - 15) * 1.2); raisons.append(f"ADX {adx_val:.0f}")
            return round(min(100, s), 1), raisons

        sc, rc = score("CALL"); sp, rp = score("PUT")
        direction = "CALL" if sc >= sp else "PUT"
        meilleur, raisons = (sc, rc) if direction == "CALL" else (sp, rp)
        if meilleur < SEUIL_MIN_STRATEGIE: return None
        return {"nom": "ADX_STC", "label": "Identifies Reversal Points", "direction": direction,
                "score": meilleur, "raisons": raisons,
                "details_txt": f"STC {stc_val:.0f} · ADX {adx_val:.0f}", "regime_natif": None}
    except Exception:
        return None

def analyser_cci_macd(df15):
    """BASELINE 3 — 'A Moment When...' (CCI 10 + MACD 10,25,5). Régime-agnostique."""
    try:
        cci = ta.trend.CCIIndicator(df15['high'], df15['low'], df15['close'], window=10).cci()
        macd_hist = ta.trend.MACD(df15['close'], window_slow=25, window_fast=10, window_sign=5).macd_diff()
        cci_val, cci_prev = float(cci.iloc[-2]), float(cci.iloc[-3])
        hist_val, hist_prev = float(macd_hist.iloc[-2]), float(macd_hist.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                if cci_prev <= -100 and cci_val > cci_prev: s += 30; raisons.append(f"CCI remonte ({cci_val:.0f})")
                elif cci_val < -50: s += 12
                if hist_val > 0: s += 20; raisons.append("MACD histogram positif")
                if hist_val > hist_prev: s += 15; raisons.append("MACD histogram en hausse")
            else:
                if cci_prev >= 100 and cci_val < cci_prev: s += 30; raisons.append(f"CCI redescend ({cci_val:.0f})")
                elif cci_val > 50: s += 12
                if hist_val < 0: s += 20; raisons.append("MACD histogram négatif")
                if hist_val < hist_prev: s += 15; raisons.append("MACD histogram en baisse")
            return round(min(100, s), 1), raisons

        sc, rc = score("CALL"); sp, rp = score("PUT")
        direction = "CALL" if sc >= sp else "PUT"
        meilleur, raisons = (sc, rc) if direction == "CALL" else (sp, rp)
        if meilleur < SEUIL_MIN_STRATEGIE: return None
        return {"nom": "CCI_MACD", "label": "A Moment When...", "direction": direction,
                "score": meilleur, "raisons": raisons,
                "details_txt": f"CCI(10) {cci_val:.0f} · MACD hist {hist_val:.5f}", "regime_natif": None}
    except Exception:
        return None

def analyser_donchian_cci(df15):
    """BASELINE 4 — 'You Know And...' (Donchian 20 + CCI 11). Régime-agnostique."""
    try:
        upper, lower = calculer_donchian(df15, 20)
        cci = ta.trend.CCIIndicator(df15['high'], df15['low'], df15['close'], window=11).cci()
        px = float(df15['close'].iloc[-2])
        up_val, low_val = float(upper.iloc[-2]), float(lower.iloc[-2])
        largeur = up_val - low_val if (up_val - low_val) > 0 else 1e-9
        position_pct = (px - low_val) / largeur
        cci_val, cci_prev = float(cci.iloc[-2]), float(cci.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                proximite = max(0, 1 - position_pct * 2.5)
                s += proximite * 35
                if proximite > 0.5: raisons.append(f"Prix proche du bas du canal ({position_pct*100:.0f}%)")
                if cci_prev <= -100 and cci_val > cci_prev: s += 30; raisons.append(f"CCI remonte ({cci_val:.0f})")
                elif cci_val < -30: s += 12
            else:
                proximite = max(0, (position_pct - 0.6) * 2.5)
                s += proximite * 35
                if proximite > 0.5: raisons.append(f"Prix proche du haut du canal ({position_pct*100:.0f}%)")
                if cci_prev >= 100 and cci_val < cci_prev: s += 30; raisons.append(f"CCI redescend ({cci_val:.0f})")
                elif cci_val > 30: s += 12
            return round(min(100, s), 1), raisons

        sc, rc = score("CALL"); sp, rp = score("PUT")
        direction = "CALL" if sc >= sp else "PUT"
        meilleur, raisons = (sc, rc) if direction == "CALL" else (sp, rp)
        if meilleur < SEUIL_MIN_STRATEGIE: return None
        return {"nom": "DONCHIAN_CCI", "label": "You Know And...", "direction": direction,
                "score": meilleur, "raisons": raisons,
                "details_txt": f"Position canal {position_pct*100:.0f}% · CCI(11) {cci_val:.0f}",
                "regime_natif": None}
    except Exception:
        return None

# ==========================================
# 3bis. STRATEGIES — 4 NOUVELLES, VERROUILLÉES PAR RÉGIME
# ==========================================

def strategie_trend_pullback(df15, df5, regime):
    """STRATEGY A — Trend Pullback. Actif UNIQUEMENT en régime TREND."""
    if regime["regime"] != "TREND": return None
    try:
        ema20 = df15['close'].ewm(span=20, adjust=False).mean()
        ema50 = df15['close'].ewm(span=50, adjust=False).mean()
        rsi = ta.momentum.RSIIndicator(df15['close'], window=14).rsi()
        direction = "CALL" if ema20.iloc[-2] > ema50.iloc[-2] else "PUT"

        px = float(df15['close'].iloc[-2])
        dist_ema20_pct = abs(px - float(ema20.iloc[-2])) / px if px else 1
        proche_ema20 = dist_ema20_pct < 0.005

        rsi_val = float(rsi.iloc[-2])
        rsi_recupere = 38 <= rsi_val <= 62  # ni extrême, ni plat — signe de reprise saine

        pattern = detecter_pattern_bougie(df5)
        confirmation = (direction == "CALL" and pattern in PATTERNS_BULL) or (direction == "PUT" and pattern in PATTERNS_BEAR)

        score, raisons = 0.0, []
        if proche_ema20: score += 35; raisons.append(f"Pullback EMA20 M15 ({dist_ema20_pct*100:.2f}%)")
        if rsi_recupere: score += 25; raisons.append(f"RSI en récupération ({rsi_val:.1f})")
        if regime["adx"] >= 22: score += 20; raisons.append(f"ADX {regime['adx']}")
        if confirmation: score += 20; raisons.append(f"Bougie de confirmation ({pattern})")

        if score < SEUIL_MIN_STRATEGIE: return None
        return {"nom": "TREND_PULLBACK", "label": "Trend Pullback", "direction": direction,
                "score": round(score, 1), "raisons": raisons,
                "details_txt": f"Dist EMA20 {dist_ema20_pct*100:.2f}% · RSI {rsi_val:.1f}",
                "regime_natif": "TREND"}
    except Exception:
        return None

def strategie_breakout_retest(df15, df5, regime):
    """STRATEGY B — Breakout + Retest. Actif en régime BREAKOUT (ou TREND en confirmation)."""
    if regime["regime"] not in ("BREAKOUT", "TREND"): return None
    try:
        upper, lower = calculer_donchian(df15, 20)
        px = float(df15['close'].iloc[-2])

        cassure_haute = float(df15['close'].iloc[-5]) > float(upper.iloc[-6])
        cassure_basse = float(df15['close'].iloc[-5]) < float(lower.iloc[-6])

        if cassure_haute:
            direction, niveau = "CALL", float(upper.iloc[-6])
        elif cassure_basse:
            direction, niveau = "PUT", float(lower.iloc[-6])
        else:
            return None

        dist_retest = abs(px - niveau) / px if px else 1
        retest_ok = dist_retest < 0.004

        pattern = detecter_pattern_bougie(df5)
        confirmation = (direction == "CALL" and pattern in PATTERNS_BULL) or (direction == "PUT" and pattern in PATTERNS_BEAR)

        score, raisons = 0.0, []
        if retest_ok: score += 40; raisons.append(f"Retest du niveau cassé ({dist_retest*100:.2f}%)")
        if confirmation: score += 30; raisons.append(f"Confirmation ({pattern})")
        if regime["atr_pct"] > 1.1: score += 15; raisons.append("Volatilité en expansion")
        if regime["structure_score"] >= 55: score += 15; raisons.append("Structure claire")

        if score < SEUIL_MIN_STRATEGIE: return None
        return {"nom": "BREAKOUT_RETEST", "label": "Breakout + Retest", "direction": direction,
                "score": round(score, 1), "raisons": raisons,
                "details_txt": f"Niveau cassé {niveau:.5f} · retest {dist_retest*100:.2f}%",
                "regime_natif": "BREAKOUT"}
    except Exception:
        return None

def strategie_momentum_expansion(df15, regime):
    """STRATEGY C — Momentum Expansion. Actif en régime TREND ou BREAKOUT."""
    if regime["regime"] not in ("TREND", "BREAKOUT"): return None
    try:
        roc = ta.momentum.ROCIndicator(df15['close'], window=10).roc()
        roc_val, roc_prev = float(roc.iloc[-2]), float(roc.iloc[-5])
        ema20 = df15['close'].ewm(span=20, adjust=False).mean()
        ema50 = df15['close'].ewm(span=50, adjust=False).mean()
        direction = "CALL" if ema20.iloc[-2] > ema50.iloc[-2] else "PUT"

        expansion = regime["atr_pct"] > 1.3
        momentum_accel = (direction == "CALL" and roc_val > roc_prev and roc_val > 0) or \
                          (direction == "PUT" and roc_val < roc_prev and roc_val < 0)

        score, raisons = 0.0, []
        if expansion: score += 30; raisons.append(f"ATR en expansion (x{regime['atr_pct']})")
        if momentum_accel: score += 35; raisons.append(f"ROC en accélération ({roc_val:.2f})")
        if regime["adx"] >= 22: score += 20; raisons.append(f"ADX {regime['adx']}")
        if regime["structure_score"] >= 55: score += 15

        if score < SEUIL_MIN_STRATEGIE: return None
        return {"nom": "MOMENTUM_EXPANSION", "label": "Momentum Expansion", "direction": direction,
                "score": round(score, 1), "raisons": raisons,
                "details_txt": f"ROC {roc_val:.2f} (prev {roc_prev:.2f}) · ATR x{regime['atr_pct']}",
                "regime_natif": "TREND/BREAKOUT"}
    except Exception:
        return None

def strategie_range_reversion(df15, regime):
    """STRATEGY D — Range Reversion. Actif UNIQUEMENT en régime RANGE — jamais en BREAKOUT."""
    if regime["regime"] != "RANGE": return None
    try:
        upper, lower = calculer_donchian(df15, 20)
        px = float(df15['close'].iloc[-2])
        largeur = float(upper.iloc[-2]) - float(lower.iloc[-2])
        position_pct = (px - float(lower.iloc[-2])) / largeur if largeur > 0 else 0.5

        cci = ta.trend.CCIIndicator(df15['high'], df15['low'], df15['close'], window=14).cci()
        cci_val = float(cci.iloc[-2])

        if position_pct < 0.15 and cci_val < -100:
            direction = "CALL"
        elif position_pct > 0.85 and cci_val > 100:
            direction = "PUT"
        else:
            return None

        score, raisons = 0.0, []
        proximite = (1 - position_pct) if direction == "CALL" else position_pct
        score += proximite * 40
        raisons.append(f"Extrême du range ({position_pct*100:.0f}%)")
        if abs(cci_val) >= 100: score += 30; raisons.append(f"CCI extrême ({cci_val:.0f})")
        if regime["adx"] < 18: score += 20; raisons.append("Range confirmé (ADX faible)")
        if regime["largeur_canal_pct"] < 1.2: score += 10

        if score < SEUIL_MIN_STRATEGIE: return None
        return {"nom": "RANGE_REVERSION", "label": "Range Reversion", "direction": direction,
                "score": round(score, 1), "raisons": raisons,
                "details_txt": f"Position canal {position_pct*100:.0f}% · CCI {cci_val:.0f}",
                "regime_natif": "RANGE"}
    except Exception:
        return None

# ==========================================
# 4. CONFLUENCE ENGINE
# ==========================================

def moteur_confluence(regime, setup):
    """
    ✅ Combine régime + structure + momentum + volatilité + score du setup
    - pénalité de contexte défavorable, selon le barème proposé. Retourne
    (score_final, bande, raisons) — bande ∈ {NO_TRADE, OBSERVATION, POTENTIEL, QUALIFIE}.
    """
    score, raisons = 0.0, []

    # Le setup n'est jamais retourné par une stratégie sauf si son régime
    # est déjà compatible (gating fait en amont) — donc ce point est acquis.
    score += BAREME_CONFLUENCE["regime_compatible"]
    raisons.append(f"Régime {regime['regime']} compatible avec {setup['label']}")

    score += min(BAREME_CONFLUENCE["structure_max"], regime["structure_score"] * (BAREME_CONFLUENCE["structure_max"] / 100))

    momentum_ok = setup["score"] >= 55  # le setup lui-même encode déjà son propre momentum
    if momentum_ok:
        score += BAREME_CONFLUENCE["momentum"]
        raisons.append("Momentum du setup confirmé")

    volatilite_ok = 0.5 <= regime["atr_pct"] <= 2.0  # ni endormi, ni explosif
    if volatilite_ok:
        score += BAREME_CONFLUENCE["volatilite"]
        raisons.append("Volatilité dans une plage exploitable")

    score += min(BAREME_CONFLUENCE["setup_max"], setup["score"] * (BAREME_CONFLUENCE["setup_max"] / 100))

    if regime["chaos"]:
        score += BAREME_CONFLUENCE["contexte_defavorable"]
        raisons.append("⚠️ Contexte défavorable (chaos détecté)")

    score = max(0, min(100, round(score, 1)))

    if score < SEUIL_NO_TRADE: bande = "NO_TRADE"
    elif score < SEUIL_OBSERVATION: bande = "OBSERVATION"
    elif score < SEUIL_POTENTIEL: bande = "POTENTIEL"
    else: bande = "QUALIFIE"

    return score, bande, raisons

# ==========================================
# 5. AI VALIDATOR (Groq — APPROVE/REJECT uniquement)
# ==========================================

def ai_validator(symbole, regime, setup, score_confluence, bande):
    """
    ✅ Reçoit un dossier structuré, répond UNIQUEMENT "APPROVE" ou "REJECT"
    avec une justification courte. Ne génère JAMAIS de direction/signal —
    ne peut que confirmer ou bloquer un signal déjà qualifié par le
    Confluence Engine. Dégrade proprement (APPROVE par défaut) si Groq est
    indisponible/absent — l'IA n'est jamais un point de défaillance dur.
    """
    if not GROQ_API_KEY:
        return {"disponible": False, "decision": "APPROVE", "avis": "Groq désactivé — décision déterministe seule."}

    dossier = {
        "asset": symbole, "regime": regime["regime"], "direction": setup["direction"],
        "strategy": setup["nom"], "adx": regime["adx"], "atr_pct": regime["atr_pct"],
        "structure_score": regime["structure_score"], "setup_score": setup["score"],
        "confluence_score": score_confluence, "bande": bande, "chaos": regime["chaos"],
    }

    prompt = (
        "Tu es un validateur de risque pour un bot d'options binaires. Tu NE génères "
        "JAMAIS de signal — un signal a déjà été produit par un moteur déterministe "
        "(régime de marché + stratégie + confluence). Ton seul rôle : répondre "
        "APPROVE ou REJECT selon que ce dossier te semble cohérent et raisonnable.\n\n"
        "Réponds UNIQUEMENT en JSON strict:\n"
        '{"decision": "APPROVE ou REJECT", "avis": "<1-2 phrases>"}\n\n'
        f"DOSSIER: {json.dumps(dossier, ensure_ascii=False)}\n\n"
        "Sois sévère si le contexte te semble incohérent ou marginal (score de "
        "confluence tout juste au-dessus du seuil, régime ambigu, structure faible)."
    )

    try:
        resp = requests.post(
            GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 150},
            timeout=8,
        )
        if resp.status_code != 200:
            return {"disponible": False, "decision": "APPROVE", "avis": "Groq indisponible (HTTP) — décision déterministe seule."}
        texte = resp.json()["choices"][0]["message"]["content"].strip().replace("```json", "").replace("```", "")
        parsed = json.loads(texte)
        decision = str(parsed.get("decision", "APPROVE")).upper()
        if decision not in ("APPROVE", "REJECT"): decision = "APPROVE"
        return {"disponible": True, "decision": decision, "avis": str(parsed.get("avis", ""))[:250]}
    except Exception as e:
        return {"disponible": False, "decision": "APPROVE", "avis": f"Groq indisponible (erreur) — décision déterministe seule."}

# ==========================================
# RISK ENGINE
# ==========================================

def _init_risk_state(chat_id):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    if chat_id not in risk_state or risk_state[chat_id]["date"] != today:
        risk_state[chat_id] = {
            "date": today, "pnl_pct": 0.0, "consecutive_losses": 0,
            "paused_until": None, "wins": 0, "losses": 0,
            "total_mise": 0.0, "total_gain": 0.0,
            "signaux_recus": 0,
        }
    return risk_state[chat_id]

def risk_engine_verifier(chat_id, symbole):
    """Retourne (ok: bool, raison: str|None). Vérifie TOUTES les protections
    indépendamment des stratégies : limite perte/jour, plafond de signaux/jour,
    pause pertes consécutives, cooldown gagné/perdu par paire."""
    etat = _init_risk_state(chat_id)

    if etat["pnl_pct"] <= -RISK_CONFIG["daily_loss_limit_pct"]:
        return False, (f"🛑 **BOT STOP** — limite de perte journalière atteinte "
                        f"({RISK_CONFIG['daily_loss_limit_pct']}%). Trading suspendu jusqu'à demain.")

    if etat["signaux_recus"] >= LIMITE_SIGNAUX_JOUR:
        return False, (f"📵 **PLAFOND ATTEINT** — {LIMITE_SIGNAUX_JOUR} signaux déjà envoyés aujourd'hui. "
                        f"Reprise demain (ou modifie LIMITE_SIGNAUX_JOUR pour ajuster).")

    if etat["paused_until"] and time.time() < etat["paused_until"]:
        minutes_restantes = int((etat["paused_until"] - time.time()) / 60) + 1
        return False, (f"⏸️ **PAUSE ACTIVE** — {RISK_CONFIG['max_consecutive_losses']} pertes consécutives. "
                        f"Reprise dans {minutes_restantes} min.")

    cle = (chat_id, symbole)
    cd = cooldown_paire_utilisateur.get(cle)
    if cd and time.time() < cd["until"]:
        minutes_restantes = int((cd["until"] - time.time()) / 60) + 1
        return False, f"⏳ **COOLDOWN {cd['raison']}** sur {nom_otc(symbole)} — {minutes_restantes} min restantes."

    return True, None

def risk_engine_enregistrer_resultat(chat_id, symbole, win, mise, gain):
    """Met à jour l'état du Risk Engine après un trade résolu (WIN ou LOSS).
    Applique le cooldown différencié + la pause après pertes consécutives."""
    etat = _init_risk_state(chat_id)
    etat["total_mise"] += mise
    etat["total_gain"] += gain
    etat["pnl_pct"] += (gain / CAPITAL_ACTUEL) * 100

    if win:
        etat["wins"] += 1
        etat["consecutive_losses"] = 0
        cooldown_paire_utilisateur[(chat_id, symbole)] = {
            "until": time.time() + RISK_CONFIG["cooldown_win_minutes"] * 60, "raison": "WIN"}
    else:
        etat["losses"] += 1
        etat["consecutive_losses"] += 1
        cooldown_paire_utilisateur[(chat_id, symbole)] = {
            "until": time.time() + RISK_CONFIG["cooldown_loss_minutes"] * 60, "raison": "LOSS"}
        if etat["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
            etat["paused_until"] = time.time() + RISK_CONFIG["pause_duration_minutes"] * 60

def marche_choc_detecte(df5):
    """Filtre de choc de marché — ATR extrême + bougie anormale + mouvement
    brutal. Utilisé comme veto indépendant, en plus du régime CHAOTIC."""
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
    """WIN/LOSS -> expectancy par trade (en % de la mise), et winrate de
    rentabilité théorique (seuil d'équilibre) pour ce payout."""
    total = wins + losses
    if total == 0: return None, None, None
    winrate = wins / total
    expectancy_pct = (winrate * payout_net) - ((1 - winrate) * 1.0)
    seuil_equilibre = 1 / (1 + payout_net)
    return round(winrate * 100, 1), round(expectancy_pct * 100, 2), round(seuil_equilibre * 100, 2)

# ==========================================
# ORCHESTRATEUR — PIPELINE COMPLET DES 5 COUCHES
# ==========================================

def analyser_binaire_pro(symbole, mode="STANDARD"):
    """
    ✅ V19 — pipeline complet : DATA -> REGIME -> STRATEGIES (gating) ->
    CONFLUENCE -> AI VALIDATOR. Le Risk Engine (par utilisateur) est
    appliqué SÉPARÉMENT au moment de l'envoi (voir risk_engine_verifier),
    car il dépend de l'état individuel de chaque utilisateur, pas du marché.

    Retourne un dict unique (plus riche que l'ancien tuple à 8 éléments) :
    {"decision": "NO_TRADE"/"SIGNAL", "action":..., "direction":...,
     "duree_secondes":..., "exp_texte":..., "regime":..., "setup":...,
     "score_confluence":..., "bande":..., "ai":..., "raisons":[...],
     "raison_no_trade": str|None}
    """
    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        return {"decision": "NO_TRADE", "raison_no_trade": "⚠️ ALERTE NEWS : Marché manipulé."}

    avec_m1 = (mode == "SCALP")
    dfs = data_engine_fetch(symbole, avec_m1=avec_m1)
    if not dfs:
        return {"decision": "NO_TRADE", "raison_no_trade": "⚠️ Données insuffisantes."}

    df15, df5 = dfs["M15"], dfs["M5"]
    df_entree = dfs.get("M1") if (mode == "SCALP" and dfs.get("M1") is not None) else df5

    if marche_choc_detecte(df5):
        return {"decision": "NO_TRADE", "raison_no_trade": "🛑 **FILTRE CHOC DE MARCHÉ** — mouvement anormal détecté. NO TRADE."}

    regime = detecter_regime_marche(df15)
    if regime["regime"] == "CHAOTIC":
        return {"decision": "NO_TRADE", "raison_no_trade": "🌪️ **RÉGIME CHAOTIQUE** — marché non exploitable actuellement. NO TRADE.", "regime": regime}

    # ── Exécution des stratégies : 4 baseline (régime-agnostiques) + les
    # stratégies verrouillées compatibles avec le régime détecté ──
    candidats = []
    for r in (analyser_aroon_rsi(df15), analyser_adx_stc(df15), analyser_cci_macd(df15), analyser_donchian_cci(df15)):
        if r: candidats.append(r)

    if regime["regime"] == "TREND":
        for r in (strategie_trend_pullback(df15, df5, regime), strategie_momentum_expansion(df15, regime)):
            if r: candidats.append(r)
    elif regime["regime"] == "BREAKOUT":
        for r in (strategie_breakout_retest(df15, df5, regime), strategie_momentum_expansion(df15, regime)):
            if r: candidats.append(r)
    elif regime["regime"] == "RANGE":
        r = strategie_range_reversion(df15, regime)
        if r: candidats.append(r)
        # ✅ Range Reversion ne tourne JAMAIS en BREAKOUT — déjà garanti par
        # le fait qu'elle exige regime["regime"]=="RANGE" en interne.

    if not candidats:
        return {"decision": "NO_TRADE", "raison_no_trade": f"⚠️ Aucune stratégie compatible avec le régime {regime['regime']} actuellement.", "regime": regime}

    setup = max(candidats, key=lambda c: c["score"])

    score_confluence, bande, raisons_confluence = moteur_confluence(regime, setup)

    if bande in ("NO_TRADE", "OBSERVATION"):
        raison = (f"👁️ **OBSERVATION** (score {score_confluence}/100, sous le seuil d'envoi de {SEUIL_OBSERVATION}) "
                  f"— {setup['label']} sur {symbole}, pas assez qualifié pour trader.") if bande == "OBSERVATION" else \
                 f"⚠️ Score de confluence insuffisant ({score_confluence}/100 < {SEUIL_NO_TRADE}). NO TRADE."
        return {"decision": "NO_TRADE", "raison_no_trade": raison, "regime": regime, "setup": setup,
                "score_confluence": score_confluence, "bande": bande}

    ai = ai_validator(symbole, regime, setup, score_confluence, bande)
    if ai["decision"] == "REJECT":
        return {"decision": "NO_TRADE",
                "raison_no_trade": f"🤖 **AI VALIDATOR — REJET** : {ai['avis']}",
                "regime": regime, "setup": setup, "score_confluence": score_confluence,
                "bande": bande, "ai": ai}

    # ── Durée d'expiration (simplifiée — le régime+setup remplacent l'ancien
    # balayage de plusieurs timeframes) ──
    if mode == "SCALP":
        duree_secondes, exp_texte = 60, "1 MINUTE (SCALP)"
    else:
        duree_secondes, exp_texte = 300, "5 MINUTES (STANDARD)"

    action = "🟢 ACHAT (CALL)" if setup["direction"] == "CALL" else "🔴 VENTE (PUT)"

    return {
        "decision": "SIGNAL", "action": action, "direction": setup["direction"],
        "duree_secondes": duree_secondes, "exp_texte": exp_texte,
        "regime": regime, "setup": setup, "score_confluence": score_confluence,
        "bande": bande, "ai": ai, "raisons": raisons_confluence + setup["raisons"][:2],
    }

# ==========================================
# EXÉCUTION DU SIGNAL — SANS MARTINGALE (V19)
# ==========================================
# ✅ Le système de paliers/Fantôme/Martingale est SUPPRIMÉ. Un signal =
# une exécution unique, mise fixe, résultat enregistré dans le Risk Engine.

def relever_prix_entree(chat_id, trade_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trade_id in trades_en_cours[chat_id]:
        trades_en_cours[chat_id][trade_id]['prix_entree'] = prix

def executer_trade(chat_id, symbole, direction, duree_secondes, resultat_analyse):
    action_affichage = "🟢 ACHAT (CALL)" if direction == "CALL" else "🔴 VENTE (PUT)"
    nom_paire = nom_otc(symbole)

    maintenant = datetime.datetime.now()
    sec_rest = 60 - maintenant.second
    if sec_rest < 15: sec_rest += 60
    heure_entree = maintenant + datetime.timedelta(seconds=sec_rest)
    heure_texte = heure_entree.strftime("%H:%M:00")

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
        f"🧩 **Stratégie :** {setup['label']}\n"
        f"📊 **Confluence :** {resultat_analyse['score_confluence']}/100\n"
        f"📍 {raisons_txt}{ai_txt}\n"
        f"──────────────────\n"
        f"⏳ *Préparez le broker.*"
    )
    try: bot.send_message(chat_id, texte, parse_mode="Markdown")
    except: pass

    # ✅ NOUVEAU — comptabilise ce signal dans le plafond journalier.
    _init_risk_state(chat_id)["signaux_recus"] += 1

    trade_id = f"{symbole}_{int(time.time()*1000)}"
    trades_en_cours.setdefault(chat_id, {})[trade_id] = {
        'symbole': symbole, 'action': direction, 'prix_entree': None, 'mise': mise,
    }
    Timer(sec_rest, relever_prix_entree, args=[chat_id, trade_id, symbole]).start()
    Timer(sec_rest + duree_secondes + 3, verifier_resultat, args=[chat_id, trade_id]).start()

def verifier_resultat(chat_id, trade_id):
    if chat_id not in trades_en_cours or trade_id not in trades_en_cours[chat_id]:
        return
    trade = trades_en_cours[chat_id][trade_id]
    if not trade.get('prix_entree'):
        trades_en_cours[chat_id].pop(trade_id, None)
        return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie: return

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

    try: bot.send_message(chat_id, texte, parse_mode="Markdown")
    except: pass

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
    if not est_autorise(user_id): return
    filtre_vip_actif[user_id] = not filtre_vip_actif.get(user_id, False)
    if filtre_vip_actif[user_id]:
        bot.send_message(user_id, "💎 **QUALIFIÉ UNIQUEMENT** — tu ne recevras que les signaux ≥ 80/100 (bande QUALIFIÉ).",
                          reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        bot.send_message(user_id, "🔓 **POTENTIEL + QUALIFIÉ** — tu reçois tous les signaux validés (≥ 70/100).",
                          reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours and trades_en_cours[user_id]: return bot.send_message(user_id, "⚠️ Trade en cours.")
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    mode_trading[user_id] = "SCALP" if mode_actuel == "STANDARD" else "STANDARD"
    bot.send_message(user_id, f"✅ Mode {mode_trading[user_id]} activé.", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 MON BILAN")
def mon_bilan(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
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
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    filtre_vip_actif[user_id] = filtre_vip_actif.get(user_id, False)
    _init_risk_state(user_id)
    texte = """🏴‍☠️ **TERMINAL PRIME - V19** 🔥

Architecture à 5 couches indépendantes :
🧭 **Market Regime** — TREND / RANGE / BREAKOUT / CHAOTIC
🧩 **8 Stratégies** — 4 piliers baseline + 4 verrouillées par régime
📊 **Confluence Engine** — score additif, bandes NO_TRADE/OBSERVATION/POTENTIEL/QUALIFIÉ
🤖 **AI Validator** — Groq en APPROVE/REJECT, jamais générateur
🛡️ **Risk Engine** — limite perte/jour, plafond de signaux/jour, pause après pertes consécutives, cooldown gagné/perdu

❌ **Martingale supprimée** — mise fixe, un signal = une exécution.
Le meilleur signal peut être l'absence de signal."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return

    actif = call.data.replace("set_", "")
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

    try: msg = bot.send_message(chat_id, "⏳ *Pipeline 5 couches en cours...*", parse_mode="Markdown")
    except: return

    resultat = analyser_binaire_pro(actif, mode_actuel)

    if resultat["decision"] == "NO_TRADE":
        try: bot.edit_message_text(resultat["raison_no_trade"], chat_id, msg.message_id, parse_mode="Markdown")
        except: pass
        return

    if filtre_vip_actif.get(chat_id, False) and resultat["bande"] != "QUALIFIE":
        try:
            bot.edit_message_text(
                f"💎 **MODE QUALIFIÉ SEUL** — signal trouvé (score {resultat['score_confluence']}/100) "
                f"mais sous le seuil QUALIFIÉ (80). Ignoré. Désactive le filtre pour le recevoir.",
                chat_id, msg.message_id, parse_mode="Markdown")
        except: pass
        return

    try: bot.delete_message(chat_id, msg.message_id)
    except: pass

    executer_trade(chat_id, actif, resultat["direction"], resultat["duree_secondes"], resultat)

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **GUIDE DES HORAIRES** 🕒

✅ **Session Asiatique (00h00-08h00) :** JPY, AUD, CAD, CHF
🇪🇺 **Session Europe (07h00-12h00) :** EUR, USD, CHF
🔥 **Zone US/CA (12h00-17h30) :** EUR/USD, AUD/USD, USD/CAD
🛑 **Repli Tactique (17h30-00h00) :** Forex bloqué.
🪙 **Week-end :** Cryptos uniquement (données Deriv réelles fermées sur Forex).

*(Bilan Automatique à 18h00 GMT)*"""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"), InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"), InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD"),
        InlineKeyboardButton("🇦🇺 AUD/USD OTC", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY OTC", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY OTC", callback_data="set_CHFJPY"),
        InlineKeyboardButton("🇪🇺 EUR/JPY OTC", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD OTC", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY OTC", callback_data="set_AUDJPY"),
        InlineKeyboardButton("🇪🇺 EUR/AUD OTC", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD OTC", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD OTC", callback_data="set_AUDCAD"),
        InlineKeyboardButton("🇺🇸 USD/CHF OTC", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF OTC", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF OTC", callback_data="set_EURCHF"),
        InlineKeyboardButton("🇯🇵 USD/JPY OTC", callback_data="set_USDJPY")
    )
    bot.send_message(message.chat.id, "Sélectionne ta cible :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    statut, msg_erreur = est_symbole_autorise(actif)
    if statut == "BLOCAGE_TOTAL": return bot.send_message(chat_id, msg_erreur, parse_mode="Markdown")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

def scanner_marche_auto():
    while True:
        try:
            time.sleep(45)
            utilisateurs_libres = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_libres: continue

            for paire in CRYPTO_PAIRS + FOREX_PAIRS:
                statut, _ = est_symbole_autorise(paire)
                if statut != "AUTORISE": continue

                for mode in ["STANDARD", "SCALP"]:
                    cle_memoire = f"{paire}_{mode}"
                    delai_repos = 300
                    if cle_memoire in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle_memoire] < delai_repos):
                        continue

                    resultat = analyser_binaire_pro(paire, mode)
                    if resultat["decision"] != "SIGNAL": continue

                    derniere_alerte_auto[cle_memoire] = time.time()
                    nom_affiche = nom_otc(paire)
                    badge = "💎" if resultat["bande"] == "QUALIFIE" else "✅"

                    for uid in utilisateurs_libres:
                        if mode_trading.get(uid, "STANDARD") != mode: continue
                        if filtre_vip_actif.get(uid, False) and resultat["bande"] != "QUALIFIE": continue
                        ok, _ = risk_engine_verifier(uid, paire)
                        if not ok: continue
                        if uid in trades_en_cours and trades_en_cours[uid]: continue

                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"📊 Analyser {nom_affiche}", callback_data=f"set_{paire}"))
                        msg = f"{badge} **SIGNAL {resultat['setup']['label']} : {nom_affiche}**\nRégime {resultat['regime']['regime']} · Score {resultat['score_confluence']}/100"
                        try: bot.send_message(uid, msg, reply_markup=markup)
                        except: pass
        except Exception:
            pass

def gestionnaire_bilan():
    bilan_envoye_aujourdhui = False
    while True:
        try:
            now = datetime.datetime.utcnow()
            if now.hour == 18 and now.minute == 0 and not bilan_envoye_aujourdhui:
                for uid in list(utilisateurs_actifs):
                    if not est_autorise(uid): continue
                    etat = _init_risk_state(uid)
                    wr, expectancy, seuil_eq = calculer_expectancy(etat["wins"], etat["losses"], RISK_CONFIG["payout_net"])
                    if wr is None: continue
                    texte = (f"📊 **BILAN JOURNALIER (18h GMT)**\n──────────────────\n"
                              f"✅ {etat['wins']}W · ❌ {etat['losses']}L · {wr}%\n"
                              f"💰 P&L : {etat['pnl_pct']:+.2f}% · Expectancy : {expectancy:+.1f}%\n"
                              f"⚖️ Seuil équilibre : {seuil_eq}% · Signaux : {etat['signaux_recus']}/{LIMITE_SIGNAUX_JOUR}")
                    try: bot.send_message(uid, texte, parse_mode="Markdown")
                    except: pass
                bilan_envoye_aujourdhui = True
            elif now.hour == 18 and now.minute > 5:
                bilan_envoye_aujourdhui = False
        except Exception:
            pass
        time.sleep(30)

# ==========================================
# COMMANDE /backtest — imprime dans les logs Render + répond sur Telegram
# ==========================================

def _decouper_texte(texte, taille_max=3500):
    """Découpe un texte trop long pour un seul message Telegram (limite 4096)."""
    morceaux = []
    while texte:
        morceaux.append(texte[:taille_max])
        texte = texte[taille_max:]
    return morceaux

@bot.message_handler(commands=['backtest'])
def commande_backtest(message):
    # Réservé à l'admin : c'est une opération lourde (plusieurs minutes,
    # beaucoup de requêtes vers Deriv) — pas quelque chose à ouvrir à tout
    # le monde.
    if message.chat.id != ADMIN_ID:
        return

    parts = message.text.split()
    pairs = parts[1] if len(parts) > 1 else "EURUSD,USDJPY,AUDJPY"
    jours = int(parts[2]) if len(parts) > 2 else 14
    mode = parts[3] if len(parts) > 3 else "STANDARD"
    limite = int(parts[4]) if len(parts) > 4 else LIMITE_SIGNAUX_JOUR

    bot.send_message(
        message.chat.id,
        f"⏳ **Backtest lancé** — {pairs} · {jours}j · {mode}\n"
        f"Ça peut prendre plusieurs minutes. Suis la progression dans Render > Logs, "
        f"ou attends le résumé ici.",
        parse_mode="Markdown",
    )
    print(f"[BACKTEST] Commande reçue de {message.chat.id} : {pairs} / {jours}j / {mode}", flush=True)

    def tache():
        try:
            import backtest_engine  # import différé pour éviter tout souci d'import circulaire
            rapport = backtest_engine.lancer_backtest_texte(pairs, jours, mode, limite)
        except Exception as e:
            rapport = f"❌ Erreur pendant le backtest : {e}"
            print(f"[BACKTEST] ERREUR : {e}", flush=True)
        for morceau in _decouper_texte(rapport):
            try:
                bot.send_message(message.chat.id, f"```\n{morceau}\n```", parse_mode="Markdown")
            except Exception:
                try: bot.send_message(message.chat.id, morceau)
                except: pass

    Thread(target=tache, daemon=True).start()


# ==========================================
# COMMANDE /diagnostic — teste la connectivité réseau depuis l'app elle-même
# (utile sur Render Free, où l'onglet Shell n'est pas disponible)
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

        # ✅ La vraie requête que fait le bot pour analyser une paire —
        # testée sur CHAQUE endpoint Deriv connu, pour voir lequel répond.
        for url in DERIV_ENDPOINTS:
            try:
                ws = websocket.WebSocket()
                ws.connect(url, timeout=8)
                req = {"ticks_history": "frxEURUSD", "end": "latest", "count": 5,
                       "style": "candles", "granularity": 300}
                ws.send(json.dumps(req))
                brut = ws.recv()
                ws.close()
                resultats.append(f"🔎 {url}\n   → réponse : {brut[:250]}")
            except Exception as e:
                resultats.append(f"❌ {url}\n   → ÉCHEC : {type(e).__name__}: {e}")

        texte = "🔍 RÉSULTAT DIAGNOSTIC RÉSEAU\n" + "\n".join(resultats)
        print("[DIAGNOSTIC] " + texte.replace("\n", " | "), flush=True)
        try:
            bot.send_message(message.chat.id, texte)
        except Exception:
            pass

    Thread(target=tache, daemon=True).start()


if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestionnaire_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition V19 — Architecture 5 Couches Démarrée.", flush=True)
    bot.infinity_polling()
