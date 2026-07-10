from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {}, ())
    }


class FailedUrlLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        url: str,
        reason: str,
        status: int | None = None,
        retry_times: int = 0,
        proxy: str | None = None,
    ) -> None:
        row = compact_dict(
            {
                "url": url,
                "reason": reason,
                "status": status,
                "retry_times": retry_times,
                "proxy": proxy,
                "failed_at": utc_now_iso(),
            }
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        logger.warning("Recorded failed URL: %s reason=%s status=%s", url, reason, status)


def load_failed_urls(path: str | Path) -> list[str]:
    failed_path = Path(path)
    if not failed_path.exists():
        return []
    urls: list[str] = []
    for line in failed_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed failed URL line: %s", line[:120])
            continue
        url = row.get("url")
        if isinstance(url, str) and url:
            urls.append(url)
    return list(dict.fromkeys(urls))
