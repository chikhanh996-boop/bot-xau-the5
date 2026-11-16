import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timezone, timedelta
from flask import Flask
import threading
import os

# ================= CÀI ĐẶT THÔNG SỐ QUỸ THE 5%ERS =================
TELEGRAM_TOKEN = "7540292886:AAHKY17nK401uS80g8GZ9J4Z83L6N1v99j8"  # Token Phimmaker_notify_bot của Khánh
TELEGRAM_CHAT_ID = "6467739905"                                  # Chat ID chuẩn của Khánh vừa lấy

SYMBOL = "XAUUSD"
ACCOUNT_SIZE = 2500.0                                          
RISK_USD = 12.5                                                

# Khởi tạo Web Server nhỏ để Render duy trì hoạt động 24/7
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 XAU/USD Trading Bot (The 5%ers) is running 24/7 on Cloud!"

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Trạng thái gửi Telegram: {response.status_code}")
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def get_high_impact_news():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        response = requests.get(url, timeout=10)
        events = response.json()
        
        high_impact_events = []
        now_utc = datetime.now(timezone.utc)
        
        for event in events:
            if event.get('impact') == 'High' and event.get('country') in ['USD', 'ALL']:
                event_time_str = event.get('date')
                event_time = datetime.fromisoformat(event_time_str.replace('Z', '+00:00'))
                if abs((event_time - now_utc).total_seconds()) <= 86400:
                    high_impact_events.append({
                        'title': event.get('title'),
                        'time': event_time
                    })
        return high_impact_events
    except Exception as e:
        return []

def is_near_news():
    events = get_high_impact_news()
    now_utc = datetime.now(timezone.utc)
    for ev in events:
        ev_time = ev['time']
        if (ev_time - timedelta(minutes=30)) <= now_utc <= (ev_time + timedelta(minutes=30)):
            return True, ev['title']
    return False, ""

def get_market_data():
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1h&range=30d"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quotes = result['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'time': pd.to_datetime(timestamps, unit='s'),
            'open': quotes['open'],
            'high': quotes['high'],
            'low': quotes['low'],
            'close': quotes['close'],
            'volume': quotes['volume']
        }).dropna()
        return df
    except Exception as e:
        print(f"Lỗi lấy dữ liệu giá: {e}")
        return None

def calculate_10_indicators(df):
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / loss)))
    
    df['bb_mid'] = df['close'].rolling(window=20).mean()
    bb_std = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_mid'] + (bb_std * 2)
    df['bb_lower'] = df['bb_mid'] - (bb_std * 2)
    
    tr = pd.concat([df['high'] - df['low'], np.abs(df['high'] - df['close'].shift()), np.abs(df['low'] - df['close'].shift())], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=14).mean()
    
    df['donchian_high'] = df['high'].rolling(window=20).max()
    df['donchian_low'] = df['low'].rolling(window=20).min()
    
    return df

def analyze_market():
    is_locked, news_name = is_near_news()
    if is_locked:
        send_telegram_message(f"🛡️ **THE 5%ERS SAFETY ALERT**\n🔴 Sắp có tin mạnh: `{news_name}`. Tạm ngưng phân tích thị trường vàng!")
        return

    df = get_market_data()
    if df is None or len(df) < 220:
        return
        
    df = calculate_10_indicators(df)
    cur = df.iloc[-2]
    current_price = df['close'].iloc[-1]
    
    resistance = round(cur['donchian_high'], 2)
    support = round(cur['donchian_low'], 2)
    
    score = 0
    if cur['close'] > cur['ema200']: score += 1
    if cur['ema50'] > cur['ema200']: score += 1
    if cur['macd'] > cur['macd_signal']: score += 1
    if cur['rsi'] > 50: score += 1
    
    bias = "TĂNG (BULLISH)" if score >= 3 else "GIẢM (BEARISH)"
    signal = "ĐỨNG NGOÀI QUAN SÁT (WAIT)"
    sl = tp = 0.0
    
    if score >= 3 and 40 <= cur['rsi'] <= 55:
        signal = "MUA (BUY)"
        sl = round(support - (1.5 * cur['atr']), 2)
        risk_dist = current_price - sl
        tp = round(current_price + (risk_dist * 2.0), 2)
    elif score < 3 and 45 <= cur['rsi'] <= 60:
        signal = "BÁN (SELL)"
        sl = round(resistance + (1.5 * cur['atr']), 2)
        risk_dist = sl - current_price
        tp = round(current_price - (risk_dist * 2.0), 2)

    risk_pips = abs(current_price - sl) if sl > 0 else 5.0
    lot_size = max(0.01, round(RISK_USD / (risk_pips * 100), 2))

    msg = (
        f"📊 **BÁO CÁO TÍN HIỆU XAU/USD (H1)** 📊\n\n"
        f"🎯 **Xu hướng chung:** `{bias}` (Điểm: {score}/4)\n"
        f"💵 **Giá hiện tại:** `{current_price}`\n\n"
        f"🛡️ **Ngưỡng Kỹ thuật:**\n"
        f"• Kháng cự: `{resistance}`\n"
        f"• Hỗ trợ: `{support}`\n\n"
        f"⚡ **Gợi ý hành động:** `{signal}`\n"
        f"📍 Entry tham khảo: `{current_price}`\n"
        f"🛑 SL gợi ý: `{sl}` | 🎯 TP gợi ý: `{tp}`\n"
        f"⚖️ Khối lượng chuẩn: `{lot_size} Lot` (${RISK_USD})\n"
        f"🛡️ *The 5%ers $2,500 - Tự quản lý lệnh tay!*"
    )
    send_telegram_message(msg)

def run_bot_loop():
    print("🤖 Bot Tín hiệu Độc Lập XAU/USD trên Cloud đã khởi động...")
    time.sleep(5)
    send_telegram_message("🚀 **Bot XAU/USD The 5%ers đã lên mây thành công!**\nĐang túc trực canh thị trường 24/7 cho Khánh (Chat ID: 6467739905).")
    analyze_market()
    
    while True:
        try:
            now = datetime.now()
            if now.minute == 2:
                analyze_market()
                time.sleep(60)
            else:
                time.sleep(20)
        except Exception as e:
            print(f"Lỗi vòng lặp bot: {e}")
            time.sleep(60)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
