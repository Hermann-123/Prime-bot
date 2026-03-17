import http.server
import socketserver
import threading
import ccxt
import pandas as pd
import time
import requests

# --- SERVEUR POUR L'HÉBERGEMENT (RENDER) ---
def run_server():
    port = 8080
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# --- TA CONFIGURATION ---
TOKEN = "8658287331:AAEqTnQ9F-PvqpFGty0woA0oZ4V66RmtdK4"
CHAT_ID = "5968288964"
SYMBOL = "BTC/USDT"

def envoyer_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage?chat_id={CHAT_ID}&text={message}&parse_mode=Markdown"
        requests.get(url)
    except: pass

def calculer_rsi(prices):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def analyser():
    ex = ccxt.binance()
    bars = ex.fetch_ohlcv(SYMBOL, timeframe='1m', limit=100)
    df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
    df['RSI'] = calculer_rsi(df['c'])
    df['MA20'] = df['c'].rolling(window=20).mean()
    df['STD'] = df['c'].rolling(window=20).std()
    df['Lower'] = df['MA20'] - (2 * df['STD'])
    df['Upper'] = df['MA20'] + (2 * df['STD'])
    df['EMA200'] = df['c'].ewm(span=200, adjust=False).mean()
    
    last = df.iloc[-1]
    prix = last['c']
    
    # SIGNAL ACHAT
    if prix < last['Lower'] and last['RSI'] < 30 and prix > last['EMA200']:
        envoyer_telegram(f"🚀 *ACHAT (CALL)*\nPrix: {prix}\nSignal sur {SYMBOL}")
    # SIGNAL VENTE
    elif prix > last['Upper'] and last['RSI'] > 70 and prix < last['EMA200']:
        envoyer_telegram(f"🔴 *VENTE (PUT)*\nPrix: {prix}\nSignal sur {SYMBOL}")

# --- LANCEMENT ---
print("🚀 Hébergement lancé...")
while True:
    try:
        analyser()
        time.sleep(30)
    except Exception as e:
        print(f"Erreur: {e}")
        time.sleep(10)

