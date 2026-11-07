#!/usr/bin/env python3
"""Signalpost evaluator command: one terminal JSON envelope per input organisation number.

    uv run python scripts/run_competition_batch.py --organisations companies.jsonl --output out/envelopes.jsonl

Input: JSONL ({"organisation_number": "..."} per line), a JSON list, or plain text (one number per line).
Optional: --bulk <BRREG enheter CSV(.gz)> frozen registry snapshot (otherwise the live register API is used),
--previous <envelopes.jsonl> from an earlier run (refresh diff + carry-forward), --time-budget-seconds.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.agent import AGENT_VERSION, Agent, load_registry, read_inputs  # noqa: E402
from norway_company_agent.evidence import utc_now  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
    tmp.replace(path)


def read_previous(path: str | None) -> dict[str, dict]:
    """Envelopes of an earlier run keyed by organisation number. Lane 9: a truncated, corrupt or foreign-shaped
    file never aborts the run; unusable lines and claims are skipped (that company simply gets no refresh diff)."""
    previous: dict[str, dict] = {}
    if not path or not Path(path).exists():
        return previous
    try:
        from norway_company_agent.inputs import decode_bytes
        text = decode_bytes(Path(path).read_bytes())
    except OSError as exc:
        print(f"warning: previous snapshot unreadable: {exc}", file=sys.stderr)
        return previous
    for line in text.splitlines():
        try:
            env = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if not isinstance(env, dict) or not isinstance(env.get("organisation_number"), str):
            continue
        claims = env.get("claims") if isinstance(env.get("claims"), list) else []
        env["claims"] = [c for c in claims if isinstance(c, dict) and isinstance(c.get("key"), str) and isinstance(c.get("field"), str)
                         and isinstance(c.get("evidence_ids", []), list)]
        env["evidence"] = [e for e in (env.get("evidence") if isinstance(env.get("evidence"), list) else []) if isinstance(e, dict) and "id" in e]
        if not isinstance(env.get("run"), dict):
            env["run"] = {}
        previous[env["organisation_number"]] = env
    return previous


class Watchdog:
    """Lane 9 last line of defence: if the pipeline has not written its envelopes `grace` seconds after the time
    budget (a hung phase, a stuck thread), write one terminal envelope per input from what is known (bulk identity,
    state `failed`) and exit, so the run is never scored as a timeout or a missing result."""

    def __init__(self, at: float, emit) -> None:
        self.lock = threading.Lock()
        self.done = False
        self._emit = emit
        self._timer = threading.Timer(max(1.0, at - time.monotonic()), self._fire)
        self._timer.daemon = True
        self._timer.start()

    def claim(self) -> bool:
        with self.lock:
            if self.done:
                return False
            self.done = True
            self._timer.cancel()
            return True

    def _fire(self) -> None:
        if not self.claim():
            return
        try:
            self._emit()
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0)  # the emergency output is complete and valid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--organisations", required=True)
    ap.add_argument("--output", required=True, help="terminal envelope JSONL")
    ap.add_argument("--bulk", default=os.environ.get("SIGNALPOST_BULK"), help="BRREG enheter bulk CSV (gzip) snapshot; optional")
    ap.add_argument("--report", help="run report JSON (default: <output>.report.json)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--expected-count", type=int, default=None, help="warn (never abort) if the input count differs")
    ap.add_argument("--previous", help="envelopes JSONL from a previous run for refresh diffs")
    ap.add_argument("--time-budget-seconds", type=float, default=float(os.environ.get("SIGNALPOST_TIME_BUDGET_SECONDS", 5400)))
    ap.add_argument("--company-cap-seconds", type=float, default=150)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("SIGNALPOST_WORKERS", 12)))
    ap.add_argument("--modules", default="registry_live,financials,roles,group,locations,website,hiring")
    ap.add_argument("--strategies", default="name_guess,email_domain,no_domain_legal_name,crawl,nav,nav_homepage,nav_containment,brreg_events,ted,cc_domains,site_hiring,site_news,social_plus,follow_redirects,state_aid,smilefjes,fin_licences,www_swap,tls_aia")
    ap.add_argument("--nav-lookback-days", type=int, default=120)
    ap.add_argument("--events-since", default="2018-01-01", help="brreg_events strategy: earliest register update date (YYYY-MM-DD)")
    ap.add_argument("--ted-since", default="2021-01-01", help="ted strategy: earliest contract-award publication date (YYYY-MM-DD)")
    ap.add_argument("--profiles-output", help="ignored (kept for starter-kit command compatibility)")
    ap.add_argument("--viewer", help="also write a static HTML viewer to this path")
    ap.add_argument("--site", default=os.environ.get("SIGNALPOST_SITE"),
                    help="directory for the static company site (default: <output dir>/site; 'none' or SIGNALPOST_SITE=0 disables)")
    ap.add_argument("--synthesis", action=argparse.BooleanOptionalAction, default=os.environ.get("SIGNALPOST_SYNTHESIS", "1") != "0",
                    help="add the deterministic per-company `synthesis` section (default on; --no-synthesis or SIGNALPOST_SYNTHESIS=0 disables)")
    args = ap.parse_args()

    t0 = time.monotonic()
    started_at = utc_now()
    run_id = args.run_id or "signalpost-" + started_at[:19].replace(":", "").replace("-", "")
    output = Path(args.output)
    report_path = Path(args.report) if args.report else output.with_suffix(".report.json")
    orgs, problems = read_inputs(args.organisations)
    if args.expected_count is not None and args.expected_count != len(orgs):
        print(f"warning: expected {args.expected_count} organisations, received {len(orgs)} unique", file=sys.stderr)
    registry, registry_meta = load_registry(args.bulk, orgs)
    previous = read_previous(args.previous)
    agent = Agent(run_id=run_id, budget_s=args.time_budget_seconds - (time.monotonic() - t0), company_cap_s=args.company_cap_seconds,
                  workers=args.workers, modules={m.strip() for m in args.modules.split(",") if m.strip()},
                  strategies={s.strip() for s in args.strategies.split(",") if s.strip()}, nav_lookback_days=args.nav_lookback_days, events_since=args.events_since, ted_since=args.ted_since,
                  log=lambda m: print(m, file=sys.stderr, flush=True))
    run_started_at = utc_now()

    def fallback_one(o: str, why: str) -> dict:
        try:
            return agent.failed_envelope(o, registry.get(o), run_started_at, why)
        except Exception:  # never let the fallback itself fail
            return {"organisation_number": o, "run_id": run_id, "state": "failed", "terminal_status": "failed",
                    "run": {"run_id": run_id, "started_at": run_started_at, "completed_at": utc_now(), "terminal_status": "failed"},
                    "identity": {}, "sections": {}, "claims": [], "evidence": [], "summary": {"text": "", "supported_claim_ids": [], "unknowns": [why]},
                    "changes": [], "errors": [{"step": "batch", "error": why}], "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0}}

    def fallback_envelopes(why: str) -> list[dict]:
        return [fallback_one(o, why) for o in orgs]

    def emergency() -> None:
        print("watchdog: pipeline did not finish within the time budget; writing failed envelopes", file=sys.stderr, flush=True)
        write_jsonl(output, fallback_envelopes("watchdog: pipeline did not finish within the time budget"))
        report_path.write_text(json.dumps({"run_id": run_id, "agent_version": AGENT_VERSION, "input_count": len(orgs), "emitted_envelopes": len(orgs),
                                           "watchdog_fired": True, "input_problems": problems, "validation": {"passed": True}}, indent=2) + "\n", encoding="utf-8")

    grace = float(os.environ.get("SIGNALPOST_WATCHDOG_GRACE_SECONDS", 45))
    watchdog = Watchdog(t0 + args.time_budget_seconds + grace, emergency)
    try:
        envelopes, details = agent.run(orgs, registry, previous)
        by_org = {e["organisation_number"]: e for e in envelopes}
        envelopes = [by_org.get(o) or fallback_one(o, "no envelope produced for this input") for o in orgs]
    except Exception as exc:  # lane 9: a crash anywhere in the pipeline still yields one terminal envelope per input
        import traceback
        traceback.print_exc()
        envelopes = fallback_envelopes(f"pipeline error: {type(exc).__name__}: {exc}"[:300])
        details = {"fetch": {}, "nav": None, "nav_fetch": None, "timed_out_companies": [], "pipeline_error": f"{type(exc).__name__}: {exc}"[:300]}
    if args.synthesis:
        try:
            from norway_company_agent.synthesis import add_synthesis
            envelopes = [add_synthesis(e) for e in envelopes]  # additive section; never raises per envelope
            if os.environ.get("SIGNALPOST_SUMMARY_FROM_SYNTHESIS", "1") != "0":  # summary.text = cited synthesis brief (old text kept as template_text)
                from norway_company_agent.synthesis import summary_from_synthesis
                envelopes = [summary_from_synthesis(e) for e in envelopes]
        except Exception as exc:  # synthesis never blocks the envelopes
            print(f"synthesis failed: {exc}", file=sys.stderr)
    if not watchdog.claim():
        time.sleep(3600)  # the watchdog is writing the emergency output and will exit the process
    write_jsonl(output, envelopes)
    completed_at = utc_now()
    states: dict[str, int] = {}
    fam: dict[str, int] = {}
    for e in envelopes:
        states[e["state"]] = states.get(e["state"], 0) + 1
        for c in e["claims"]:
            if c["availability"] == "available":
                fam[c["family"]] = fam.get(c["family"], 0) + 1
    runtimes = sorted(e["operations"]["runtime_ms"] for e in envelopes)
    pct = lambda q: runtimes[min(len(runtimes) - 1, int(q * len(runtimes)))] if runtimes else None
    report = {
        "run_id": run_id, "agent_version": AGENT_VERSION, "started_at": started_at, "completed_at": completed_at,
        "input_count": len(orgs), "input_problems": problems, "emitted_envelopes": len(envelopes),
        "validation": {"one_envelope_per_input": len(envelopes) == len(orgs) and len({e["organisation_number"] for e in envelopes}) == len(orgs),
                       "all_terminal": all(e["state"] in ("completed", "partial", "failed", "not_found") for e in envelopes)},
        "states": states, "available_claims_by_family": fam,
        "company_runtime_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": runtimes[-1] if runtimes else None},
        "registry": registry_meta, "operations": {**details["fetch"], "third_party_cost_usd": 0, "wall_seconds": round(time.monotonic() - t0, 1)},
        "nav": details["nav"], "ted": details.get("ted"), "official_signals": {k: details.get(k) for k in ("smilefjes", "state_aid", "fin_licences", "rcn_projects") if details.get(k)}, "nav_fetch": details["nav_fetch"], "timed_out_companies": details["timed_out_companies"],
        "time_budget_seconds": args.time_budget_seconds, "modules": args.modules, "strategies": args.strategies,
    }
    report["validation"]["passed"] = all(report["validation"].values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.viewer:
        try:
            from norway_company_agent.viewer import write_viewer
            write_viewer(envelopes, Path(args.viewer))
        except Exception as exc:  # the viewer never blocks the envelopes
            print(f"viewer failed: {exc}", file=sys.stderr)
    site_dir = None if str(args.site or "").lower() in ("0", "none", "off") else Path(args.site) if args.site else output.parent / "site"
    if site_dir is not None:
        try:  # self-contained static site (find, compare, verify; no network): open <site_dir>/index.html
            from norway_company_agent.viewer_site import build_site
            built = build_site(envelopes, site_dir)
            print(f"site: {Path(built['out_dir']).resolve() / 'index.html'} ({built['companies']} companies)", file=sys.stderr)
        except Exception as exc:  # the site never blocks the envelopes
            print(f"site failed: {exc}", file=sys.stderr)
    print(json.dumps({k: report[k] for k in ("run_id", "input_count", "emitted_envelopes", "validation", "states", "available_claims_by_family", "operations")}, indent=1))
    return 0 if report["validation"]["passed"] else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # lane 9: exit now; do not wait for worker threads still blocked in network calls after the deadline
    # (ThreadPoolExecutor workers are joined at interpreter exit, which kept finished runs alive for minutes)
    os._exit(code)
