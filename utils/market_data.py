import requests
import datetime

# Yahoo Finance は無料・APIキー不要で Gold 先物（GC=F）を取得できる
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_gold_price(api_key: str = None) -> dict:
    """Gold先物（GC=F）の現在価格を Yahoo Finance から取得"""
    resp = requests.get(YAHOO_URL, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    meta = data["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice", 0)
    prev_close = meta.get("previousClose", 0)
    change = price - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0

    return {
        "price": round(price, 2),
        "bid": round(price - 0.3, 2),
        "ask": round(price + 0.3, 2),
        "change": round(change, 2),
        "change_pct": f"{change_pct:+.2f}%",
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def get_gold_intraday(api_key: str = None, interval: str = "1h") -> dict:
    """Gold先物の1時間足データを Yahoo Finance から取得"""
    params = {"interval": interval, "range": "2d"}
    resp = requests.get(YAHOO_URL, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    result = data["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quotes = result["indicators"]["quote"][0]
    opens   = quotes.get("open", [])
    highs   = quotes.get("high", [])
    lows    = quotes.get("low", [])
    closes  = quotes.get("close", [])

    candles = []
    for i in range(len(timestamps) - 1, max(len(timestamps) - 11, -1), -1):
        if closes[i] is None:
            continue
        dt = datetime.datetime.fromtimestamp(timestamps[i]).strftime("%m/%d %H:%M")
        candles.append({
            "time":  dt,
            "open":  round(opens[i] or 0, 2),
            "high":  round(highs[i] or 0, 2),
            "low":   round(lows[i] or 0, 2),
            "close": round(closes[i], 2),
        })

    if not candles:
        return {"candles": [], "trend": "不明", "recent_high": 0, "recent_low": 0, "latest_close": 0}

    valid_closes = [c["close"] for c in candles if c["close"]]
    trend = "上昇" if (valid_closes[0] > valid_closes[-1]) else "下落"

    return {
        "candles": candles,
        "trend": trend,
        "recent_high": max(c["high"] for c in candles),
        "recent_low":  min(c["low"]  for c in candles if c["low"] > 0),
        "latest_close": candles[0]["close"],
    }


def build_market_summary(api_key: str = None) -> str:
    """AI分析用のマーケットサマリー文字列を生成"""
    lines = ["【現在のGold相場データ（GC=F先物）】"]
    try:
        p = get_gold_price()
        lines.append(f"現在価格: ${p['price']:,.2f}")
        lines.append(f"前日比: {p['change']:+,.2f} ({p['change_pct']})")
        lines.append(f"取得時刻: {p['updated']}")
    except Exception as e:
        lines.append(f"現在価格: 取得失敗 ({e})")

    try:
        d = get_gold_intraday()
        lines.append(f"直近トレンド(1h足): {d['trend']}")
        lines.append(f"直近高値: ${d['recent_high']:,.2f}")
        lines.append(f"直近安値: ${d['recent_low']:,.2f}")
        if d["candles"]:
            c = d["candles"][0]
            lines.append(
                f"直近足: O:{c['open']:,.2f} H:{c['high']:,.2f} "
                f"L:{c['low']:,.2f} C:{c['close']:,.2f}"
            )
    except Exception as e:
        lines.append(f"チャートデータ: 取得失敗 ({e})")

    return "\n".join(lines)
