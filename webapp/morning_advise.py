#!/usr/bin/env python3
"""早盘定时任务: 为持仓列表逐只生成当日操作方案(供收盘复盘对照)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portfolio  # noqa: E402

for h in portfolio.load_holdings():
    try:
        r = portfolio.advise_holding(h["market"], h["code"], h["shares"],
                                     h.get("cost"), refresh=True)
        print(h["code"], "->", r["advice"].get("action"))
    except Exception as e:
        print(h["code"], "失败:", type(e).__name__, str(e)[:80])
