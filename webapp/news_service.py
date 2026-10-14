#!/usr/bin/env python3
"""新闻服务: 采集 -> LLM 分类/重要性/个股影响 -> 当日缓存 -> 搜索

缓存: ../data/web_cache/news_analyzed_YYYY-MM-DD.json
"""
import csv
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import llm  # noqa: E402
import news_crawler  # noqa: E402

CACHE_DIR = os.path.join(HERE, "..", "data", "web_cache")
CATEGORIES = ["地缘政治", "宏观政策", "国际市场", "能源商品", "科技产业", "公司动态", "其他"]

CLASSIFY_SYSTEM = (
    "你是财经新闻分析员。对每条新闻输出JSON对象: "
    '{"id": 原样返回, "category": 必须取[' + ",".join(f'"{c}"' for c in CATEGORIES) + ']之一, '
    '"importance": 1到5整数(5=全球级大事如领导人互访/战争/央行决议, 1=琐碎), '
    '"impact": "一句话说明对相关股票/板块/商品的影响方向, 例如: 利好石油与军工板块(供给担忧), '
    '利空航运成本敏感行业。若无明显影响写-无明显影响-"}。'
    "严格输出JSON数组, 不要任何其他文字。"
)


def _today():
    return date.today().isoformat()


def _cache_path(d):
    return os.path.join(CACHE_DIR, f"news_analyzed_{d}.json")


def load_raw_news(codes=(), refresh=False):
    path = os.path.join(news_crawler.NEWS_DIR, f"{_today()}.csv")
    if refresh or not os.path.exists(path):
        items = news_crawler.fetch_all(list(codes))
        news_crawler.save(items)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def classify(rows):
    """分批送LLM, 返回 {hash: {category, importance, impact}}。"""
    out = {}
    batch_size = 20
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        lines = [{"id": r["hash"],
                  "title": r["title"][:80],
                  "content": (r["content"] or "")[:220]} for r in batch]
        try:
            arr = llm.chat_json("请分析以下新闻:\n" + json.dumps(lines, ensure_ascii=False),
                                system=CLASSIFY_SYSTEM, max_tokens=6000)
            for a in arr:
                out[a["id"]] = a
        except Exception as e:
            print(f"[news] 批次{i // batch_size}分析失败: {type(e).__name__}", file=sys.stderr)
    return out


SUMMARY_SYSTEM = (
    "你是严谨的投资策略分析师, 熟悉艾略特波浪理论与缠论。基于今日新闻给出当日股市总体研判。"
    '输出JSON: {"stance": "偏多/中性/偏空", "summary": "150字内的当日大势研判", '
    '"advice": "100字内的总体仓位建议", "watch": ["需要盯的2-4个风险或机会点"]}。'
    "注意: 这是提示性研判而非保证, 语气克制, 不夸大。严格输出JSON。"
)


def daily_summary(items):
    top = sorted(items, key=lambda x: -x.get("importance", 1))[:15]
    brief = [{"title": it["title"] or it["content"][:40],
              "category": it["category"], "impact": it["impact"]} for it in top]
    return llm.chat_json("今日重要新闻:\n" + json.dumps(brief, ensure_ascii=False),
                         system=SUMMARY_SYSTEM, model=llm.SMART_MODEL, max_tokens=1500)


def get_analyzed(refresh=False, codes=()):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = _cache_path(_today())
    if not refresh and os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)

    rows = load_raw_news(codes=codes, refresh=refresh)
    tags = classify(rows)
    items = []
    for r in rows:
        t = tags.get(r["hash"], {})
        items.append({
            "hash": r["hash"], "source": r["source"], "time": r["time"],
            "title": r["title"] or (r["content"] or "")[:50],
            "content": r["content"], "url": r.get("url", ""),
            "category": t.get("category", "其他"),
            "importance": int(t.get("importance", 1)),
            "impact": t.get("impact", "-"),
        })

    cats = []
    for c in CATEGORIES:
        sub = sorted([it for it in items if it["category"] == c],
                     key=lambda x: -x["importance"])
        if sub:
            cats.append({"name": c, "top": sub[:3], "count": len(sub)})

    try:
        summary = daily_summary(items)
    except Exception as e:
        summary = {"stance": "未知", "summary": f"总结生成失败: {type(e).__name__}",
                   "advice": "请稍后刷新重试", "watch": []}

    result = {"date": _today(), "n_items": len(items),
              "categories": cats, "items": items, "summary": summary}
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def search(q, data=None):
    data = data or get_analyzed()
    q = q.strip().lower()
    hits = [it for it in data["items"]
            if q in it["title"].lower() or q in it["content"].lower()
            or q in it["category"] or q in it["impact"].lower()]
    return sorted(hits, key=lambda x: -x["importance"])


if __name__ == "__main__":
    d = get_analyzed(refresh="--refresh" in sys.argv)
    print(f"{d['date']} 共{d['n_items']}条")
    for c in d["categories"]:
        print(f"\n[{c['name']}] {c['count']}条")
        for it in c["top"]:
            print(f"  ★{it['importance']} {it['title'][:40]}  => {it['impact'][:50]}")
    print("\n== 当日研判 ==")
    print(json.dumps(d["summary"], ensure_ascii=False, indent=1))
