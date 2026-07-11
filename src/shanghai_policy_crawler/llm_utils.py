"""
llm_utils.py — LLM helper utilities for policy content extraction.

Uses OpenRouter as the LLM backend (free-tier compatible).
Provides:
  - call_openrouter_llm()               : raw LLM call with retry/backoff
  - extract_policy_expire_date()        : parse expire date from policy text
  - extract_conditions_and_standards()  : extract condition + payment standard fields
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

OPENROUTER_KEY: str = (
    os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_KEY") or ""
)
_LLM_MODEL = "openrouter/free"
_LLM_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_MAX_RETRIES = 8
_BASE_SLEEP = 3.0   # seconds before every call (rate-limit guard)
_BACKOFF_FACTOR = 10  # seconds × attempt on error


# ── Core LLM call ─────────────────────────────────────────────────────────────

def call_openrouter_llm(prompt: str) -> str:
    """
    Send *prompt* to OpenRouter and return the text response.

    Retries up to _MAX_RETRIES times with linear back-off.
    Returns an empty string on persistent failure.
    """
    if not OPENROUTER_KEY:
        print("[LLM] Warning: OPENROUTER_API_KEY is not set. Skipping LLM call.")
        return ""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://joyspace.work",
        "X-Title": "JoySpace Crawler",
    }
    payload = {
        "model": _LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    for attempt in range(_MAX_RETRIES):
        try:
            time.sleep(_BASE_SLEEP)
            resp = requests.post(_LLM_ENDPOINT, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content")
            return content.strip() if content else ""
        except Exception as exc:
            backoff = (attempt + 1) * _BACKOFF_FACTOR
            print(f"[LLM] Error (attempt {attempt + 1}/{_MAX_RETRIES}): {exc}. Retry in {backoff}s …")
            time.sleep(backoff)
    return ""


# ── Domain-specific extraction helpers ────────────────────────────────────────

def extract_policy_expire_date(title: str, content: str) -> str | None:
    """
    Find the expiration / effective-end date in *content* using regex first.
    Falls back to LLM if regex fails and OPENROUTER_KEY is configured.

    Returns a 'YYYY-MM-DD' string, or None if not found.
    """
    if not content:
        return None

    # 1. Regex Pattern 1: 有效期至YYYY年MM月DD日
    m1 = re.search(r"有效期至\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", content)
    if m1:
        return f"{m1.group(1)}-{int(m1.group(2)):02d}-{int(m1.group(3)):02d}"

    # 2. Regex Pattern 2: 自YYYY年MM月DD日起施行，有效期至YYYY年MM月DD日
    m2 = re.search(r"施行.*?有效期至\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", content, re.DOTALL)
    if m2:
        return f"{m2.group(1)}-{int(m2.group(2)):02d}-{int(m2.group(3)):02d}"

    # 3. Regex Pattern 3: 自YYYY年MM月DD日起施行，有效期为X年
    m_start = re.search(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起?\s*(?:施行|起?执行)", content)
    m_span = re.search(r"有效期\s*(?:为|共)?\s*(\d{1,2})\s*年", content)
    if m_start and m_span:
        try:
            start_year = int(m_start.group(1))
            start_month = int(m_start.group(2))
            start_day = int(m_start.group(3))
            span_years = int(m_span.group(1))
            expire_year = start_year + span_years
            return f"{expire_year}-{start_month:02d}-{start_day:02d}"
        except Exception:
            pass

    # 4. Regex Pattern 4: 自...起算，有效期为X年
    m_start = re.search(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起", content)
    m_span2 = re.search(r"期满\s*(\d{1,2})\s*年", content)
    if m_start and m_span2:
        try:
            start_year = int(m_start.group(1))
            start_month = int(m_start.group(2))
            start_day = int(m_start.group(3))
            span_years = int(m_span2.group(1))
            expire_year = start_year + span_years
            return f"{expire_year}-{start_month:02d}-{start_day:02d}"
        except Exception:
            pass

    # Fallback to LLM if key is set
    # Guard against LLM fallback if there are no signs of validity limits
    has_validity_indicator = any(x in content for x in ["有效期", "期满", "有效期限", "截止"])
    if not has_validity_indicator:
        return None

    if not OPENROUTER_KEY:
        return None

    prompt = f"""你是一个专业的政策文本分析助手。请阅读以下政策文件，找出它的失效日期/废止日期/有效期截止日期。
政策标题: {title}
政策正文片段:
{content[:3000]}

请仔细寻找正文里如"本办法自XXXX年XX月XX日起施行，有效期至XXXX年XX月XX日"或"有效期X年"的内容。
如果找到明确的失效日期，请将其规整为 YYYY-MM-DD 格式输出。
如果只说有效期几年，请根据发文时间或施行时间计算出失效日期，规整为 YYYY-MM-DD 输出。
如果政策中没有任何关于有效期或失效日期的描述，请直接输出 "null"。
不要有任何多余的话，直接输出日期（如 2031-12-31）或者 "null"。"""

    output = call_openrouter_llm(prompt).strip()
    match = re.search(r"20\d{2}-\d{2}-\d{2}", output)
    return match.group(0) if match else None


def extract_conditions_and_standards(title: str, raw_text: str) -> tuple[str, str]:
    """
    Extract 'policy_conditions' and 'payment_standard' from *raw_text* using regex first.
    Falls back to LLM if regex fails and OPENROUTER_KEY is configured.

    Returns a (conditions, standard) tuple; either element may be an empty string.
    """
    if not raw_text or ("未在页面结构化" in raw_text and len(raw_text.strip()) < 150):
        return "", ""

    # Parse using regex headers in raw_text
    cond_match = re.search(r"申报条件:\s*(.*?)(?=\n\n兑付标准:|\n\n申报材料:|$)", raw_text, re.DOTALL)
    supp_match = re.search(r"兑付标准:\s*(.*?)(?=\n\n申报材料:|$)", raw_text, re.DOTALL)

    cond = cond_match.group(1).strip() if cond_match else ""
    supp = supp_match.group(1).strip() if supp_match else ""

    # Clear placeholders
    if "未在页面结构化" in cond:
        cond = ""
    if "未在页面结构化" in supp:
        supp = ""

    # If we got both extracted cleanly via regex, return immediately
    if cond or supp:
        return cond, supp

    # Fallback to LLM if key is set
    if not OPENROUTER_KEY:
        return "", ""

    truncated = (raw_text or "")[:10000]
    prompt = f"""你是一个专业的政策申报数据提取助手。请根据以下项目名称和正文内容，提取出该项目的"申报条件"和"兑付标准/支持标准"。
项目名称: {title}
正文内容:
{truncated}

请务必按以下JSON格式输出，不要包含任何额外的Markdown格式标记（如```json）、推理过程或额外文字，直接输出合法的JSON对象：
{{
  "policy_conditions": "这里写详细的申报条件...",
  "payment_standard": "这里写详细的兑付/补贴标准，如果有明确的资金补贴数额也写在这里..."
}}
"""
    raw = call_openrouter_llm(prompt)

    # Strip ```json … ``` fences if present
    if "```" in raw:
        for chunk in raw.split("```"):
            stripped = chunk.strip()
            if stripped.startswith("{") or stripped.startswith("json\n{"):
                raw = stripped.replace("json\n", "", 1).strip()
                break

    try:
        parsed = json.loads(raw)
        return parsed.get("policy_conditions", ""), parsed.get("payment_standard", "")
    except Exception:
        cond_val = _regex_extract(raw, "policy_conditions")
        std_val = _regex_extract(raw, "payment_standard")
        if cond_val or std_val:
            print(f"[LLM] Partial extraction via regex fallback for: {title}")
        else:
            print(f"[LLM] Failed to parse JSON output: {raw[:300]}")
        return (
            cond_val.replace("\\n", "\n").replace('\\"', '"'),
            std_val.replace("\\n", "\n").replace('\\"', '"'),
        )


def _regex_extract(text: str, key: str) -> str:
    """Best-effort regex extraction for a single JSON string field."""
    # Greedy: try to capture up to closing quote + , or }
    match = re.search(rf'"{key}"\s*:\s*"(.*?)"(?:,|\s*\}})', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: capture to end-of-string (truncated JSON)
    match = re.search(rf'"{key}"\s*:\s*"(.*)', text, re.DOTALL)
    if match:
        tail = match.group(1)
        # If another key follows, cut there
        if f'"payment_standard"' in tail and key == "policy_conditions":
            tail = tail.split('"payment_standard"')[0].strip().rstrip(",").rstrip('"').strip()
        return tail.strip().rstrip("}").rstrip('"').strip()
    return ""
