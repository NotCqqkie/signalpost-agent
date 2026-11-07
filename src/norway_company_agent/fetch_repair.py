"""Lane 14: honest repairs for website candidates we find but fail to fetch.

tls_aia — incomplete certificate chains. Browsers complete a chain the server sent only partly by downloading the
missing issuer certificates from the "CA Issuers" URL in each certificate's Authority Information Access extension.
Python does not. On CERTIFICATE_VERIFY_FAILED "unable to get local issuer certificate" we read the server's leaf
certificate, follow its CA Issuers URLs (at most 3 levels), and retry with a context that holds the normal trust
roots plus those downloaded intermediates. Verification stays fully on: hostname check, expiry, signatures, and the
chain must end in a root already trusted by the system/certifi store (partial chains are refused). Nothing is
trusted that a browser would not trust. Typical case (Nov 2026): servers sending leaf + Let's Encrypt YR1/YE1 without
the cross-signed "Root YR/YE" certificate, which Debian/certifi stores do not yet hold as roots.

polite_429 — rate limits. Honour Retry-After (seconds or HTTP-date, capped), put the host and its resolved IP into a
cool-down (shared front ends such as Wix serve thousands of hosts from one address, and their limiter counts the
client, not the host), make later requests to a cooling backend wait instead of adding to the burst, and send the
ordinary Accept-Language header a Norwegian reader's client sends. Never changes the User-Agent, TLS fingerprint or IP.
"""
from __future__ import annotations

import email.utils
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

ACCEPT_LANGUAGE = "nb-NO,nb;q=0.9,no;q=0.8,nn;q=0.7,en;q=0.5"
_CA_ISSUERS_OID = bytes.fromhex("06082b06010505073002")  # 1.3.6.1.5.5.7.48.2 id-ad-caIssuers
MAX_AIA_BYTES = 64_000
MAX_AIA_DEPTH = 3


# ---------------------------------------------------------------- AIA parsing (DER, no third-party ASN.1 library)
def _der_len(buf: bytes, i: int) -> tuple[int, int]:
    first = buf[i]
    if first < 0x80:
        return first, i + 1
    n = first & 0x7F
    if n == 0 or n > 4 or i + 1 + n > len(buf):
        raise ValueError("bad DER length")
    return int.from_bytes(buf[i + 1:i + 1 + n], "big"), i + 1 + n


def ca_issuer_urls(der: bytes) -> list[str]:
    """CA Issuers URLs (http/https) from a DER certificate's AIA extension: each AccessDescription is
    SEQUENCE { OID id-ad-caIssuers, [6] IA5String uniformResourceIdentifier }."""
    urls: list[str] = []
    start = 0
    while True:
        i = der.find(_CA_ISSUERS_OID, start)
        if i < 0:
            break
        start = i + len(_CA_ISSUERS_OID)
        if start < len(der) and der[start] == 0x86:  # [6] IMPLICIT IA5String
            try:
                n, j = _der_len(der, start + 1)
            except (ValueError, IndexError):
                continue
            url = der[j:j + n].decode("ascii", errors="replace")
            if url.lower().startswith(("http://", "https://")) and url not in urls:
                urls.append(url)
    return urls


def _cert_name_fields(der: bytes) -> tuple[bytes, bytes]:
    """(issuer, subject) raw DER of a certificate: TBSCertificate = SEQ{ [0] version, serial, sigAlg, issuer, validity, subject, ...}."""
    def elem(buf: bytes, i: int) -> tuple[int, int, int]:  # tag, content start, end
        tag = buf[i]
        n, j = _der_len(buf, i + 1)
        return tag, j, j + n
    _, c0, _ = elem(der, 0)          # Certificate SEQUENCE
    _, t0, _ = elem(der, c0)         # TBSCertificate SEQUENCE
    i = t0
    tag, _, end = elem(der, i)
    if tag == 0xA0:                  # explicit version
        i = end
    for _ in range(2):               # serial, signature algorithm
        _, _, i = elem(der, i)
    _, _, iss_end = elem(der, i)
    issuer = der[i:iss_end]
    _, _, val_end = elem(der, iss_end)
    _, _, sub_end = elem(der, val_end)
    return issuer, der[val_end:sub_end]


def self_issued(der: bytes) -> bool:
    try:
        issuer, subject = _cert_name_fields(der)
        return issuer == subject
    except (ValueError, IndexError):
        return False


def _to_der(body: bytes) -> bytes | None:
    """A CA Issuers response is a DER certificate (RFC 5280: application/pkix-cert) or, from some CAs, PEM.
    PKCS#7 bundles (.p7c) are not parsed (rare among the CAs Norwegian sites use)."""
    if body.startswith(b"0\x82") or body.startswith(b"0\x81"):
        return body
    if b"-----BEGIN CERTIFICATE-----" in body:
        try:
            return ssl.PEM_cert_to_DER_cert(body.decode("ascii", errors="ignore").split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n")
        except ValueError:
            return None
    return None


def peer_leaf_der(host: str, port: int = 443, timeout: float = 8.0) -> bytes | None:
    """The server's own certificate, read with verification off only to learn where its issuer lives (no request is
    sent on this connection; the page fetch afterwards is fully verified)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock, ctx.wrap_socket(sock, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return None


def base_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:  # Mozilla's root list as well as the OS store (Windows only ships roots it has been asked for)
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    # a downloaded intermediate must never act as a trust anchor: the chain has to reach a trusted root
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    return ctx


class AiaRepair:
    """Thread-safe cache of downloaded issuer certificates and one repaired SSL context holding all of them."""

    def __init__(self, fetch_bytes: Callable[[str], bytes | None], leaf_reader: Callable[[str], bytes | None] = peer_leaf_der):
        self._fetch_bytes = fetch_bytes
        self._leaf_reader = leaf_reader
        self._lock = threading.Lock()
        self._by_url: dict[str, bytes | None] = {}
        self._added: set[bytes] = set()
        self._host_done: dict[str, bool] = {}
        self.context = base_context()
        self.stats = {"hosts_tried": 0, "hosts_repaired": 0, "issuer_downloads": 0}

    def _issuer(self, url: str) -> bytes | None:
        with self._lock:
            if url in self._by_url:
                return self._by_url[url]
        body = self._fetch_bytes(url)
        der = _to_der(body) if body else None
        with self._lock:
            self._by_url[url] = der
            self.stats["issuer_downloads"] += 1
        return der

    def repair(self, host: str) -> bool:
        """Download the missing issuers for this host's chain into the shared context. True if anything new was
        added (the caller then retries once with self.context); the retry still does full verification."""
        with self._lock:
            if host in self._host_done:
                return self._host_done[host]
            self.stats["hosts_tried"] += 1
        leaf = self._leaf_reader(host)
        added = known = False
        cert = leaf
        for _ in range(MAX_AIA_DEPTH):
            if not cert or self_issued(cert):
                break
            urls = ca_issuer_urls(cert)
            nxt = None
            for url in urls[:2]:
                nxt = self._issuer(url)
                if nxt:
                    break
            if not nxt:
                break
            with self._lock:
                if nxt in self._added:
                    known = True  # added earlier for another host on the same chain
                elif not self_issued(nxt):  # never add a self-signed download: roots come only from the trust store
                    try:
                        self.context.load_verify_locations(cadata=nxt)
                    except (ssl.SSLError, ValueError, TypeError):
                        break  # main2 fresh13: an issuer download that is not a usable certificate ends the repair
                    self._added.add(nxt)
                    added = True
            cert = nxt
        with self._lock:
            self._host_done[host] = added or known
            if added:
                self.stats["hosts_repaired"] += 1
        return self._host_done[host]


# ---------------------------------------------------------------- rate limits
def retry_after_seconds(value: str | None, now: float | None = None) -> float | None:
    """Retry-After as seconds (delta-seconds or HTTP-date); None when absent or unparseable."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


def resolved_ip(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)[0][4][0]
    except (OSError, IndexError):
        return None


class Cooldowns:
    """Per-key (host or IP) 'do not ask before' times, set from 429/503 answers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}
        self.events: list[dict[str, Any]] = []

    def set(self, keys: list[str], seconds: float, note: str = "") -> None:
        until = time.monotonic() + seconds
        with self._lock:
            for k in keys:
                if k:
                    self._until[k] = max(self._until.get(k, 0.0), until)
            if len(self.events) < 500:
                self.events.append({"keys": keys, "seconds": round(seconds, 1), "note": note})

    def wait_needed(self, keys: list[str]) -> float:
        now = time.monotonic()
        with self._lock:
            return max([self._until.get(k, 0.0) - now for k in keys if k] + [0.0])


def host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


# ---------------------------------------------------------------- script shells (flag shell_read)
import html as _html
import json as _json
import re as _re

_SKIP_FORWARD = ("/lander", "sgcaptcha", "captcha", "/cdn-cgi/", "challenge")
_META_REFRESH = _re.compile(r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*["']?\s*\d*\s*;?\s*url\s*=\s*['"]?([^"'>\s]+)""", _re.I)
_JS_LOCATION = _re.compile(r"""(?:window|document|top|self)\.location(?:\.href)?\s*=\s*['"]([^'"]+)['"]|location\.replace\(\s*['"]([^'"]+)['"]\s*\)""", _re.I)


def _idna_url(url: str) -> str | None:
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if not host or parts.scheme not in ("http", "https"):
            return None
        ascii_host = host.encode("idna").decode("ascii")
        netloc = ascii_host + (f":{parts.port}" if parts.port else "")
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    except (UnicodeError, ValueError):
        return None


def forward_target(raw_html: str, base_url: str) -> str | None:
    """Where an (almost) empty homepage forwards a reader: a frameset/full-page iframe (registrar "web forwarding"),
    a meta refresh, or a script assigning location. Parking landers and bot challenges are never followed."""
    candidates: list[str] = []
    frames = _re.findall(r"""<i?frame[^>]+src\s*=\s*["']([^"']+)["']""", raw_html, _re.I)
    if frames and (_re.search(r"<frameset", raw_html, _re.I) or len(frames) == 1):
        candidates.append(frames[0])
    m = _META_REFRESH.search(raw_html)
    if m:
        candidates.append(m.group(1))
    m = _JS_LOCATION.search(raw_html)
    if m:
        candidates.append(m.group(1) or m.group(2))
    for c in candidates:
        target = urllib.parse.urljoin(base_url, _html.unescape(c.strip()))
        if any(s in target.lower() for s in _SKIP_FORWARD):
            continue
        target = _idna_url(target)
        if target and target.rstrip("/") != base_url.rstrip("/"):
            return target
    return None


_JSON_SCRIPT = _re.compile(r"""<script[^>]+(?:type\s*=\s*["']application/json["']|id\s*=\s*["'](?:__NEXT_DATA__|wix-warmup-data|wix-viewer-model)["'])[^>]*>(.*?)</script>""", _re.I | _re.S)


def embedded_json_text(raw_html: str, limit: int = 20_000) -> str:
    """Human-readable strings from JSON the server already put in the page (Next.js __NEXT_DATA__, Wix warmup data,
    other application/json blocks): values with a letter and a space or digit, no URLs/code, de-duplicated."""
    out: list[str] = []
    seen: set[str] = set()

    def walk(v: Any) -> None:
        if len(" ".join(out)) > limit:
            return
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            s = " ".join(_re.sub(r"<[^>]+>", " ", _html.unescape(v)).split())
            if 3 <= len(s) <= 400 and s not in seen and _re.search(r"[A-Za-zÆØÅæøå]", s) and _re.search(r"[\s\d]", s) \
                    and not _re.match(r"^(https?:|/|\{|\[|data:|#)", s) and not _re.search(r"[{};=]{2,}|function\s*\(", s):
                seen.add(s)
                out.append(s)
    for block in _JSON_SCRIPT.findall(raw_html):
        try:
            walk(_json.loads(block.strip()))
        except ValueError:
            continue
    return " ".join(out)[:limit]
