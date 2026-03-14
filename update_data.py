import yfinance as yf
import firebase_admin
from firebase_admin import credentials, db
import json
import os
from datetime import datetime
import pytz

# Firebase 인증
cred_dict = json.loads(os.environ['FIREBASE_CREDENTIALS'])
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred, {
    'databaseURL': os.environ['FIREBASE_DB_URL']
})

# 데이터 가져오기
def get_market_data():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea).strftime('%Y-%m-%d %H:%M')

    fngu = yf.Ticker("FNGU")
    sp500 = yf.Ticker("^GSPC")
    nasdaq = yf.Ticker("^IXIC")
    vix = yf.Ticker("^VIX")

    fngu_info = fngu.fast_info
    sp_info = sp500.fast_info
    nq_info = nasdaq.fast_info
    vix_info = vix.fast_info

    fngu_price = round(fngu_info.last_price, 2)
    fngu_prev = fngu_info.previous_close
    fngu_change = round((fngu_price - fngu_prev) / fngu_prev * 100, 2)

    fngu_hist = fngu.history(period="ytd")
    fngu_ytd = round((fngu_price - fngu_hist['Close'].iloc[0]) / fngu_hist['Close'].iloc[0] * 100, 2)

    sp_price = round(sp_info.last_price, 2)
    sp_change = round((sp_price - sp_info.previous_close) / sp_info.previous_close * 100, 2)
    nq_price = int(nq_info.last_price)
    vix_price = round(vix_info.last_price, 2)

    # 공포탐욕 지수 (VIX 기반 근사치)
    if vix_price >= 40:
        fear = 10
        fear_status = "극도의 공포"
    elif vix_price >= 30:
        fear = 20
        fear_status = "극도의 공포"
    elif vix_price >= 20:
        fear = 35
        fear_status = "공포"
    else:
        fear = 55
        fear_status = "중립"

    # 매수 신호 판단
    checks = 0
    if fear <= 25: checks += 1
    if vix_price >= 30: checks += 1
    if fngu_ytd <= -20: checks += 1
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
            "fear_greed": fear,
            "fear_greed_status": fear_status,
            "vix": vix_price,
            "sp500": sp_price,
            "sp500_change": sp_change,
            "nasdaq": nq_price,
            "last_updated": now
        },
        "signal": {
            "fngu_status": fngu_status,
            "buy_readiness": buy_readiness,
            "strategy": strategy
        }
    }

data = get_market_data()
db.reference('/').set(data)
print("✅ Firebase 업데이트 완료:", data['market']['last_updated'])
