# /// script
# requires-python = ">=3.9"
# ///
# ─── How to run ───
# PYTHONPATH=. uv run scripts/m0_analyze.py --fixture tests/fixtures/m0/samples.json

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gitseed.m0 import FeatureVector, RecordedSample, analyze


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    samples = tuple(
        RecordedSample(
            repo=str(row["repo"]),
            category=str(row["category"]),
            stars=int(row["stars"]),
            features=FeatureVector(**row["features"]),
        )
        for row in fixture["accessible"]
    )
    result = analyze(samples)
    print(
        json.dumps(
            {
                "collection_complete": fixture["collection_complete"],
                "inaccessible_count": len(fixture["inaccessible"]),
                "unscoreable_count": len(fixture["unscoreable"]),
                "sample_count": result.sample_count,
                "breakout_count": result.breakout_count,
                "auc": result.auc,
                "passes_preregistered_threshold": result.auc is not None and result.auc >= 0.65,
                "contributions": [
                    {"feature": name, "auc_drop": drop} for name, drop in result.contributions
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
