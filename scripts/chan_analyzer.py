#!/usr/bin/env python3
"""缠论结构分析器（纯标准库，无第三方依赖）

流程: K线包含合并 -> 顶底分型 -> 笔 -> 中枢划分 -> 三类买卖点提示 -> MACD背驰检查

用法:
    python3 chan_analyzer.py <csv文件>      # CSV列: date,open,high,low,close[,volume]
    python3 chan_analyzer.py --demo         # 用内置合成行情演示
"""
import csv
import math
import random
import sys
from dataclasses import dataclass, field


# ---------- 数据结构 ----------

@dataclass
class Bar:
    dt: str
    high: float
    low: float
    close: float
    orig_i: int          # 对应原始K线序号（合并后取区间末根）


@dataclass
class Fractal:
    kind: str            # 'top' / 'bottom'
    m_idx: int           # 合并K线序号（分型中间那根）
    dt: str
    price: float         # top取high, bottom取low
    orig_i: int


@dataclass
class Zhongshu:
    zg: float
    zd: float
    start_dt: str
    end_dt: str
    n_seg: int
    stroke_lo: int       # 构成中枢的首笔端点在strokes中的下标
    stroke_hi: int


# ---------- 读数据 ----------

def load_csv(path):
    bars = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f)):
            bars.append(Bar(row["date"], float(row["high"]), float(row["low"]),
                            float(row["close"]), i))
    return bars


def demo_bars(n=300, seed=42):
    """合成行情: 下跌 -> 盘整 -> 上涨 -> 冲高回落，便于观察中枢与买卖点。"""
    random.seed(seed)
    price, bars = 100.0, []
    drift = [-0.35] * (n // 4) + [0.0] * (n // 4) + [0.45] * (n // 4) + [-0.1] * (n - 3 * (n // 4))
    for i in range(n):
        price = max(5.0, price + drift[i] + random.gauss(0, 0.9))
        h = price + abs(random.gauss(0, 0.5))
        l = price - abs(random.gauss(0, 0.5))
        bars.append(Bar(f"D{i:03d}", round(h, 2), round(l, 2), round(price, 2), i))
    return bars


# ---------- 1. 包含关系合并 ----------

def merge_inclusion(bars):
    merged, direction = [], 1
    for b in bars:
        if not merged:
            merged.append(Bar(b.dt, b.high, b.low, b.close, b.orig_i))
            continue
        last = merged[-1]
        contains = (last.high >= b.high and last.low <= b.low) or \
                   (b.high >= last.high and b.low <= last.low)
        if contains:
            if direction >= 0:   # 向上处理: 高高取大
                last.high = max(last.high, b.high)
                last.low = max(last.low, b.low)
            else:                # 向下处理: 低低取小
                last.high = min(last.high, b.high)
                last.low = min(last.low, b.low)
            last.dt, last.close, last.orig_i = b.dt, b.close, b.orig_i
        else:
            direction = 1 if b.high > last.high else -1
            merged.append(Bar(b.dt, b.high, b.low, b.close, b.orig_i))
    return merged


# ---------- 2. 分型 ----------

def find_fractals(m):
    out = []
    for i in range(1, len(m) - 1):
        a, b, c = m[i - 1], m[i], m[i + 1]
        if b.high > a.high and b.high > c.high and b.low > a.low and b.low > c.low:
            out.append(Fractal("top", i, b.dt, b.high, b.orig_i))
        elif b.low < a.low and b.low < c.low and b.high < a.high and b.high < c.high:
            out.append(Fractal("bottom", i, b.dt, b.low, b.orig_i))
    return out


# ---------- 3. 笔 ----------

MIN_GAP = 4  # 顶底分型中间K线序号差>=4 => 分型不共用K线且有独立K线（常用简化标准）

def build_strokes(fractals):
    """返回笔端点序列（交替的顶/底分型）。"""
    pts = []
    for f in fractals:
        if not pts:
            pts.append(f)
            continue
        last = pts[-1]
        if f.kind == last.kind:
            # 同类分型取更极端者
            better = (f.kind == "top" and f.price > last.price) or \
                     (f.kind == "bottom" and f.price < last.price)
            if better:
                pts[-1] = f
        else:
            valid_price = (f.kind == "top" and f.price > last.price) or \
                          (f.kind == "bottom" and f.price < last.price)
            if f.m_idx - last.m_idx >= MIN_GAP and valid_price:
                pts.append(f)
            elif not valid_price and len(pts) >= 2:
                # 新极值反向吞掉上一端点: 回退
                prev = pts[-2]
                if f.kind == prev.kind and (
                        (f.kind == "top" and f.price > prev.price) or
                        (f.kind == "bottom" and f.price < prev.price)):
                    pts.pop()
                    pts[-1] = f
    return pts


# ---------- 4. 中枢（以笔代次级别走势的简化口径） ----------

def seg_range(p1, p2):
    return max(p1.price, p2.price), min(p1.price, p2.price)  # (high, low)


def find_zhongshu(strokes):
    zss, i = [], 0
    n_seg_total = len(strokes) - 1
    while i <= n_seg_total - 3:
        highs, lows = [], []
        for k in range(3):
            h, l = seg_range(strokes[i + k], strokes[i + k + 1])
            highs.append(h)
            lows.append(l)
        zg, zd = min(highs), max(lows)
        if zg > zd:
            j = i + 3
            while j < n_seg_total:
                h, l = seg_range(strokes[j], strokes[j + 1])
                if l <= zg and h >= zd:   # 与区间有重叠 => 延伸
                    j += 1
                else:
                    break
            zss.append(Zhongshu(round(zg, 2), round(zd, 2),
                                strokes[i].dt, strokes[j].dt, j - i, i, j))
            i = j
        else:
            i += 1
    return zss


# ---------- 5. 三类买卖点提示 ----------

def detect_points(strokes, zss):
    """基于中枢的简化买卖点扫描（提示性质，仅看三买/三卖与一买/一卖雏形）。"""
    signals = []
    for z in zss:
        hi = z.stroke_hi
        # 中枢结束后至少还需两个端点: 离开笔 + 回抽笔
        if hi + 2 >= len(strokes):
            continue
        leave, pull = strokes[hi + 1], strokes[hi + 2]
        if leave.kind == "top" and leave.price > z.zg and pull.price > z.zg:
            signals.append(("三买(提示)", pull.dt, pull.price,
                            f"离开笔突破ZG={z.zg}, 回抽低点{pull.price}未回中枢"))
        if leave.kind == "bottom" and leave.price < z.zd and pull.price < z.zd:
            signals.append(("三卖(提示)", pull.dt, pull.price,
                            f"离开笔跌破ZD={z.zd}, 反抽高点{pull.price}未回中枢"))
    return signals


# ---------- 6. MACD 与背驰 ----------

def ema(vals, n):
    k, out, e = 2 / (n + 1), [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def macd_hist(closes):
    dif = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    dea = ema(dif, 9)
    return [2 * (a - b) for a, b in zip(dif, dea)]


def check_divergence(bars, strokes):
    """比较最后两段同向笔的MACD柱面积, 新极值+面积萎缩 => 疑似背驰。"""
    if len(strokes) < 5:
        return None
    hist = macd_hist([b.close for b in bars])

    def area(p_from, p_to, sign):
        s = sum(h for h in hist[p_from.orig_i:p_to.orig_i + 1]
                if (h > 0) == (sign > 0))
        return abs(s)

    last = strokes[-1]
    sign = 1 if last.kind == "top" else -1
    cur_a, cur_b = strokes[-2], strokes[-1]       # 当前同向笔
    pre_a, pre_b = strokes[-4], strokes[-3]       # 上一同向笔
    new_extreme = (sign > 0 and cur_b.price > pre_b.price) or \
                  (sign < 0 and cur_b.price < pre_b.price)
    if not new_extreme:
        return None
    a_pre, a_cur = area(pre_a, pre_b, sign), area(cur_a, cur_b, sign)
    if a_pre > 0 and a_cur < a_pre:
        d = "顶" if sign > 0 else "底"
        return (f"疑似{d}背驰: 价格新{'高' if sign > 0 else '低'} "
                f"({pre_b.price} -> {cur_b.price}), MACD柱面积 "
                f"{a_pre:.2f} -> {a_cur:.2f} (萎缩{(1 - a_cur / a_pre) * 100:.0f}%)")
    return None


# ---------- 报告 ----------

def analyze(bars):
    """返回结构化分析结果(供网站/其他程序使用)。"""
    merged = merge_inclusion(bars)
    fr = find_fractals(merged)
    strokes = build_strokes(fr)
    zss = find_zhongshu(strokes)
    signals = detect_points(strokes, zss)
    div = check_divergence(bars, strokes)
    last_close = bars[-1].close

    pos = None
    if zss:
        z = zss[-1]
        pos = "above" if last_close > z.zg else ("below" if last_close < z.zd else "inside")

    return {
        "n_bars": len(bars),
        "last_dt": bars[-1].dt,
        "last_close": last_close,
        "strokes": [{"dt": p.dt, "kind": p.kind, "price": p.price, "orig_i": p.orig_i}
                    for p in strokes],
        "zhongshu": [{"zg": z.zg, "zd": z.zd, "start_dt": z.start_dt,
                      "end_dt": z.end_dt, "n_seg": z.n_seg} for z in zss],
        "signals": [{"type": s[0], "dt": s[1], "price": s[2], "note": s[3]}
                    for s in signals],
        "divergence": div,
        "position_vs_last_zs": pos,
    }


def report(bars):
    r = analyze(bars)
    strokes, zss = r["strokes"], r["zhongshu"]

    print(f"原始K线 {r['n_bars']} 根 | 笔端点 {len(strokes)} 个 | 中枢 {len(zss)} 个")

    print("\n== 笔(最近10个端点) ==")
    for p in strokes[-10:]:
        print(f"  {p['dt']}  {'顶' if p['kind'] == 'top' else '底'}  {p['price']}")

    print("\n== 中枢划分 ==")
    if not zss:
        print("  未发现有效中枢")
    for k, z in enumerate(zss, 1):
        print(f"  中枢{k}: [{z['zd']}, {z['zg']}]  {z['start_dt']} ~ {z['end_dt']}  含{z['n_seg']}段")

    print("\n== 买卖点提示 ==")
    if not r["signals"]:
        print("  无")
    for s in r["signals"]:
        print(f"  {s['type']}  @{s['dt']}  价格{s['price']}  ({s['note']})")

    print("\n== 背驰检查(最近两段同向笔) ==")
    print(f"  {r['divergence'] if r['divergence'] else '未检测到背驰'}")

    if zss:
        z = zss[-1]
        pos_cn = {"above": "上方", "below": "下方", "inside": "内部"}[r["position_vs_last_zs"]]
        print(f"\n当前收盘 {r['last_close']}, 位于最近中枢[{z['zd']}, {z['zg']}]的{pos_cn}")
    print("\n[提示] 本脚本为简化口径(以笔代线段构中枢), 结果仅供结构参考, 不构成投资建议。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    data = demo_bars() if sys.argv[1] == "--demo" else load_csv(sys.argv[1])
    report(data)
