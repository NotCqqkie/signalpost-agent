"""Add the deterministic `synthesis` section to every envelope in a JSONL file (post-processing an existing run).

    python scripts/add_synthesis.py runs/<run>/envelopes.jsonl runs/<run>/envelopes.synth.jsonl

Prints a validation summary (citation problems must be 0). Lines that are not JSON objects are copied unchanged.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.synthesis import add_synthesis, validate_synthesis  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if src.resolve() == dst.resolve():
        raise SystemExit("refusing to overwrite the input run; write to a new file")
    status: Counter = Counter()
    problems, errors, n = 0, 0, 0
    out_lines = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            env = json.loads(line)
        except json.JSONDecodeError:
            out_lines.append(line)
            status["unparseable"] += 1
            continue
        env = add_synthesis(env)
        n += 1
        if isinstance(env, dict):
            syn = env["synthesis"]
            status[syn["status"]] += 1
            problems += len(validate_synthesis(env))
            errors += len(syn.get("errors") or [])
        out_lines.append(json.dumps(env, ensure_ascii=False, separators=(",", ":")))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(json.dumps({"envelopes": n, "status": dict(status), "citation_problems": problems, "topic_errors": errors, "output": str(dst)}))
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
