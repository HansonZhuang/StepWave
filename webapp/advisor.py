#!/usr/bin/env python3
"""建议引擎: 缠论/波浪结构 + 新闻 + 大盘(上证/纳指) -> LLM 生成当日操作建议。

缓存: ../data/web_cache/advice_<market>_<code>_YYYY-MM-DD.json / overview_YYYY-MM-DD.json
"""
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm  # noqa: E402
import market_service  # noqa: E402
import news_service  # noqa: E402

CACHE_DIR = os.path.join(HERE, "..", "data", "web_cache")

STOCK_SYSTEM = (
    "你是严谨的技术分析顾问, 精通艾略特波浪理论与缠论。基于给定的结构化数据给出当日操作建议。\n"
    "分析时必须: 1)用缠论框架解读中枢位置/三类买卖点/背驰; 2)用波浪理论判断当前可能处于第几浪, "
    "给出斐波那契关键位; 3)结合大盘(上证/纳指)结构与当日新闻情绪; 4)给出具体可执行的挂单点位。\n"
    "风控先验(必须体现在建议中): 单笔风险不超过总资金2%; 趋势未确认不重仓; "
    "反转形态必须结合位置(高位看跌形态/低位看涨形态才有效); 放量突破才可信, 缩量新高警惕背离; "
    "亏损仓位禁止摊平加仓, 加仓只加盈利仓。\n"
    '严格输出JSON: {"action": "清仓/减仓/持有/加仓/买入/观望之一", "position_pct": 建议仓位0-100, '
    '"wave_view": "波浪视角80字内", "chan_view": "缠论视角80字内", "news_view": "消息面40字内", '
    '"orders": [{"side": "买入或卖出", "price": 数字, "reason": "30字内"}], '
    '"stop_loss": 止损价数字或null, "rationale": "综合结论60字内"}\n'
    "点位必须从给定的中枢上下沿/笔端点/斐波那契位推导, 不得凭空编造。语气克制。"
)

OVERVIEW_SYSTEM = (
    "你是严谨的策略分析师, 精通艾略特波浪理论与缠论。基于大盘结构与新闻给出当日总体研判。\n"
    '严格输出JSON: {"stance": "偏多/中性/偏空", "clear_all": true或false(是否建议大面积清仓), '
    '"position_pct": 总仓位建议0-100, "sh_view": "上证结构解读60字内", '
    '"ixic_view": "纳指结构解读60字内", "logic": "综合逻辑100字内", '
    '"actions": ["今日2-4条具体操作要点"]}\n'
    "特别注意: 若指数处于长期上涨后新高且出现背驰/新闻极度亢奋, 应提示兑现风险; "
    "若大跌后背驰可提示左侧机会。语气克制, 这是研判不是保证。"
)


def _chan_brief(chan):
    if "error" in chan:
        return chan
    return {"last_close": chan["last_close"], "last_dt": chan["last_dt"],
            "recent_strokes": chan["strokes"][-8:],
            "zhongshu": chan["zhongshu"][-3:],
            "signals": chan["signals"],
            "divergence": chan["divergence"],
            "position_vs_last_zs": chan["position_vs_last_zs"]}


def advise_stock(market, code, refresh=False):
    cp = os.path.join(CACHE_DIR, f"advice_{market}_{code}_{date.today().isoformat()}.json")
    if not refresh and os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)

    k = market_service.get_kline(market, code)
    ctx = market_service.index_context()
    news = news_service.get_analyzed()
    related = news_service.search(code, news)[:5]
    quote = None
    try:
        quote = market_service.get_quotes([market_service.quote_symbol(market, code)])
        quote = quote[0] if quote else None
    except Exception:
        pass

    payload = {
        "股票": {"market": market, "code": code,
                "实时": quote, "近20根K线": k["bars"][-20:],
                "缠论结构": _chan_brief(k["chan"])},
        "大盘": {"上证": _chan_brief(ctx["sh_chan"]), "纳指": _chan_brief(ctx["ixic_chan"]),
                "实时": ctx["quotes"]},
        "相关新闻": [{"title": n["title"][:60], "impact": n["impact"]} for n in related],
        "今日大势研判": news["summary"],
    }
    import portfolio
    system = STOCK_SYSTEM + "\n历史复盘教训(先验知识, 必须遵循):\n" + portfolio.lessons_text()
    advice = llm.chat_json(json.dumps(payload, ensure_ascii=False),
                           system=system, model=llm.SMART_MODEL, max_tokens=2000)
    result = {"date": date.today().isoformat(), "market": market, "code": code,
              "quote": quote, "advice": advice,
              "related_news": payload["相关新闻"],
              "disclaimer": "结构化信号+模型研判, 仅供参考, 不构成投资建议"}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


DEBATE_BULL = (
    "你是多头研究员。仅基于给定数据提出3-5条做多论据, 每条不超过40字, "
    "必须引用具体的结构位/数值(中枢/斐波位/背驰/量价/新闻), 禁止空话。"
    '严格输出JSON: {"points": ["论据1", ...]}'
)
DEBATE_BEAR = (
    "你是空头研究员。仅基于给定数据提出3-5条做空/回避论据, 每条不超过40字, "
    "必须引用具体的结构位/数值(中枢/斐波位/背驰/量价/新闻), 禁止空话。"
    '严格输出JSON: {"points": ["论据1", ...]}'
)
DEBATE_JUDGE = (
    "你是研究主管, 精通缠论与波浪理论。评判多空双方论据的质量与证据强度, 给出裁决。"
    '严格输出JSON: {"verdict": "偏多/偏空/中性", "confidence": 0到100整数, '
    '"key_reason": "60字内裁决核心依据", "action_hint": "40字内操作提示", '
    '"invalidation": "40字内: 什么条件出现则本裁决作废"}\n'
    "注意: 哪方论据更贴合结构事实就偏向哪方, 不和稀泥; 语气克制。"
)


def debate(market, code, refresh=False):
    """多空辩论: 牛熊双研究员各自陈词 -> 主管裁决(借鉴 TradingAgents 机制)。"""
    cp = os.path.join(CACHE_DIR, f"debate_{market}_{code}_{date.today().isoformat()}.json")
    if not refresh and os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)

    k = market_service.get_kline(market, code)
    ctx = market_service.index_context()
    news = news_service.get_analyzed()
    related = news_service.search(code, news)[:5]
    payload = {
        "股票": {"market": market, "code": code, "近20根K线": k["bars"][-20:],
                "缠论结构": _chan_brief(k["chan"])},
        "大盘": {"上证": _chan_brief(ctx["sh_chan"]), "纳指": _chan_brief(ctx["ixic_chan"])},
        "相关新闻": [{"title": n["title"][:60], "impact": n["impact"]} for n in related],
    }
    pj = json.dumps(payload, ensure_ascii=False)
    bull = llm.chat_json(pj, system=DEBATE_BULL, model=llm.SMART_MODEL, max_tokens=1200)
    bear = llm.chat_json(pj, system=DEBATE_BEAR, model=llm.SMART_MODEL, max_tokens=1200)
    judge = llm.chat_json(
        json.dumps({"数据": payload, "多头论据": bull.get("points", []),
                    "空头论据": bear.get("points", [])}, ensure_ascii=False),
        system=DEBATE_JUDGE, model=llm.SMART_MODEL, max_tokens=1000)
    result = {"date": date.today().isoformat(), "market": market, "code": code,
              "last_close": k["chan"]["last_close"],
              "bull": bull.get("points", []), "bear": bear.get("points", []),
              "judge": judge,
              "disclaimer": "多空辩论为模型研判, 仅供参考, 不构成投资建议"}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def daily_overview(refresh=False):
    cp = os.path.join(CACHE_DIR, f"overview_{date.today().isoformat()}.json")
    if not refresh and os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)

    ctx = market_service.index_context()
    news = news_service.get_analyzed()
    payload = {
        "上证结构": _chan_brief(ctx["sh_chan"]),
        "纳指结构": _chan_brief(ctx["ixic_chan"]),
        "指数实时": ctx["quotes"],
        "新闻研判": news["summary"],
        "重要新闻": [{"title": it["title"][:50], "impact": it["impact"]}
                   for c in news["categories"] for it in c["top"][:2]
                   if it["importance"] >= 3][:12],
    }
    ov = llm.chat_json(json.dumps(payload, ensure_ascii=False),
                       system=OVERVIEW_SYSTEM, model=llm.SMART_MODEL, max_tokens=1500)
    result = {"date": date.today().isoformat(), "overview": ov,
              "index_quotes": ctx["quotes"],
              "disclaimer": "结构化信号+模型研判, 仅供参考, 不构成投资建议"}
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print(json.dumps(advise_stock(sys.argv[1], sys.argv[2], refresh=True),
                         ensure_ascii=False, indent=1))
    else:
        print(json.dumps(daily_overview(refresh=True), ensure_ascii=False, indent=1))
