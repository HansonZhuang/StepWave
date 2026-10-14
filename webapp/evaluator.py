#!/usr/bin/env python3
"""收盘复盘评估器: 模拟"执行当日建议" vs "持有不动"的当日盈亏, 出报告并沉淀教训。

逻辑:
  - 读当日各持仓的建议缓存 holding_<m>_<c>_<date>.json (由 portfolio.advise_holding 生成)
  - 拉当日实际 OHLC, 用限价单规则模拟成交: 买单在 日内最低<=挂价 时成交, 卖单在 日内最高>=挂价 时成交
  - 对比两种当日盈亏: 执行建议(成交按挂单价) vs 不动(股数*(收盘-昨收))
  - 总差额<0 时调用 LLM 总结教训, 追加到 ../knowledge/lessons.md 作为后续建议的先验

用法:
    ../.venv/bin/python evaluator.py            # 复盘今天
    ../.venv/bin/python evaluator.py 2026-10-08 # 复盘指定日
建议 crontab: 工作日 15:05 自动跑。
"""
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm  # noqa: E402
import market_service  # noqa: E402
import portfolio  # noqa: E402

CACHE_DIR = os.path.join(HERE, "..", "data", "web_cache")
LESSONS = os.path.join(HERE, "..", "knowledge", "lessons.md")


def _today_bar_from_quote(market, code):
    """日线数据源缺当日K线时, 从实时报价合成当日bar(仅A股)。返回 (bar, prev_close)。"""
    if market != "a":
        return None, None
    import requests
    sym = market_service.quote_symbol(market, code)
    try:
        if sym.startswith("bj"):
            r = requests.get("https://qt.gtimg.cn/q=" + sym, timeout=10)
            r.encoding = "gbk"
            v = r.text.split('"')[1].split("~")
            bar = {"date": date.today().isoformat(), "open": float(v[5]),
                   "high": float(v[33]), "low": float(v[34]), "close": float(v[3])}
            return bar, float(v[4])
        r = requests.get(market_service.SINA_HQ + sym,
                         headers=market_service.HEADERS, timeout=10)
        r.encoding = "gbk"
        v = r.text.split('"')[1].split(",")
        bar = {"date": date.today().isoformat(), "open": float(v[1]),
               "high": float(v[4]), "low": float(v[5]), "close": float(v[3])}
        return bar, float(v[2])
    except Exception:
        return None, None


def day_bar(market, code, d):
    k = market_service.get_kline(market, code, refresh=True)
    bars = k["bars"]
    for i, b in enumerate(bars):
        if b["date"] == d:
            prev_close = bars[i - 1]["close"] if i > 0 else b["open"]
            return b, prev_close
    if d == date.today().isoformat():
        bar, prev_close = _today_bar_from_quote(market, code)
        if bar and bar["high"] > 0:
            return bar, prev_close
    return None, None


def simulate(advice, bar, prev_close, shares):
    """返回 (hold_pl, plan_pl, fills)。估值基准: 开盘前持仓按昨收计价。"""
    hold_pl = round(shares * (bar["close"] - prev_close), 2)
    cur, cash, fills = shares, 0.0, []
    for o in advice.get("orders", []):
        side, px = o.get("side"), float(o.get("price") or 0)
        n = int(o.get("shares") or 0)
        if px <= 0 or n <= 0:
            continue
        if side == "卖出" and bar["high"] >= px:
            n = min(n, cur)
            cur -= n
            cash += n * px
            fills.append({"side": "卖出", "shares": n, "price": px, "filled": True})
        elif side == "买入" and bar["low"] <= px:
            cur += n
            cash -= n * px
            fills.append({"side": "买入", "shares": n, "price": px, "filled": True})
        else:
            fills.append({"side": side, "shares": n, "price": px, "filled": False})
    plan_pl = round(cur * bar["close"] + cash - shares * prev_close, 2)
    return hold_pl, plan_pl, fills


LESSON_SYSTEM = (
    "你是投资复盘分析师。当日按模型建议执行的结果不如持有不动, 请基于给定数据总结1-2条"
    "可复用的教训规则(各50字内), 规则要具体可执行(什么情境下应如何调整建议), "
    '不要空话。严格输出JSON: {"lessons": ["规则1", "规则2"]}'
)


def run(d=None):
    d = d or date.today().isoformat()
    holdings = portfolio.load_holdings()
    rows, total_hold, total_plan = [], 0.0, 0.0
    for h in holdings:
        cp = os.path.join(CACHE_DIR, f"holding_{h['market']}_{h['code']}_{d}.json")
        if not os.path.exists(cp):
            rows.append({"code": h["code"], "skip": "当日无建议记录"})
            continue
        with open(cp, encoding="utf-8") as f:
            rec = json.load(f)
        bar, prev_close = day_bar(h["market"], h["code"], d)
        if not bar:
            rows.append({"code": h["code"], "skip": f"{d}无K线(未开市?)"})
            continue
        hold_pl, plan_pl, fills = simulate(rec["advice"], bar, prev_close, h["shares"])
        total_hold += hold_pl
        total_plan += plan_pl
        rows.append({"code": h["code"], "name": rec.get("name", h["code"]),
                     "shares": h["shares"], "prev_close": prev_close,
                     "ohlc": [bar["open"], bar["high"], bar["low"], bar["close"]],
                     "action": rec["advice"].get("action"),
                     "fills": fills, "hold_pl": hold_pl, "plan_pl": plan_pl,
                     "diff": round(plan_pl - hold_pl, 2)})

    evaluated = [r for r in rows if "skip" not in r]
    diff = round(total_plan - total_hold, 2)
    verdict = ("当日建议有效(优于持有不动)" if diff > 0 else
               "当日建议无增益(等同持有不动)" if diff == 0 else
               "当日建议劣于持有不动, 已总结教训")

    lessons = []
    if evaluated and diff < 0:
        try:
            out = llm.chat_json(json.dumps({"复盘明细": evaluated}, ensure_ascii=False),
                                system=LESSON_SYSTEM, model=llm.SMART_MODEL,
                                max_tokens=800)
            lessons = out.get("lessons", [])
            if lessons:
                with open(LESSONS, "a", encoding="utf-8") as f:
                    for ls in lessons:
                        f.write(f"- {d} | 建议劣于不动{diff}元 | {ls}\n")
        except Exception as e:
            lessons = [f"教训生成失败: {type(e).__name__}"]

    report = {"date": d, "rows": rows,
              "total_hold_pl": round(total_hold, 2),
              "total_plan_pl": round(total_plan, 2),
              "diff": diff, "verdict": verdict, "new_lessons": lessons,
              "note": "盈亏为日内口径(持仓按昨收计价), 模拟限价成交未计手续费/滑点"}
    with open(os.path.join(CACHE_DIR, f"report_{d}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


if __name__ == "__main__":
    r = run(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(r, ensure_ascii=False, indent=1))
