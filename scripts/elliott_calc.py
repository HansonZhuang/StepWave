#!/usr/bin/env python3
"""波浪理论斐波那契测算器（纯标准库）

给定已确认的浪端点价格, 输出后续浪的常用斐波那契目标位。

用法:
    python3 elliott_calc.py <浪起点> <浪1终点> [浪2终点] [浪3终点] [浪4终点]

示例:
    python3 elliott_calc.py 100 130            # 只有浪1 -> 测浪2回撤位
    python3 elliott_calc.py 100 130 112        # 有浪2   -> 测浪3目标位
    python3 elliott_calc.py 100 130 112 160 142  # 测浪5目标位
"""
import sys

FIB_RETR = (0.382, 0.5, 0.618, 0.786)
FIB_EXT = (1.0, 1.382, 1.618, 2.0, 2.618)


def fmt(v):
    return f"{v:,.2f}"


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    p = [float(x) for x in argv]
    p0, p1 = p[0], p[1]
    up = p1 > p0
    w1 = abs(p1 - p0)
    sign = 1 if up else -1
    print(f"趋势方向: {'上涨' if up else '下跌'} | 浪1幅度: {fmt(w1)}")

    print("\n== 浪2 回撤目标(回撤浪1) ==")
    for r in FIB_RETR:
        print(f"  {r:.3f}: {fmt(p1 - sign * w1 * r)}")
    print(f"  [铁律] 浪2不得越过浪起点 {fmt(p0)}")

    if len(p) >= 3:
        p2 = p[2]
        print("\n== 浪3 目标(自浪2终点投射浪1倍数) ==")
        for r in FIB_EXT:
            print(f"  {r:.3f}x浪1: {fmt(p2 + sign * w1 * r)}")
        print("  [铁律] 浪3不能是1/3/5中最短的一浪")

    if len(p) >= 4:
        p3 = p[3]
        w3 = abs(p3 - p[2])
        print(f"\n浪3实际幅度: {fmt(w3)} ({w3 / w1:.3f}x浪1)")
        print("\n== 浪4 回撤目标(回撤浪3) ==")
        for r in (0.236, 0.382, 0.5):
            print(f"  {r:.3f}: {fmt(p3 - sign * w3 * r)}")
        print(f"  [铁律] 现金市场浪4不应进入浪1价格区域(勿越过 {fmt(p1)})")

    if len(p) >= 5:
        p4 = p[4]
        whole = abs(p3 - p0)
        print("\n== 浪5 目标(自浪4终点) ==")
        print(f"  等长浪1:        {fmt(p4 + sign * w1)}")
        print(f"  0.618x浪1:      {fmt(p4 + sign * w1 * 0.618)}")
        print(f"  1.618x浪1:      {fmt(p4 + sign * w1 * 1.618)}")
        print(f"  0.618x(浪1~3):  {fmt(p4 + sign * whole * 0.618)}")
        print("  [提示] 若浪3已延长(>1.618x浪1), 浪5更倾向与浪1等长; "
              "浪5伴随量能/MACD背离时警惕衰竭")
    print("\n[提示] 测算位是概率参考区, 需结合浪型结构与背离信号使用, 不构成投资建议。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
