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

# ✅ Le nom du fichier principal du bot varie selon comment tu l'as déployé
# (main.py, bot.py, bot_v19_updated.py...). On essaie les noms courants
# dans l'ordre, pour éviter un "No module named ..." si le nom ne
# correspond pas exactement.
_ERREURS_IMPORT = []
_module_bot = None
for _nom_module in ("main", "bot_v19_updated", "bot", "app"):
    try:
        _module_bot = __import__(_nom_module)
        break
    except ImportError as _e:
        _ERREURS_IMPORT.append(f"{_nom_module}: {_e}")

if _module_bot is None:
    raise ImportError(
        "Impossible de trouver le fichier principal du bot pour importer ses fonctions "
        "d'analyse. Noms essayés : main.py, bot_v19_updated.py, bot.py, app.py. "
        "Si ton fichier a un autre nom, ajoute-le dans la liste _nom_module de backtest_engine.py.\n"
        + "\n".join(_ERREURS_IMPORT)
    )

CRYPTO_PAIRS = _module_bot.CRYPTO_PAIRS
FOREX_PAIRS = _module_bot.FOREX_PAIRS
prefixer_symbole = _module_bot.prefixer_symbole
detecter_regime_marche = _module_bot.detecter_regime_marche
marche_choc_detecte = _module_bot.marche_choc_detecte
analyser_aroon_rsi = _module_bot.analyser_aroon_rsi
analyser_adx_stc = _module_bot.analyser_adx_stc
analyser_cci_macd = _module_bot.analyser_cci_macd
analyser_donchian_cci = _module_bot.analyser_donchian_cci
strategie_trend_pullback = _module_bot.strategie_trend_pullback
strategie_breakout_retest = _module_bot.strategie_breakout_retest
strategie_momentum_expansion = _module_bot.strategie_momentum_expansion
strategie_range_reversion = _module_bot.strategie_range_reversion
moteur_confluence = _module_bot.moteur_confluence

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
    compteurs = {
        "bougies_m15": 0, "bougies_m5": 0,
        "barres_examinees": 0, "hors_session": 0, "cooldown_scanner": 0,
        "data_m5_insuffisante": 0, "choc_marche": 0, "regime_chaotic": 0,
        "aucun_candidat": 0, "bande_trop_basse": 0, "pas_de_cloture_future": 0,
        "signaux": 0,
    }
    print(f"[BACKTEST] {symbole}/{mode} — téléchargement historique...", flush=True)

    c15 = fetch_history(symbole, 900, jours + 3)
    c5 = fetch_history(symbole, 300, jours + 3)
    compteurs["bougies_m15"], compteurs["bougies_m5"] = len(c15), len(c5)
    if len(c15) < 70 or len(c5) < 50:
        print(f"[BACKTEST] {symbole}/{mode} — historique insuffisant (M15={len(c15)}, M5={len(c5)}), ignoré.", flush=True)
        return [], compteurs

    df15, df5 = _vers_df(c15), _vers_df(c5)
    signaux, derniere_alerte = [], 0
    borne_min_epoch = time.time() - jours * 86400

    for i in range(60, len(df15) - 1):
        epoch_decision = int(df15["epoch"].iloc[i])
        if epoch_decision < borne_min_epoch:
            continue
        compteurs["barres_examinees"] += 1

        if not est_symbole_autorise_epoch(symbole, epoch_decision):
            compteurs["hors_session"] += 1
            continue
        if epoch_decision - derniere_alerte < 300:
            compteurs["cooldown_scanner"] += 1
            continue

        d15 = df15.iloc[: i + 1].reset_index(drop=True)
        d5 = df5[df5["epoch"] <= epoch_decision].reset_index(drop=True)
        if len(d5) < 30:
            compteurs["data_m5_insuffisante"] += 1
            continue
        if marche_choc_detecte(d5):
            compteurs["choc_marche"] += 1
            continue

        regime = detecter_regime_marche(d15)
        if regime["regime"] == "CHAOTIC":
            compteurs["regime_chaotic"] += 1
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
            compteurs["aucun_candidat"] += 1
            continue
        setup = max(candidats, key=lambda c: c["score"])
        score, bande, _ = moteur_confluence(regime, setup)
        if bande not in ("POTENTIEL", "QUALIFIE"):
            compteurs["bande_trop_basse"] += 1
            continue

        futurs = df5[df5["epoch"] >= epoch_decision + duree_option]
        if futurs.empty:
            compteurs["pas_de_cloture_future"] += 1
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
        compteurs["signaux"] += 1

    print(f"[BACKTEST] {symbole}/{mode} — diagnostic : {compteurs}", flush=True)
    return signaux, compteurs


def _fusionner_compteurs(liste_compteurs):
    total = {}
    for c in liste_compteurs:
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
    return total


def _rapport_texte(tous_signaux, tous_compteurs, jours, limite_cible):
    diag = _fusionner_compteurs(tous_compteurs)
    lignes_diag = [
        "🔍 DIAGNOSTIC (où les bougies sont éliminées) :",
        f"Bougies M15/M5 téléchargées : {diag.get('bougies_m15', 0)}/{diag.get('bougies_m5', 0)}",
        f"Barres examinées (fenêtre demandée) : {diag.get('barres_examinees', 0)}",
        f"  ├─ rejetées hors session horaire : {diag.get('hors_session', 0)}",
        f"  ├─ rejetées cooldown scanner (300s) : {diag.get('cooldown_scanner', 0)}",
        f"  ├─ rejetées data M5 insuffisante : {diag.get('data_m5_insuffisante', 0)}",
        f"  ├─ rejetées choc de marché : {diag.get('choc_marche', 0)}",
        f"  ├─ rejetées régime CHAOTIC : {diag.get('regime_chaotic', 0)}",
        f"  ├─ rejetées aucun candidat (stratégies) : {diag.get('aucun_candidat', 0)}",
        f"  ├─ rejetées bande < POTENTIEL (confluence) : {diag.get('bande_trop_basse', 0)}",
        f"  └─ rejetées pas de clôture future : {diag.get('pas_de_cloture_future', 0)}",
        f"  ➜ SIGNAUX PRODUITS : {diag.get('signaux', 0)}",
    ]

    if not tous_signaux:
        if diag.get("bougies_m15", 0) == 0 and diag.get("bougies_m5", 0) == 0:
            explication = ("\n❌ 0 bougie téléchargée pour TOUTES les paires testées. Le problème n'est pas "
                            "les seuils — c'est le TÉLÉCHARGEMENT des données Deriv qui échoue "
                            "(symbole mal préfixé, app_id bloqué, ou le serveur Render n'arrive pas à "
                            "joindre wss://ws.derivws.com). Vérifie dans Render > Logs si tu vois des "
                            "erreurs de connexion WebSocket pendant le backtest.")
        elif diag.get("barres_examinees", 0) == 0:
            explication = ("\n❌ Aucune barre dans la fenêtre de temps demandée n'a passé le filtre horaire "
                            "(hors_session). Essaie avec plus de jours (--days 30) ou avec des cryptos "
                            "(BTCUSD, actives 7j/7).")
        elif diag.get("regime_chaotic", 0) == diag.get("barres_examinees", 0) - diag.get("hors_session", 0) - diag.get("cooldown_scanner", 0):
            explication = "\n❌ Quasi 100% des barres sont classées CHAOTIC — regarde le seuil chaos dans detecter_regime_marche."
        elif diag.get("aucun_candidat", 0) > 0:
            explication = ("\n❌ Le régime passe, mais aucune stratégie ne produit de candidat "
                            f"(SEUIL_MIN_STRATEGIE trop haut encore, ou bug dans une des fonctions "
                            f"analyser_.../strategie_...). Baisse SEUIL_MIN_STRATEGIE davantage (essaie 10).")
        elif diag.get("bande_trop_basse", 0) > 0:
            explication = "\n❌ Des candidats existent mais la bande de confluence reste sous POTENTIEL — baisse encore SEUIL_OBSERVATION."
        else:
            explication = "\n❌ Aucun signal, cause non identifiée par ce diagnostic — regarde les compteurs ci-dessus ligne par ligne."
        return "\n".join(lignes_diag) + explication

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

    lignes = lignes_diag + [
        "",
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
        lignes.append(f"\n💡 Bien SOUS ta cible de {limite_cible}/j → baisse SEUIL_OBSERVATION/SEUIL_MIN_STRATEGIE.")
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
    tous_signaux, tous_compteurs = [], []
    for paire in paires:
        for m in modes:
            signaux, compteurs = backtester_paire(paire, jours, m)
            tous_signaux += signaux
            tous_compteurs.append(compteurs)

    rapport = _rapport_texte(tous_signaux, tous_compteurs, jours, limite_cible)
    print("[BACKTEST] Terminé.\n" + rapport, flush=True)
    return rapport
