#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a synthetic RevisionLedger for Word COM/PDF acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from co_reviewer.word_pdf_acceptance import build_word_pdf_acceptance_ledger


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create an isolated RevisionLedger for Word COM/PDF acceptance. "
            "Uses only the synthetic five-target Word compatibility fixture."
        )
    )
    parser.add_argument("--fixture-dir", required=True)
    parser.add_argument("--ledger-out", required=True)
    args = parser.parse_args()
    result = build_word_pdf_acceptance_ledger(
        Path(args.fixture_dir),
        Path(args.ledger_out),
    )
    print("[WORD/PDF FIXTURE] Synthetic RevisionLedger created.")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("[BOUNDARY] Synthetic acceptance evidence only; no real manuscript or SI data was read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
