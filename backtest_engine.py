"""
BACKTEST ENGINE — pensé pour tourner À L'INTÉRIEUR du service Render déjà
en ligne (même process que le bot), pas en local. Deux façons de voir le
résultat :
  1. Onglet "Logs" de Render (chaque étape est imprimée avec print()).
  2. Un résumé final envoyé directement en message Telegram.

Réutilise les fonctions d'analyse déjà définies dans bot_v19_updated.py
(régime, stratégies, confluence) — donc si tu modifies un seuil ou une
stratégie là-bas, le backtest reflète automatiquement le changement.
"""

import json
import time
import datetime
import websocket
import pandas as pd

from bot_v19_updated import (
    CRYPTO_PAIRS, FOREX_PAIRS, prefixer_symbole,
    detecter_regime_marche, marche_choc_detecte,
    analyser_aroon_rsi, analyser_adx_stc, analyser_cci_macd, analyser_donchian_cci,
    strategie_trend_pullback, strategie_breakout_retest,
    strategie_momentum_expansion, strategie_range_reversion,
    moteur_confluence,
)

DERIV_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"


def est_symbole_autorise_epoch(symbole, epoch_utc):
    """Même logique horaire que le bot, appliquée à un epoch historique."""
    now = datetime.datetime.utcfromtimestamp(epoch_utc)
    jour = now.weekday()
    heure_dec = now.hour + now.minute / 60.0
    est_week_end = (jour == 4 and heure_dec >= 21.0) or jour == 5 or (jour == 6 and heure_dec < 21.0)
    if est_week_end:
        return symbole in CRYPTO_PAIRS
    if symbole in CRYPTO_PAIRS:
        return False
    if heure_dec >= 17.5:
        return False
    if 0.0 <= heure_dec < 8.0:
        return symbole in ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"]
    if 7.0 <= heure_dec < 12.0:
        paires = ["EURUSD", "EURJPY", "EURAUD", "EURCHF", "USDCHF", "CADCHF"]
        if heure_dec < 8.0:
            paires += ["AUDJPY", "CADJPY", "CHFJPY", "USDJPY", "AUDCAD"]
        return symbole in paires
    if 12.0 <= heure_dec < 17.5:
        return symbole in ["EURUSD", "USDCAD", "AUDUSD"]
    return False


def fetch_history(symbole_brut, granularity_sec, jours, max_par_appel=5000):
    symbole = prefixer_symbole(symbole_brut)
    fin = int(time.time())
    debut_cible = fin - jours * 86400
    toutes_bougies = []
    end = fin

    while end > debut_cible:
        essais, bougies = 0, None
        while essais < 3 and bougies is None:
            try:
                ws = websocket.WebSocket()
                ws.connect(DERIV_URL, timeout=10)
                req = {"ticks_history": symbole, "end": end, "count": max_par_appel,
                       "style": "candles", "granularity": granularity_sec}
                ws.send(json.dumps(req))
                res = json.loads(ws.recv())
                ws.close()
                if "candles" in res:
                    bougies = res["candles"]
            except Exception:
                essais += 1
                time.sleep(1)
        if not bougies:
            break
        toutes_bougies = bougies + toutes_bougies
        nouvel_end = bougies[0]["epoch"] - 1
        if nouvel_end >= end:
            break
        end = nouvel_end
        time.sleep(0.3)

    vus, resultat = set(), []
    for c in toutes_bougies:
        if c["epoch"] not in vus:
            vus.add(c["epoch"])
            resultat.append(c)
    resultat.sort(key=lambda c: c["epoch"])
    return [c for c in resultat if c["epoch"] >= debut_cible]


def _vers_df(candles):
    return pd.DataFrame([{
        "epoch": c["epoch"], "open": float(c["open"]), "high": float(c["high"]),
        "low": float(c["low"]), "close": float(c["close"]),
    } for c in candles])


def backtester_paire(symbole, jours, mode="STANDARD"):
    duree_option = 60 if mode == "SCALP" else 300
    print(f"[BACKTEST] {symbole}/{mode} — téléchargement historique...", flush=True)

    c15 = fetch_history(symbole, 900, jours + 3)
    c5 = fetch_history(symbole, 300, jours + 3)
    if len(c15) < 70 or len(c5) < 50:
        print(f"[BACKTEST] {symbole} — historique insuffisant, ignoré.", flush=True)
        return []

    df15, df5 = _vers_df(c15), _vers_df(c5)
    signaux, derniere_alerte = [], 0
    borne_min_epoch = time.time() - jours * 86400

    for i in range(60, len(df15) - 1):
        epoch_decision = int(df15["epoch"].iloc[i])
        if epoch_decision < borne_min_epoch:
            continue
        if not est_symbole_autorise_epoch(symbole, epoch_decision):
            continue
        if epoch_decision - derniere_alerte < 300:
            continue

        d15 = df15.iloc[: i + 1].reset_index(drop=True)
        d5 = df5[df5["epoch"] <= epoch_decision].reset_index(drop=True)
        if len(d5) < 30:
            continue
        if marche_choc_detecte(d5):
            continue

        regime = detecter_regime_marche(d15)
        if regime["regime"] == "CHAOTIC":
            continue

        candidats = []
        for f in (analyser_aroon_rsi, analyser_adx_stc, analyser_cci_macd, analyser_donchian_cci):
            r = f(d15)
            if r: candidats.append(r)
        if regime["regime"] == "TREND":
            for r in (strategie_trend_pullback(d15, d5, regime), strategie_momentum_expansion(d15, regime)):
                if r: candidats.append(r)
        elif regime["regime"] == "BREAKOUT":
            for r in (strategie_breakout_retest(d15, d5, regime), strategie_momentum_expansion(d15, regime)):
                if r: candidats.append(r)
        elif regime["regime"] == "RANGE":
            r = strategie_range_reversion(d15, regime)
            if r: candidats.append(r)

        if not candidats:
            continue
        setup = max(candidats, key=lambda c: c["score"])
        score, bande, _ = moteur_confluence(regime, setup)
        if bande not in ("POTENTIEL", "QUALIFIE"):
            continue

        futurs = df5[df5["epoch"] >= epoch_decision + duree_option]
        if futurs.empty:
            continue
        prix_entree = float(d5["close"].iloc[-1])
        prix_sortie = float(futurs["close"].iloc[0])
        gagne = (setup["direction"] == "CALL" and prix_sortie > prix_entree) or \
                (setup["direction"] == "PUT" and prix_sortie < prix_entree)

        derniere_alerte = epoch_decision
        dt = datetime.datetime.utcfromtimestamp(epoch_decision)
        signaux.append({
            "symbole": symbole, "date": dt.strftime("%Y-%m-%d"), "regime": regime["regime"],
            "strategie": setup["label"], "bande": bande, "gagne": gagne,
        })

    print(f"[BACKTEST] {symbole}/{mode} — {len(signaux)} signaux trouvés.", flush=True)
    return signaux


def _rapport_texte(tous_signaux, jours, limite_cible):
    if not tous_signaux:
        return ("❌ Aucun signal produit sur la période.\n"
                "Le pipeline est probablement trop strict (SEUIL_OBSERVATION/SEUIL_POTENTIEL "
                "trop hauts, ou trop peu de stratégies compatibles avec les régimes rencontrés).")

    df = pd.DataFrame(tous_signaux)
    par_jour = df.groupby("date").size()
    jours_couverts = par_jour.reindex(
        pd.date_range(df["date"].min(), df["date"].max()).strftime("%Y-%m-%d"), fill_value=0
    )

    total = len(df)
    wins = int(df["gagne"].sum())
    winrate = round(100 * wins / total, 1)
    payout = 0.80
    expectancy = round((winrate / 100 * payout - (1 - winrate / 100)) * 100, 2)
    seuil_equilibre = round(100 / (1 + payout), 2)
    moyenne = round(jours_couverts.mean(), 1)

    lignes = [
        "📊 RÉSULTAT DU BACKTEST",
        f"Période : {jours}j ({df['date'].min()} → {df['date'].max()})",
        f"Signaux totaux : {total}",
        f"Moyenne/jour : {moyenne} · Min : {int(jours_couverts.min())} · Max : {int(jours_couverts.max())}",
        f"Jours à 0 signal : {int((jours_couverts == 0).sum())}/{len(jours_couverts)}",
        f"Win rate estimé : {winrate}% (seuil équilibre {seuil_equilibre}% @ payout {int(payout*100)}%)",
        f"Expectancy/trade : {expectancy:+.1f}%",
        "",
        "Par bande :",
        df["bande"].value_counts().to_string(),
        "",
        "Par régime :",
        df["regime"].value_counts().to_string(),
        "",
        "Par paire :",
        df["symbole"].value_counts().to_string(),
    ]

    if moyenne < limite_cible * 0.5:
        lignes.append(f"\n💡 Bien SOUS ta cible de {limite_cible}/j → baisse SEUIL_OBSERVATION/SEUIL_POTENTIEL.")
    elif moyenne > limite_cible * 1.3:
        lignes.append(f"\n💡 Dépasse ta cible de {limite_cible}/j → remonte les seuils ou force le filtre QUALIFIÉ.")
    else:
        lignes.append(f"\n✅ Proche de ta cible de {limite_cible}/j.")

    return "\n".join(lignes)


def lancer_backtest_texte(pairs_str, jours, mode, limite_cible=15):
    """Point d'entrée appelé par la commande /backtest du bot. Imprime la
    progression (visible dans Render > Logs) et retourne le rapport final
    en texte (à envoyer sur Telegram)."""
    paires = (CRYPTO_PAIRS + FOREX_PAIRS) if pairs_str.upper() == "ALL" else [p.strip().upper() for p in pairs_str.split(",")]
    modes = ["STANDARD", "SCALP"] if mode.upper() == "BOTH" else [mode.upper()]

    print(f"[BACKTEST] Démarrage — paires={paires} jours={jours} modes={modes}", flush=True)
    tous_signaux = []
    for paire in paires:
        for m in modes:
            tous_signaux += backtester_paire(paire, jours, m)

    rapport = _rapport_texte(tous_signaux, jours, limite_cible)
    print("[BACKTEST] Terminé.\n" + rapport, flush=True)
    return rapport
