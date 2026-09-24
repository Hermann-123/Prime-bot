"""
backtest_v19.py — Backtest du pipeline Terminal Prime V19
===========================================================
Rejoue le pipeline RÉEL du bot (Market Regime -> 8 Stratégies gated ->
Confluence Engine) sur des données Deriv historiques, pour répondre
concrètement à : "combien de signaux par jour, et avec quel win rate ?"

⚠️ LIMITES ASSUMÉES (transparence) :
  • L'AI Validator (Groq) n'est PAS simulé ici — impossible de rejouer un
    appel API historique de façon fiable. Les chiffres obtenus sont donc
    le volume MAXIMUM que le pipeline déterministe peut produire ; en
    production, Groq peut encore rejeter une partie de ces signaux.
  • Le Risk Engine (pause/cooldown/limite journalière) n'est PAS simulé
    non plus — il dépend de l'historique de CHAQUE utilisateur, pas du
    marché. Ce backtest mesure la capacité du marché à produire des
    signaux, pas ce qu'un utilisateur donné recevrait après filtrage.
  • Chaque bougie historique est traitée comme définitivement close (pas
    de distinction "bougie en formation" comme sur un flux live) — c'est
    la convention correcte pour du backtest et légèrement plus précise
    que le direct.
  • Résolution du résultat : STANDARD (durée 300s) via la bougie M5 la
    plus proche après expiration ; SCALP (durée 60s) via la bougie M1 la
    plus proche. Le prix d'entrée est le close de la bougie M15 de décision.

Usage :
    pip install pandas ta websocket-client --break-system-packages
    python backtest_v19.py

Sortie : un rapport texte + un CSV détaillé (backtest_signaux_detail.csv)
de chaque signal simulé, pour analyse plus poussée si besoin.
"""

import json
import time
import datetime
import csv
import websocket
import pandas as pd
import numpy as np
import ta

# ==========================================
# CONFIGURATION DU BACKTEST
# ==========================================

PAIRES_A_TESTER = ["EURUSD", "USDJPY", "AUDUSD", "USDCAD", "EURJPY", "AUDJPY"]
MODES_A_TESTER = ["STANDARD", "SCALP"]   # STANDARD -> M15 signal, durée 300s | SCALP -> durée 60s
NB_BOUGIES_CIBLE = 5000                   # max demandé par requête Deriv (peut retourner moins)
PAYOUT_NET = 0.80                         # payout Pocket Option — ajustable
WARMUP_BARRES = 60                        # nombre de bougies M15 nécessaires avant le 1er signal possible

CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]

# ==========================================
# DATA ENGINE (identique en logique à bot_v19.py)
# ==========================================

def prefixer_symbole(symbole_brut):
    if symbole_brut in CRYPTO_PAIRS: return f"cry{symbole_brut}"
    return f"frx{symbole_brut}"

def obtenir_donnees_deriv(symbole_brut, granularite, count=NB_BOUGIES_CIBLE):
    symbole = prefixer_symbole(symbole_brut)
    for _ in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=8)
            req = {"ticks_history": symbole, "end": "latest", "count": count,
                   "style": "candles", "granularity": granularite}
            ws.send(json.dumps(req))
            history = json.loads(ws.recv())
            ws.close()
            if "error" not in history and "candles" in history:
                return history["candles"]
        except Exception:
            time.sleep(1)
            continue
    return None

def candles_vers_df(candles):
    df = pd.DataFrame([{
        "epoch": int(c["epoch"]), "open": float(c["open"]), "high": float(c["high"]),
        "low": float(c["low"]), "close": float(c["close"])
    } for c in candles])
    return df.sort_values("epoch").reset_index(drop=True)

# ==========================================
# INDICATEURS VECTORISÉS (mêmes formules que bot_v19.py)
# ==========================================

def calculer_aroon(df, period=9):
    high_idx = df["high"].rolling(period + 1).apply(lambda x: period - x.argmax(), raw=True)
    low_idx = df["low"].rolling(period + 1).apply(lambda x: period - x.argmin(), raw=True)
    return ((period - high_idx) / period) * 100, ((period - low_idx) / period) * 100

def calculer_stc(df, fast=14, slow=50, cycle=5, d1=3, d2=3):
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    low_macd, high_macd = macd.rolling(cycle).min(), macd.rolling(cycle).max()
    k1 = 100 * (macd - low_macd) / (high_macd - low_macd).replace(0, 1e-9)
    d1_line = k1.ewm(span=d1, adjust=False).mean()
    low_d, high_d = d1_line.rolling(cycle).min(), d1_line.rolling(cycle).max()
    k2 = 100 * (d1_line - low_d) / (high_d - low_d).replace(0, 1e-9)
    return k2.ewm(span=d2, adjust=False).mean().clip(0, 100)

def calculer_donchian(df, period=20):
    return df["high"].rolling(period).max(), df["low"].rolling(period).min()

def structure_score_series(df, lookback=20):
    """Version vectorisée (rolling) de evaluer_structure() du bot."""
    hd_pos = (df["high"].diff() > 0).astype(int)
    hd_neg = (df["high"].diff() < 0).astype(int)
    ld_pos = (df["low"].diff() > 0).astype(int)
    ld_neg = (df["low"].diff() < 0).astype(int)
    w = lookback - 1
    coherence_bull = (hd_pos.rolling(w).sum() + ld_pos.rolling(w).sum()) / (2 * w)
    coherence_bear = (hd_neg.rolling(w).sum() + ld_neg.rolling(w).sum()) / (2 * w)
    return pd.concat([coherence_bull, coherence_bear], axis=1).max(axis=1) * 100

def pattern_series(df):
    """Version vectorisée de detecter_pattern_bougie() — une bougie = un
    point de décision déjà clos (pas de bougie 'en formation')."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    po, pc = o.shift(1), c.shift(1)
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l

    pin_bull = (lower_wick > body * 1.8) & (upper_wick < body)
    pin_bear = (upper_wick > body * 1.8) & (lower_wick < body)
    engulf_bull = (pc < po) & (c > o) & (c > po) & (o < pc)
    engulf_bear = (pc > po) & (c < o) & (c < po) & (o > pc)
    marubozu_bull = (body > rng * 0.75) & (c > o)
    marubozu_bear = (body > rng * 0.75) & (c < o)

    pat = pd.Series("NONE", index=df.index)
    pat[marubozu_bull.fillna(False)] = "MARUBOZU_BULL"
    pat[marubozu_bear.fillna(False)] = "MARUBOZU_BEAR"
    pat[engulf_bear.fillna(False)] = "ENGULFING_BEAR"
    pat[engulf_bull.fillna(False)] = "ENGULFING_BULL"
    pat[pin_bear.fillna(False)] = "PIN_BEAR"
    pat[pin_bull.fillna(False)] = "PIN_BULL"
    return pat

PATTERNS_BULL = ("PIN_BULL", "ENGULFING_BULL", "MARUBOZU_BULL")
PATTERNS_BEAR = ("PIN_BEAR", "ENGULFING_BEAR", "MARUBOZU_BEAR")

# ==========================================
# PRÉCALCUL COMPLET SUR M15 (une seule passe, vectorisée)
# ==========================================

def precalculer_indicateurs_m15(df15):
    ind = {}
    ind["aroon_up"], ind["aroon_down"] = calculer_aroon(df15, 9)
    ind["rsi6"] = ta.momentum.RSIIndicator(df15["close"], window=6).rsi()
    ind["rsi14"] = ta.momentum.RSIIndicator(df15["close"], window=14).rsi()

    adx_ind = ta.trend.ADXIndicator(df15["high"], df15["low"], df15["close"], window=14)
    ind["adx"] = adx_ind.adx()
    ind["di_pos"] = adx_ind.adx_pos()
    ind["di_neg"] = adx_ind.adx_neg()

    ind["stc"] = calculer_stc(df15)
    ind["cci10"] = ta.trend.CCIIndicator(df15["high"], df15["low"], df15["close"], window=10).cci()
    ind["cci11"] = ta.trend.CCIIndicator(df15["high"], df15["low"], df15["close"], window=11).cci()
    ind["cci14"] = ta.trend.CCIIndicator(df15["high"], df15["low"], df15["close"], window=14).cci()
    ind["macd_hist"] = ta.trend.MACD(df15["close"], window_slow=25, window_fast=10, window_sign=5).macd_diff()

    ind["upper20"], ind["lower20"] = calculer_donchian(df15, 20)
    ind["ema20"] = df15["close"].ewm(span=20, adjust=False).mean()
    ind["ema50"] = df15["close"].ewm(span=50, adjust=False).mean()

    atr = ta.volatility.AverageTrueRange(df15["high"], df15["low"], df15["close"], window=14).average_true_range()
    ind["atr"] = atr
    ind["atr_moy"] = atr.rolling(30).mean()
    ind["atr_pct"] = (atr / ind["atr_moy"]).fillna(1.0)

    ind["roc10"] = ta.momentum.ROCIndicator(df15["close"], window=10).roc()
    ind["structure"] = structure_score_series(df15, 20)

    corps = (df15["close"] - df15["open"]).abs()
    taille = (df15["high"] - df15["low"]).replace(0, 1e-9)
    ratio_corps = corps / taille
    ind["ratio_corps_recent"] = ratio_corps.rolling(3).mean().shift(1)

    ind["largeur_canal_pct"] = ((ind["upper20"] - ind["lower20"]) / df15["close"]).fillna(0)
    return ind

def classer_regime(ind, i):
    adx_val = ind["adx"].iat[i]
    atr_pct = ind["atr_pct"].iat[i]
    structure = ind["structure"].iat[i]
    largeur = ind["largeur_canal_pct"].iat[i]
    ratio_recent = ind["ratio_corps_recent"].iat[i]

    if pd.isna(adx_val) or pd.isna(atr_pct) or pd.isna(structure):
        return None

    chaos = (atr_pct > 2.2) or (pd.notna(ratio_recent) and ratio_recent < 0.15)
    if i >= 3:
        px = ind["_close"].iat[i]
        proche_haut = px >= ind["upper20"].iat[i - 3] * 0.999 if pd.notna(ind["upper20"].iat[i - 3]) else False
        proche_bas = px <= ind["lower20"].iat[i - 3] * 1.001 if pd.notna(ind["lower20"].iat[i - 3]) else False
    else:
        proche_haut = proche_bas = False
    expansion = atr_pct > 1.3

    if chaos:
        regime = "CHAOTIC"
    elif (proche_haut or proche_bas) and expansion:
        regime = "BREAKOUT"
    elif adx_val >= 22 and structure >= 55:
        regime = "TREND"
    elif adx_val < 18 and largeur < 0.010:
        regime = "RANGE"
    else:
        regime = "TREND" if adx_val >= 20 else "RANGE"

    direction_biais = "BULL" if ind["ema20"].iat[i] > ind["ema50"].iat[i] else "BEAR"
    return {"regime": regime, "direction_biais": direction_biais, "adx": round(float(adx_val), 1),
            "atr_pct": round(float(atr_pct), 2), "structure_score": round(float(structure), 1),
            "largeur_canal_pct": round(float(largeur) * 100, 3), "chaos": bool(chaos)}

# ==========================================
# LES 8 STRATÉGIES (formules identiques à bot_v19.py, lues sur indices précalculés)
# ==========================================

def eval_aroon_rsi(ind, i):
    if i < 3: return None
    au, ad = ind["aroon_up"].iat[i], ind["aroon_down"].iat[i]
    au_p, ad_p = ind["aroon_up"].iat[i - 1], ind["aroon_down"].iat[i - 1]
    rsi_val = ind["rsi6"].iat[i]
    if pd.isna(au) or pd.isna(ad) or pd.isna(rsi_val): return None

    sc = min(35, max(0, (au - ad) * 0.5))
    if au_p <= ad_p and au > ad: sc += 20
    if 40 <= rsi_val <= 68: sc += 20
    if au >= 70: sc += 15

    sp = min(35, max(0, (ad - au) * 0.5))
    if ad_p <= au_p and ad > au: sp += 20
    if 32 <= rsi_val <= 60: sp += 20
    if ad >= 70: sp += 15

    direction = "CALL" if sc >= sp else "PUT"
    meilleur = max(sc, sp)
    if meilleur < 45: return None
    return {"nom": "AROON_RSI", "label": "Show The Direction", "direction": direction, "score": round(meilleur, 1)}

def eval_adx_stc(ind, i):
    if i < 3: return None
    adx_val, dip, din = ind["adx"].iat[i], ind["di_pos"].iat[i], ind["di_neg"].iat[i]
    stc_val, stc_prev = ind["stc"].iat[i], ind["stc"].iat[i - 1]
    if pd.isna(adx_val) or pd.isna(stc_val) or pd.isna(stc_prev): return None

    sc = 0.0
    if stc_prev <= 25 and stc_val > stc_prev: sc += 35
    elif stc_val < 40: sc += 15
    if dip > din: sc += 20
    if adx_val >= 15: sc += min(20, (adx_val - 15) * 1.2)

    sp = 0.0
    if stc_prev >= 75 and stc_val < stc_prev: sp += 35
    elif stc_val > 60: sp += 15
    if din > dip: sp += 20
    if adx_val >= 15: sp += min(20, (adx_val - 15) * 1.2)

    direction = "CALL" if sc >= sp else "PUT"
    meilleur = max(sc, sp)
    if meilleur < 45: return None
    return {"nom": "ADX_STC", "label": "Identifies Reversal Points", "direction": direction, "score": round(meilleur, 1)}

def eval_cci_macd(ind, i):
    if i < 3: return None
    cci_val, cci_prev = ind["cci10"].iat[i], ind["cci10"].iat[i - 1]
    hist_val, hist_prev = ind["macd_hist"].iat[i], ind["macd_hist"].iat[i - 1]
    if pd.isna(cci_val) or pd.isna(hist_val): return None

    sc = 0.0
    if cci_prev <= -100 and cci_val > cci_prev: sc += 30
    elif cci_val < -50: sc += 12
    if hist_val > 0: sc += 20
    if hist_val > hist_prev: sc += 15

    sp = 0.0
    if cci_prev >= 100 and cci_val < cci_prev: sp += 30
    elif cci_val > 50: sp += 12
    if hist_val < 0: sp += 20
    if hist_val < hist_prev: sp += 15

    direction = "CALL" if sc >= sp else "PUT"
    meilleur = max(sc, sp)
    if meilleur < 45: return None
    return {"nom": "CCI_MACD", "label": "A Moment When...", "direction": direction, "score": round(meilleur, 1)}

def eval_donchian_cci(ind, i):
    px = ind["_close"].iat[i]
    up_val, low_val = ind["upper20"].iat[i], ind["lower20"].iat[i]
    if pd.isna(up_val) or pd.isna(low_val) or px is None: return None
    largeur = up_val - low_val if (up_val - low_val) > 0 else 1e-9
    position_pct = (px - low_val) / largeur
    cci_val, cci_prev = ind["cci11"].iat[i], ind["cci11"].iat[i - 1] if i >= 1 else np.nan
    if pd.isna(cci_val): return None

    sc = max(0, 1 - position_pct * 2.5) * 35
    if cci_prev is not np.nan and cci_prev <= -100 and cci_val > cci_prev: sc += 30
    elif cci_val < -30: sc += 12

    sp = max(0, (position_pct - 0.6) * 2.5) * 35
    if cci_prev is not np.nan and cci_prev >= 100 and cci_val < cci_prev: sp += 30
    elif cci_val > 30: sp += 12

    direction = "CALL" if sc >= sp else "PUT"
    meilleur = max(sc, sp)
    if meilleur < 45: return None
    return {"nom": "DONCHIAN_CCI", "label": "You Know And...", "direction": direction, "score": round(meilleur, 1)}

def eval_trend_pullback(ind, i, pat_entree):
    if ind["regime_lbl"][i] != "TREND": return None
    px = ind["_close"].iat[i]
    ema20_val = ind["ema20"].iat[i]
    if pd.isna(ema20_val) or px is None: return None
    direction = "CALL" if ind["ema20"].iat[i] > ind["ema50"].iat[i] else "PUT"
    dist_ema20_pct = abs(px - ema20_val) / px if px else 1
    proche = dist_ema20_pct < 0.005
    rsi_val = ind["rsi14"].iat[i]
    rsi_ok = pd.notna(rsi_val) and 38 <= rsi_val <= 62
    pattern = pat_entree[i]
    confirmation = (direction == "CALL" and pattern in PATTERNS_BULL) or (direction == "PUT" and pattern in PATTERNS_BEAR)

    score = 0.0
    if proche: score += 35
    if rsi_ok: score += 25
    if ind["adx"].iat[i] >= 22: score += 20
    if confirmation: score += 20
    if score < 45: return None
    return {"nom": "TREND_PULLBACK", "label": "Trend Pullback", "direction": direction, "score": round(score, 1)}

def eval_breakout_retest(ind, i, pat_entree):
    if ind["regime_lbl"][i] not in ("BREAKOUT", "TREND"): return None
    if i < 6: return None
    px = ind["_close"].iat[i]
    cassure_haute = ind["_close"].iat[i - 5] > ind["upper20"].iat[i - 6] if pd.notna(ind["upper20"].iat[i - 6]) else False
    cassure_basse = ind["_close"].iat[i - 5] < ind["lower20"].iat[i - 6] if pd.notna(ind["lower20"].iat[i - 6]) else False

    if cassure_haute:
        direction, niveau = "CALL", ind["upper20"].iat[i - 6]
    elif cassure_basse:
        direction, niveau = "PUT", ind["lower20"].iat[i - 6]
    else:
        return None

    dist_retest = abs(px - niveau) / px if px else 1
    retest_ok = dist_retest < 0.004
    pattern = pat_entree[i]
    confirmation = (direction == "CALL" and pattern in PATTERNS_BULL) or (direction == "PUT" and pattern in PATTERNS_BEAR)

    score = 0.0
    if retest_ok: score += 40
    if confirmation: score += 30
    if ind["atr_pct"].iat[i] > 1.1: score += 15
    if ind["structure"].iat[i] >= 55: score += 15
    if score < 45: return None
    return {"nom": "BREAKOUT_RETEST", "label": "Breakout + Retest", "direction": direction, "score": round(score, 1)}

def eval_momentum_expansion(ind, i):
    if ind["regime_lbl"][i] not in ("TREND", "BREAKOUT"): return None
    if i < 5: return None
    roc_val, roc_prev = ind["roc10"].iat[i], ind["roc10"].iat[i - 3]
    if pd.isna(roc_val) or pd.isna(roc_prev): return None
    direction = "CALL" if ind["ema20"].iat[i] > ind["ema50"].iat[i] else "PUT"
    expansion = ind["atr_pct"].iat[i] > 1.3
    momentum_accel = (direction == "CALL" and roc_val > roc_prev and roc_val > 0) or \
                      (direction == "PUT" and roc_val < roc_prev and roc_val < 0)

    score = 0.0
    if expansion: score += 30
    if momentum_accel: score += 35
    if ind["adx"].iat[i] >= 22: score += 20
    if ind["structure"].iat[i] >= 55: score += 15
    if score < 45: return None
    return {"nom": "MOMENTUM_EXPANSION", "label": "Momentum Expansion", "direction": direction, "score": round(score, 1)}

def eval_range_reversion(ind, i):
    if ind["regime_lbl"][i] != "RANGE": return None
    px = ind["_close"].iat[i]
    up_val, low_val = ind["upper20"].iat[i], ind["lower20"].iat[i]
    if pd.isna(up_val) or pd.isna(low_val): return None
    largeur = up_val - low_val if (up_val - low_val) > 0 else 1e-9
    position_pct = (px - low_val) / largeur
    cci_val = ind["cci14"].iat[i]
    if pd.isna(cci_val): return None

    if position_pct < 0.15 and cci_val < -100:
        direction = "CALL"
    elif position_pct > 0.85 and cci_val > 100:
        direction = "PUT"
    else:
        return None

    proximite = (1 - position_pct) if direction == "CALL" else position_pct
    score = proximite * 40
    if abs(cci_val) >= 100: score += 30
    if ind["adx"].iat[i] < 18: score += 20
    if ind["largeur_canal_pct"].iat[i] < 1.2: score += 10
    if score < 45: return None
    return {"nom": "RANGE_REVERSION", "label": "Range Reversion", "direction": direction, "score": round(score, 1)}

# ==========================================
# CONFLUENCE ENGINE (barème identique à bot_v19.py)
# ==========================================

BAREME = {"regime_compatible": 25, "structure_max": 20, "momentum": 20, "volatilite": 15, "setup_max": 15, "contexte_defavorable": -30}
SEUIL_NO_TRADE, SEUIL_OBSERVATION, SEUIL_POTENTIEL = 55, 70, 80

def moteur_confluence(regime, setup):
    score = BAREME["regime_compatible"]
    score += min(BAREME["structure_max"], regime["structure_score"] * (BAREME["structure_max"] / 100))
    if setup["score"] >= 55: score += BAREME["momentum"]
    if 0.5 <= regime["atr_pct"] <= 2.0: score += BAREME["volatilite"]
    score += min(BAREME["setup_max"], setup["score"] * (BAREME["setup_max"] / 100))
    if regime["chaos"]: score += BAREME["contexte_defavorable"]
    score = max(0, min(100, round(score, 1)))
    if score < SEUIL_NO_TRADE: bande = "NO_TRADE"
    elif score < SEUIL_OBSERVATION: bande = "OBSERVATION"
    elif score < SEUIL_POTENTIEL: bande = "POTENTIEL"
    else: bande = "QUALIFIE"
    return score, bande

# ==========================================
# RÉSOLUTION DU RÉSULTAT (recherche du prix futur par epoch)
# ==========================================

def resoudre_prix_futur(df_entree_epoch, df_entree_close, epoch_cible):
    idx = np.searchsorted(df_entree_epoch, epoch_cible, side="left")
    if idx >= len(df_entree_epoch): return None
    return float(df_entree_close[idx])

# ==========================================
# BOUCLE DE BACKTEST PRINCIPALE
# ==========================================

def backtester_paire(symbole, mode, resultats):
    duree_secondes = 300 if mode == "STANDARD" else 60
    granularite_entree = 300 if mode == "STANDARD" else 60

    c15 = obtenir_donnees_deriv(symbole, 900)
    c_entree = obtenir_donnees_deriv(symbole, granularite_entree)
    if not c15 or len(c15) < WARMUP_BARRES + 10 or not c_entree or len(c_entree) < 20:
        print(f"  ⚠️ {symbole}/{mode} : données insuffisantes, ignoré.")
        return

    df15 = candles_vers_df(c15)
    df_entree = candles_vers_df(c_entree)

    ind = precalculer_indicateurs_m15(df15)
    ind["_close"] = df15["close"]

    pat_e = pattern_series(df_entree)
    epoch_entree = df_entree["epoch"].values
    close_entree = df_entree["close"].values

    # index M15 -> pattern de la bougie d'entrée la plus proche (<=) en temps
    idx_e = np.searchsorted(epoch_entree, df15["epoch"].values, side="right") - 1
    pat_entree_aligne = [pat_e.iat[j] if 0 <= j < len(pat_e) else "NONE" for j in idx_e]

    regime_lbl = [None] * len(df15)
    for i in range(len(df15)):
        r = classer_regime(ind, i)
        regime_lbl[i] = r["regime"] if r else None
    ind["regime_lbl"] = regime_lbl

    n_jours = (df15["epoch"].iloc[-1] - df15["epoch"].iloc[0]) / 86400 if len(df15) > 1 else 0
    n_signaux, n_no_trade_chaos, n_no_trade_score, n_observation = 0, 0, 0, 0

    for i in range(WARMUP_BARRES, len(df15) - 1):
        regime = classer_regime(ind, i)
        if not regime: continue
        if regime["regime"] == "CHAOTIC":
            n_no_trade_chaos += 1
            continue

        candidats = []
        for fn in (eval_aroon_rsi, eval_adx_stc, eval_cci_macd, eval_donchian_cci):
            r = fn(ind, i)
            if r: candidats.append(r)

        if regime["regime"] == "TREND":
            for r in (eval_trend_pullback(ind, i, pat_entree_aligne), eval_momentum_expansion(ind, i)):
                if r: candidats.append(r)
        elif regime["regime"] == "BREAKOUT":
            for r in (eval_breakout_retest(ind, i, pat_entree_aligne), eval_momentum_expansion(ind, i)):
                if r: candidats.append(r)
        elif regime["regime"] == "RANGE":
            r = eval_range_reversion(ind, i)
            if r: candidats.append(r)

        if not candidats: continue

        setup = max(candidats, key=lambda c: c["score"])
        score_confluence, bande = moteur_confluence(regime, setup)

        if bande == "NO_TRADE": n_no_trade_score += 1; continue
        if bande == "OBSERVATION": n_observation += 1; continue

        epoch_signal = int(df15["epoch"].iat[i])
        prix_entree = float(df15["close"].iat[i])
        epoch_cible = epoch_signal + duree_secondes
        prix_sortie = resoudre_prix_futur(epoch_entree, close_entree, epoch_cible)
        if prix_sortie is None: continue

        gagne = (setup["direction"] == "CALL" and prix_sortie > prix_entree) or \
                (setup["direction"] == "PUT" and prix_sortie < prix_entree)

        n_signaux += 1
        resultats.append({
            "paire": symbole, "mode": mode, "date": datetime.datetime.utcfromtimestamp(epoch_signal).strftime("%Y-%m-%d %H:%M"),
            "regime": regime["regime"], "strategie": setup["label"], "bande": bande,
            "score_confluence": score_confluence, "direction": setup["direction"], "gagne": gagne,
        })

    print(f"  {symbole}/{mode} : {n_signaux} signaux sur ~{n_jours:.1f} jours "
          f"(chaos={n_no_trade_chaos}, score_insuffisant={n_no_trade_score}, observation={n_observation})")

# ==========================================
# RAPPORT
# ==========================================

def generer_rapport(resultats):
    if not resultats:
        print("\n❌ Aucun signal généré — vérifie la connexion Deriv ou élargis PAIRES_A_TESTER.")
        return

    df = pd.DataFrame(resultats)
    df.to_csv("backtest_signaux_detail.csv", index=False)

    jours_couverts = {}
    for paire in df["paire"].unique():
        sous = df[df["paire"] == paire]
        jours_couverts[paire] = (pd.to_datetime(sous["date"]).max() - pd.to_datetime(sous["date"]).min()).days + 1

    total_jours_moyen = np.mean(list(jours_couverts.values())) if jours_couverts else 1

    print("\n" + "=" * 60)
    print("RAPPORT DE BACKTEST — TERMINAL PRIME V19")
    print("=" * 60)
    print(f"\nTotal signaux simulés : {len(df)}")
    print(f"Signaux/jour en moyenne (toutes paires confondues) : {len(df) / max(total_jours_moyen,1):.2f}")
    print(f"Signaux/jour/paire en moyenne : {len(df) / len(PAIRES_A_TESTER) / max(total_jours_moyen,1):.2f}")

    print("\n--- Par bande ---")
    for bande, g in df.groupby("bande"):
        wr = g["gagne"].mean() * 100
        print(f"  {bande:10s} : {len(g):4d} signaux · win rate {wr:.1f}%")

    print("\n--- Par stratégie ---")
    for strat, g in df.groupby("strategie"):
        wr = g["gagne"].mean() * 100
        print(f"  {strat:26s} : {len(g):4d} signaux · win rate {wr:.1f}%")

    print("\n--- Par régime ---")
    for regime, g in df.groupby("regime"):
        wr = g["gagne"].mean() * 100
        print(f"  {regime:10s} : {len(g):4d} signaux · win rate {wr:.1f}%")

    print("\n--- Par paire ---")
    for paire, g in df.groupby("paire"):
        wr = g["gagne"].mean() * 100
        print(f"  {paire:8s} : {len(g):4d} signaux · win rate {wr:.1f}%")

    winrate_global = df["gagne"].mean()
    expectancy = (winrate_global * PAYOUT_NET) - ((1 - winrate_global) * 1.0)
    seuil_equilibre = 1 / (1 + PAYOUT_NET)
    print(f"\n--- Rentabilité globale (payout {int(PAYOUT_NET*100)}%) ---")
    print(f"  Win rate global      : {winrate_global*100:.1f}%")
    print(f"  Seuil de rentabilité : {seuil_equilibre*100:.1f}%")
    print(f"  Expectancy/trade     : {expectancy*100:+.1f}%")
    print(f"  {'🟢 Au-dessus du seuil' if winrate_global > seuil_equilibre else '🔴 En dessous du seuil'} de rentabilité théorique")

    print(f"\n📄 Détail complet exporté dans backtest_signaux_detail.csv")
    print("=" * 60)

# ==========================================
# LANCEMENT
# ==========================================

if __name__ == "__main__":
    resultats = []
    print(f"Backtest V19 — {len(PAIRES_A_TESTER)} paires × {len(MODES_A_TESTER)} modes\n")
    for symbole in PAIRES_A_TESTER:
        for mode in MODES_A_TESTER:
            backtester_paire(symbole, mode, resultats)
    generer_rapport(resultats)
