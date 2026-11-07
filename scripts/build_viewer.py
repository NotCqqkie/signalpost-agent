"""Build the static Signalpost viewer from an envelopes JSONL file.

    python scripts/build_viewer.py runs/<run>/envelopes.jsonl runs/<run>/viewer

Open <out_dir>/index.html in any browser (works offline from file://; no network, no CDN). Envelopes without a
`synthesis` section get one computed on the fly (deterministic, no model).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.viewer_site import build_site, read_jsonl  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    envelopes = read_jsonl(Path(sys.argv[1]))
    print(json.dumps(build_site(envelopes, Path(sys.argv[2]))))


if __name__ == "__main__":
    main()
