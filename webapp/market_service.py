#!/usr/bin/env python3
"""行情服务: K线+缠论结构JSON、新浪实时报价、指数概览。"""
import json
import os
import re
import sys
import time
from datetime import date, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import chan_analyzer  # noqa: E402
import fetch_stock  # noqa: E402

CACHE_DIR = os.path.join(HERE, "..", "data", "web_cache")
SINA_HQ = "https://hq.sinajs.cn/list="
HEADERS = {"Referer": "https://finance.sina.com.cn"}

_kline_cache = {}  # (market, code) -> (ts, payload)
KLINE_TTL = 1800


def quote_symbol(market, code):
    if market == "a":
        return fetch_stock.sina_symbol(code)   # 北交所返回 bj 前缀, 由腾讯源报价
    if market == "hk":
        return f"rt_hk{code}"
    return f"gb_{code.lower()}"


def get_kline(market, code, days=550, refresh=False):
    """返回 {bars: [...], chan: {...}} 并带30分钟内存缓存。"""
    key = (market, code)
    now = time.time()
    if not refresh and key in _kline_cache and now - _kline_cache[key][0] < KLINE_TTL:
        return _kline_cache[key][1]
    start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    rows, source = fetch_stock.fetch(code, start, end, market)
    bars = [{"date": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in rows]
    cbars = [chan_analyzer.Bar(b["date"], b["high"], b["low"], b["close"], i)
             for i, b in enumerate(bars)]
    payload = {"market": market, "code": code, "source": source,
               "bars": bars, "chan": chan_analyzer.analyze(cbars)}
    _kline_cache[key] = (now, payload)
    return payload


def _parse_quote(line):
    m = re.match(r'var hq_str_(\w+)="([^"]*)"', line)
    if not m:
        return None
    sym, body = m.group(1), m.group(2).split(",")
    try:
        if sym.startswith("sz") or sym.startswith("sh") or sym.startswith("bj"):
            return {"symbol": sym, "name": body[0], "price": float(body[3]),
                    "prev_close": float(body[2]), "time": f"{body[30]} {body[31]}"}
        if sym.startswith("rt_hk"):
            return {"symbol": sym, "name": body[1], "price": float(body[6]),
                    "prev_close": float(body[3]), "time": f"{body[17]} {body[18]}"}
        if sym.startswith("gb_"):
            return {"symbol": sym, "name": body[0], "price": float(body[1]),
                    "prev_close": float(body[26]), "time": body[3]}
        if sym.startswith("s_") or sym.startswith("int_"):
            return {"symbol": sym, "name": body[0], "price": float(body[1]),
                    "prev_close": float(body[1]) - float(body[2]), "time": ""}
    except (IndexError, ValueError):
        return None
    return None


def get_quotes(symbols):
    """symbols: 新浪格式(sz/sh/rt_hk/gb_/s_/int_)与北交所bj前缀混合均可。"""
    sina_syms = [s for s in symbols if not s.startswith("bj")]
    bj_syms = [s for s in symbols if s.startswith("bj")]
    out = []
    if sina_syms:
        r = requests.get(SINA_HQ + ",".join(sina_syms), headers=HEADERS, timeout=10)
        r.encoding = "gbk"
        for line in r.text.strip().splitlines():
            q = _parse_quote(line)
            if q:
                out.append(q)
    if bj_syms:
        r = requests.get("https://qt.gtimg.cn/q=" + ",".join(bj_syms), timeout=10)
        r.encoding = "gbk"
        for line in r.text.strip().splitlines():
            m = re.match(r"v_(\w+)=\"([^\"]*)\"", line)
            if not m:
                continue
            v = m.group(2).split("~")
            if len(v) > 31 and v[3]:
                t = v[30]
                out.append({"symbol": m.group(1), "name": v[1], "price": float(v[3]),
                            "prev_close": float(v[4]),
                            "time": f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}"
                                    if len(t) >= 12 else t})
    for q in out:
        q["change_pct"] = round((q["price"] / q["prev_close"] - 1) * 100, 2) \
            if q.get("prev_close") else 0.0
    return out


def index_context():
    """上证+纳指: 实时价 + 日线缠论结构摘要(供建议引擎)。"""
    quotes = {q["symbol"]: q for q in get_quotes(["s_sh000001", "int_nasdaq"])}
    ctx = {"quotes": quotes}
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000001")
        df = df.tail(300)
        cbars = [chan_analyzer.Bar(str(r["date"])[:10], float(r["high"]),
                                   float(r["low"]), float(r["close"]), i)
                 for i, (_, r) in enumerate(df.iterrows())]
        ctx["sh_chan"] = chan_analyzer.analyze(cbars)
    except Exception as e:
        ctx["sh_chan"] = {"error": type(e).__name__}
    try:
        import akshare as ak
        df = ak.index_us_stock_sina(symbol=".IXIC").tail(300)
        cbars = [chan_analyzer.Bar(str(r["date"])[:10], float(r["high"]),
                                   float(r["low"]), float(r["close"]), i)
                 for i, (_, r) in enumerate(df.iterrows())]
        ctx["ixic_chan"] = chan_analyzer.analyze(cbars)
    except Exception as e:
        ctx["ixic_chan"] = {"error": type(e).__name__}
    return ctx


if __name__ == "__main__":
    print(json.dumps(get_quotes(["sz002491", "rt_hk00700", "gb_aapl",
                                 "s_sh000001", "int_nasdaq"]),
                     ensure_ascii=False, indent=1))
    k = get_kline("a", "002491")
    print(k["source"], len(k["bars"]), "bars;",
          len(k["chan"]["zhongshu"]), "zhongshu")
    ctx = index_context()
    print("sh zhongshu:", len(ctx["sh_chan"].get("zhongshu", [])),
          "| ixic zhongshu:", len(ctx["ixic_chan"].get("zhongshu", [])))
