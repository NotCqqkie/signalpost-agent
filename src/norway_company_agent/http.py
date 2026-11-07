from __future__ import annotations

import json
import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class FetchResult:
    url: str
    status: int
    elapsed_ms: int
    bytes_received: int
    body: Any = None
    error: str | None = None
    content_sha256: str | None = None
    retrieved_at: str | None = None
    effective_at: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_json(url: str, *, timeout: float = 20.0, attempts: int = 4, deadline: float | None = None) -> FetchResult:
    """GET JSON with retries. `deadline` (time.monotonic() value): no attempt starts, and no timeout or back-off runs,
    past it (lane 9: a slow or rate-limiting register must not hold a company past its cap or the global deadline).
    429/503 back off longer (Retry-After seconds, at most 8 s)."""
    last_error = "request failed"
    for attempt in range(attempts):
        left = None if deadline is None else deadline - time.monotonic()
        if left is not None and left < 1.5:
            last_error = last_error if attempt else "deadline"
            break
        started = time.monotonic()
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)"},
        )
        delay = 0.4 * (2**attempt)
        try:
            with urllib.request.urlopen(request, timeout=timeout if left is None else max(1.0, min(timeout, left - 0.5))) as response:
                raw = response.read()
                elapsed = int((time.monotonic() - started) * 1000)
                return FetchResult(url, response.status, elapsed, len(raw), json.loads(raw), content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now())
        except urllib.error.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            try:
                raw = exc.read()
            except Exception:
                raw = b""
            if exc.code in {404, 410}:
                return FetchResult(url, exc.code, elapsed, len(raw), error=f"HTTP {exc.code}", content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now())
            if exc.code == 500 and b"oppstillingsplan som ikke er st" in raw:
                # Regnskapsregisteret's open API cannot serve this accounts layout (insurance, bank, pension fund, non-profit):
                # a deterministic answer about the source, not an outage, so it is not retried
                try:
                    message = str(json.loads(raw).get("message") or "")[:160]
                except Exception:
                    message = "Regnskapet inneholder en oppstillingsplan som ikke er stottet"
                return FetchResult(url, exc.code, elapsed, len(raw), error=f"unsupported_layout: {message}", content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now())
            last_error = f"HTTP {exc.code}"
            if exc.code == 400:
                break  # invalid request (e.g. malformed organisation number): retrying cannot help
            if exc.code in {429, 500, 502, 503, 504}:  # main2: 5xx bursts from the register get the same back-off as 429
                try:
                    after = float((exc.headers or {}).get("Retry-After") or 0)
                except (TypeError, ValueError):
                    after = 0.0
                delay = min(8.0, max(2.0 * (attempt + 1), after))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
            last_error = type(exc).__name__
        if attempt + 1 < attempts:
            if deadline is not None and time.monotonic() + delay > deadline - 1.5:
                break
            time.sleep(delay)
    return FetchResult(url, 0, 0, 0, error=last_error, retrieved_at=_utc_now())
