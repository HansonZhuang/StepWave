#!/usr/bin/env python3
"""持仓顾问: 持仓存储 + 带持仓上下文的 LLM 操作建议(买/卖多少股, 保留多少)。

持仓文件: ../data/portfolio.json
建议缓存: ../data/web_cache/holding_<market>_<code>_<date>.json
先验知识: ../knowledge/lessons.md (注入提示词)
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

PORT_FILE = os.path.join(HERE, "..", "data", "portfolio.json")
CACHE_DIR = os.path.join(HERE, "..", "data", "web_cache")
LESSONS = os.path.join(HERE, "..", "knowledge", "lessons.md")


def lessons_text(max_chars=2500):
    if not os.path.exists(LESSONS):
        return ""
    with open(LESSONS, encoding="utf-8") as f:
        t = f.read()
    return t[-max_chars:]


def load_holdings():
    if not os.path.exists(PORT_FILE):
        return []
    with open(PORT_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_holdings(h):
    os.makedirs(os.path.dirname(PORT_FILE), exist_ok=True)
    with open(PORT_FILE, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)


def upsert(market, code, shares, cost=None):
    h = load_holdings()
    for it in h:
        if it["market"] == market and it["code"] == code:
            it["shares"] = shares
            if cost is not None:
                it["cost"] = cost
            save_holdings(h)
            return h
    h.append({"market": market, "code": code, "shares": shares,
              **({"cost": cost} if cost is not None else {})})
    save_holdings(h)
    return h


def remove(market, code):
    h = [it for it in load_holdings()
         if not (it["market"] == market and it["code"] == code)]
    save_holdings(h)
    return h


HOLD_SYSTEM_TPL = (
    "你是严谨的技术分析顾问, 精通艾略特波浪理论与缠论, 为用户的实际持仓给出当日可执行方案。\n"
    "交易规则约束: A股沪深买入须100股整数倍, 卖出可一次清零碎股; 北交所(92/8/4开头)最小申报100股、"
    "之后可1股递增; T+1, 当日买入不能当日卖出。\n"
    "分析要求: 1)缠论框架(中枢位置/三类买卖点/背驰)判断当下结构; 2)波浪视角判断当前第几浪及斐波那契关键位; "
    "3)结合大盘与新闻情绪; 4)给出具体数量: 卖多少股/买多少股/保留多少股, 挂单价必须锚定结构位。\n"
    "严格输出JSON: {\"action\": \"清仓/减仓/持有/加仓/观望之一\", "
    "\"orders\": [{\"side\": \"买入或卖出\", \"shares\": 整数股数, \"price\": 挂单价, \"reason\": \"30字内\"}], "
    "\"keep_shares\": 建议保留股数, \"add_price\": 后续合适的加仓价或null, "
    "\"exit_price\": 触发清仓的价位或null, \"stop_loss\": 止损价或null, "
    "\"wave_view\": \"波浪视角80字内\", \"chan_view\": \"缠论视角80字内\", \"news_view\": \"消息面40字内\", "
    "\"rationale\": \"综合结论80字内\"}\n"
    "语气克制, 这是研判不是保证。\n"
    "历史复盘教训(先验知识, 必须遵循):\n{lessons}"
)


def _chan_brief(chan):
    if "error" in chan:
        return chan
    return {"last_close": chan["last_close"], "last_dt": chan["last_dt"],
            "recent_strokes": chan["strokes"][-8:], "zhongshu": chan["zhongshu"][-3:],
            "signals": chan["signals"], "divergence": chan["divergence"],
            "position_vs_last_zs": chan["position_vs_last_zs"]}


def advise_holding(market, code, shares, cost=None, refresh=False):
    cp = os.path.join(CACHE_DIR, f"holding_{market}_{code}_{date.today().isoformat()}.json")
    if not refresh and os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)

    k = market_service.get_kline(market, code)
    ctx = market_service.index_context()
    news = news_service.get_analyzed()
    related = news_service.search(code, news)[:5]
    quote = None
    try:
        qs = market_service.get_quotes([market_service.quote_symbol(market, code)])
        quote = qs[0] if qs else None
    except Exception:
        pass

    payload = {
        "持仓": {"market": market, "code": code, "持有股数": shares,
                "成本价": cost, "实时": quote},
        "近20根K线": k["bars"][-20:],
        "缠论结构": _chan_brief(k["chan"]),
        "大盘": {"上证": _chan_brief(ctx["sh_chan"]), "纳指": _chan_brief(ctx["ixic_chan"]),
                "实时": ctx["quotes"]},
        "相关新闻": [{"title": n["title"][:60], "impact": n["impact"]} for n in related],
        "今日大势研判": news["summary"],
    }
    system = HOLD_SYSTEM_TPL.replace("{lessons}", lessons_text())
    advice = llm.chat_json(json.dumps(payload, ensure_ascii=False),
                           system=system, model=llm.SMART_MODEL, max_tokens=2200)
    result = {"date": date.today().isoformat(), "market": market, "code": code,
              "shares": shares, "cost": cost, "quote": quote,
              "name": (quote or {}).get("name", code),
              "prev_close": (quote or {}).get("prev_close"),
              "advice": advice, "source": k["source"],
              "disclaimer": "结构化信号+模型研判, 仅供参考, 不构成投资建议"}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


if __name__ == "__main__":
    if len(sys.argv) >= 4:
        print(json.dumps(advise_holding(sys.argv[1], sys.argv[2], int(sys.argv[3]),
                                        refresh=True), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(load_holdings(), ensure_ascii=False))
