"""Tolerant reader for the organisation-number batch file (lane 9 robustness).

Accepted: JSONL (one object or value per line), a JSON array, one JSON object (optionally wrapping a list under
`organisation_numbers` / `organisations` / `companies`), concatenated pretty-printed objects, plain text or CSV (one
number per line, header rows ignored). Tolerates UTF-8 with or without BOM, UTF-16/32 with BOM, Windows-1252, CRLF,
blank lines, trailing whitespace, numbers written as "923 609 016", "923.609.016", "NO923609016MVA", JSON numbers
(also 923609016.0), case or spelling variants of the key (organization_number, orgNr, Organisasjonsnummer) and one
level of nesting ({"company": {"organisation_number": ...}}).

Never raises on content: a record without any digits is reported in `problems` and gets no envelope (documented
rejection); a record with digits but not a valid 9-digit number still gets one (terminal `not_found`) envelope and a
problem row; duplicates are collapsed to one envelope and reported.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ORG_KEYS = {"organisationnumber", "organizationnumber", "organisasjonsnummer", "orgnr", "orgno", "orgnumber", "orgnummer",
            "organisationno", "organizationno", "orgnrs", "organisation", "organization", "org", "orgid", "organisationid",
            "organizationid", "enhet", "orgnum"}
LIST_KEYS = ("organisation_numbers", "organization_numbers", "organisations", "organizations", "companies", "items", "data", "records")
NINE = re.compile(r"(?<!\d)(\d{3}[ .\- ]?\d{3}[ .\- ]?\d{3})(?!\d)")
_DEC = json.JSONDecoder()


def decode_bytes(data: bytes) -> str:
    """Decode a text file of unknown encoding; never raises."""
    for bom, enc in ((b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\xef\xbb\xbf", "utf-8-sig"),
                     (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if data.startswith(bom):
            return data[len(bom):].decode(enc.replace("-sig", ""), errors="replace")
    head = data[:400]
    if head and head.count(b"\x00") > len(head) // 4:  # UTF-16 without BOM
        return data.decode("utf-16-le" if head[1:2] == b"\x00" else "utf-16-be", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _json_stream(text: str) -> list[Any] | None:
    """All JSON values in `text` (array, object, JSONL, concatenated objects), or None if any part is not JSON."""
    values: list[Any] = []
    i, n = 0, len(text)
    while True:
        while i < n and text[i] in " \t\r\n,﻿\x00":
            i += 1
        if i >= n:
            return values
        try:
            value, i = _DEC.raw_decode(text, i)
        except ValueError:
            return None
        if not isinstance(value, (dict, list)):
            return None  # a bare top-level scalar ("923 609 016" splits into 923, 609, ...): read line by line instead
        values.append(value)


def _flatten(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for v in values:
        if isinstance(v, list) and len(values) == 1:
            out.extend(v)
        elif isinstance(v, dict) and _org_value(v) is None:
            listed = next((v[k] for k in LIST_KEYS if isinstance(v.get(k), list)), None)
            out.extend(listed if listed is not None else [v])
        else:
            out.append(v)
    return out


def _norm_key(k: Any) -> str:
    return re.sub(r"[^a-z]", "", str(k).lower().replace("æ", "ae").replace("ø", "o").replace("å", "a"))


def _has_digits(v: Any) -> bool:
    return not isinstance(v, (dict, list, bool)) and v is not None and bool(re.search(r"\d", str(v)))


def _org_value(value: dict, depth: int = 0) -> Any:
    """The organisation-number field of a record: exact key variants first, then any org*nr/num key, then one nested level."""
    items = [(_norm_key(k), v) for k, v in value.items()]
    for nk, v in items:
        if nk in ORG_KEYS and _has_digits(v):
            return v
    for nk, v in items:
        if nk.startswith("org") and ("nr" in nk or "num" in nk or nk.endswith("no")) and _has_digits(v):
            return v
    if depth < 1:
        for v in value.values():
            if isinstance(v, dict):
                found = _org_value(v, depth + 1)
                if found is not None:
                    return found
    return None


def org_from_value(value: Any) -> tuple[str, str]:
    """(digits, raw text for reports) of one input record."""
    if isinstance(value, dict):
        value = _org_value(value)
    while isinstance(value, list):
        value = value[0] if value else None
    if value is None or isinstance(value, bool):
        return "", str(value)
    if isinstance(value, float):
        raw = str(int(value)) if value.is_integer() else repr(value)
        return re.sub(r"\D", "", raw) if value.is_integer() else "", raw
    raw = str(value).strip()
    m = NINE.search(raw)
    if m:
        return re.sub(r"\D", "", m.group(1)), raw
    return re.sub(r"\D", "", raw), raw


def mod11_ok(org: str) -> bool:
    if len(org) != 9 or not org.isdigit():
        return False
    s = sum(int(d) * w for d, w in zip(org[:8], (3, 2, 7, 6, 5, 4, 3, 2)))
    r = 11 - s % 11
    return (0 if r == 11 else r) == int(org[8]) and r != 10


def parse_text(text: str) -> tuple[list[str], list[dict]]:
    text = text.replace("﻿", "")
    stripped = text.strip()
    values: list[Any] = []
    if stripped:
        stream = _json_stream(stripped) if stripped[0] in "[{" else None
        if stream is not None:
            values = _flatten(stream)
        else:
            for line in text.splitlines():
                line = line.strip().strip("\x00")
                if not line:
                    continue
                try:
                    values.append(json.loads(line))
                except ValueError:
                    values.append(line)
    orgs: list[str] = []
    problems: list[dict] = []
    seen: set[str] = set()
    for value in values:
        org, raw = org_from_value(value)
        if not org:
            problems.append({"input": raw[:60], "problem": "no organisation number in this record; skipped"})
            continue
        if org in seen:
            problems.append({"input": raw[:60], "problem": "duplicate input; one envelope emitted"})
            continue
        if len(org) != 9:
            problems.append({"input": raw[:60], "problem": "not a 9-digit organisation number"})
        elif not mod11_ok(org):
            problems.append({"input": raw[:60], "problem": "organisation number fails the mod-11 check digit"})
        seen.add(org)
        orgs.append(org)
    return orgs, problems


def read_inputs(path: str) -> tuple[list[str], list[dict]]:
    """Return (unique organisation numbers in input order, input problems). Never raises on file content."""
    return parse_text(decode_bytes(Path(path).read_bytes()))
