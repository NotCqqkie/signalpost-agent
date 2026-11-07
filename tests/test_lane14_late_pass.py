"""Lane 14: the polite_429 late retry pass inside Agent.run (regression: it crashed twice on names in agent.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import norway_company_agent.agent as agent_mod  # noqa: E402
from norway_company_agent.agent import Agent  # noqa: E402

ORG = "923609016"


def test_late_pass_retries_rate_limited_candidate_after_cooldown(monkeypatch):
    ag = Agent(run_id="t", budget_s=600, modules={"registry_live", "website"}, strategies={"polite_429", "name_guess"}, log=lambda m: None)
    ag.fetcher.cooldowns.set(["vpmetall.no"], 0.2, "test")

    def phase_a(org, reg):
        return {"org": org, "errors": [], "official": {}, "ident_probe": {"organisation_number": org, "name": "VP METALL AS"},
                "site": {"status": "failed", "attempts": [{"url": "https://vpmetall.no/", "source": "registry_email",
                                                           "verdict": {"status": "failed", "reasons": ["HTTP 429"]}}]}}
    calls = []

    def discover(f, prof, guess=True, **kw):
        calls.append(prof["organisation_number"])
        return {"status": "failed", "selected": None, "attempts": [{"url": "https://vpmetall.no/", "source": "registry_email",
                                                                      "verdict": {"status": "failed", "reasons": ["HTTP 429"]}}]}
    monkeypatch.setattr(ag, "phase_a", phase_a)
    monkeypatch.setattr(agent_mod, "discover_website", discover)
    monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)
    reg = {ORG: {"organisation_number": ORG, "name": "VP METALL AS", "raw": {"navn": "VP METALL AS"}, "bulk_sha256": "h", "bulk_retrieved_at": "t"}}
    envelopes, _ = ag.run([ORG], reg, None)
    assert calls == [ORG]
    assert len(envelopes) == 1


def test_cooldown_left():
    ag = Agent(run_id="t", budget_s=600, modules={"website"}, strategies={"polite_429"}, log=lambda m: None)
    ag.fetcher._backend_keys = lambda h: [h]
    assert ag.fetcher.cooldown_left(["https://a.no/"]) == 0.0
    ag.fetcher.cooldowns.set(["a.no"], 30)
    assert 25 < ag.fetcher.cooldown_left(["https://a.no/x", "https://b.no/"]) <= 30
    off = Agent(run_id="t", budget_s=600, modules={"website"}, strategies=set(), log=lambda m: None)
    assert off.fetcher.cooldown_left(["https://a.no/"]) == 0.0
