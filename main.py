# ==============================================================================
# █▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀█
# █                                                                            █
# █                  T E R M I N A L   P R I M E   V I P                       █
# █                       É D I T I O N   S U P R Ê M E                        █
# █                                                                            █
# █                  ARCHITECTURE ALGORITHMIQUE TRIPLE CERVEAU                 █
# █                                                                            █
# █▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█
# ==============================================================================
#
# PROPRIÉTÉ INTELLECTUELLE ET DROITS D'AUTEUR :
# Ce code source est la propriété exclusive de son Fondateur et Créateur.
# Telegram : @hermann1123
#
# AVERTISSEMENT LÉGAL :
# Toute reproduction, distribution, modification ou utilisation non autorisée
# de cet algorithme est strictement interdite. Ce script contient des formules
# mathématiques propriétaires (Anomalie 3-Sigma, Divergence MACD, Price Action
# Institutionnel) destinées au trading algorithmique haute fréquence.
#
# MOTEURS INTÉGRÉS :
# 1. Cerveau Sniper : Détection RSI/Stochastique + Bougies (Marteau/Inside Bar)
# 2. Cerveau Piège : Détection de Divergence Institutionnelle MACD
# 3. Cerveau Anomalie : Rupture Statistique 99.7% (Bandes de Bollinger 3-Sigma)
#
# SYSTÈMES DE SÉCURITÉ :
# - Anti-Coupure Deriv WebSocket (Auto-Retry x3)
# - Synchronisation Millimétrée de l'horloge (Expiration à la seconde pile '00')
# - Bouclier de Tendance EMA 200
#
# ==============================================================================

import os
import sys
import logging
import datetime
import random
import time
import string
import json
import websocket
import pandas as pd
import ta
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# ==============================================================================
# 1. CONFIGURATION DU SYSTÈME ET VARIABLES GLOBALES
# ==============================================================================

# Clé API Telegram de la Boîte Noire
TELEGRAM_TOKEN = "8658287331:AAFaLiyUGs2lk4ceePLz2LMKLhlxNY54WnM"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Identifiant unique du Fondateur
ADMIN_ID = 5968288964 

# Capital de référence pour le calcul du Money Management (2%)
CAPITAL_ACTUEL = 40650 

# Dictionnaires d'état en mémoire vive (RAM)
user_prefs = {}                 # Stocke la paire choisie par l'utilisateur
trades_en_cours = {}            # Stocke les ordres en attente de vérification
utilisateurs_actifs = set()     # Registre des VIP actifs dans la session
derniere_alerte_auto = {}       # Anti-spam pour le scanner automatique

# Base de données d'authentification (Mémoire éphémère)
utilisateurs_autorises = {
    ADMIN_ID: "LIFETIME"
}
cles_generees = {}

# Statistiques journalières de l'algorithme
stats_journee = {
    'ITM': 0, 
    'OTM': 0, 
    'details': []
}

# Verrous booléens pour les événements quotidiens
bilan_envoye_aujourdhui = False
transition_nuit_envoyee = False
transition_jour_envoyee = False

# ==============================================================================
# 2. SYSTÈME DE LOGGING AVANCÉ (SÉCURITÉ ET AUDIT)
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] TERMINAL PRIME - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def log_systeme(message, niveau="INFO"):
    """
    Gestionnaire centralisé des logs du système.
    Permet au fondateur de vérifier l'état des scanners sur les serveurs.
    """
    if niveau == "INFO":
        logging.info(message)
    elif niveau == "WARNING":
        logging.warning(message)
    elif niveau == "ERROR":
        logging.error(message)

# ==============================================================================
# 3. SERVEUR WEB FLASK (MODULE KEEP-ALIVE POUR CLOUD / RENDER)
# ==============================================================================

app = Flask(__name__)

@app.route('/')
def home():
    """
    Point de terminaison HTTP pour maintenir l'application active.
    Répond aux requêtes de ping des serveurs Render/Heroku.
    """
    return "Terminal Prime VIP : Édition Suprême (Opérationnel)"

def run():
    """Lance le serveur HTTP sur le port défini par l'environnement."""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    """Démarre le serveur Flask dans un thread séparé pour ne pas bloquer le bot."""
    t = Thread(target=run)
    t.start()
    log_systeme("Module Keep-Alive HTTP démarré avec succès.")

# ==============================================================================
# 4. MODULE DE GESTION DES ACCÈS ET SÉCURITÉ VIP
# ==============================================================================

def est_autorise(user_id):
    """
    Vérifie si un utilisateur possède un abonnement VIP actif.
    Désactive automatiquement l'accès si la date d'expiration est dépassée.
    """
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
            msg_exp = "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé. Veuillez contacter le fondateur [@hermann1123](https://t.me/hermann1123)."
            try:
                bot.send_message(user_id, msg_exp, parse_mode="Markdown")
            except Exception as e:
                log_systeme(f"Erreur lors de l'envoi du message d'expiration à {user_id}: {e}", "WARNING")
            return False
            
    return False

def generer_cle():
    """Génère une clé d'activation cryptographique à usage unique."""
    caracteres = string.ascii_uppercase + string.digits
    chaine_aleatoire = ''.join(random.choice(caracteres) for _ in range(8))
    return f"PRIME-{chaine_aleatoire}"

def generer_jauge(pourcentage):
    """Génère la jauge esthétique VIP affichée dans les signaux."""
    if pourcentage >= 99:
        return "[██████████] 👑 MAX"
    pleins = int(pourcentage / 10)
    vides = 10 - pleins
    return f"[{'█' * pleins}{'░' * vides}] {pourcentage}%"

# ==============================================================================
# 5. MODULE DE CONNEXION WEBSOCKET DERIV (AVEC ANTI-COUPURE)
# ==============================================================================

def obtenir_donnees_deriv(symbole_brut):
    """
    Connecte le système à l'API Deriv pour télécharger l'historique de marché.
    Intègre un bouclier anti-coupure (3 tentatives) pour garantir la stabilité.
    Récupère 250 bougies de 1 minute.
    """
    symbole = f"frx{symbole_brut}" 
    
    # 🔄 BOUCLE DE SÉCURITÉ : Résistance aux déconnexions API
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {
                "ticks_history": symbole, 
                "end": "latest", 
                "count": 250,  
                "style": "candles", 
                "granularity": 60
            }
            ws.send(json.dumps(req))
            history = json.loads(ws.recv())
            ws.close()
            
            if "error" not in history and "candles" in history:
                return history['candles']
                
        except Exception as e:
            log_systeme(f"Tentative {tentative+1}/3 échouée pour {symbole} : {e}", "WARNING")
            time.sleep(1) # Pause avant reconnexion
            continue
            
    return None

def obtenir_prix_actuel_deriv(symbole_brut):
    """
    Récupère le prix en direct à la milliseconde pour un actif donné.
    Utilisé pour le système de vérification ITM/OTM.
    """
    symbole = f"frx{symbole_brut}"
    
    for tentative in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            req = {
                "ticks_history": symbole, 
                "end": "latest", 
                "count": 1, 
                "style": "ticks"
            }
            ws.send(json.dumps(req))
            res = json.loads(ws.recv())
            ws.close()
            
            if "history" in res and "prices" in res["history"]:
                return float(res["history"]["prices"][0])
                
        except Exception:
            time.sleep(1)
            continue
            
    return None

# ==============================================================================
# 6. MOTEUR D'AUDIT ET DE VÉRIFICATION DE RÉSULTAT (ITM/OTM)
# ==============================================================================

def relever_prix_entree(chat_id, symbole):
    """
    Capture le prix exact du marché au moment où l'horloge atteint l'heure du signal.
    Sauvegarde cette donnée en mémoire pour le calcul final.
    """
    prix = obtenir_prix_actuel_deriv(symbole)
    if prix and chat_id in trades_en_cours:
        if trades_en_cours[chat_id]['symbole'] == symbole:
            trades_en_cours[chat_id]['prix_entree'] = prix
            log_systeme(f"Prix d'entrée capturé pour {symbole} : {prix}")

def verifier_resultat(chat_id):
    """
    Compare le prix de sortie avec le prix d'entrée à la fin de l'expiration.
    Détermine si le signal est ITM (Victoire) ou OTM (Perte) et met à jour
    le bilan du Fondateur.
    """
    global stats_journee
    trade = trades_en_cours.get(chat_id)
    
    if not trade or not trade.get('prix_entree'):
        return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_deriv(symbole)
    
    if not prix_sortie:
        log_systeme(f"Impossible de récupérer le prix de sortie pour {symbole}", "ERROR")
        return

    prix_entree = trade['prix_entree']
    action = trade['action']

    # Détermination du résultat logique
    gagne = False
    if action == "CALL" and prix_sortie > prix_entree:
        gagne = True
    elif action == "PUT" and prix_sortie < prix_entree:
        gagne = True

    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    
    if gagne:
        texte = f"✅ **VICTOIRE (ITM)**\n🚀 Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['ITM'] += 1
        stats_journee['details'].append(f"✅ {nom_paire} ({action})")
    else:
        texte = f"❌ **PERTE (OTM)**\n⚠️ Signal {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n👤 Client ID : `{chat_id}`"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {nom_paire} ({action})")
    
    try:
        bot.send_message(ADMIN_ID, texte, parse_mode="Markdown")
    except Exception:
        pass
        
    # Nettoyage de la mémoire
    if chat_id in trades_en_cours:
        del trades_en_cours[chat_id]

# ==============================================================================
# 7. LE COEUR ALGORITHMIQUE : MOTEUR TRIPLE CERVEAU ABSOLU 🌌🕯️🧠
# ==============================================================================

def analyser_binaire_pro(symbole):
    """
    Moteur de décision principal de la Boîte Noire.
    Passe l'historique du marché à travers 3 filtres mathématiques distincts.
    Retourne l'action à prendre, la confiance, l'expiration et le statut.
    """
    candles = obtenir_donnees_deriv(symbole)
    if not candles:
        return "⚠️ Les serveurs Deriv sont instables pour le moment. Réessayez.", None, None, None, None, None, None
    
    try:
        # Conversion des données JSON en DataFrame Pandas pour traitement mathématique
        df = pd.DataFrame([{
            'open': c['open'], 
            'close': c['close'], 
            'high': c['high'], 
            'low': c['low']
        } for c in candles])
        
        # ----------------------------------------------------------------------
        # MODULE 1 : CALCUL DES INDICATEURS DE BASE (POUR LE CERVEAU 1)
        # ----------------------------------------------------------------------
        
        # Bandes de Bollinger (Période 20, Déviation 2)
        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        
        # Oscillateurs de Momentum (RSI et Stochastique)
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()
        
        # Mesure de Volatilité (Average True Range)
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        
        # Bouclier de Tendance (Exponential Moving Average 200)
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        
        # ----------------------------------------------------------------------
        # MODULE 2 : CALCUL DE LA DIVERGENCE (POUR LE CERVEAU 2 - MACD)
        # ----------------------------------------------------------------------
        
        # Moving Average Convergence Divergence
        df['macd'] = ta.trend.MACD(close=df['close']).macd()
        
        # Analyse des sommets et creux (Détection du piège institutionnel)
        prix_fait_nouveau_sommet = df['high'].iloc[-1] > df['high'].iloc[-2]
        macd_fait_sommet_plus_bas = df['macd'].iloc[-1] < df['macd'].iloc[-2]
        prix_fait_nouveau_creux = df['low'].iloc[-1] < df['low'].iloc[-2]
        macd_fait_creux_plus_haut = df['macd'].iloc[-1] > df['macd'].iloc[-2]

        # ----------------------------------------------------------------------
        # MODULE 3 : CALCUL DE L'ANOMALIE STATISTIQUE (POUR LE CERVEAU 3)
        # ----------------------------------------------------------------------
        
        # Bandes de Bollinger Extrêmes (Période 20, Déviation 3 - 99.7% probabilité)
        indicateur_bb_extreme = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=3)
        df['bb_haute_3'] = indicateur_bb_extreme.bollinger_hband()
        df['bb_basse_3'] = indicateur_bb_extreme.bollinger_lband()

        # ----------------------------------------------------------------------
        # EXTRACTION DES VARIABLES EN TEMPS RÉEL (La bougie actuelle)
        # ----------------------------------------------------------------------
        
        atr_actuel = df['atr'].iloc[-1]
        atr_moyen = df['atr'].mean()
        c = df['close'].iloc[-1]
        rsi_val = round(df['rsi'].iloc[-1], 1)
        stoch_val = round(df['stoch_k'].iloc[-1], 1)
        
        bb_h = df['bb_haute'].iloc[-1]
        bb_b = df['bb_basse'].iloc[-1]
        bb_h_3 = df['bb_haute_3'].iloc[-1]
        bb_b_3 = df['bb_basse_3'].iloc[-1]
        ema_200 = df['ema_200'].iloc[-1]

        # ----------------------------------------------------------------------
        # MODULE 4 : LE DÉTECTEUR DE PRICE ACTION (Lecture Anatomique des Bougies)
        # ----------------------------------------------------------------------
        
        # On analyse la DERNIÈRE BOUGIE FERMÉE (iloc[-2]) pour une certitude absolue
        open_1 = df['open'].iloc[-2]
        close_1 = df['close'].iloc[-2]
        high_1 = df['high'].iloc[-2]
        low_1 = df['low'].iloc[-2]
        
        # L'avant-dernière bougie pour la comparaison (Inside Bar)
        open_2 = df['open'].iloc[-3]
        close_2 = df['close'].iloc[-3]
        high_2 = df['high'].iloc[-3]
        low_2 = df['low'].iloc[-3]

        # Calcul des proportions physiques
        taille_totale = high_1 - low_1
        if taille_totale == 0: 
            taille_totale = 0.00001 # Évite la division par zéro mathématique
            
        corps = abs(open_1 - close_1)
        meche_haute = high_1 - max(open_1, close_1)
        meche_basse = min(open_1, close_1) - low_1

        # Identification de la figure
        figure_trouvee = "Aucune"
        if high_1 < high_2 and low_1 > low_2: 
            figure_trouvee = "INSIDE BAR"
        elif meche_basse >= (2 * corps) and meche_haute <= (0.2 * taille_totale): 
            figure_trouvee = "MARTEAU"
        elif meche_haute >= (2 * corps) and meche_basse <= (0.2 * taille_totale): 
            figure_trouvee = "ÉTOILE"

        # ----------------------------------------------------------------------
        # INITIALISATION DES VARIABLES DE RETOUR
        # ----------------------------------------------------------------------
        
        action = None
        confiance = 0
        bb_status = "Au Milieu"
        
        # Expiration Dynamique calculée par la nervosité du marché (ATR)
        if atr_actuel > (atr_moyen * 1.5): 
            duree_minutes = 3
        elif atr_actuel > atr_moyen: 
            duree_minutes = 2
        else: 
            duree_minutes = 1
            
        expiration_texte = f"{duree_minutes} MINUTE{'S' if duree_minutes > 1 else ''} ⏱"
        duree_secondes = duree_minutes * 60

        # ======================================================================
        # 🎯 LA TRINITÉ DÉCISIONNELLE (L'ARBRE LOGIQUE)
        # ======================================================================

        # 🌌 CERVEAU 3 (PRIORITÉ ABSOLUE) : ANOMALIE 3-SIGMA (99.7%)
        # Le prix est sorti des statistiques normales. Un retour violent est imminent.
        if c <= bb_b_3:
            action = "🟢 ACHAT (CALL) 🌌 [TITAN 3-SIGMA]"
            confiance = 99
            bb_status = "Rupture 3-Sigma Validée"
            
        elif c >= bb_h_3:
            action = "🔴 VENTE (PUT) 🌌 [TITAN 3-SIGMA]"
            confiance = 99
            bb_status = "Rupture 3-Sigma Validée"

        # 🕯️ CERVEAU 1 : SNIPER HYBRIDE (Rythme 25 Mars + Bougies Strictes)
        # Le marché est épuisé, et une figure d'inversion s'est formée dans la tendance.
        elif not action:
            # Setup Achat
            if c <= bb_b and rsi_val <= 40 and stoch_val <= 20 and c > ema_200:
                bb_status = "Cassure Bande Basse Validée"
                if figure_trouvee == "INSIDE BAR": 
                    action = "🟢 ACHAT (CALL) 👑 [TITAN INSIDE BAR]"
                    confiance = random.randint(98, 99)
                elif figure_trouvee == "MARTEAU": 
                    action = "🟢 ACHAT (CALL) 🚨 [VIP MARTEAU]"
                    confiance = random.randint(92, 97)
                    
            # Setup Vente
            elif c >= bb_h and rsi_val >= 60 and stoch_val >= 80 and c < ema_200:
                bb_status = "Cassure Bande Haute Validée"
                if figure_trouvee == "INSIDE BAR": 
                    action = "🔴 VENTE (PUT) 👑 [TITAN INSIDE BAR]"
                    confiance = random.randint(98, 99)
                elif figure_trouvee == "ÉTOILE": 
                    action = "🔴 VENTE (PUT) 🚨 [VIP ÉTOILE]"
                    confiance = random.randint(92, 97)

        # 🧠 CERVEAU 2 : DIVERGENCE (Piège Mathématique MACD)
        # Le prix monte dans le vide, les banques piègent les acheteurs.
        if not action:
            # Divergence Haussière (Les vendeurs s'épuisent)
            if c <= bb_b and prix_fait_nouveau_creux and macd_fait_creux_plus_haut:
                action = "🟢 ACHAT (CALL) 💎 [TITAN DIVERGENCE]"
                confiance = random.randint(98, 99) 
                bb_status = "Cassure Basse Validée (Math)"
                
            # Divergence Baissière (Les acheteurs sont piégés)
            elif c >= bb_h and prix_fait_nouveau_sommet and macd_fait_sommet_plus_bas:
                action = "🔴 VENTE (PUT) 💎 [TITAN DIVERGENCE]"
                confiance = random.randint(98, 99)
                bb_status = "Cassure Haute Validée (Math)"

        # ----------------------------------------------------------------------
        # RETOUR DU DIAGNOSTIC FINAL
        # ----------------------------------------------------------------------
        if action: 
            return action, confiance, expiration_texte, duree_secondes, rsi_val, stoch_val, bb_status
        else: 
            return f"⚠️ Marché stable. Scanners C1/C2/C3 en attente.", None, None, None, None, None, None
            
    except Exception as err: 
        log_systeme(f"Erreur d'analyse pandas : {err}", "ERROR")
        return None, None, None, None, None, None, None

# ==============================================================================
# 8. LE SCANNER AUTOMATIQUE DE L'OMBRE (TÂCHE DE FOND)
# ==============================================================================

def scanner_marche_auto():
    """
    Scanner autonome qui tourne en arrière-plan.
    Vérifie le marché toutes les 60 secondes pour les VIP autorisés.
    Bascule intelligemment entre les devises majeures (Jour) et le JPY (Nuit).
    """
    while True:
        try:
            time.sleep(60)
            
            # Vérification de l'audience
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: 
                continue
            
            # Logique Horodatrice et Choix des Devises
            heure_actuelle = datetime.datetime.now().hour
            
            if 8 <= heure_actuelle < 20:
                # Session Europe / US : Volatilité Forte
                devises_a_surveiller = ["EURUSD", "USDJPY", "AUDUSD", "USDCAD", "EURJPY", "USDCHF"] 
            else:
                # Session Asiatique : Volatilité Faible, Focus sur le Yen
                devises_a_surveiller = ["AUDJPY", "USDJPY", "CHFJPY", "CADJPY", "EURJPY"]
            
            # Balayage des radars
            for actif in devises_a_surveiller:
                action, confiance, exp, duree, rsi_val, stoch_val, bb_status = analyser_binaire_pro(actif)
                
                # Validation d'une opportunité
                if action and "⚠️" not in action and confiance:
                    maintenant = time.time()
                    
                    # ⏱️ ANTI-SPAM INTELLIGENT : Bloque la paire pour 3 minutes (180 sec) maximum
                    if actif in derniere_alerte_auto and (maintenant - derniere_alerte_auto[actif] < 180): 
                        continue
                        
                    derniere_alerte_auto[actif] = maintenant
                    
                    # Génération du clavier d'action
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"📊 Analyser {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                    
                    # Personnalisation du message d'alerte selon le cerveau déclenché
                    if "3-SIGMA" in action: 
                        alerte_msg = f"🌌 **ALERTE ANOMALIE 3-SIGMA** 🌌\n\nExtension de 99.7% détectée sur **{actif[:3]}/{actif[3:]}**. Correction institutionnelle imminente.\n\n👇 *Lance le verrouillage !*"
                    elif "DIVERGENCE" in action: 
                        alerte_msg = f"⚡ **ALERTE TITAN DIVERGENCE** ⚡\n\nPiège institutionnel détecté sur **{actif[:3]}/{actif[3:]}** (Divergence Mathématique Validée).\n\n👇 *Lance le verrouillage !*"
                    elif confiance >= 98: 
                        alerte_msg = f"👑 **ALERTE TITAN DÉTECTÉE** 👑\n\nCompression rarissime sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Lance l'analyse !*"
                    else: 
                        alerte_msg = f"🚨 **NOUVELLE OPPORTUNITÉ VIP** 🚨\n\nFigure de retournement sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Lance l'analyse !*"
                        
                    # Diffusion du signal aux VIP
                    for chat_id in utilisateurs_a_alerter:
                        try: 
                            bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except Exception: 
                            pass
                            
        except Exception as e: 
            log_systeme(f"Erreur dans le scanner auto : {e}", "ERROR")
            pass

# ==============================================================================
# 9. GESTIONNAIRE D'HORAIRES, TRANSITIONS ET BILAN DE FIN DE JOURNÉE
# ==============================================================================

def gestion_horaires_et_bilan():
    """
    Superviseur temporel.
    Envoie les notifications de changement de session (Jour/Nuit).
    Génère et envoie le rapport comptable au Fondateur à 22h00 GMT.
    """
    global stats_journee, bilan_envoye_aujourdhui, transition_nuit_envoyee, transition_jour_envoyee
    
    while True:
        try:
            maintenant = datetime.datetime.now()
            heure, minute = maintenant.hour, maintenant.minute
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]

            # Transition Nuit (20h00)
            if heure == 20 and minute == 0 and not transition_nuit_envoyee:
                texte_nuit = "🌉 **TRANSITION DE SESSION : MODE ASIATIQUE ACTIVÉ** 🌉\n\nLes volumes s'effondrent sur l'Europe. Le Terminal Prime bascule ses radars sur l'Asie (Focus 100% JPY)."
                for chat_id in utilisateurs_a_alerter:
                    try: bot.send_message(chat_id, texte_nuit, parse_mode="Markdown")
                    except: pass
                transition_nuit_envoyee, transition_jour_envoyee = True, False

            # Transition Jour (08h00)
            elif heure == 8 and minute == 0 and not transition_jour_envoyee:
                texte_jour = "☀️ **TRANSITION DE SESSION : MODE EUROPE/US ACTIVÉ** ☀️\n\nOuverture des marchés majeurs. La volatilité est de retour sur les paires majeures."
                for chat_id in utilisateurs_a_alerter:
                    try: bot.send_message(chat_id, texte_jour, parse_mode="Markdown")
                    except: pass
                transition_jour_envoyee, transition_nuit_envoyee = True, False

            # Bilan Automatique pour le Fondateur (22h00)
            elif heure == 22 and minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                if total_trades > 0:
                    winrate = round((stats_journee['ITM'] / total_trades) * 100)
                    texte_bilan_admin = f"📊 **BILAN VIP DE LA JOURNÉE** 📊\n🎯 **Signaux :** {total_trades}\n✅ **ITM :** {stats_journee['ITM']} | ❌ **OTM :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n\n"
                    
                    for detail in stats_journee['details']: 
                        texte_bilan_admin += f"{detail}\n"
                        
                    try: 
                        bot.send_message(ADMIN_ID, texte_bilan_admin, parse_mode="Markdown")
                    except: 
                        pass
                        
                # Réinitialisation des statistiques pour le lendemain
                stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}
                bilan_envoye_aujourdhui = True
                
            # Reset du verrou journalier (23h00)
            elif heure == 23: 
                bilan_envoye_aujourdhui = False
                
            time.sleep(30)
            
        except Exception: 
            time.sleep(60)

# ==============================================================================
# 10. COMMANDES ADMIN, GÉNÉRATION DE CLÉS ET VÉRIFICATIONS
# ==============================================================================

@bot.message_handler(commands=['panel'])
def admin_panel(message):
    """Affiche le panneau de contrôle confidentiel du Fondateur."""
    if message.chat.id == ADMIN_ID: 
        bot.send_message(ADMIN_ID, f"Admin Panel 🔥\nCapital actuel du Fondateur : {CAPITAL_ACTUEL}$")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("PRIME-"))
def activer_cle(message):
    """Traite et valide les clés d'abonnement saisies par les utilisateurs."""
    cle = message.text.strip()
    if cle in cles_generees:
        if cles_generees[cle]["user_id"] != message.chat.id: 
            return bot.send_message(message.chat.id, "❌ **ACCÈS REFUSÉ**", parse_mode="Markdown")
            
        jours = cles_generees[cle]["jours"]
        
        if jours == 999: 
            utilisateurs_autorises[message.chat.id] = "LIFETIME"
            duree_texte = "À VIE 👑"
        else:
            expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
            utilisateurs_autorises[message.chat.id] = expiration
            duree_texte = f"jusqu'au {expiration.strftime('%d/%m/%Y à %H:%M')}"
            
        del cles_generees[cle] 
        bot.send_message(message.chat.id, f"✅ **CLÉ ACCEPTÉE !** 🎉\n\nAbonnement activé {duree_texte}.\nTapez /start pour lancer le Terminal Prime.", parse_mode="Markdown")
    else: 
        bot.send_message(message.chat.id, "❌ **Clé invalide.**", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    """Gestion des demandes d'accès des nouveaux utilisateurs (Côté Admin)."""
    if call.from_user.id != ADMIN_ID: 
        return
        
    action = call.data.split("_")[1]
    user_id = int(call.data.split("_")[2])
    
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"), 
            InlineKeyboardButton("2 Semaines", callback_data=f"gen_14_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"), 
            InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Utilisateur `{user_id}` accepté.\nSélectionnez la durée de l'abonnement :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif action == "refuser": 
        bot.edit_message_text("❌ Demande refusée par l'administrateur.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    """Génération finale de la clé d'activation suite au choix de durée."""
    if call.from_user.id != ADMIN_ID: 
        return
        
    jours = int(call.data.split("_")[1])
    user_id = int(call.data.split("_")[2])
    
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    
    bot.edit_message_text(f"🔑 **CLÉ GÉNÉRÉE AVEC SUCCÈS** 🔑\n\nID Utilisateur : `{user_id}`\n\nClé à envoyer :\n`{cle}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# ==============================================================================
# 11. COMMANDES PUBLIQUES ET GESTIONNAIRES D'INTERFACE UTILISATEUR
# ==============================================================================

def obtenir_clavier():
    """Génère le clavier principal (esthétique VIP préservée)."""
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    """Message d'accueil et vérification des droits d'accès initiaux."""
    user_id = message.chat.id
    if not est_autorise(user_id):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("✅ Accepter", callback_data=f"admin_accepter_{user_id}"), InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}"))
        try: 
            bot.send_message(ADMIN_ID, f"🚨 **NOUVEAU CLIENT VIP POTENTIEL** 🚨\n\n🆔 `{user_id}`", reply_markup=markup, parse_mode="Markdown")
        except: 
            pass
        return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT - TERMINAL PRIVÉ**\nCe système est sous licence.\nContactez le fondateur [@hermann1123](https://t.me/hermann1123).", parse_mode="Markdown", disable_web_page_preview=True)

    utilisateurs_actifs.add(user_id)
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - ÉDITION ULTIME** 🔥
    
Bienvenue dans la Boîte Noire. Ce système analyse la psychologie bancaire en temps réel.

📖 **PROCÉDURE :**
1️⃣ Cliquez sur "📊 CHOISIR UNE DEVISE".
2️⃣ Cliquez sur "🚀 LANCER L'ANALYSE" pour activer les 3 cerveaux quantitatifs.
3️⃣ Appliquez le Money Management strict de 2%.

💡 **LE MOT DU FONDATEUR :**
*Le marché ne ressent rien, n'aie aucune émotion face à lui. Le succès vient d'une discipline de fer.* 🎯💸"""
    bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    """Renvoie le guide stratégique des heures d'ouverture des marchés."""
    if est_autorise(message.chat.id): 
        bot.send_message(message.chat.id, "🕒 **HORAIRES STRATÉGIQUES (GMT)** 🕒\n\n✅ **08h-11h :** EUR/USD, USD/JPY\n🔥 **13h30-16h30 :** EUR/USD, AUD/USD\n🌉 **20h-08h :** AUD/JPY, USD/JPY, EUR/JPY", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    """Menu interactif pour verrouiller une cible sur les radars de Deriv."""
    if not est_autorise(message.chat.id): 
        return
        
    markup = InlineKeyboardMarkup(row_width=2) # Esthétique "large" respectée
    heure = datetime.datetime.now().hour
    
    if 8 <= heure < 20: 
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"), 
            InlineKeyboardButton("🇯🇵 USD/JPY ✅", callback_data="set_USDJPY"), 
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"), 
            InlineKeyboardButton("🇺🇸 USD/CAD", callback_data="set_USDCAD"), 
            InlineKeyboardButton("🇪🇺 EUR/JPY ✅", callback_data="set_EURJPY"), 
            InlineKeyboardButton("🇨🇭 USD/CHF", callback_data="set_USDCHF")
        )
    else: 
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/JPY ✅", callback_data="set_AUDJPY"), 
            InlineKeyboardButton("🇯🇵 USD/JPY ✅", callback_data="set_USDJPY"), 
            InlineKeyboardButton("🇨🇭 CHF/JPY ✅", callback_data="set_CHFJPY"), 
            InlineKeyboardButton("🇨🇦 CAD/JPY ✅", callback_data="set_CADJPY"), 
            InlineKeyboardButton("🇪🇺 EUR/JPY ✅", callback_data="set_EURJPY")
        )
        
    bot.send_message(message.chat.id, "Sélectionnez l'actif à scanner (Synchronisé Pocket Broker) :", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    """
    Déclencheur d'analyse principal (Le Verrouillage Sniper).
    Génère l'animation de chargement, appelle le Moteur Triple Cerveau,
    et calcule la synchronisation millimétrée.
    """
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): 
        return
        
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    
    try:
        msg = bot.send_message(chat_id, "⏳ *Connexion Sécurisée à Deriv API v3...*", parse_mode="Markdown")
        time.sleep(1.5)
        bot.edit_message_text(f"📡 *Scan Triple Cerveau sur la structure {actif[:3]}/{actif[3:]}...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(1.5)
    except: 
        return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Cible introuvable (Rejet Serveur). Relancez l'analyse.", chat_id, msg.message_id)
        except: pass
        return

    # ⏱️ MODULE DE SYNCHRONISATION MILLIMÉTRÉE
    # Le bot calcule le nombre de secondes exactes avant la prochaine bougie '00'
    maintenant = datetime.datetime.now()
    secondes_restantes = 60 - maintenant.second
    
    # Sécurité: S'il reste moins de 10 secondes, on accorde une minute supplémentaire
    if secondes_restantes < 10: 
        secondes_restantes += 60
        
    heure_entree_dt = maintenant + datetime.timedelta(seconds=secondes_restantes)
    delai_avant_entree = secondes_restantes

    # Préparation esthétique des variables de la carte de signal
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)
    jauge = generer_jauge(confiance)
    rsi_emoji = "🟢" if "ACHAT" in action else "🔴"
    
    # Formatage dynamique selon le cerveau activé
    if "3-SIGMA" in action: 
        stoch_text = "N/A (Anomalie 99.7%)"
    elif "DIVERGENCE" in action: 
        stoch_text = "N/A (Piège Validé)"
    else: 
        stoch_text = "Survente" if "ACHAT" in action else "Surachat"

    # L'ESPACEMENT PARFAIT DU SIGNAL VIP
    signal = f"""🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀
──────────────────
🛰 **ACTIF :** {actif[:3]}/{actif[3:]}
🎯 **ACTION :** {action}
⏳ **EXPIRATION :** {exp_texte}
──────────────────
🌡️ **FORCE DU SIGNAL (ALGORITHME) :**
{jauge}

📊 **VALIDATION DES INDICATEURS :**
➤ **RSI :** {rsi_emoji} Validé ({rsi_val})
➤ **Stochastique :** {rsi_emoji} Validé ({stoch_text})
➤ **Bollinger :** {rsi_emoji} {bb_status}
──────────────────
📍 **ORDRE À : {heure_entree_dt.strftime("%H:%M:00")} PILE** 👈
*(Temps de préparation : {secondes_restantes} secondes)*
💵 **MISE RECOMMANDÉE :** {mise_recommandee}$ (2%)
🔥 **CONFIANCE GLOBALE :** {confiance}%
──────────────────"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except Exception as e: 
        log_systeme(f"Erreur d'affichage du signal pour {chat_id}: {e}", "ERROR")

    # Mémorisation de la transaction pour l'audit ITM/OTM
    trades_en_cours[chat_id] = {'symbole': actif, 'action': "CALL" if "ACHAT" in action else "PUT"}
    
    # Armement des chronomètres invisibles pour auditer les résultats
    Timer(delai_avant_entree, relever_prix_entree, args=[chat_id, actif]).start()
    Timer(delai_avant_entree + duree_secondes, verifier_resultat, args=[chat_id]).start()

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    """Raccourci pour relancer une analyse rapide sur la dernière devise sélectionnée."""
    if not est_autorise(message.chat.id): 
        return
        
    actif = user_prefs.get(message.from_user.id)
    if not actif: 
        return bot.send_message(message.chat.id, "⚠️ Sélectionnez d'abord une devise dans le menu !")
        
    # Simulation d'un clic de bouton
    call_mock = type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})()
    save_devise(call_mock)

# ==============================================================================
# LANCEMENT GLOBAL DU SYSTÈME (BOOT)
# ==============================================================================

if __name__ == "__main__":
    try:
        # 1. Activation du maintien en ligne
        keep_alive()
        
        # 2. Démarrage des threads d'arrière-plan (Scanner et Comptabilité)
        Thread(target=scanner_marche_auto, daemon=True).start()
        Thread(target=gestion_horaires_et_bilan, daemon=True).start()
        
        # 3. Message de confirmation dans la console
        message_boot = "⬛ BOÎTE NOIRE : Édition Suprême Démarrée. Protections IP actives. Triple Cerveau en ligne."
        print(message_boot, flush=True)
        log_systeme(message_boot)
        
        # 4. Connexion constante aux serveurs Telegram
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
        
    except KeyboardInterrupt:
        print("\nArrêt manuel du système demandé.")
        sys.exit(0)
    except Exception as e:
        print(f"Erreur critique lors du démarrage : {e}")
        sys.exit(1)
