#!/usr/bin/env python3
"""每日财经新闻采集器

数据源(akshare 封装的公开接口):
  - 财联社电报   stock_info_global_cls
  - 新浪全球财经 stock_info_global_sina
  - 东财个股新闻 stock_news_em (可选, 传股票代码)

特性: 按天落盘到 ../data/news/YYYY-MM-DD.csv, 以标题+时间哈希去重, 重复运行只增量追加,
可直接挂 crontab 定时执行。

用法:
    ../.venv/bin/python news_crawler.py                 # 抓大盘快讯
    ../.venv/bin/python news_crawler.py 002491 600519   # 额外抓这些个股的新闻
"""
import csv
import hashlib
import os
import sys
import time
from datetime import date

NEWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "news")
FIELDS = ["hash", "source", "time", "title", "content", "url"]


def norm(row_source, t, title, content, url=""):
    title = (title or "").strip()
    content = (content or "").strip()
    h = hashlib.md5(f"{title}|{t}".encode()).hexdigest()[:12]
    return {"hash": h, "source": row_source, "time": str(t),
            "title": title, "content": content, "url": url}


def fetch_all(stock_codes):
    import akshare as ak
    items = []

    def guard(name, fn):
        try:
            items.extend(fn())
            print(f"  [{name}] ok")
        except Exception as e:
            print(f"  [{name}] 失败: {type(e).__name__}")
        time.sleep(1)  # 接口间隔, 避免频控

    guard("财联社电报", lambda: [
        norm("财联社", f"{r['发布日期']} {r['发布时间']}", r["标题"], r["内容"])
        for _, r in ak.stock_info_global_cls().iterrows()])

    guard("新浪全球财经", lambda: [
        norm("新浪", r["时间"], r["内容"][:50], r["内容"])
        for _, r in ak.stock_info_global_sina().iterrows()])

    for code in stock_codes:
        guard(f"个股新闻 {code}", lambda c=code: [
            norm(f"东财-{c}", r["发布时间"], r["新闻标题"], r["新闻内容"], r["新闻链接"])
            for _, r in ak.stock_news_em(symbol=c).iterrows()])
    return items


def save(items):
    os.makedirs(NEWS_DIR, exist_ok=True)
    path = os.path.join(NEWS_DIR, f"{date.today().isoformat()}.csv")
    seen = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            seen = {r["hash"] for r in csv.DictReader(f)}
    new_items = [it for it in items if it["hash"] not in seen]
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(new_items)
    return path, len(new_items), len(items)


def main():
    codes = sys.argv[1:]
    print(f"采集开始 {date.today()} (个股: {codes or '无'})")
    items = fetch_all(codes)
    path, n_new, n_total = save(items)
    print(f"抓到 {n_total} 条, 新增 {n_new} 条 (其余为重复) -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
