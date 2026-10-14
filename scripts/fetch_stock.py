#!/usr/bin/env python3
"""行情拉取 + 缠论中枢分析一体化脚本（A股/港股/美股）

数据源:
  A股: 东方财富(stock_zh_a_hist) 优先, 连接失败自动切新浪(stock_zh_a_daily)
  港股: 新浪 stock_hk_daily    美股: 新浪 stock_us_daily
流程: 拉日线(前复权) -> 存 CSV 到 ../data/ -> 调用 chan_analyzer 出结构报告。

用法 (须用 stock_knowledge/.venv 里的 python):
    ../.venv/bin/python fetch_stock.py <A股6位代码> [起始YYYYMMDD] [结束YYYYMMDD]
    ../.venv/bin/python fetch_stock.py --hk <5位代码>  [起始] [结束]
    ../.venv/bin/python fetch_stock.py --us <美股代码> [起始] [结束]

示例:
    ../.venv/bin/python fetch_stock.py 002491
    ../.venv/bin/python fetch_stock.py --hk 00700
    ../.venv/bin/python fetch_stock.py --us AAPL 20250101 20261006
"""
import csv
import os
import sys
import time
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chan_analyzer  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def is_bj(code):
    return code.startswith(("4", "8", "92"))


def sina_symbol(code):
    if is_bj(code):
        return "bj" + code
    return ("sh" if code.startswith(("6", "9", "5")) else "sz") + code


def tx_kline(sym, start, end):
    """腾讯日K(前复权): 支持 sh/sz/bj 前缀, 返回统一行格式。"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,640,qfq"
    d = requests.get(url, timeout=15).json()
    node = d.get("data", {}).get(sym, {})
    k = node.get("qfqday") or node.get("day") or []
    s = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    # 腾讯行格式: [date, open, close, high, low, volume]
    return [(r[0], float(r[1]), float(r[3]), float(r[4]), float(r[2]), float(r[5]))
            for r in k if s <= r[0] <= e]


def sina_kline(sym, start, end):
    """新浪日K JSON接口(未复权), 北交所也覆盖。"""
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen=1023")
    d = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"},
                     timeout=15).json()
    s = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    return [(r["day"], float(r["open"]), float(r["high"]), float(r["low"]),
             float(r["close"]), float(r["volume"]))
            for r in (d or []) if s <= r["day"] <= e]


def fetch(symbol, start, end, market="a"):
    """返回统一行格式 [(date, open, high, low, close, volume), ...] 与数据源名。"""
    import akshare as ak

    def to_rows(df, date_col="date"):
        return [(str(r[date_col])[:10], r["open"], r["high"], r["low"],
                 r["close"], r["volume"]) for _, r in df.iterrows()]

    def clip(df):
        s = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        e = f"{end[:4]}-{end[4:6]}-{end[6:]}"
        return df[(df["date"].astype(str) >= s) & (df["date"].astype(str) <= e)]

    def fetch_or_fail(fn, what):
        try:
            df = fn()
        except Exception as e:
            raise ValueError(f"未取到{what} {symbol} 的日线: 代码可能不存在"
                             f"(个股代码才支持, ETF/指数/北交所暂不支持), "
                             f"或数据源暂时不可用") from e
        if df is None or df.empty:
            raise ValueError(f"{what} {symbol} 返回数据为空, 请检查代码或日期区间")
        return df

    if market == "hk":
        if not (symbol.isdigit() and len(symbol) == 5):
            raise ValueError(f"港股代码应为5位数字, 如 00700, 收到: {symbol}")
        df = fetch_or_fail(lambda: ak.stock_hk_daily(symbol=symbol, adjust="qfq"), "港股")
        return to_rows(clip(df)), "新浪(港股)"
    if market == "us":
        if not symbol.replace(".", "").isalpha():
            raise ValueError(f"美股代码应为字母, 如 AAPL / NVDA, 收到: {symbol}")
        df = fetch_or_fail(lambda: ak.stock_us_daily(symbol=symbol, adjust="qfq"), "美股")
        return to_rows(clip(df)), "新浪(美股)"

    if not (symbol.isdigit() and len(symbol) == 6):
        raise ValueError(f"A股代码应为6位数字, 如 600519 / 002491, 收到: {symbol}")
    for attempt in range(2):
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                    start_date=start, end_date=end, adjust="qfq")
            if df is not None and not df.empty:
                rows = [(str(r["日期"]), r["开盘"], r["最高"], r["最低"],
                         r["收盘"], r["成交量"]) for _, r in df.iterrows()]
                return rows, "东方财富"
        except Exception:
            time.sleep(2)
    if not is_bj(symbol):   # 新浪复权日线不含北交所
        try:
            df = ak.stock_zh_a_daily(symbol=sina_symbol(symbol), start_date=start,
                                     end_date=end, adjust="qfq")
            if df is not None and not df.empty:
                return to_rows(df), "新浪"
        except Exception:
            pass
    else:                   # 北交所: 新浪K线JSON接口有全量历史(未复权)
        try:
            rows = sina_kline(sina_symbol(symbol), start, end)
            if rows:
                return rows, "新浪K线(未复权)"
        except Exception:
            pass
    try:
        rows = tx_kline(sina_symbol(symbol), start, end)
        if rows:
            return rows, "腾讯"
    except Exception:
        pass
    raise ValueError(f"未取到A股 {symbol} 的日线(已尝试东财/新浪/腾讯三个数据源): "
                     f"代码可能不存在(仅支持个股, ETF/指数暂不支持), 或数据源暂时限流, 请稍后重试")


def save_csv(rows, symbol):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{symbol}_daily.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    return path


def main():
    args = sys.argv[1:]
    market = "a"
    if args and args[0] in ("--hk", "--us"):
        market = args.pop(0)[2:]
    if not args:
        print(__doc__)
        return 1
    symbol = args[0]
    start = args[1] if len(args) > 1 else \
        (date.today() - timedelta(days=550)).strftime("%Y%m%d")
    end = args[2] if len(args) > 2 else date.today().strftime("%Y%m%d")

    label = {"a": "A股", "hk": "港股", "us": "美股"}[market]
    print(f"拉取 {label} {symbol} 日线(前复权) {start} ~ {end} ...")
    rows, source = fetch(symbol, start, end, market)
    path = save_csv(rows, f"{market}_{symbol}" if market != "a" else symbol)
    print(f"数据源: {source} | 已保存 {len(rows)} 根K线 -> {path}")

    print(f"\n{'=' * 20} 缠论结构报告: {label} {symbol} {'=' * 20}")
    chan_analyzer.report(chan_analyzer.load_csv(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
