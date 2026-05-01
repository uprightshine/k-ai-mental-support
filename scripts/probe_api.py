"""MiniMax 호환 엔드포인트 탐지 스크립트.

주어진 API 키를 여러 후보 엔드포인트에 동시 호출해서 어디에 붙는지 확인.
테스트 후 이 파일은 삭제해도 됨.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent.parent
ENV_PATH = BASE / ".env.local"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.environ.get("MINIMAX_API_KEY")
if not API_KEY:
    sys.exit("MINIMAX_API_KEY 미설정")

BASE_URL = "https://api.minimaxi.chat/v1"
PATH = "/text/chatcompletion_v2"

CANDIDATES = [
    ("MiniMax-M2",                BASE_URL, PATH, "openai"),
    ("MiniMax-M1",                BASE_URL, PATH, "openai"),
    ("MiniMax-01",                BASE_URL, PATH, "openai"),
    ("MiniMax-Text-02",           BASE_URL, PATH, "openai"),
    ("abab7-chat-preview",        BASE_URL, PATH, "openai"),
    ("abab6.5s",                  BASE_URL, PATH, "openai"),
    ("abab6.5",                   BASE_URL, PATH, "openai"),
    ("minimax-text-01",           BASE_URL, PATH, "openai"),
    ("MiniMax-Text-01-v1",        BASE_URL, PATH, "openai"),
    ("MiniMax-Text",              BASE_URL, PATH, "openai"),
]

USER_PROMPT = "한 단어로만 답해: 안녕"

async def probe(model: str, base_url: str, path: str, style: str) -> str:
    url = base_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": USER_PROMPT}],
        "max_tokens": 20,
        "temperature": 0.3,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except Exception as e:
        return f"[{model}] NETWORK ERROR: {e!r}"

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:400]}

    base_resp = body.get("base_resp", {}) if isinstance(body, dict) else {}
    bcode = base_resp.get("status_code")
    msg = base_resp.get("status_msg", "")
    choices = body.get("choices") if isinstance(body, dict) else None
    if bcode in (0, None) and choices:
        reply = choices[0].get("message", {}).get("content", "")
        return f"✅ [{model}]  응답 OK: {reply!r}"
    return f"❌ [{model}]  base_resp={bcode} / {msg}"


async def main():
    results = await asyncio.gather(*[probe(*c) for c in CANDIDATES])
    print("\n".join(results))


if __name__ == "__main__":
    asyncio.run(main())
