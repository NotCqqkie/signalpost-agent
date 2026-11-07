import copy
import json
import os
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.synthesis import LEGAL_FORMS, add_synthesis, build_synthesis, validate_synthesis  # noqa: E402

REG = "https://data.brreg.no/enhetsregisteret/api/enheter/986389180"
ACC = "https://data.brreg.no/regnskapsregisteret/regnskap/986389180"


def envelope(form: str = "AS", **over) -> dict:
    """A small contract envelope in the main lane's shape (claims + evidence + availability)."""
    claims, evidence = [], []

    def ev(url, span, period=None):
        i = f"ev-{len(evidence) + 1}"
        evidence.append({"id": i, "source_url": url, "source_class": "official_registry_live", "retrieved_at": "2026-11-06T00:00:00Z",
                         "content_sha256": "0" * 64, "claim_span": span, "reporting_period": period})
        return i

    def c(section, field, value, availability="available", family="registry", key=None, evidence=True, **kw):
        cid = f"c-{len(claims) + 1}"
        ids = [ev(ACC if family == "financials" else REG, f"{field}={value}", kw.get("reporting_period"))] if evidence and availability == "available" else []
        claims.append({"id": cid, "section": section, "field": field, "family": family, "key": key or field, "value": value,
                       "availability": availability, "confidence": 1.0 if availability == "available" else None, "evidence_ids": ids, **kw})
        return cid

    L, A, P, W, H = "legal_identity", "annual_accounts", "leadership_workplaces", "website_profiles", "hiring_activity"
    c(L, "legal_name", "HELGEVOLD AS")
    c(L, "organisation_number", "986389180")
    c(L, "legal_form", form)
    c(L, "business_address", {"adresse": ["Saudavegen 5772"], "kommune": "VINDAFJORD", "postnummer": "5585", "poststed": "SANDEID", "land": "Norge"})
    c(L, "industry", {"kode": "43.210", "beskrivelse": "Elektrisk installasjonsarbeid"})
    c(L, "registered_employees", 332)
    c(L, "founded_date", "2003-12-10")
    c(L, "stated_activity", "Elektrisk installasjonsvirksomhet.")
    c(L, "bankrupt", False)
    c(L, "under_liquidation", False)
    for year, rev, res, eq, assets in (("2025", 461342972.0, -9396745.0, 38215595.0, 199262643.0), ("2024", 407484468.0, 15618115.0, 47612340.0, 180000000.0)):
        per = f"{year}-01-01/{year}-12-31"
        for f, v in (("revenue", rev), ("annual_result", res), ("equity", eq), ("assets", assets)):
            c(A, f, v, family="financials", key=f"{f}@{year}-12-31", reporting_period=per, unit="NOK", latest=year == "2025", account_type="SELSKAP")
    c(A, "operating_result", None, "not_available", family="financials", key="operating_result@2025-12-31", reporting_period="2025-01-01/2025-12-31",
      note="field absent in the filed accounts record (not treated as zero)")
    c(P, "role", {"role": "Daglig leder", "role_code": "DAGL", "name": "Filippo Ballarin", "organisation_number": None, "last_changed": "2026-02-06"}, family="roles", key="role:DAGL")
    c(P, "role", {"role": "Styrets leder", "role_code": "LEDE", "name": "Bjørn Tollefsen", "organisation_number": None, "last_changed": "2020-01-01"}, family="roles", key="role:LEDE")
    c(P, "role", {"role": "Revisor", "role_code": "REVI", "name": ["CEDRA NORGE AS"], "organisation_number": "935416779"}, family="roles", key="role:REVI")
    c(P, "registered_workplace", {"organisation_number": "973000001", "name": "HELGEVOLD AS AVD STORD", "address": {"kommune": "STORD"}}, family="workplaces", key="subunit:1")
    c(P, "group_structure", None, "not_available", family="group", note="no group structure published for this entity")
    c(W, "official_website", "https://helgevold.com/", family="website", key="website", identity_method="orgnr_on_site")
    c(W, "social_profile", {"platform": "linkedin", "url": "https://linkedin.com/company/helgevold"}, family="social_profiles", key="social:li")
    c(H, "job_posting", None, "not_available", family="hiring", note="no active NAV ad with this organisation number (feed: not_run)")
    c(H, "dated_activity", {"date": "2026-01-07", "title": "Lederbytte", "url": "https://helgevold.com/lederbytte/"}, family="dated_activity", key="act:1")
    env = {"organisation_number": "986389180", "state": "completed", "identity": {"name": "HELGEVOLD AS", "legal_form": form, "latest_submitted_accounts": "2025"},
           "claims": claims, "evidence": evidence, "changes": [], "refresh": {"previous_run_id": None}}
    env.update(over)
    return env


def text(env: dict) -> str:
    return build_synthesis(env)["text"]


class SynthesisContentTests(unittest.TestCase):
    def test_every_sentence_is_cited_and_valid(self):
        env = add_synthesis(envelope())
        syn = env["synthesis"]
        self.assertEqual(syn["status"], "available")
        self.assertEqual(validate_synthesis(env), [])
        self.assertTrue(all(s["claim_ids"] and s["evidence_ids"] for s in syn["sentences"]))
        self.assertNotIn("errors", syn)

    def test_profile_covers_brief_topics(self):
        t = text(envelope())
        for needle in ("private limited company", "Vindafjord", "Elektrisk installasjonsarbeid", "managing director Filippo Ballarin",
                       "Latest filed company accounts (2025)", "NOK 461.3 million", "Revenue was up 13% on 2024", "https://helgevold.com/",
                       "linkedin", "2026-01-07", "332 employees", "1 registered workplace (subunit) in Stord", "CEDRA NORGE AS (935416779)"):
            self.assertIn(needle, t)

    def test_missing_financial_field_is_unknown_not_zero(self):
        syn = build_synthesis(envelope())
        self.assertNotIn("operating result NOK 0", syn["text"])
        self.assertTrue(any("operating result" in u["text"] and "not zero" in u["text"] for u in syn["unknowns"]))

    def test_unknowns_name_reason_and_state(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "official_website":
                c.update(value=None, availability="ambiguous", evidence_ids=[], note="no candidate proved the exact legal entity")
        syn = build_synthesis(env)
        web = [u for u in syn["unknowns"] if u["topic"] == "online"][0]
        self.assertEqual(web["availability"], "ambiguous")
        self.assertIn("could not be confirmed", web["text"])
        self.assertNotIn("helgevold.com/ (", syn["text"])
        hiring = [u for u in syn["unknowns"] if u["topic"] == "hiring"][0]
        self.assertIn("not checked", hiring["text"])
        self.assertEqual(hiring["availability"], "failed")  # not checked is never shown as "checked, none found"

    def test_checked_absence_is_stated_with_its_evidence(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "job_posting":
                c.update(note="feed checked: zero ads", evidence_ids=["ev-1"])
        syn = build_synthesis(env)
        self.assertTrue(any(s.get("kind") == "checked_absence" and s["topic"] == "hiring" for s in syn["sentences"]))
        self.assertEqual(validate_synthesis(env, syn), [])

    def test_negative_equity_and_bankruptcy_warn(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "equity" and c.get("latest"):
                c["value"] = -1200000.0
            if c["field"] == "bankrupt":
                c["value"] = True
        syn = build_synthesis(env)
        self.assertIn("Equity was negative (NOK -1.2 million)", syn["text"])
        self.assertIn("flags this entity as bankrupt", syn["text"])
        self.assertIn("warning", syn["headline"])

    def test_group_parent_and_subsidiary_labelled(self):
        env = envelope()
        tree = {"navn": "MAIKEN LEONARDSEN AS", "organisasjonsnummer": "927195321", "children": [
            {"navn": "HELGEVOLD AS", "organisasjonsnummer": "986389180", "grunnlag": "90,1%", "parentNavn": "MAIKEN LEONARDSEN AS",
             "parentOrganisasjonsnummer": "927195321", "knytningsform": {"beskrivelse": "Konsern datter"},
             "children": [{"navn": "SUB AS", "organisasjonsnummer": "900000001", "children": [{"navn": "SUBSUB AS", "organisasjonsnummer": "900000002"}]}]}]}
        for c in env["claims"]:
            if c["field"] == "group_structure":
                c.update(value=tree, availability="available", evidence_ids=["ev-1"])
        t = text(env)
        self.assertIn("subsidiary (90,1% owned) of MAIKEN LEONARDSEN AS (927195321)", t)
        self.assertIn("parent company of 1 direct subsidiary and 1 further down the chain", t)

    def test_changes_are_described_and_cited(self):
        env = envelope(refresh={"previous_run_id": "run-1"}, changes=[
            {"change_type": "new_role", "field": "role", "key": "role:DAGL", "old_value": None,
             "new_value": {"role": "Daglig leder", "role_code": "DAGL", "name": "Filippo Ballarin"}, "new_evidence_ids": ["ev-1"], "material": True},
            {"change_type": "changed_value", "field": "registered_employees", "key": "registered_employees", "old_value": 300, "new_value": 332,
             "new_evidence_ids": [], "material": True},
            {"change_type": "removed_role", "field": "role", "key": "role:OLD", "old_value": {"role_code": "DAGL", "name": "Old Boss"}, "new_value": None,
             "old_evidence": [{"source_url": REG}], "material": True},
            {"change_type": "changed_value", "field": "stated_activity", "key": "x", "material": False}])
        syn = build_synthesis(env)
        self.assertEqual(len(syn["changes"]), 3)
        self.assertIn("Registered employees changed from 300 to 332", syn["text"])
        self.assertIn("Role removed: managing director Old Boss", syn["text"])
        self.assertEqual(syn["changes"][2]["previous_source_urls"], [REG])
        self.assertEqual(validate_synthesis(env, syn), [])

    def test_no_changes_with_previous_run_says_so(self):
        self.assertIn("No material changes since the previous run (run-1)", text(envelope(refresh={"previous_run_id": "run-1"})))

    def test_stale_claims_are_flagged(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "official_website":
                c["stale"] = True
        self.assertIn("Carried forward unchanged from the previous run", text(env))

    def test_every_universe_legal_form(self):
        forms = ["AS", "BRL", "ESEK", "STI", "NUF", "FLI", "SA", "ENK", "DA", "ANS", "VPFO", "SAM", "ASA", "IKS", "KS", "PRE", "PK", "SPA",
                 "ANNA", "SÆR", "BBL", "GFS", "BA", "SF", "KIRK", "KF", "SE", "XYZ", ""]
        for f in forms:
            env = envelope(f)
            syn = build_synthesis(env)
            self.assertEqual(validate_synthesis(env, syn), [], f)
            self.assertNotIn("errors", syn, f)
            if f in LEGAL_FORMS:
                self.assertIn(LEGAL_FORMS[f], syn["sentences"][0]["text"], f)
        brl = build_synthesis(envelope("BRL"))
        self.assertTrue(all(u.get("expected_for_legal_form") for u in brl["unknowns"] if u["topic"] == "hiring"))

    def test_unspecified_industry(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "industry":
                c["value"] = {"kode": "00.000", "beskrivelse": "Uoppgitt"}
        self.assertIn("no industry code", text(env))

    def test_deterministic(self):
        env = envelope()
        self.assertEqual(json.dumps(build_synthesis(env), sort_keys=True), json.dumps(build_synthesis(copy.deepcopy(env)), sort_keys=True))

    def test_add_synthesis_does_not_touch_other_sections(self):
        env = envelope()
        before = json.dumps(env, sort_keys=True)
        out = add_synthesis(env)
        self.assertEqual(json.dumps(env, sort_keys=True), before)
        self.assertEqual({k: v for k, v in out.items() if k != "synthesis"}, env)


class SynthesisRobustnessTests(unittest.TestCase):
    def test_failed_and_not_found_envelopes(self):
        for state in ("failed", "not_found"):
            env = {"organisation_number": "123456789", "state": state, "claims": [], "evidence": []}
            syn = build_synthesis(env)
            self.assertEqual(syn["status"], "not_available")
            self.assertEqual(syn["sentences"], [])
            self.assertTrue(syn["unknowns"])

    def test_starter_profile_shape(self):
        syn = build_synthesis({"run_id": "x", "organisation_number": "915665721", "state": "complete", "profile": {"name": "AHW HOLDING AS"}})
        self.assertEqual(syn["status"], "not_available")

    def test_non_dict_inputs(self):
        for bad in (None, [], "x", 5):
            self.assertEqual(add_synthesis(bad), bad)
            self.assertEqual(build_synthesis(bad)["status"], "failed")

    def test_fuzz_never_raises_and_never_miscites(self):
        rng = random.Random(20261106)
        junk = [None, "", 0, -1, 1e30, float("nan"), [], {}, [None], {"x": None}, "NaN", True, "2025", {"kode": None}, ["a", "b"]]
        base = envelope()
        for _ in range(int(os.environ.get("SIGNALPOST_SYNTH_FUZZ", 400))):
            env = copy.deepcopy(base)
            for _ in range(rng.randint(1, 8)):
                op = rng.random()
                dicts = [c for c in env["claims"] if isinstance(c, dict)] if isinstance(env.get("claims"), list) else []
                if op < 0.5 and dicts:
                    c = rng.choice(dicts)
                    k = rng.choice(["value", "availability", "evidence_ids", "field", "id", "reporting_period", "unit", "latest", "key", "note"])
                    c[k] = rng.choice(junk)
                elif op < 0.65 and dicts:
                    env["claims"].pop(rng.randrange(len(env["claims"])))
                elif op < 0.75:
                    env[rng.choice(["claims", "evidence", "identity", "changes", "refresh", "state"])] = rng.choice(junk)
                elif op < 0.85 and isinstance(env.get("claims"), list):
                    env["claims"].append(rng.choice(junk))
                else:
                    env["changes"] = [rng.choice(junk), {"change_type": rng.choice(junk), "field": rng.choice(junk), "old_value": rng.choice(junk),
                                                        "new_value": rng.choice(junk), "old_evidence": rng.choice(junk), "new_evidence_ids": rng.choice(junk)}]
            out = add_synthesis(env)
            syn = out["synthesis"]
            self.assertIn(syn["status"], ("available", "not_available", "failed"))
            json.dumps(syn)  # serialisable
            if isinstance(env.get("claims"), list) and isinstance(env.get("evidence"), list):
                self.assertEqual(validate_synthesis(env, syn), [], syn)

    @unittest.skipUnless(os.environ.get("SIGNALPOST_SYNTH_RUNS"), "set SIGNALPOST_SYNTH_RUNS=path1;path2 to fuzz on real run envelopes")
    def test_real_runs(self):
        for p in os.environ["SIGNALPOST_SYNTH_RUNS"].split(";"):
            for line in Path(p).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    env = json.loads(line)
                    syn = build_synthesis(env)
                    self.assertEqual(validate_synthesis(env, syn), [], env.get("organisation_number"))
                    self.assertNotIn("errors", syn, env.get("organisation_number"))


if __name__ == "__main__":
    unittest.main()
