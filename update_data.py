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

    # 기본 지수
    sp500 = yf.Ticker("^GSPC")
    nasdaq = yf.Ticker("^IXIC")
    vix = yf.Ticker("^VIX")

    sp_price = round(sp500.fast_info.last_price, 2)
    sp_change = round((sp_price - sp500.fast_info.previous_close) / sp500.fast_info.previous_close * 100, 2)
    sp_ytd = get_ytd(sp500)
    nq_price = int(nasdaq.fast_info.last_price)
    nq_ytd = get_ytd(nasdaq)
    vix_price = round(vix.fast_info.last_price, 2)

    # 공포탐욕
    fear, fear_status = get_fear_greed()
    if fear is None:
        if vix_price >= 40: fear, fear_status = 10, "극도의 공포"
        elif vix_price >= 30: fear, fear_status = 22, "극도의 공포"
        elif vix_price >= 20: fear, fear_status = 38, "공포"
        else: fear, fear_status = 55, "중립"

    # Firebase tickers 읽기
    tickers_snap = db.reference('tickers').get()
    tickers_data = {}
    inactive_symbols = []

    if tickers_snap:
        for symbol, info in tickers_snap.items():
            status = info.get('status', 'active')
            if status in ['active', 'watch']:
                # 활성/관심 종목만 데이터 수집
                data = get_ticker_data(symbol)
                if data:
                    tickers_data[symbol] = {
                        **data,
                        "name": info.get('name', symbol),
                        "type": info.get('type', 'etf'),
                        "threshold": info.get('threshold', -20),
                        "status": status
                    }
                    print(f"✅ {symbol}: ${data['price']} ({data['ytd']}% YTD) [{status}]")
            else:
                # 비활성 종목은 tickers_data에서 삭제
                inactive_symbols.append(symbol)
                print(f"⛔ {symbol}: 비활성 → tickers_data에서 제거")

    # 비활성 종목 tickers_data에서 삭제
    for symbol in inactive_symbols:
        try:
            db.reference(f'tickers_data/{symbol}').delete()
            print(f"🗑️ {symbol} tickers_data 삭제 완료")
        except:
            pass

    # 기준값
    criteria_snap = db.reference('criteria').get()
    cr = criteria_snap if criteria_snap else {
        'fear': 25, 'vix': 30, 'sp500': -10,
        'nasdaq': -15, 'fngu': -20, 'soxl': -20
    }

    # 체크리스트 계산
    checks = 0
    if fear <= cr.get('fear', 25): checks += 1
    if vix_price >= cr.get('vix', 30): checks += 1
    if sp_ytd <= cr.get('sp500', -10): checks += 1
    if nq_ytd <= cr.get('nasdaq', -15): checks += 1

    # 활성 종목 중 FNGU, SOXL 기준 체크
    fngu_ytd = tickers_data.get('FNGU', {}).get('ytd', 0)
    soxl_ytd = tickers_data.get('SOXL', {}).get('ytd', 0)
    if fngu_ytd <= cr.get('fngu', -20): checks += 1
    if soxl_ytd <= cr.get('soxl', -20): checks += 1

    buy_readiness = round(checks / 6 * 100)

    # 메시지
    msg_snap = db.reference('messages').get()
    msg = msg_snap if msg_snap else {}
    if buy_readiness >= 60:
        strategy = msg.get('strong', '강력 매수 시작')
        fngu_status = "강력 매수 구간"
    elif buy_readiness >= 30:
        strategy = msg.get('partial', '부분 매수 + 관망')
        fngu_status = "강력 매수 구간"
    else:
        strategy = msg.get('watch', '관망 유지')
        fngu_status = "관망 구간"

    return {
        "market": {
            "fear_greed": fear,
            "fear_greed_status": fear_status,
            "vix": vix_price,
            "sp500": sp_price,
            "sp500_change": sp_change,
            "sp500_ytd": sp_ytd,
            "nasdaq": nq_price,
            "nasdaq_ytd": nq_ytd,
            "last_updated": now,
            "fngu_price": tickers_data.get('FNGU', {}).get('price', 0),
            "fngu_change": tickers_data.get('FNGU', {}).get('change', 0),
            "fngu_ytd": fngu_ytd,
            "soxl_price": tickers_data.get('SOXL', {}).get('price', 0),
            "soxl_ytd": soxl_ytd,
        },
        "signal": {
            "fngu_status": fngu_status,
            "buy_readiness": buy_readiness,
            "checks": checks,
            "strategy": strategy
        },
        "tickers_data": tickers_data
    }

data = get_market_data()
db.reference('market').set(data['market'])
db.reference('signal').set(data['signal'])

# tickers_data 업데이트 (활성 종목만)
if data['tickers_data']:
    db.reference('tickers_data').update(data['tickers_data'])

print("✅ 전체 업데이트 완료:", data['market']['last_updated'])
print("활성 종목:", list(data['tickers_data'].keys()))
print("매수준비도:", data['signal']['buy_readiness'], "%")
