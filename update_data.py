import yfinance as yf
import firebase_admin
from firebase_admin import credentials, db
import json
import os
import requests
from datetime import datetime
import pytz

# Firebase 인증
cred_dict = json.loads(os.environ['FIREBASE_CREDENTIALS'])
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred, {
    'databaseURL': os.environ['FIREBASE_DB_URL']
})

def get_fear_greed():
    """Alternative.me API - 무료, 키 불필요"""
    try:
        res = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10
        )
        data = res.json()
        val = int(data['data'][0]['value'])
        status = data['data'][0]['value_classification']
        # 한국어 변환
        status_map = {
            "Extreme Fear": "극도의 공포",
            "Fear": "공포",
            "Neutral": "중립",
            "Greed": "탐욕",
            "Extreme Greed": "극도의 탐욕"
        }
        return val, status_map.get(status, status)
    except Exception as e:
        print(f"공포탐욕 API 실패: {e}")
        return None, None

def get_market_data():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea).strftime('%Y-%m-%d %H:%M')

    # 주가 데이터
    fngu = yf.Ticker("FNGU")
    soxl = yf.Ticker("SOXL")
    sp500 = yf.Ticker("^GSPC")
    nasdaq = yf.Ticker("^IXIC")
    vix = yf.Ticker("^VIX")

    fngu_info = fngu.fast_info
    soxl_info = soxl.fast_info
    sp_info = sp500.fast_info
    nq_info = nasdaq.fast_info
    vix_info = vix.fast_info

    # FNGU
    fngu_price = round(fngu_info.last_price, 2)
    fngu_change = round((fngu_price - fngu_info.previous_close) / fngu_info.previous_close * 100, 2)
    fngu_hist = fngu.history(period="ytd")
    fngu_ytd = round((fngu_price - fngu_hist['Close'].iloc[0]) / fngu_hist['Close'].iloc[0] * 100, 2)

    # SOXL
    soxl_price = round(soxl_info.last_price, 2)
    soxl_hist = soxl.history(period="ytd")
    soxl_ytd = round((soxl_price - soxl_hist['Close'].iloc[0]) / soxl_hist['Close'].iloc[0] * 100, 2)

    # S&P500
    sp_price = round(sp_info.last_price, 2)
    sp_change = round((sp_price - sp_info.previous_close) / sp_info.previous_close * 100, 2)
    sp_hist = sp500.history(period="ytd")
    sp_ytd = round((sp_price - sp_hist['Close'].iloc[0]) / sp_hist['Close'].iloc[0] * 100, 2)

    # NASDAQ
    nq_price = int(nq_info.last_price)
    nq_hist = nasdaq.history(period="ytd")
    nq_ytd = round((nq_price - nq_hist['Close'].iloc[0]) / nq_hist['Close'].iloc[0] * 100, 2)

    # VIX
    vix_price = round(vix_info.last_price, 2)

    # 공포탐욕 - Alternative.me 실시간
    fear, fear_status = get_fear_greed()
    if fear is None:
        # API 실패시 VIX 기반 근사치
        if vix_price >= 40: fear, fear_status = 10, "극도의 공포"
        elif vix_price >= 30: fear, fear_status = 22, "극도의 공포"
        elif vix_price >= 20: fear, fear_status = 38, "공포"
        else: fear, fear_status = 55, "중립"

    # 매수 체크리스트
    checks = 0
    if fear <= 25: checks += 1
    if vix_price >= 30: checks += 1
    if sp_ytd <= -10: checks += 1
    if fngu_ytd <= -20: checks += 1
    if soxl_ytd <= -20: checks += 1
    if nq_ytd <= -15: checks += 1

    buy_readiness = round(checks / 6 * 100)

    if buy_readiness >= 60:
        fngu_status = "강력 매수 구간"
        strategy = "강력 매수 시작"
    elif buy_readiness >= 30:
        fngu_status = "강력 매수 구간"
        strategy = "부분 매수 + 관망"
    else:
        fngu_status = "관망 구간"
        strategy = "관망 유지"

    return {
        "market": {
            "fngu_price": fngu_price,
            "fngu_change": fngu_change,
            "fngu_ytd": fngu_ytd,
            "soxl_price": soxl_price,
            "soxl_ytd": soxl_ytd,
            "fear_greed": fear,
            "fear_greed_status": fear_status,
            "vix": vix_price,
            "sp500": sp_price,
            "sp500_change": sp_change,
            "sp500_ytd": sp_ytd,
            "nasdaq": nq_price,
            "nasdaq_ytd": nq_ytd,
            "last_updated": now
        },
        "signal": {
            "fngu_status": fngu_status,
            "buy_readiness": buy_readiness,
            "checks": checks,
            "strategy": strategy
        }
    }

data = get_market_data()
db.reference('/').set(data)
print("✅ 업데이트 완료:", data['market']['last_updated'])
print("공포탐욕:", data['market']['fear_greed'], data['market']['fear_greed_status'])
print("매수준비도:", data['signal']['buy_readiness'], "%", f"({data['signal']['checks']}/6)")
