#!/usr/bin/env python3
"""谷底观察窗: 用可计算量框出"底部信号密集的时间范围", 而非预言日期。

三个独立依据(全部确定性计算, 可复现):
  1. 本股历史下跌笔持续天数的分位数(P25/P50/P75) vs 当前下跌笔已持续天数
  2. 斐波那契时间窗: 自重要顶点起第 8/13/21/34/55 根K线
  3. ABC时间比例: C浪持续时间 ≈ A浪的 1.0x / 1.618x

输出 = 时间观察窗 + 价格底候选(中枢下沿/斐波回撤) + 确认信号要求(背驰/底分型)。
诚实声明: 窗口只提示"概率密集区", 入场必须等确认信号, 不构成投资建议。

用法: ../.venv/bin/python bottom_window.py a 920725
"""
import json
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import market_service  # noqa: E402

FIB_SEQ = (8, 13, 21, 34, 55, 89)


def _q(sorted_vals, p):
    if not sorted_vals:
        return None
    i = (len(sorted_vals) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo), 1)


def _future_date(last_dt, extra_tdays):
    """最后一根K线日期 + N个交易日 ≈ 日历日(×7/5), 近似值。"""
    d = date.fromisoformat(last_dt[:10])
    return (d + timedelta(days=round(extra_tdays * 7 / 5))).isoformat()


def analyze(market, code):
    k = market_service.get_kline(market, code)
    bars, chan = k["bars"], k["chan"]
    strokes = chan["strokes"]
    if len(strokes) < 6:
        return {"error": "笔端点不足6个, 无法做统计"}
    n = len(bars)
    last_dt = bars[-1]["date"]

    # --- 1. 历史下跌笔持续天数分位 ---
    down_durs = [strokes[i + 1]["orig_i"] - strokes[i]["orig_i"]
                 for i in range(len(strokes) - 1)
                 if strokes[i]["kind"] == "top" and strokes[i + 1]["orig_i"] > strokes[i]["orig_i"]]
    down_durs.sort()
    q25, q50, q75 = _q(down_durs, .25), _q(down_durs, .5), _q(down_durs, .75)

    last = strokes[-1]
    in_down = last["kind"] == "top"          # 最后端点是顶 => 当前走在下跌笔上
    elapsed = (n - 1) - last["orig_i"] if in_down else None

    stroke_window = None
    if in_down and q25 is not None:
        stroke_window = {
            "当前下跌笔起点": last["dt"], "已持续交易日": elapsed,
            "历史下跌笔P25/P50/P75": [q25, q50, q75], "样本数": len(down_durs),
            "窗口(交易日)": [max(0, round(q50 - elapsed)), max(0, round(q75 - elapsed))],
            "窗口(日历日期)": [_future_date(last_dt, max(0, q50 - elapsed)),
                            _future_date(last_dt, max(0, q75 - elapsed))],
            "解读": ("已超P75, 处于历史尾部区间, 任何背驰都值得重视"
                     if elapsed > q75 else
                     f"按历史分布, 本笔在未来{max(0, round(q50 - elapsed))}~"
                     f"{max(0, round(q75 - elapsed))}个交易日内结束的概率密集")}

    # --- 2. 斐波那契时间窗(自近120根内最高的顶分型起) ---
    recent_tops = [p for p in strokes if p["kind"] == "top" and p["orig_i"] >= n - 120]
    fib_window = None
    if recent_tops:
        major = max(recent_tops, key=lambda p: p["price"])
        since = (n - 1) - major["orig_i"]
        nxt = [f for f in FIB_SEQ if f >= since][:2]
        fib_window = {"重要顶": f"{major['dt']} @ {major['price']}",
                      "距今交易日": since,
                      "将到达的斐波时间位": [
                          {"第N根K线": f, "约在": _future_date(last_dt, f - since)}
                          for f in nxt]}

    # --- 3. ABC 时间比例(若顶后可辨识 A底-B顶 结构) ---
    abc = None
    if recent_tops:
        major = max(recent_tops, key=lambda p: p["price"])
        after = [p for p in strokes if p["orig_i"] > major["orig_i"]]
        if len(after) >= 2 and after[0]["kind"] == "bottom" and after[1]["kind"] == "top":
            a_dur = after[0]["orig_i"] - major["orig_i"]
            b_top = after[1]
            c_elapsed = (n - 1) - b_top["orig_i"]
            abc = {"A浪": f"{major['dt']} -> {after[0]['dt']} ({a_dur}交易日)",
                   "B浪顶": b_top["dt"], "C浪已走": c_elapsed,
                   "C=1.0A 到期": _future_date(last_dt, max(0, a_dur - c_elapsed)),
                   "C=1.618A 到期": _future_date(last_dt, max(0, round(a_dur * 1.618) - c_elapsed))}

    # --- 价格底候选 ---
    price_floors = []
    cur = chan["last_close"]
    for z in chan["zhongshu"][::-1]:
        if z["zd"] < cur:
            price_floors.append({"依据": f"中枢下沿({z['start_dt']}~{z['end_dt']})", "价位": z["zd"]})
            break
    bottoms_all = [p["price"] for p in strokes if p["kind"] == "bottom"]
    tops_all = [p["price"] for p in strokes if p["kind"] == "top"]
    if bottoms_all and tops_all:
        lo, hi = min(bottoms_all), max(tops_all)
        for r in (0.618, 0.786):
            lv = round(hi - (hi - lo) * r, 2)
            if lv < cur:
                price_floors.append({"依据": f"{r}回撤位(全段{lo}~{hi})", "价位": lv})

    return {"market": market, "code": code, "last_dt": last_dt, "last_close": cur,
            "当前状态": "下跌笔进行中" if in_down else "上行/反弹笔进行中(谷底窗暂不适用)",
            "时间窗_历史笔分位": stroke_window,
            "时间窗_斐波那契": fib_window,
            "时间窗_ABC比例": abc,
            "价格底候选": price_floors,
            "确认信号要求": "窗内出现 日线MACD底背驰 或 底分型+缩量 才视为底部候选成立; 窗口与价位均为概率参考",
            "divergence_now": chan["divergence"],
            "声明": "时间窗=概率密集区而非预测日期; 不构成投资建议"}


if __name__ == "__main__":
    print(json.dumps(analyze(sys.argv[1] if len(sys.argv) > 1 else "a",
                             sys.argv[2] if len(sys.argv) > 2 else "920725"),
                     ensure_ascii=False, indent=1))
