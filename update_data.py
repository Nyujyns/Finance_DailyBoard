#!/usr/bin/env python3
"""
Finance Daily Board — 자동 데이터 업데이트 스크립트
yfinance로 시세를 가져와 index.html의 AUTO-DATA 블록을 교체합니다.
"""

import re, json, sys
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd


# ── 심볼 ──────────────────────────────────────────────
MARKET = {
    'SP500':  '^GSPC',
    'NASDAQ': '^IXIC',
    'DXY':    'DX-Y.NYB',
    'WTI':    'CL=F',
    'VIX':    '^VIX',
    'VXN':    '^VXN',
    'BX':     'BX',
    'OWL':    'OWL',
    'QQQ':    'QQQ',
    'SPY':    'SPY',
    'SOXX':   'SOXX',
}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}")


def calc_rsi(prices, period=14):
    """Wilder RSI"""
    if len(prices) < period + 1:
        return 50.0
    d = prices.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    avg_up = up.ewm(alpha=1/period, min_periods=period).mean()
    avg_dn = dn.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_up / avg_dn.replace(0, 1e-10)
    rsi = 100 - 100 / (1 + rs)
    return float(rsi.iloc[-1])


# ── 데이터 수집 ───────────────────────────────────────
def fetch_all():
    log("데이터 수집 시작")
    end = datetime.now()
    start = end - timedelta(days=45)       # 30거래일 확보
    data = {}

    for key, sym in MARKET.items():
        try:
            log(f"  {key} ({sym})")
            hist = yf.Ticker(sym).history(start=start, end=end)
            if hist.empty:
                raise ValueError("empty")
            close = hist['Close']
            cur  = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) > 1 else cur
            chg  = (cur - prev) / prev * 100 if prev else 0
            data[key] = dict(
                current=round(cur, 2),
                change=round(chg, 2),
                prices=[round(float(v), 2) for v in close.values[-30:]],
                dates=[d.strftime('%m/%d') for d in close.index[-30:]],
            )
            log(f"    → {cur:,.2f}  ({chg:+.2f}%)")
        except Exception as e:
            log(f"    ✗ {key} 실패: {e}")

    # RSI 계산
    for key in ('SP500', 'QQQ', 'SOXX'):
        if key in data and len(data[key]['prices']) >= 15:
            try:
                rsi = calc_rsi(pd.Series(data[key]['prices']))
                data[f'{key}_RSI'] = round(rsi, 1)
                log(f"  RSI({key}) = {rsi:.1f}")
            except Exception as e:
                log(f"  RSI({key}) 실패: {e}")

    # QQQ/SPY 비율
    if 'QQQ' in data and 'SPY' in data:
        try:
            ratio = data['QQQ']['current'] / data['SPY']['current']
            data['QQQ_SPY'] = round(ratio, 3)
            log(f"  QQQ/SPY = {ratio:.3f}")
        except Exception:
            pass

    return data


# ── HTML 교체 ─────────────────────────────────────────
def arr(vals):
    return '[' + ','.join(str(v) for v in vals) + ']'

def darr(vals):
    return "['" + "','".join(vals) + "']"

def replace_block(html, tag, content):
    """AUTO-{tag}-START ~ AUTO-{tag}-END 블록을 교체"""
    pat = re.compile(
        rf'(// AUTO-{tag}-START\n).*?(// AUTO-{tag}-END)',
        re.DOTALL,
    )
    return pat.sub(rf'\g<1>{content}\n\g<2>', html)


def update_html(html, data):
    log("HTML 업데이트 시작")

    # ── 시장 지수 차트 데이터 ──
    dates = data.get('SP500', {}).get('dates', [])
    market_lines = []
    market_lines.append(f"const marketDates = {darr(dates)};")
    for key, js in [('SP500','sp500Data'),('NASDAQ','nasdaqData'),('DXY','dxyData'),('WTI','wtiData')]:
        prices = data.get(key, {}).get('prices', [])
        market_lines.append(f"const {js} = {arr(prices)};")
    html = replace_block(html, 'MARKET', '\n'.join(market_lines))

    # ── 현재값 ──
    cv = {}
    mapping = {
        'sp500': 'SP500', 'nasdaq': 'NASDAQ', 'dxy': 'DXY', 'wti': 'WTI',
        'vix': 'VIX', 'vxn': 'VXN', 'bx': 'BX', 'owl': 'OWL',
    }
    for js_key, data_key in mapping.items():
        if data_key in data:
            cv[js_key] = data[data_key]['current']
            cv[f'{js_key}_chg'] = data[data_key]['change']
    html = replace_block(html, 'CURRENT',
        f"const currentValues = {json.dumps(cv)};")

    # ── 시그널 (API 자동 + 수동 지표는 기존값 유지) ──
    m = re.search(r'const signalValues = (\{.*?\});', html, re.DOTALL)
    old_sv = json.loads(m.group(1)) if m else {}

    sv = dict(old_sv)
    if 'SP500_RSI' in data:  sv['rsi_sp']   = data['SP500_RSI']
    if 'QQQ_RSI'   in data:  sv['rsi_qqq']  = data['QQQ_RSI']
    if 'SOXX_RSI'  in data:  sv['rsi_soxx'] = data['SOXX_RSI']
    if 'QQQ_SPY'   in data:  sv['qqq_spy']  = data['QQQ_SPY']
    if 'VXN' in data:        sv['vxn']      = data['VXN']['current']

    html = replace_block(html, 'SIGNAL',
        f"const signalValues = {json.dumps(sv)};")

    log("HTML 업데이트 완료")
    return html


# ── main ──────────────────────────────────────────────
def main():
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            html = f.read()
    except FileNotFoundError:
        log("ERROR: index.html 없음")
        return 1

    data = fetch_all()
    if not data:
        log("ERROR: 데이터 수집 실패")
        return 1

    html = update_html(html, data)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    log("index.html 저장 완료 ✓")
    return 0


if __name__ == '__main__':
    sys.exit(main())
