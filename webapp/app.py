#!/usr/bin/env python3
"""股票分析站 Flask 入口

启动:
    cd /data/zhuanghancheng/stock_knowledge/webapp
    ../.venv/bin/python app.py          # 默认 0.0.0.0:8688
"""
import os
import sys

from flask import Flask, jsonify, render_template, request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import advisor  # noqa: E402
import evaluator  # noqa: E402
import market_service  # noqa: E402
import news_service  # noqa: E402
import portfolio  # noqa: E402

app = Flask(__name__)
app.json.ensure_ascii = False

WATCHLIST = [("a", "002491", "通鼎互联"), ("hk", "00700", "腾讯控股"), ("us", "AAPL", "苹果")]


@app.get("/")
def index():
    return render_template("index.html",
                           watchlist=[{"market": m, "code": c, "name": n}
                                      for m, c, n in WATCHLIST])


@app.get("/api/news")
def api_news():
    refresh = request.args.get("refresh") == "1"
    return jsonify(news_service.get_analyzed(refresh=refresh,
                                             codes=[c for m, c, _ in WATCHLIST if m == "a"]))


@app.get("/api/news/search")
def api_news_search():
    q = request.args.get("q", "")
    if not q.strip():
        return jsonify([])
    return jsonify(news_service.search(q)[:30])


@app.get("/api/stock/<market>/<code>")
def api_stock(market, code):
    if market not in ("a", "hk", "us"):
        return jsonify({"error": "market须为a/hk/us"}), 400
    try:
        return jsonify(market_service.get_kline(market, code))
    except Exception as e:
        return jsonify({"error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"}), 502


@app.get("/api/quote")
def api_quote():
    syms = [s for s in request.args.get("symbols", "").split(",") if s]
    if not syms:
        return jsonify([])
    try:
        return jsonify(market_service.get_quotes(syms[:20]))
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}"}), 502


@app.get("/api/advise/<market>/<code>")
def api_advise(market, code):
    if market not in ("a", "hk", "us"):
        return jsonify({"error": "market须为a/hk/us"}), 400
    refresh = request.args.get("refresh") == "1"
    try:
        return jsonify(advisor.advise_stock(market, code, refresh=refresh))
    except Exception as e:
        return jsonify({"error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"}), 502


@app.get("/api/overview")
def api_overview():
    refresh = request.args.get("refresh") == "1"
    try:
        return jsonify(advisor.daily_overview(refresh=refresh))
    except Exception as e:
        return jsonify({"error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"}), 502


@app.get("/api/portfolio")
def api_portfolio_list():
    return jsonify(portfolio.load_holdings())


@app.post("/api/portfolio")
def api_portfolio_add():
    d = request.get_json(force=True)
    market, code = d.get("market"), (d.get("code") or "").strip()
    shares = int(d.get("shares") or 0)
    cost = d.get("cost")
    if market not in ("a", "hk", "us") or not code or shares <= 0:
        return jsonify({"error": "参数不完整: 需要 market/code/shares"}), 400
    return jsonify(portfolio.upsert(market, code, shares,
                                    float(cost) if cost not in (None, "") else None))


@app.delete("/api/portfolio/<market>/<code>")
def api_portfolio_del(market, code):
    return jsonify(portfolio.remove(market, code))


@app.get("/api/portfolio/advise/<market>/<code>")
def api_portfolio_advise(market, code):
    refresh = request.args.get("refresh") == "1"
    h = [it for it in portfolio.load_holdings()
         if it["market"] == market and it["code"] == code]
    if not h:
        return jsonify({"error": "持仓列表中无此股票, 请先添加"}), 404
    try:
        return jsonify(portfolio.advise_holding(market, code, h[0]["shares"],
                                                h[0].get("cost"), refresh=refresh))
    except Exception as e:
        return jsonify({"error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"}), 502


@app.get("/api/report")
def api_report():
    d = request.args.get("date") or __import__("datetime").date.today().isoformat()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "data", "web_cache", f"report_{d}.json")
    if request.args.get("generate") == "1" or not os.path.exists(path):
        try:
            return jsonify(evaluator.run(d))
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 502
    import json as _json
    with open(path, encoding="utf-8") as f:
        return jsonify(_json.load(f))


def _scheduler():
    """内建定时器(此环境无cron): 工作日9:36后出持仓方案, 15:07后收盘复盘。"""
    import datetime as dt
    import time as _t
    done = set()
    while True:
        now = dt.datetime.now()
        today = now.date().isoformat()
        if now.weekday() < 5:
            if (dt.time(9, 36) <= now.time() < dt.time(11, 30)
                    and (today, "m") not in done):
                done.add((today, "m"))
                for h in portfolio.load_holdings():
                    try:   # refresh=False: 当日已有方案则直接复用
                        portfolio.advise_holding(h["market"], h["code"],
                                                 h["shares"], h.get("cost"))
                    except Exception:
                        pass
            report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "data", "web_cache",
                                       f"report_{today}.json")
            if (now.time() >= dt.time(15, 7) and (today, "e") not in done
                    and not os.path.exists(report_path)):
                done.add((today, "e"))
                try:
                    evaluator.run(today)
                except Exception:
                    pass
        _t.sleep(60)


if __name__ == "__main__":
    import threading
    threading.Thread(target=_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8688)))
