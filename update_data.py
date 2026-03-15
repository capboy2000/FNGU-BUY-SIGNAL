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
                inactive_symbols.append(symbol)
                print(f"⛔ {symbol}: 비활성 → 제거")

    # 비활성 종목 삭제
    for symbol in inactive_symbols:
        try:
            db.reference(f'tickers_data/{symbol}').delete()
        except:
            pass

    # ✅ criteria DB에서 기준값 읽기
    criteria_snap = db.reference('criteria').get()
    cr = criteria_snap if criteria_snap else {
        'fear': 25, 'vix': 30, 'sp500': -10,
        'nasdaq': -15, 'fngu': -20, 'soxl': -20
    }
    print(f"기준값: {cr}")

    # ✅ 체크리스트 계산 - criteria 기준으로
    check_results = {}
    total_checks = 0

    # 고정 지표 체크
    fear_ok = fear <= cr.get('fear', 25)
    vix_ok = vix_price >= cr.get('vix', 30)
    sp_ok = sp_ytd <= cr.get('sp500', -10)
    nq_ok = nq_ytd <= cr.get('nasdaq', -15)

    check_results['fear'] = {'ok': fear_ok, 'val': fear, 'label': f"공포지수 {cr.get('fear',25)}↓"}
    check_results['vix'] = {'ok': vix_ok, 'val': vix_price, 'label': f"VIX {cr.get('vix',30)}↑"}
    check_results['sp500'] = {'ok': sp_ok, 'val': sp_ytd, 'label': f"S&P {cr.get('sp500',-10)}%↓"}
    check_results['nasdaq'] = {'ok': nq_ok, 'val': nq_ytd, 'label': f"NASDAQ {cr.get('nasdaq',-15)}%↓"}

    if fear_ok: total_checks += 1
    if vix_ok: total_checks += 1
    if sp_ok: total_checks += 1
    if nq_ok: total_checks += 1

    # ✅ 종목별 체크 (각 종목의 threshold 사용)
    ticker_checks = {}
    for symbol, data in tickers_data.items():
        threshold = data.get('threshold', -20)
        ok = data['ytd'] <= threshold
        ticker_checks[symbol] = {'ok': ok, 'val': data['ytd'], 'label': f"{symbol} {threshold}%↓"}
        if ok: total_checks += 1

    total_conditions = 4 + len(tickers_data)  # 고정 4개 + 종목 수
    buy_readiness = round(total_checks / total_conditions * 100) if total_conditions > 0 else 0

    # 메시지
    msg_snap = db.reference('messages').get()
    msg = msg_snap if msg_snap else {}
    if buy_readiness >= 60:
        strategy = msg.get('strong', '강력 매수 시작')
        signal_status = "강력 매수 구간"
    elif buy_readiness >= 30:
        strategy = msg.get('partial', '부분 매수 + 관망')
        signal_status = "부분 매수 구간"
    else:
        strategy = msg.get('watch', '관망 유지')
        signal_status = "관망 구간"

    fngu_ytd = tickers_data.get('FNGU', {}).get('ytd', 0)
    soxl_ytd = tickers_data.get('SOXL', {}).get('ytd', 0)

    print(f"체크: {total_checks}/{total_conditions} = {buy_readiness}%")

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
            "signal_status": signal_status,
            "buy_readiness": buy_readiness,
            "checks": total_checks,
            "total_conditions": total_conditions,
            "strategy": strategy,
            "check_results": check_results,
            "ticker_checks": ticker_checks
        },
        "tickers_data": tickers_data
    }

data = get_market_data()
db.reference('market').set(data['market'])
db.reference('signal').set(data['signal'])

if data['tickers_data']:
    db.reference('tickers_data').update(data['tickers_data'])

print("✅ 전체 업데이트 완료:", data['market']['last_updated'])
