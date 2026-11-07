"""Lane 9: the batch-file reader never crashes and finds every organisation number in documented and near formats."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import read_inputs  # noqa: E402
from norway_company_agent.inputs import mod11_ok  # noqa: E402

A, B = "923609016", "986389180"

CASES = [
    ("jsonl", '{"organisation_number": "923609016"}\n{"organisation_number": "986389180"}\n', [A, B]),
    ("bom_crlf_blank_trailing", '﻿{"organisation_number": "923609016"}\r\n\r\n{"organisation_number": "986389180"}  \r\n', [A, B]),
    ("plain", "923609016\n986389180\n", [A, B]),
    ("spaced_and_vat", "923 609 016\nNO 986389180 MVA\n", [A, B]),
    ("dotted_dashed", "923.609.016\n986-389-180\n", [A, B]),
    ("json_array_mixed", '["923609016", 986389180]', [A, B]),
    ("json_array_objects", '[{"organisation_number": "923609016"}, {"organisation_number": "986389180"}]', [A, B]),
    ("pretty_array", '[\n  {"organisation_number": "923609016"},\n  {"organisation_number": "986389180"}\n]\n', [A, B]),
    ("one_object", '{"organisation_number": "923609016"}', [A]),
    ("wrapped_list", '{"organisation_numbers": ["923609016", "986389180"]}', [A, B]),
    ("wrapped_companies", '{"companies": [{"orgnr": "923609016"}, {"orgnr": "986389180"}]}', [A, B]),
    ("concatenated_pretty", '{\n "organisation_number": "923609016"\n}\n{\n "organisation_number": "986389180"\n}\n', [A, B]),
    ("int_and_float", '{"organisation_number": 923609016}\n{"organisation_number": 986389180.0}\n', [A, B]),
    ("key_variants", '{"Organization_Number": "923609016"}\n{"organisasjonsnummer": "986389180"}\n', [A, B]),
    ("name_key_before_number", '{"organisation": "Foo AS", "organisation_number": "923609016"}\n', [A]),
    ("nested", '{"company": {"organisation_number": "923609016"}}\n', [A]),
    ("extra_fields_unicode", '{"organisation_number": "923609016", "name": "Ærø Åsane ✓", "x": [1, {"y": 2}]}\n', [A]),
    ("csv_header", "organisation_number,name\n923609016,Foo 2 AS\n986389180,Bar AS\n", [A, B]),
    ("csv_name_first", "name;orgnr\nFoo 2 AS;923609016\n", [A]),
    ("jsonl_with_bad_line", '{"organisation_number": "923609016"}\n{bad json\n{"organisation_number": "986389180"}\n', [A, B]),
    ("jsonl_array_lines", '["923609016"]\n["986389180"]\n', [A, B]),
    ("duplicates_collapsed", '923609016\n"923609016"\n{"organisation_number": "923 609 016"}\n986389180\n', [A, B]),
    ("empty", "", []),
    ("whitespace_only", "  \r\n\n\t\n", []),
    ("null_bool_missing", 'null\ntrue\n{"name": "X"}\n923609016\n', [A]),
]


@pytest.mark.parametrize("name,text,expected", CASES, ids=[c[0] for c in CASES])
def test_formats(tmp_path, name, text, expected):
    p = tmp_path / f"{name}.txt"
    p.write_bytes(text.encode("utf-8"))
    orgs, problems = read_inputs(str(p))
    assert orgs == expected
    assert all(isinstance(x, dict) and "problem" in x for x in problems)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "cp1252", "latin-1", "utf-8-sig"])
def test_encodings(tmp_path, encoding):
    p = tmp_path / "in.jsonl"
    p.write_bytes('{"organisation_number": "923609016", "name": "Ærø Østre"}\r\n923 609 016\r\n986389180\r\n'.encode(encoding))
    orgs, _ = read_inputs(str(p))
    assert orgs == [A, B]


def test_invalid_numbers_still_get_one_record_and_a_problem(tmp_path):
    p = tmp_path / "in.txt"
    p.write_text("92360901\n923609017\nabc\n", encoding="utf-8")
    orgs, problems = read_inputs(str(p))
    assert orgs == ["92360901", "923609017"]  # each gets a terminal not_found envelope
    kinds = [x["problem"] for x in problems]
    assert any("9-digit" in k for k in kinds) and any("mod-11" in k for k in kinds) and any("no organisation number" in k for k in kinds)


def test_1100_rows(tmp_path):
    rows = [f'{{"organisation_number": "{800000000 + i}"}}' for i in range(1100)]
    p = tmp_path / "in.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    orgs, _ = read_inputs(str(p))
    assert len(orgs) == 1100 and len(set(orgs)) == 1100


def test_mod11():
    assert mod11_ok(A) and mod11_ok(B) and not mod11_ok("923609017") and not mod11_ok("12345678")
