"""Polite, bounded HTTP client shared by every connector.

- honest User-Agent, robots.txt (RFC 9309: 4xx = no rules, 5xx/unreachable = disallow), cached per host
- per-host minimum interval between request starts
- SSRF guard on every URL and redirect hop (public HTTP(S) only)
- byte caps, timeouts, and a global deadline: no request starts after the deadline
- every response keeps requested/final URL, redirect chain, status, retrieval time and SHA-256
"""
from __future__ import annotations

import gzip
import hashlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, field
from typing import Any

from .evidence import utc_now
from .website import assert_public_url

USER_AGENT = "SignalpostAgent/1.0 (Norwegian company research; respects robots.txt; low rate)"


@dataclass
class Response:
    requested_url: str
    final_url: str
    status: int
    body: bytes = b""
    content_type: str = ""
    retrieved_at: str = ""
    elapsed_ms: int = 0
    error: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    tls_verified: bool = True
    retry_after: str | None = None  # lane 14: Retry-After header of a 429/503 answer
    tls_repaired: str | None = None  # lane 14: "aia" when the chain was completed from CA Issuers URLs

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and self.error is None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def text(self) -> str:
        charset = "utf-8"
        lowered = self.content_type.lower()
        if "charset=" in lowered:
            charset = lowered.split("charset=", 1)[1].split(";")[0].strip().strip('"') or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 6

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        assert_public_url(newurl)
        chain = getattr(req, "_chain", [])
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new._chain = chain + [newurl]
        return new


_OPENER = urllib.request.build_opener(_Redirects())
# Read-only fallback for public pages whose server sends an incomplete certificate chain (browsers repair it via AIA,
# Python does not). Nothing is ever sent but a GET with our User-Agent; evidence from it is marked tls_unverified.
_OPENER_UNVERIFIED = urllib.request.build_opener(_Redirects(), urllib.request.HTTPSHandler(context=__import__("ssl")._create_unverified_context()))


class DeadlineExceeded(Exception):
    pass


class Fetcher:
    def __init__(self, *, deadline: float | None = None, per_host_interval: float = 1.0, user_agent: str = USER_AGENT):
        self.deadline = deadline
        self.per_host_interval = per_host_interval
        self.user_agent = user_agent
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_next: dict[str, float] = {}
        self._robots: dict[str, Any] = {}
        self._robots_events: dict[str, threading.Event] = {}
        self._stats = {"requests": 0, "bytes": 0}
        self.allow_unverified_tls = False
        self.by_host: dict[str, int] = {}
        # lane 14 (flags tls_aia, polite_429); shared with child() fetchers by copy.copy
        self.aia: Any = None  # fetch_repair.AiaRepair when tls_aia is on
        self._aia_opener: Any = None
        self.polite_429 = False
        self.cooldowns: Any = None  # fetch_repair.Cooldowns when polite_429 is on
        self._ip_cache: dict[str, str | None] = {}

    def enable_lane14(self, *, tls_aia: bool = False, polite_429: bool = False) -> None:
        """Lane 14 hook: tls_aia completes incomplete certificate chains from CA Issuers URLs (full verification kept);
        polite_429 honours Retry-After, cools down a rate-limited host and its shared IP, sends Accept-Language."""
        from .fetch_repair import AiaRepair, Cooldowns
        if tls_aia:
            self.aia = AiaRepair(self._fetch_issuer)
            self._aia_opener = urllib.request.build_opener(_Redirects(), urllib.request.HTTPSHandler(context=self.aia.context))
        if polite_429:
            self.polite_429 = True
            self.cooldowns = Cooldowns()

    def _fetch_issuer(self, url: str) -> bytes | None:
        r = self._raw_get(url, timeout=8.0, max_bytes=64_000, accept="application/pkix-cert,application/x-x509-ca-cert,*/*")
        return r.body if r.ok else None

    def _backend_keys(self, host: str) -> list[str]:
        if host not in self._ip_cache:
            from .fetch_repair import resolved_ip
            self._ip_cache[host] = resolved_ip(host)
        ip = self._ip_cache[host]
        return [host] + (["ip:" + ip] if ip else [])

    # -- bookkeeping -------------------------------------------------
    def remaining(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - time.monotonic()

    def _count(self, host: str, nbytes: int) -> None:
        with self._lock:
            self._stats["requests"] += 1
            self._stats["bytes"] += nbytes
            self.by_host[host] = self.by_host.get(host, 0) + 1

    def _throttle(self, host: str, interval: float | None = None) -> None:
        interval = self.per_host_interval if interval is None else interval
        with self._lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock:
            now = time.monotonic()
            wait = self._host_next.get(host, 0.0) - now
            if wait > 0:
                if self.remaining() < wait + 1:
                    raise DeadlineExceeded(host)
                time.sleep(wait)
            self._host_next[host] = time.monotonic() + interval

    def forget_hosts(self, urls: list[str]) -> None:
        """Drop cached robots/unreachable state for these hosts so a later retry pass re-checks them."""
        with self._lock:
            for u in urls:
                parsed = urllib.parse.urlparse(u)
                for scheme in ("https", "http"):
                    key = f"{scheme}://{parsed.netloc}".lower()
                    if self._robots.get(key) in ("unreachable", "deny"):
                        self._robots.pop(key, None)
                        self._robots_events.pop(key, None)

    def child(self, seconds: float) -> "Fetcher":
        """Same throttle/robots/counters, tighter deadline (per-company time cap)."""
        import copy
        clone = copy.copy(self)
        cap = time.monotonic() + seconds
        clone.deadline = cap if self.deadline is None else min(self.deadline, cap)
        return clone

    # -- robots ------------------------------------------------------
    def robots_allowed(self, url: str, timeout: float = 8.0) -> bool:
        parsed = urllib.parse.urlparse(url)
        key = f"{parsed.scheme}://{parsed.netloc}".lower()
        with self._lock:
            event = self._robots_events.get(key)
            owner = event is None
            if owner:
                event = self._robots_events[key] = threading.Event()
        if owner:
            parser: Any
            response = self._raw_get(key + "/robots.txt", timeout=timeout, max_bytes=500_000, accept="text/plain,*/*")
            if (response.status == 0 or response.status >= 500) and response.error != "deadline" and self.remaining() > 20:
                time.sleep(2.0)  # one retry: a transient failure must not turn into "disallow" or "unreachable"
                response = self._raw_get(key + "/robots.txt", timeout=timeout, max_bytes=500_000, accept="text/plain,*/*")
            if response.ok:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response.text().splitlines())
            elif 400 <= response.status < 500:
                parser = "allow"
            elif response.status == 0 and response.error and "certificate" in response.error.lower():
                parser = "allow"  # TLS problems are handled by the page fetch itself
            elif response.status == 0:
                parser = "unreachable"  # host down: no page fetch either (callers may try http://)
            else:
                parser = "deny"
            self._robots[key] = parser
            event.set()
        else:
            event.wait(timeout + 5)
        parser = self._robots.get(key, "deny")
        if parser == "allow":
            return True
        if parser in ("deny", "unreachable"):
            return False
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:
            return False

    def sitemaps_from_robots(self, url: str) -> list[str]:
        parsed = urllib.parse.urlparse(url)
        key = f"{parsed.scheme}://{parsed.netloc}".lower()
        parser = self._robots.get(key)
        if parser in (None, "allow", "deny"):
            return []
        try:
            return list(parser.site_maps() or [])
        except Exception:
            return []

    # -- fetching ----------------------------------------------------
    def _raw_get(self, url: str, *, timeout: float, max_bytes: int, accept: str, headers: dict[str, str] | None = None, _verified: bool = True, _opener: Any = None) -> Response:
        started = time.monotonic()
        if self.remaining() < 2:
            return Response(url, url, 0, retrieved_at=utc_now(), error="deadline")
        try:
            assert_public_url(url)
        except ValueError as exc:
            return Response(url, url, 0, retrieved_at=utc_now(), error=f"blocked_url: {exc}")
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if self.cooldowns is not None:
            wait = self.cooldowns.wait_needed(self._backend_keys(host))
            if wait > 0:
                if wait > 90 or self.remaining() < wait + 10:
                    return Response(url, url, 429, retrieved_at=utc_now(), error="HTTP 429 (backend cooling down; not asked)")
                time.sleep(wait)
        try:
            self._throttle(host)
        except DeadlineExceeded:
            return Response(url, url, 0, retrieved_at=utc_now(), error="deadline")
        base_headers = {"User-Agent": self.user_agent, "Accept": accept, "Accept-Encoding": "gzip, deflate"}
        if self.polite_429:
            base_headers["Accept-Language"] = "nb-NO,nb;q=0.9,no;q=0.8,nn;q=0.7,en;q=0.5"
        request = urllib.request.Request(url, headers={**base_headers, **(headers or {})})
        request._chain = []  # type: ignore[attr-defined]
        timeout = max(1.0, min(timeout, self.remaining() - 1))
        try:
            with (_opener or (_OPENER if _verified else _OPENER_UNVERIFIED)).open(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                encoding = (response.headers.get("content-encoding") or "").lower()
                status = response.status
                final_url = response.geturl()
                content_type = response.headers.get("content-type", "")
            truncated = len(body) > max_bytes
            body = body[:max_bytes]
            if encoding == "gzip" and not truncated:
                body = gzip.decompress(body)
            elif encoding == "deflate" and not truncated:
                body = zlib.decompress(body)
            self._count(host, len(body))
            chain = [url] + [u for u in getattr(request, "_chain", [])]
            resp = Response(url, final_url, status, body[:max_bytes * 4], content_type, utc_now(), int((time.monotonic() - started) * 1000),
                            "oversized" if truncated else None, chain, _verified)
            if _opener is not None:
                resp.tls_repaired = "aia"
            return resp
        except urllib.error.HTTPError as exc:
            self._count(host, 0)
            try:
                raw = exc.read(200_000)
            except Exception:
                raw = b""
            return Response(url, exc.geturl() or url, exc.code, raw, exc.headers.get("content-type", "") if exc.headers else "", utc_now(),
                            int((time.monotonic() - started) * 1000), f"HTTP {exc.code}",
                            retry_after=exc.headers.get("retry-after") if exc.headers else None)
        except Exception as exc:  # URLError, timeouts, TLS, decompression, SSRF on redirect
            self._count(host, 0)
            reason = getattr(exc, "reason", exc)
            if _verified and _opener is None and self.aia is not None and "unable to get local issuer" in str(reason) and self.remaining() > 15:
                # lane 14 tls_aia: complete the chain from CA Issuers URLs (what browsers do), then verify again in full
                try:
                    repaired = self.aia.repair(host)
                except Exception:  # main2 fresh13: a failed repair must not fail the company's website step
                    repaired = False
                if repaired:
                    return self._raw_get(url, timeout=timeout, max_bytes=max_bytes, accept=accept, headers=headers, _opener=self._aia_opener)
            if _verified and self.allow_unverified_tls and "CERTIFICATE_VERIFY_FAILED" in str(reason):
                return self._raw_get(url, timeout=timeout, max_bytes=max_bytes, accept=accept, headers=headers, _verified=False)
            return Response(url, url, 0, retrieved_at=utc_now(), elapsed_ms=int((time.monotonic() - started) * 1000),
                            error=f"{type(exc).__name__}: {str(reason)[:160]}")

    def get(self, url: str, *, timeout: float = 12.0, max_bytes: int = 2_000_000, accept: str = "text/html,application/xhtml+xml,*/*;q=0.5",
            respect_robots: bool = True, headers: dict[str, str] | None = None) -> Response:
        if respect_robots and not self.robots_allowed(url):
            parsed = urllib.parse.urlparse(url)
            state = self._robots.get(f"{parsed.scheme}://{parsed.netloc}".lower())
            return Response(url, url, 0, retrieved_at=utc_now(), error="host_unreachable" if state == "unreachable" else "robots_disallowed")
        resp = self._raw_get(url, timeout=timeout, max_bytes=max_bytes, accept=accept, headers=headers)
        if self.polite_429 and resp.status in (429, 503):
            return self._polite_retry(url, resp, timeout=timeout, max_bytes=max_bytes, accept=accept, headers=headers)
        if resp.status in (429, 503) and self.remaining() > 20:
            # one polite retry, honouring a short Retry-After (seconds form) and never more than 6 s
            time.sleep(4.0)
            resp = self._raw_get(url, timeout=timeout, max_bytes=max_bytes, accept=accept, headers=headers)
        return resp

    def cooldown_left(self, urls: list[str]) -> float:
        """lane 14: seconds until every backend (host or shared IP) of these URLs may be asked again (0 if none cooling)."""
        if self.cooldowns is None:
            return 0.0
        hosts = {(urllib.parse.urlparse(u).hostname or "").lower() for u in urls}
        return max([self.cooldowns.wait_needed(self._backend_keys(h)) for h in hosts if h] + [0.0])

    def _polite_retry(self, url: str, resp: Response, **kw: Any) -> Response:
        """lane 14 polite_429: wait what the server asked (Retry-After, capped at 60 s; 15 s if it said nothing), with the
        host AND its IP cooling down meanwhile so sibling sites on a shared front end are not asked either; one retry.
        A second refusal cools the backend for 120 s; the run's late retry pass may ask once more after that."""
        from .fetch_repair import retry_after_seconds
        if "not asked" in (resp.error or ""):
            return resp
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        keys = self._backend_keys(host)
        asked = retry_after_seconds(resp.retry_after)
        wait = min(60.0, asked) if asked is not None else 15.0
        self.cooldowns.set(keys, wait, f"{resp.status} {host} retry-after={resp.retry_after}")
        if (asked is not None and asked > 60) or self.remaining() < wait + 15:
            return resp
        again = self._raw_get(url, **kw)
        if again.status in (429, 503) and "not asked" not in (again.error or ""):
            self.cooldowns.set(keys, 120.0, f"{again.status} again {host}")
        return again

    def stats(self) -> dict[str, Any]:
        with self._lock:
            out = {"requests": self._stats["requests"], "bytes": self._stats["bytes"], "hosts": len(self.by_host),
                   "max_requests_per_host": max(self.by_host.values()) if self.by_host else 0}
        if self.aia is not None:  # lane 14
            out["tls_aia"] = dict(self.aia.stats)
        if self.cooldowns is not None:
            out["polite_429"] = {"cooldowns": len(self.cooldowns.events), "examples": self.cooldowns.events[:20]}
        return out
