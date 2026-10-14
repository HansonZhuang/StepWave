#!/usr/bin/env python3
"""模拟盘: 把每日 LLM 持仓建议在虚拟账户上自动成交, 积累 净值 vs 持有不动 的统计证据。

规则(防作弊):
  - 只执行"当日15:00前生成"的建议(按建议缓存文件mtime判定), 盘后补生成的方案不参与当日成交
  - 限价成交: 卖单 日内最高>=挂价 成交; 买单 日内最低<=挂价 且现金足够 成交; 不计手续费/滑点
  - 账户初始化 = 当前持仓列表按起始日昨收计价, 现金0; 基线 = 同样持仓冻结不动
  - 幂等: 同一交易日只结算一次

实盘门槛(写死在报告里): 累计>=20个交易日 且 累计跑赢持有不动 且 日胜率>50%, 才值得讨论实盘。

用法:
    ../.venv/bin/python paper_trader.py 2026-10-08   # 结算指定日
    ../.venv/bin/python paper_trader.py --stats      # 看累计统计
"""
import json
import os
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import evaluator  # noqa: E402
import portfolio  # noqa: E402

ACCT = os.path.join(HERE, "..", "data", "paper_account.json")
CACHE_DIR = os.path.join(HERE, "..", "data", "web_cache")


def _load():
    if os.path.exists(ACCT):
        with open(ACCT, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save(a):
    with open(ACCT, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)


def init_account(start_date):
    holds = portfolio.load_holdings()
    positions, miss = {}, []
    for h in holds:
        key = f"{h['market']}:{h['code']}"
        bar, prev_close = evaluator.day_bar(h["market"], h["code"], start_date)
        if prev_close is None:
            miss.append(key)
            continue
        positions[key] = {"shares": h["shares"], "init_price": prev_close}
    a = {"start_date": start_date, "cash": 0.0,
         "positions": {k: v["shares"] for k, v in positions.items()},
         "baseline": {k: v for k, v in positions.items()},
         "history": []}
    _save(a)
    if miss:
        print(f"警告: {miss} 无法取到{start_date}前收盘, 未纳入账户", file=sys.stderr)
    return a


def _advice_usable(market, code, d):
    """当日建议存在且在15:00前生成才可执行。返回 (orders, note)。"""
    cp = os.path.join(CACHE_DIR, f"holding_{market}_{code}_{d}.json")
    if not os.path.exists(cp):
        return [], "当日无建议"
    mtime = datetime.fromtimestamp(os.path.getmtime(cp))
    if mtime.date().isoformat() == d and mtime.hour >= 15:
        return [], f"建议生成于{mtime:%H:%M}(盘后), 不执行"
    with open(cp, encoding="utf-8") as f:
        return json.load(f)["advice"].get("orders", []), "ok"


def run_day(d=None):
    d = d or date.today().isoformat()
    a = _load() or init_account(d)
    if any(h["date"] == d for h in a["history"]):
        return {"date": d, "skip": "该日已结算"}

    fills, notes, closes = [], {}, {}
    for key in list(a["baseline"].keys()):
        market, code = key.split(":")
        bar, _prev = evaluator.day_bar(market, code, d)
        if not bar:
            notes[key] = "无当日K线(未开市?)"
            continue
        closes[key] = bar["close"]
        orders, note = _advice_usable(market, code, d)
        if note != "ok":
            notes[key] = note
            continue
        for o in orders:
            side, px = o.get("side"), float(o.get("price") or 0)
            n = int(o.get("shares") or 0)
            if px <= 0 or n <= 0:
                continue
            pos = a["positions"].get(key, 0)
            if side == "卖出" and bar["high"] >= px and pos > 0:
                n = min(n, pos)
                a["positions"][key] = pos - n
                a["cash"] = round(a["cash"] + n * px, 2)
                fills.append({"key": key, "side": "卖出", "shares": n, "price": px})
            elif side == "买入" and bar["low"] <= px:
                cost = n * px
                if a["cash"] >= cost:
                    a["positions"][key] = pos + n
                    a["cash"] = round(a["cash"] - cost, 2)
                    fills.append({"key": key, "side": "买入", "shares": n, "price": px})
                else:
                    notes[key] = f"现金不足, 跳过买入{n}@{px}"

    if not closes:
        return {"date": d, "skip": "所有标的均无当日K线"}

    equity = round(a["cash"] + sum(a["positions"].get(k, 0) * c
                                   for k, c in closes.items()), 2)
    bh = round(sum(v["shares"] * closes.get(k, v["init_price"])
                   for k, v in a["baseline"].items()), 2)
    entry = {"date": d, "equity": equity, "bh_equity": bh,
             "diff": round(equity - bh, 2), "cash": a["cash"],
             "fills": fills, "notes": notes}
    a["history"].append(entry)
    _save(a)
    return entry


def stats():
    a = _load()
    if not a or not a["history"]:
        return {"msg": "账户尚无结算记录"}
    hist = a["history"]
    day_moves = [(h2["diff"] - h1["diff"]) for h1, h2 in zip(hist, hist[1:])]
    day_moves.insert(0, hist[0]["diff"])
    wins = sum(1 for x in day_moves if x > 0)
    flats = sum(1 for x in day_moves if x == 0)
    init_val = sum(v["shares"] * v["init_price"] for v in a["baseline"].values())
    cur = hist[-1]
    ready = (len(hist) >= 20 and cur["diff"] > 0
             and wins > (len(hist) - flats) / 2)
    return {"start_date": a["start_date"], "初始市值": round(init_val, 2),
            "交易日数": len(hist),
            "当前净值": cur["equity"], "持有不动净值": cur["bh_equity"],
            "累计差额(建议-不动)": cur["diff"],
            "日胜/平/负": [wins, flats, len(hist) - wins - flats],
            "实盘门槛(>=20日&累计为正&胜率>50%)": "已达标" if ready else "未达标",
            "curve": [{"date": h["date"], "equity": h["equity"],
                       "bh": h["bh_equity"], "diff": h["diff"]} for h in hist]}


if __name__ == "__main__":
    if "--stats" in sys.argv:
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
    else:
        d = sys.argv[1] if len(sys.argv) > 1 else None
        print(json.dumps(run_day(d), ensure_ascii=False, indent=1))
