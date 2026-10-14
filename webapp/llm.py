#!/usr/bin/env python3
"""LLM 客户端: 调用 Anthropic Messages API(或任何兼容代理)。

配置(环境变量):
    ANTHROPIC_BASE_URL   API 地址, 默认 https://api.anthropic.com
    ANTHROPIC_API_KEY    或 ANTHROPIC_AUTH_TOKEN, 二选一
    LLM_FAST_MODEL       批量任务模型(新闻分类), 默认 claude-haiku-4-5-20251001
    LLM_SMART_MODEL      重判断模型(个股/大盘/持仓建议), 默认 claude-sonnet-4-6
"""
import json
import os

import requests

BASE = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
TOKEN = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
FAST_MODEL = os.environ.get("LLM_FAST_MODEL", "claude-haiku-4-5-20251001")
SMART_MODEL = os.environ.get("LLM_SMART_MODEL", "claude-sonnet-4-6")


def chat(prompt, system="", model=FAST_MODEL, max_tokens=4000, timeout=180):
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    r = requests.post(f"{BASE}/v1/messages", timeout=timeout,
                      headers={"x-api-key": TOKEN,
                               "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json=body)
    r.raise_for_status()
    data = r.json()
    return "".join(b.get("text", "") for b in data.get("content", []))


def chat_json(prompt, system="", model=FAST_MODEL, max_tokens=4000):
    """要求模型输出 JSON 并解析; 容忍 ```json 包裹。"""
    text = chat(prompt, system=system, model=model, max_tokens=max_tokens).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


if __name__ == "__main__":
    print(chat("用5个字回答: 你是谁?"))
