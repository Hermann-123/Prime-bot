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
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAE4LqM1z5xb8rWvrUuZtG6GjlIGtJ0-cjo"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================

user_prefs = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
cooldown_actifs = {} # Le "Silencieux" pour bloquer les paires perdantes

utilisateurs_autorises = {
    ADMIN_ID: "LIFETIME"
}
cles_generees = {}

stats_journee = {
    'ITM': 0, 
    'OTM': 0, 
    'details': []
}

bilan_envoye_aujourdhui = False
transition_nuit_envoyee = False
transition_jour_envoyee = False

CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD", 
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF", 
    "CADCHF", "EURCHF", "USDJPY"
]

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition GOD MODE — 4 Piliers Indépendants (V9)"

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
        if expiration == "LIFETIME":
            return True
        if datetime.datetime.now() < expiration:
            return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
            except: pass
            return False
    return False

def generer_cle():
    caracteres = string.ascii_uppercase + string.digits
    aleatoire = ''.join(random.choice(caracteres) for _ in range(8))
    return f"PRIME-{aleatoire}"

def generer_jauge(pourcentage):
    if pourcentage >= 99:
        return "[██████████] 👑 MAX"
    pleins = int(pourcentage / 10)
    vides = 10 - pleins
    return f"[{'█' * pleins}{'░' * vides}] {pourcentage}%"

# ==========================================
# NOUVELLES FONCTIONS PRO (NEWS & H1)
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
                    if diff <= 30: 
                        return True
    except: pass
    return False

def obtenir_tendance_H1(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
        req = {"ticks_history": symbole, "end": "latest", "count": 50, "style": "candles", "granularity": 3600}
        ws.send(json.dumps(req))
        res = json.loads(ws.recv())
        ws.close()
        if "candles" in res and len(res["candles"]) > 20:
            df = pd.DataFrame(res['candles'])
            ema20 = ta.trend.EMAIndicator(close=df['close'].astype(float), window=20).ema_indicator()
            return "UP" if float(df['close'].iloc[-1]) > ema20.iloc[-1] else "DOWN"
    except: pass
    return "NEUTRE"

# ==========================================
# ROUTEUR API DERIV (FOREX VS CRYPTO)
# ==========================================

def prefixer_symbole(symbole_brut):
    if symbole_brut in CRYPTO_PAIRS:
        return f"cry{symbole_brut}"
    return f"frx{symbole_brut}"

def obtenir_donnees_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 250, "style": "candles", "granularity": 300}
            ws.send(json.dumps(req))
            history = json.loads(ws.recv())
            ws.close()
            if "error" not in history and "candles" in history:
                return history['candles']
        except:
            time.sleep(1)
            continue
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    symbole = prefixer_symbole(symbole_brut)
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {"ticks_history": symbole, "end": "latest", "count": 1, "style": "ticks"}
            ws.send(json.dumps(req))
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res and "prices" in res["history"]:
                return float(res["history"]["prices"][0])
        except:
            time.sleep(1)
            continue
    return None

# ==========================================
# SYSTÈME DE VÉRIFICATION ITM/OTM & COOLDOWN
# ==========================================
# ✅ V9: trades_en_cours passe d'un dict simple (1 seul trade actif par
# utilisateur) à un dict de dicts indexé par trade_id — indispensable
# maintenant que 4 piliers indépendants peuvent chacun ouvrir un trade en
# même temps pour le même utilisateur (ex: Aroon+RSI et CCI+MACD
# déclenchent tous les deux sur la même paire au même cycle).

def relever_prix_entree(chat_id, trade_id, symbole):
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours and trade_id in trades_en_cours[chat_id]:
        trades_en_cours[chat_id][trade_id]['prix_entree'] = prix

def verifier_resultat(chat_id, trade_id):
    global stats_journee, cooldown_actifs
    if chat_id not in trades_en_cours or trade_id not in trades_en_cours[chat_id]:
        return
    trade = trades_en_cours[chat_id][trade_id]
    if not trade.get('prix_entree'):
        trades_en_cours[chat_id].pop(trade_id, None)
        return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    if not prix_sortie: return

    prix_entree = trade['prix_entree']
    action = trade['action']
    pilier = trade.get('pilier', '?')

    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    type_emoji = "🪙" if symbole in CRYPTO_PAIRS else "💱"
    
    if gagne:
        texte = f"✅ **VICTOIRE (ITM)**\n🚀 Signal {nom_paire} ({action})\n🧩 Pilier : {pilier}\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['ITM'] += 1
        stats_journee['details'].append(f"✅ {type_emoji} {nom_paire} ({action}) · {pilier}")
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
    else:
        texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n🧩 Pilier : {pilier}\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {type_emoji} {nom_paire} ({action}) · {pilier}")
        cooldown_actifs[symbole] = time.time()
    
    try: bot.send_message(ADMIN_ID, texte, parse_mode="Markdown")
    except: pass

    trades_en_cours[chat_id].pop(trade_id, None)
    if not trades_en_cours[chat_id]:
        trades_en_cours.pop(chat_id, None)

# ==========================================
# MOTEUR D'ANALYSE ( GOD MODE ELITE V9 — 4 PILIERS INDÉPENDANTS )
# ==========================================
# ✅ V8: Remplace les conditions en ET strict (prix EXACTEMENT sur la bande
# Bollinger ET tendance H1 alignée ET EMA200 alignée ET score>=8 dès le
# départ, ce qui rendait VSA ou FVG quasi-obligatoires) par un SCORE
# CUMULATIF — chaque critère apporte des points, le signal est émis dès
# que le score dépasse un seuil raisonnable. C'est la même correction que
# celle appliquée au moteur MT5 (Terminal Prime V55): empiler des
# conditions dures fait chuter la probabilité combinée de façon
# exponentielle, alors qu'un score cumulatif reste sélectif sans devenir
# statistiquement quasi impossible à déclencher.
#
# Diagnostic ajouté ([DEBUG-BIN]) à chaque rejet et à chaque signal émis,
# pour identifier immédiatement quel critère manque si jamais le volume
# de signaux reste insuffisant.

# ==========================================
# ✅ V9 NEW: QUATRE PILIERS D'ANALYSE INDÉPENDANTS
# ==========================================
# Chaque pilier utilise des indicateurs et une logique différente — ils
# ne dépendent jamais les uns des autres. Chacun peut produire un signal
# de son côté (0 à 4 signaux possibles par actif et par cycle). Tous
# utilisent le même principe de SCORE CUMULATIF que le reste du moteur :
# chaque critère apporte des points, pas de condition ET stricte qui
# ferait chuter la probabilité combinée à presque zéro.

def calculer_aroon(df, period=9):
    """Aroon Up/Down — mesure depuis combien de temps le plus haut (resp.
    plus bas) récent a été établi. Proche de 100 = extrême très récent
    (dominance claire d'un camp), proche de 0 = extrême ancien."""
    high_idx = df['high'].rolling(period + 1).apply(lambda x: period - x.values.argmax(), raw=True)
    low_idx  = df['low'].rolling(period + 1).apply(lambda x: period - x.values.argmin(), raw=True)
    aroon_up   = ((period - high_idx) / period) * 100
    aroon_down = ((period - low_idx) / period) * 100
    return aroon_up, aroon_down

def calculer_stc(df, fast=14, slow=50, cycle=5, d1=3, d2=3):
    """Schaff Trend Cycle — double lissage stochastique du MACD. Oscille
    0-100 ; détecte un changement de cycle plus tôt qu'un MACD classique."""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow

    low_macd  = macd.rolling(cycle).min()
    high_macd = macd.rolling(cycle).max()
    k1 = 100 * (macd - low_macd) / (high_macd - low_macd).replace(0, 1e-9)
    d1_line = k1.ewm(span=d1, adjust=False).mean()

    low_d  = d1_line.rolling(cycle).min()
    high_d = d1_line.rolling(cycle).max()
    k2 = 100 * (d1_line - low_d) / (high_d - low_d).replace(0, 1e-9)
    stc = k2.ewm(span=d2, adjust=False).mean()
    return stc.clip(0, 100)

def calculer_donchian(df, period=20):
    """Canal de Donchian — plus haut/plus bas des N dernières bougies,
    sert de référence de zone extrême (comme un support/résistance objectif)."""
    upper = df['high'].rolling(period).max()
    lower = df['low'].rolling(period).min()
    return upper, lower


def analyser_aroon_rsi(symbole, df):
    """PILIER 1 — 'Show The Direction' (Aroon 9 + RSI 6).
    L'Aroon indique qui domine (acheteurs ou vendeurs) selon la récence
    des plus hauts/plus bas ; le RSI(6) confirme que le momentum va dans
    le même sens sans être déjà à bout de souffle."""
    try:
        aroon_up, aroon_down = calculer_aroon(df, 9)
        rsi6 = ta.momentum.RSIIndicator(close=df['close'], window=6).rsi()

        au, ad = float(aroon_up.iloc[-2]), float(aroon_down.iloc[-2])
        au_prev, ad_prev = float(aroon_up.iloc[-3]), float(aroon_down.iloc[-3])
        rsi_val = float(rsi6.iloc[-2])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                s += min(35, max(0, (au - ad) * 0.5))
                if au_prev <= ad_prev and au > ad:
                    s += 20; raisons.append("Croisement Aroon Up/Down")
                if 40 <= rsi_val <= 68:
                    s += 20; raisons.append(f"RSI sain ({rsi_val:.1f})")
                if au >= 70:
                    s += 15; raisons.append(f"Aroon Up fort ({au:.0f})")
            else:
                s += min(35, max(0, (ad - au) * 0.5))
                if ad_prev <= au_prev and ad > au:
                    s += 20; raisons.append("Croisement Aroon Down/Up")
                if 32 <= rsi_val <= 60:
                    s += 20; raisons.append(f"RSI sain ({rsi_val:.1f})")
                if ad >= 70:
                    s += 15; raisons.append(f"Aroon Down fort ({ad:.0f})")
            return round(min(100, s), 1), raisons

        score_call, raisons_call = score("CALL")
        score_put, raisons_put = score("PUT")
        return {
            "nom": "AROON_RSI", "label": "Show The Direction",
            "score_call": score_call, "score_put": score_put,
            "raisons_call": raisons_call, "raisons_put": raisons_put,
            "details_txt": f"Aroon Up {au:.0f} / Down {ad:.0f} · RSI(6) {rsi_val:.1f}",
        }
    except Exception as e:
        print(f"[DEBUG-P1-AROON] {symbole} EXCEPTION: {e}", flush=True)
        return None


def analyser_adx_stc(symbole, df):
    """PILIER 2 — 'Identifies Reversal Points' (ADX 14 + Schaff Trend
    Cycle 14,50,5,3,3). Le STC détecte un retournement de cycle (sortie
    d'une zone basse/haute), l'ADX/DI confirme que la force du marché
    accompagne ce retournement plutôt que de le contredire."""
    try:
        adx_ind = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        adx = adx_ind.adx()
        di_pos = adx_ind.adx_pos()
        di_neg = adx_ind.adx_neg()
        stc = calculer_stc(df)

        adx_val = float(adx.iloc[-2])
        dip, din = float(di_pos.iloc[-2]), float(di_neg.iloc[-2])
        stc_val, stc_prev = float(stc.iloc[-2]), float(stc.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                if stc_prev <= 25 and stc_val > stc_prev:
                    s += 35; raisons.append(f"STC remonte depuis zone basse ({stc_val:.0f})")
                elif stc_val < 40:
                    s += 15
                if dip > din:
                    s += 20; raisons.append("+DI > -DI")
                if adx_val >= 15:
                    s += min(20, (adx_val - 15) * 1.2); raisons.append(f"ADX {adx_val:.0f}")
            else:
                if stc_prev >= 75 and stc_val < stc_prev:
                    s += 35; raisons.append(f"STC redescend depuis zone haute ({stc_val:.0f})")
                elif stc_val > 60:
                    s += 15
                if din > dip:
                    s += 20; raisons.append("-DI > +DI")
                if adx_val >= 15:
                    s += min(20, (adx_val - 15) * 1.2); raisons.append(f"ADX {adx_val:.0f}")
            return round(min(100, s), 1), raisons

        score_call, raisons_call = score("CALL")
        score_put, raisons_put = score("PUT")
        return {
            "nom": "ADX_STC", "label": "Identifies Reversal Points",
            "score_call": score_call, "score_put": score_put,
            "raisons_call": raisons_call, "raisons_put": raisons_put,
            "details_txt": f"STC {stc_val:.0f} · ADX {adx_val:.0f} (+DI {dip:.0f}/-DI {din:.0f})",
        }
    except Exception as e:
        print(f"[DEBUG-P2-ADXSTC] {symbole} EXCEPTION: {e}", flush=True)
        return None


def analyser_cci_macd(symbole, df):
    """PILIER 3 — 'A Moment When...' (CCI 10 + MACD 10,25,5). Le CCI
    capte les excès de prix à court terme, le MACD confirme que la
    dynamique de fond change de sens au même moment."""
    try:
        cci = ta.trend.CCIIndicator(high=df['high'], low=df['low'], close=df['close'], window=10).cci()
        macd_ind = ta.trend.MACD(close=df['close'], window_slow=25, window_fast=10, window_sign=5)
        macd_hist = macd_ind.macd_diff()

        cci_val, cci_prev = float(cci.iloc[-2]), float(cci.iloc[-3])
        hist_val, hist_prev = float(macd_hist.iloc[-2]), float(macd_hist.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                if cci_prev <= -100 and cci_val > cci_prev:
                    s += 30; raisons.append(f"CCI remonte depuis survente ({cci_val:.0f})")
                elif cci_val < -50:
                    s += 12
                if hist_val > 0:
                    s += 20; raisons.append("MACD histogram positif")
                if hist_val > hist_prev:
                    s += 15; raisons.append("MACD histogram en hausse")
            else:
                if cci_prev >= 100 and cci_val < cci_prev:
                    s += 30; raisons.append(f"CCI redescend depuis surachat ({cci_val:.0f})")
                elif cci_val > 50:
                    s += 12
                if hist_val < 0:
                    s += 20; raisons.append("MACD histogram négatif")
                if hist_val < hist_prev:
                    s += 15; raisons.append("MACD histogram en baisse")
            return round(min(100, s), 1), raisons

        score_call, raisons_call = score("CALL")
        score_put, raisons_put = score("PUT")
        return {
            "nom": "CCI_MACD", "label": "A Moment When...",
            "score_call": score_call, "score_put": score_put,
            "raisons_call": raisons_call, "raisons_put": raisons_put,
            "details_txt": f"CCI(10) {cci_val:.0f} · MACD hist {hist_val:.5f}",
        }
    except Exception as e:
        print(f"[DEBUG-P3-CCIMACD] {symbole} EXCEPTION: {e}", flush=True)
        return None


def analyser_donchian_cci(symbole, df):
    """PILIER 4 — 'You Know And...' (Donchian Channel 20 + CCI 11). Le
    canal de Donchian repère les extrêmes récents (comme un support/
    résistance objectif), le CCI confirme que le prix reprend son souffle
    plutôt que de continuer sa cassure."""
    try:
        upper, lower = calculer_donchian(df, 20)
        cci = ta.trend.CCIIndicator(high=df['high'], low=df['low'], close=df['close'], window=11).cci()

        px = float(df['close'].iloc[-2])
        up_val, low_val = float(upper.iloc[-2]), float(lower.iloc[-2])
        largeur = up_val - low_val if (up_val - low_val) > 0 else 1e-9
        position_pct = (px - low_val) / largeur  # 0 = bord bas, 1 = bord haut

        cci_val, cci_prev = float(cci.iloc[-2]), float(cci.iloc[-3])

        def score(direction):
            s, raisons = 0.0, []
            if direction == "CALL":
                proximite = max(0, 1 - position_pct * 2.5)
                s += proximite * 35
                if proximite > 0.5:
                    raisons.append(f"Prix proche du bas du canal Donchian ({position_pct*100:.0f}%)")
                if cci_prev <= -100 and cci_val > cci_prev:
                    s += 30; raisons.append(f"CCI remonte depuis survente ({cci_val:.0f})")
                elif cci_val < -30:
                    s += 12
            else:
                proximite = max(0, (position_pct - 0.6) * 2.5)
                s += proximite * 35
                if proximite > 0.5:
                    raisons.append(f"Prix proche du haut du canal Donchian ({position_pct*100:.0f}%)")
                if cci_prev >= 100 and cci_val < cci_prev:
                    s += 30; raisons.append(f"CCI redescend depuis surachat ({cci_val:.0f})")
                elif cci_val > 30:
                    s += 12
            return round(min(100, s), 1), raisons

        score_call, raisons_call = score("CALL")
        score_put, raisons_put = score("PUT")
        return {
            "nom": "DONCHIAN_CCI", "label": "You Know And...",
            "score_call": score_call, "score_put": score_put,
            "raisons_call": raisons_call, "raisons_put": raisons_put,
            "details_txt": f"Position canal {position_pct*100:.0f}% · CCI(11) {cci_val:.0f}",
        }
    except Exception as e:
        print(f"[DEBUG-P4-DONCHIAN] {symbole} EXCEPTION: {e}", flush=True)
        return None


SEUIL_SIGNAL_PILIER = 45  # sur ~100 max par pilier — ajustable directement ici

def cerveau_binaire_4_piliers(symbole):
    """
    Orchestrateur : vérifie cooldown/news (inchangé), récupère les
    bougies UNE seule fois, calcule l'expiration ATR UNE seule fois, puis
    fait tourner les 4 piliers INDÉPENDAMMENT les uns des autres. Retourne
    une liste de signaux (0 à 4), chacun avec son pilier d'origine, sa
    direction, son score et ses raisons — jamais de fusion entre piliers.
    """
    if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole] < 3600):
        print(f"[DEBUG-BIN] {symbole} REJET GLOBAL: cooldown actif", flush=True)
        return []

    if est_heure_de_news_dynamique() and symbole not in CRYPTO_PAIRS:
        print(f"[DEBUG-BIN] {symbole} REJET GLOBAL: fenêtre de news à fort impact", flush=True)
        return []

    tendance_h1 = obtenir_tendance_H1(symbole)
    candles = obtenir_donnees_deriv(symbole)
    if not candles or len(candles) < 60:
        print(f"[DEBUG-BIN] {symbole} REJET GLOBAL: données insuffisantes", flush=True)
        return []

    try:
        df = pd.DataFrame([{
            'open': float(c['open']), 'close': float(c['close']),
            'high': float(c['high']), 'low': float(c['low'])
        } for c in candles])

        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        atr_actuel = df['atr'].iloc[-1]
        atr_moyen = df['atr'].rolling(window=20).mean().iloc[-1]

        if atr_actuel > (atr_moyen * 1.5):
            duree_secondes, expiration_texte = 120, "2 MINUTES (Vitesse Élevée ⚡)"
        elif atr_actuel < (atr_moyen * 0.8):
            duree_secondes, expiration_texte = 600, "10 MINUTES (Marché Lent 🐢)"
        else:
            duree_secondes, expiration_texte = 300, "5 MINUTES (Standard 💎)"

        signaux = []
        for fn in (analyser_aroon_rsi, analyser_adx_stc, analyser_cci_macd, analyser_donchian_cci):
            resultat = fn(symbole, df)
            if not resultat:
                continue

            meilleur_score = max(resultat["score_call"], resultat["score_put"])
            if meilleur_score < SEUIL_SIGNAL_PILIER:
                print(f"[DEBUG-BIN] {symbole}/{resultat['nom']} REJET: "
                      f"CALL={resultat['score_call']} PUT={resultat['score_put']} < seuil {SEUIL_SIGNAL_PILIER}", flush=True)
                continue

            direction = "CALL" if resultat["score_call"] >= resultat["score_put"] else "PUT"
            score_final = meilleur_score
            raisons = resultat["raisons_call"] if direction == "CALL" else resultat["raisons_put"]

            # Le biais H1 sert de bonus/malus léger, jamais de filtre dur —
            # chaque pilier reste maître de sa propre décision.
            if (direction == "CALL" and tendance_h1 == "UP") or (direction == "PUT" and tendance_h1 == "DOWN"):
                score_final = min(100, score_final + 5)

            print(f"[DEBUG-BIN] {symbole}/{resultat['nom']} ✅ SIGNAL ÉMIS — {direction} "
                  f"score={score_final} ({resultat['details_txt']})", flush=True)

            signaux.append({
                "pilier": resultat["nom"], "label": resultat["label"],
                "action": "🟢 ACHAT (CALL)" if direction == "CALL" else "🔴 VENTE (PUT)",
                "action_simple": direction,
                "score": score_final, "raisons": raisons,
                "details_txt": resultat["details_txt"],
                "expiration_texte": expiration_texte, "duree_secondes": duree_secondes,
            })

        return signaux
    except Exception as e:
        print(f"[DEBUG-BIN] {symbole} EXCEPTION GLOBALE: {type(e).__name__}: {e}", flush=True)
        return []


def analyser_binaire_pro(symbole):
    """
    ✅ V9: conservée pour compatibilité (utilisée par les anciens appels
    éventuels) — délègue au meilleur signal parmi les 4 piliers
    indépendants. Le vrai moteur multi-piliers est
    cerveau_binaire_4_piliers(), utilisé par le scanner et le menu de
    sélection de devise, qui peut retourner PLUSIEURS signaux distincts
    (un par pilier) au lieu d'un seul.
    """
    signaux = cerveau_binaire_4_piliers(symbole)
    if not signaux:
        return "⚠️ Marché sans confluence suffisante actuellement (ou cooldown/news actif).", None, None, None, None, None, None, None

    meilleur = max(signaux, key=lambda s: s["score"])
    return (meilleur["action"], 99, meilleur["expiration_texte"], meilleur["duree_secondes"],
            None, None, meilleur["details_txt"], int(round(meilleur["score"] / 10)))


# ==========================================
# LE SCANNER AUTOMATIQUE DE L'OMBRE
# ==========================================

def scanner_marche_auto():
    """
    ✅ V9: boucle maintenant sur la LISTE de signaux retournée par
    cerveau_binaire_4_piliers() — un même actif peut générer 0 à 4 alertes
    distinctes par cycle (une par pilier déclenché), chacune dédupliquée
    séparément via une clé {actif}_{pilier} (au lieu d'une seule clé par
    actif comme avant), pour ne jamais bloquer un pilier à cause du
    cooldown d'un autre.
    """
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: continue
                
            maintenant = datetime.datetime.now()
            jour_semaine = maintenant.weekday() 
            devises_a_surveiller = CRYPTO_PAIRS if jour_semaine >= 5 else FOREX_PAIRS
            
            for actif in devises_a_surveiller:
                signaux = cerveau_binaire_4_piliers(actif)
                for signal in signaux:
                    cle = f"{actif}_{signal['pilier']}"
                    temps_actuel = time.time()
                    if cle in derniere_alerte_auto and (temps_actuel - derniere_alerte_auto[cle] < 3600):
                        continue
                    derniere_alerte_auto[cle] = temps_actuel
                    nom_affiche = f"{actif[:3]}/{actif[3:]}"

                    jauge_visuelle = generer_jauge(min(99, int(signal["score"])))
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(
                        f"📊 Analyser {nom_affiche} ({signal['label']})",
                        callback_data=f"set_{actif}|{signal['pilier']}"
                    ))

                    if signal["score"] >= 80:
                        alerte_msg = (f"🔥 **ALERTE GOD MODE — {signal['label']}** 🔥\n\n"
                                      f"Configuration mathématique lourde\n**CONFIANCE :** {jauge_visuelle}\n"
                                      f"Cible : **{nom_affiche}**\n\n👇 *Clique sur le bouton pour déclencher la frappe !*")
                    else:
                        alerte_msg = (f"🚨 **OPPORTUNITÉ VIP — {signal['label']}** 🚨\n\n"
                                      f"Le radar a esquivé les pièges. Signal propre !\n**CONFIANCE :** {jauge_visuelle}\n"
                                      f"Cible : **{nom_affiche}**\n\n👇 *Clique sur le bouton pour l'analyse !*")

                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except: pass
        except: pass

# ==========================================
# GESTIONNAIRE D'HORAIRES ET DE BILAN (22H00)
# ==========================================

def gestion_horaires_et_bilan():
    global stats_journee, bilan_envoye_aujourdhui, transition_nuit_envoyee, transition_jour_envoyee
    while True:
        try:
            maintenant = datetime.datetime.now()
            heure, minute, jour_semaine = maintenant.hour, maintenant.minute, maintenant.weekday()
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]

            if jour_semaine < 5: 
                if heure == 20 and minute == 0 and not transition_nuit_envoyee:
                    texte_nuit = "🌉 **TRANSITION DE SESSION : MODE ASIATIQUE ACTIVÉ** 🌉\n\nLes volumes s'effondrent sur l'Europe. Le Terminal Prime bascule ses radars exclusivement sur l'Asie.\n\n*La chasse continue de nuit. Restez concentrés.* 🥷"
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, texte_nuit, parse_mode="Markdown")
                        except: pass
                    transition_nuit_envoyee, transition_jour_envoyee = True, False

                elif heure == 8 and minute == 0 and not transition_jour_envoyee:
                    texte_jour = "☀️ **TRANSITION DE SESSION : MODE EUROPE/US ACTIVÉ** ☀️\n\nOuverture des marchés majeurs. La volatilité est de retour.\n\n*Bonne journée de trading à tous les VIP !* 🚀"
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, texte_jour, parse_mode="Markdown")
                        except: pass
                    transition_jour_envoyee, transition_nuit_envoyee = True, False

            if heure == 22 and minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                if total_trades > 0:
                    winrate = round((stats_journee['ITM'] / total_trades) * 100)
                    texte_bilan_admin = f"📊 **BILAN VIP DE LA JOURNÉE** 📊\n──────────────────\n🎯 **Total Signaux :** {total_trades}\n✅ **Victoires (ITM) :** {stats_journee['ITM']}\n❌ **Pertes (OTM) :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n──────────────────\n"
                    for detail in stats_journee['details']: texte_bilan_admin += f"{detail}\n"
                    try: bot.send_message(ADMIN_ID, texte_bilan_admin, parse_mode="Markdown")
                    except: pass
                stats_journee, bilan_envoye_aujourdhui = {'ITM': 0, 'OTM': 0, 'details': []}, True
            elif heure == 23: bilan_envoye_aujourdhui = False
            time.sleep(30)
        except: time.sleep(60)

# ==========================================
# COMMANDES ADMIN ET GÉNÉRATION DE CLÉS
# ==========================================

@bot.message_handler(commands=['panel'])
def admin_panel(message):
    if message.chat.id != ADMIN_ID: return
    bot.send_message(ADMIN_ID, f"Admin Panel 🔥\nCapital actuel : {CAPITAL_ACTUEL}$")

# 💰 AJOUT DE LA COMMANDE CAPITAL SÉPARÉE
@bot.message_handler(commands=['capital'])
def voir_capital(message):
    if message.chat.id != ADMIN_ID: return
    bot.send_message(ADMIN_ID, f"💰 **SOLDE ACTUEL DU COMPTE** 💰\n──────────────────\n💵 Montant : `{CAPITAL_ACTUEL}$`\n──────────────────\n⚖️ *Prêt pour la prochaine session !*", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("PRIME-"))
def activer_cle(message):
    cle = message.text.strip()
    if cle in cles_generees:
        infos_cle = cles_generees[cle]
        if infos_cle["user_id"] != message.chat.id:
            bot.send_message(message.chat.id, "❌ **ACCÈS REFUSÉ**", parse_mode="Markdown")
            return
        jours = infos_cle["jours"]
        if jours == 999:
            utilisateurs_autorises[message.chat.id] = "LIFETIME"
            duree_texte = "À VIE 👑"
        else:
            expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
            utilisateurs_autorises[message.chat.id] = expiration
            duree_texte = f"jusqu'au {expiration.strftime('%d/%m/%Y à %H:%M')}"
        del cles_generees[cle] 
        bot.send_message(message.chat.id, f"✅ **CLÉ ACCEPTÉE !** 🎉\n\nVotre abonnement est activé {duree_texte}.\n\nTapez /start pour lancer le Terminal Prime.", parse_mode="Markdown")
    else: bot.send_message(message.chat.id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    if call.from_user.id != ADMIN_ID: return
    action, user_id = call.data.split("_")[1], int(call.data.split("_")[2])
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"),
            InlineKeyboardButton("2 Semaines 🔥", callback_data=f"gen_14_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"),
            InlineKeyboardButton("2 Mois 💎", callback_data=f"gen_60_{user_id}"),
            InlineKeyboardButton("3 Mois ✨", callback_data=f"gen_90_{user_id}"),
            InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Utilisateur `{user_id}` accepté.\nChoisis la durée :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    elif action == "refuser": bot.edit_message_text("❌ Demande refusée.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    if call.from_user.id != ADMIN_ID: return
    jours, user_id = int(call.data.split("_")[1]), int(call.data.split("_")[2])
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    duree_texte = {7:"1 Semaine", 14:"2 Semaines", 30:"1 Mois", 60:"2 Mois", 90:"3 Mois", 999:"À VIE"}.get(jours, f"{jours} Jours")
    msg = f"🔑 **CLÉ GÉNÉRÉE** 🔑\n\n⏳ Durée : {duree_texte}\n👤 ID : `{user_id}`\n\nCopie ce message à ton client :\n\n`{cle}`"
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# ==========================================
# COMMANDES TÉLÉGRAM ET MENUS VIP
# ==========================================

def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("✅ Accepter", callback_data=f"admin_accepter_{user_id}"), InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}"))
        try: bot.send_message(ADMIN_ID, f"🚨 **NOUVEAU CLIENT POTENTIEL** 🚨\n\n🆔 `{user_id}`\n\nGénérer un abonnement ?", reply_markup=markup, parse_mode="Markdown")
        except: pass
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT - TERMINAL PRIVÉ** 🔒\n\nCe système est une intelligence artificielle de trading haute précision sous licence payante.\n\n📲 **Pour obtenir votre clé d'accès (Abonnement), veuillez contacter le fondateur : [@hermann1123](https://t.me/hermann1123)**", parse_mode="Markdown", disable_web_page_preview=True)

    utilisateurs_actifs.add(user_id)
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - ÉDITION GOD MODE — 4 PILIERS (V9)** 🔥
    
Bienvenue dans le radar institutionnel. Ce système fait tourner **4 piliers d'analyse indépendants**, chacun avec sa propre logique — s'ils déclenchent en même temps, tu reçois plusieurs signaux distincts.

🧩 **Show The Direction** — Aroon(9) + RSI(6)
🧩 **Identifies Reversal Points** — ADX(14) + Schaff Trend Cycle
🧩 **A Moment When...** — CCI(10) + MACD(10,25,5)
🧩 **You Know And...** — Donchian Channel(20) + CCI(11)

Le système choisit **LUI-MÊME** le meilleur temps d'expiration (2, 5 ou 10 minutes) en fonction du marché.

📖 **MODE D'EMPLOI :**
1️⃣ **SÉLECTION :** Clique sur "📊 CHOISIR UNE DEVISE" pour verrouiller un actif.
2️⃣ **RADAR :** Clique sur "🚀 LANCER L'ANALYSE" pour déclencher le scan Sniper.
3️⃣ **DISCIPLINE :** N'oublie pas : 2% de mise maximum et stop total après 3 pertes.

⚠️ **ATTENTION : RÉGLEZ SOIGNEUSEMENT L'HORLOGE DE POCKET BROKER SELON LE SIGNAL !** ⏱️"""
    bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **GUIDE DES HORAIRES DE TRADING (Heure GMT)** 🕒

✅ **SESSION SEMAINE 1 (08h00 - 11h00) :** EUR/USD, GBP/USD
🔥 **SESSION SEMAINE 2 (13h30 - 16h30) :** EUR/USD, AUD/USD
🌉 **SESSION SEMAINE 3 (20h00 - 08h00) :** AUD/JPY, USD/JPY, EUR/JPY
🪙 **SESSION WEEK-END (Samedi/Dimanche) :** CRYPTOMONNAIES UNIQUEMENT

*Rappel de Discipline : Fixe-toi tes 2% de mise max et arrête-toi après 3 pertes !*"""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    jour_semaine = datetime.datetime.now().weekday()
    
    if jour_semaine >= 5:
        markup.add(
            InlineKeyboardButton("🪙 BTC/USD", callback_data="set_BTCUSD"),
            InlineKeyboardButton("🔷 ETH/USD", callback_data="set_ETHUSD"),
            InlineKeyboardButton("⚡ LTC/USD", callback_data="set_LTCUSD")
        )
        message_texte = "Mode Week-End 🪙 : Les banques sont fermées. Sélectionne la Crypto :"
    else:
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"), InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY"), InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD"), InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
            InlineKeyboardButton("🇺🇸 USD/CHF", callback_data="set_USDCHF"), InlineKeyboardButton("🇨🇦 CAD/CHF", callback_data="set_CADCHF"), InlineKeyboardButton("🇪🇺 EUR/CHF", callback_data="set_EURCHF"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY")
        )
        message_texte = "Mode Semaine 💱 : Arsenal Pocket Broker synchronisé. Sélectionne ta cible :"
    bot.send_message(message.chat.id, message_texte, reply_markup=markup)

def envoyer_signal_et_tracker(chat_id, actif, nom_affiche, signal):
    """
    ✅ V9 NEW: construit et envoie le message pour UN signal (un pilier),
    puis ouvre son propre suivi ITM/OTM indépendant (trade_id unique).
    Peut être appelée plusieurs fois d'affilée si plusieurs piliers ont
    déclenché en même temps sur le même actif — chacun tourne alors en
    parallèle sans se marcher dessus.
    """
    maintenant = datetime.datetime.now()
    secondes_restantes = (60 - maintenant.second) + 60
    if (60 - maintenant.second) < 15: secondes_restantes += 60
    heure_entree_dt = maintenant + datetime.timedelta(seconds=secondes_restantes)

    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)
    titre_signal = "🔥 SIGNAL VALIDÉ DYNAMIQUE 🔥" if signal["score"] >= 80 else "⚡ SIGNAL VIP SÉCURISÉ ⚡"
    jauge_visuelle = generer_jauge(min(99, int(signal["score"])))
    raisons_txt = " · ".join(signal["raisons"][:3]) if signal["raisons"] else "Confluence validée"

    texte = f"""{titre_signal}
──────────────────
🛰 **ACTIF :** {nom_affiche}
🧩 **PILIER :** {signal['label']}
🎯 **ACTION :** {signal['action']}
⏳ **EXPIRATION :** {signal['expiration_texte']}
──────────────────
🧠 **CONFIANCE :** {jauge_visuelle}
🛡️ **FILTRE ANTI-PIÈGE :** VALIDÉ ✅

📊 **DÉTAIL DU PILIER :**
➤ {signal['details_txt']}
➤ {raisons_txt}
──────────────────
📍 **ORDRE À : {heure_entree_dt.strftime("%H:%M:00")}** 👈
💵 **MISE RECOMMANDÉE :** {mise_recommandee}$ (2%)
──────────────────
⚠️ *Préparez l'ordre sur Pocket Broker avec le temps d'expiration indiqué ci-dessus.*"""

    try:
        bot.send_message(chat_id, texte, parse_mode="Markdown")
    except:
        return

    trade_id = f"{actif}_{signal['pilier']}_{int(time.time()*1000)}"
    trades_en_cours.setdefault(chat_id, {})[trade_id] = {
        'symbole': actif, 'action': signal['action_simple'],
        'prix_entree': None, 'pilier': signal['pilier'],
    }
    Timer(secondes_restantes, relever_prix_entree, args=[chat_id, trade_id, actif]).start()
    Timer(secondes_restantes + signal['duree_secondes'], verifier_resultat, args=[chat_id, trade_id]).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    """
    ✅ V9: gère deux cas —
      • "set_{actif}" (menu manuel / bouton LANCER L'ANALYSE) : relance
        les 4 piliers à neuf et envoie UN message par pilier déclenché.
      • "set_{actif}|{pilier}" (bouton d'une alerte scanner) : revérifie
        EN TEMPS RÉEL ce pilier précis uniquement (le marché a pu bouger
        depuis l'alerte) et n'envoie le signal que s'il est encore valide.
    """
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return

    cle_brute = call.data.replace("set_", "", 1)
    if "|" in cle_brute:
        actif, pilier_demande = cle_brute.split("|", 1)
    else:
        actif, pilier_demande = cle_brute, None

    user_prefs[call.from_user.id] = actif
    nom_affiche = f"{actif[:3]}/{actif[3:]}"

    try:
        msg = bot.send_message(chat_id, "⏳ *Initialisation du scan DYNAMIQUE...*", parse_mode="Markdown")
        time.sleep(1)
        bot.edit_message_text("⚙️ *Lecture de l'Order Flow et calcul de la Volatilité ATR...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(1)
    except: return

    signaux = cerveau_binaire_4_piliers(actif)
    if pilier_demande:
        signaux = [s for s in signaux if s["pilier"] == pilier_demande]

    if not signaux:
        try:
            bot.edit_message_text(
                f"⚠️ Plus de configuration valable sur {nom_affiche} actuellement "
                f"(le marché a évolué depuis l'alerte, ou cooldown/news actif). "
                f"Relance une analyse.",
                chat_id, msg.message_id)
        except: pass
        return

    try: bot.delete_message(chat_id, msg.message_id)
    except: pass

    for signal in signaux:
        envoyer_signal_et_tracker(chat_id, actif, nom_affiche, signal)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id): return
    commande = message.text.split()
    if len(commande) < 2: return bot.send_message(message.chat.id, "⚠️ Précise la devise. Exemple : `/vision EURUSD`", parse_mode="Markdown")
    symbole = commande[1].upper()
    try: msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X de {symbole}...*", parse_mode="Markdown")
    except: return
    
    candles = obtenir_donnees_deriv(symbole)
    if not candles: return bot.edit_message_text("⚠️ Impossible de scanner (manque de données).", message.chat.id, msg.message_id)
        
    try:
        df = pd.DataFrame([{'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        ema_200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator().iloc[-1]
        prix_actuel = df['close'].iloc[-1]
        
        position_bb = "🔴 Au Plafond (Touche la bande haute)" if prix_actuel >= bb_haute else "🟢 Au Plancher (Touche la bande basse)" if prix_actuel <= bb_basse else "⚪ Au Milieu (Zone neutre)"
        nom_affiche = f"{symbole[:3]}/{symbole[3:]}"
        
        rapport = f"""👁️ **VISION RAYONS X : {nom_affiche}** 👁️
──────────────────
💰 **Prix actuel :** `{prix_actuel:.5f}`
🛡️ **EMA 200 (Tendance) :** `{ema_200:.5f}`
📏 **Position Bollinger :** {position_bb}

📊 **Niveau RSI :** `{rsi:.2f}` *(Rappel: >60 = Surchauffe, <40 = Essoufflé)*
📉 **Niveau Stochastique :** `{stoch_k:.2f}` *(Rappel: >80 = Surachat, <20 = Survente)*
──────────────────"""
        rapport += "\n⚠️ *Le prix teste les limites, tiens-toi prêt !*" if position_bb != "⚪ Au Milieu (Zone neutre)" else "\n💤 *Le marché respire tranquillement.*"
        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
    except Exception as e: bot.edit_message_text(f"❌ Erreur : {e}", message.chat.id, msg.message_id)

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=gestion_horaires_et_bilan, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition GOD MODE — 4 Piliers Indépendants (V9) Démarrée.", flush=True)
    bot.infinity_polling()
