import yfinance as yf
import firebase_admin
from firebase_admin import credentials, db
import json
import os
import requests
from datetime import datetime
import pytz

cred_dict = json.loads(os.environ['FIREBASE_CREDENTIALS'])
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred, {
    'databaseURL': os.environ['FIREBASE_DB_URL']
})

def get_fear_greed():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = res.json()
        val = int(data['data'][0]['value'])
        status_map = {
            "Extreme Fear": "극도의 공포", "Fear": "공포",
            "Neutral": "중립", "Greed": "탐욕", "Extreme Greed": "극도의 탐욕"
        }
        return val, status_map.get(data['data'][0]['value_classification'], '알 수 없음')
    except Exception as e:
        print(f"공포탐욕 API 실패: {e}")
        return None, None

def get_ytd(ticker_obj):
    try:
        hist = ticker_obj.history(period="ytd")
        if hist.empty: return 0
        current = ticker_obj.fast_info.last_price
        return round((current - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100, 2)
    except:
        return 0

def get_ticker_data(symbol):
    try:
        t = yf.Ticker(symbol)
        price = round(t.fast_info.last_price, 2)
        prev = t.fast_info.previous_close
        change = round((price - prev) / prev * 100, 2)
        ytd = get_ytd(t)
        return {"price": price, "change": change, "ytd": ytd}
    except Exception as e:
        print(f"{symbol} 데이터 수집 실패: {e}")
        return None

def get_market_data():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea).strftime('%Y-%m-%d %H:%M')

    sp500 = yf.Ticker("^GSPC")
    nasdaq = yf.Ticker("^IXIC")
    vix = yf.Ticker("^VIX")

    sp_price = round(sp500.fast_info.last_price, 2)
    sp_change = round((sp_price - sp500.fast_info.previous_close) / sp500.fast_info.previous_close * 100, 2)
    sp_ytd = get_ytd(sp500)
    nq_price = int(nasdaq.fast_info.last_price)
    nq_ytd = get_ytd(nasdaq)
    vix_price = round(vix.fast_info.last_price, 2)

    fear, fear_status = get_fear_greed()
    if fear is None:
        if vix_price >= 40: fear, fear_status = 10, "극도의 공포"
        elif vix_price >= 30: fear, fear_status = 22, "극도의 공포"
        elif vix_price >= 20: fear, fear_status = 38, "공포"
        else: fear, fear_status = 55, "중립"

    tickers_snap = db.reference('tickers').get()
    tickers_data = {}
    inactive_symbols = []

    if tickers_snap:
        for symbol, info in tickers_snap.items():
            status = info.get('status', 'active')
            if status in ['active', 'watch']:
                data = get_ticker_data(symbol)
                if data:
                    tickers_data[symbol] = {
                        "price": data["price"],
                        "change": data["change"],
                        "ytd": data["ytd"],
                        "name": str(info.get('name', symbol)),
                        "type": str(info.get('type', 'etf')),
                        "threshold": float(info.get('threshold', -20)),
                        "status": str(status)
                    }
                    print(f"✅ {symbol}: ${data['price']} ({data['ytd']}% YTD)")
            else:
                inactive_symbols.append(symbol)

    for symbol in inactive_symbols:
        try:
            db.reference(f'tickers_data/{symbol}').delete()
        except:
            pass

    criteria_snap = db.reference('criteria').get()
    cr = criteria_snap if criteria_snap else {
        'fear': 25, 'vix': 30, 'sp500': -10, 'nasdaq': -15, 'fngu': -20, 'soxl': -20
    }
    print(f"기준값: {cr}")

    fngu_ytd = float(tickers_data.get('FNGU', {}).get('ytd', 0))
    soxl_ytd = float(tickers_data.get('SOXL', {}).get('ytd', 0))

    fear_t  = float(cr.get('fear',   25))
    vix_t   = float(cr.get('vix',    30))
    sp_t    = float(cr.get('sp500', -10))
    nq_t    = float(cr.get('nasdaq',-15))
    fngu_t  = float(cr.get('fngu',  -20))
    soxl_t  = float(cr.get('soxl',  -20))

    fear_ok  = int(fear      <= fear_t)
    vix_ok   = int(vix_price >= vix_t)
    sp_ok    = int(sp_ytd    <= sp_t)
    nq_ok    = int(nq_ytd    <= nq_t)
    fngu_ok  = int(fngu_ytd  <= fngu_t)
    soxl_ok  = int(soxl_ytd  <= soxl_t)

    total_checks = fear_ok + vix_ok + sp_ok + nq_ok + fngu_ok + soxl_ok
    total_conditions = 6
    buy_readiness = round(total_checks / total_conditions * 100)

    check_results = {
        'fear':   {'ok': fear_ok,  'val': float(fear),     'label': f"공포지수 {int(fear_t)}↓"},
        'vix':    {'ok': vix_ok,   'val': float(vix_price),'label': f"VIX {int(vix_t)}↑"},
        'sp500':  {'ok': sp_ok,    'val': float(sp_ytd),   'label': f"S&P {sp_t}%↓"},
        'nasdaq': {'ok': nq_ok,    'val': float(nq_ytd),   'label': f"NASDAQ {nq_t}%↓"},
        'fngu':   {'ok': fngu_ok,  'val': float(fngu_ytd), 'label': f"FNGU {fngu_t}%↓"},
        'soxl':   {'ok': soxl_ok,  'val': float(soxl_ytd), 'label': f"SOXL {soxl_t}%↓"},
    }

    msg_snap = db.reference('messages').get()
    msg = msg_snap if msg_snap else {}
    if buy_readiness >= 60:
        strategy = str(msg.get('strong', '강력 매수 시작'))
        signal_status = "강력 매수 구간"
    elif buy_readiness >= 30:
        strategy = str(msg.get('partial', '부분 매수 + 관망'))
        signal_status = "부분 매수 구간"
    else:
        strategy = str(msg.get('watch', '관망 유지'))
        signal_status = "관망 구간"

    print(f"체크: {total_checks}/6 = {buy_readiness}%")

    return {
        "market": {
            "fear_greed": int(fear),
            "fear_greed_status": str(fear_status),
            "vix": float(vix_price),
            "sp500": float(sp_price),
            "sp500_change": float(sp_change),
            "sp500_ytd": float(sp_ytd),
            "nasdaq": int(nq_price),
            "nasdaq_ytd": float(nq_ytd),
            "last_updated": str(now),
            "fngu_price": float(tickers_data.get('FNGU', {}).get('price', 0)),
            "fngu_change": float(tickers_data.get('FNGU', {}).get('change', 0)),
            "fngu_ytd": fngu_ytd,
            "soxl_price": float(tickers_data.get('SOXL', {}).get('price', 0)),
            "soxl_ytd": soxl_ytd,
        },
        "signal": {
            "signal_status": str(signal_status),
            "buy_readiness": int(buy_readiness),
            "checks": int(total_checks),
            "total_conditions": int(total_conditions),
            "strategy": str(strategy),
            "check_results": check_results,
            "ticker_checks": {}
        },
        "tickers_data": tickers_data
    }

data = get_market_data()
db.reference('market').set(data['market'])
db.reference('signal').set(data['signal'])
if data['tickers_data']:
    db.reference('tickers_data').update(data['tickers_data'])

print("✅ 업데이트 완료:", data['market']['last_updated'])
print("매수준비도:", data['signal']['buy_readiness'], "%")
