# /// script
# requires-python = ">=3.9"
# ///
# ─── How to run ───
# PYTHONPATH=. uv run scripts/m0_collect.py --output tests/fixtures/m0

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from gitseed.m0 import FEATURE_NAMES, FeatureVector, features_at, three_month_cutoff

CATEGORIES = {
    "coding-agents": "topic:coding-agents created:2025-01-27..2025-07-27 fork:false archived:false",
    "mcp": "topic:mcp created:2025-01-27..2025-07-27 fork:false archived:false",
    "local-ai": "topic:local-ai created:2025-01-27..2025-07-27 fork:false archived:false",
}


def _search(query: str, token: str | None) -> tuple[int, dict[str, str], bytes]:
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": query, "per_page": 100, "sort": "created", "order": "asc"}
    )
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "gitseed-m0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _vector_json(vector: FeatureVector) -> dict[str, bool]:
    return dict(zip(FEATURE_NAMES, vector.values()))


def _collect_features(
    repo: str, clone_url: str, created_at: str, clone_root: Path
) -> dict[str, bool] | None:
    destination = clone_root / repo.replace("/", "__")
    subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", "--quiet", clone_url, str(destination)],
        check=True,
        capture_output=True,
        timeout=180,
    )
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    vector = features_at(destination, three_month_cutoff(created))
    return None if vector is None else _vector_json(vector)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output: Path = args.output
    token = os.environ.get("GITHUB_TOKEN")
    raw_responses: dict[str, dict[str, object]] = {}
    selected: list[tuple[str, dict[str, object]]] = []
    seen: set[str] = set()
    incomplete_reasons: list[str] = []
    if not token:
        incomplete_reasons.append("GITHUB_TOKEN absent; unauthenticated collection cannot establish a complete sample")
    for category, query in CATEGORIES.items():
        status, headers, body = _search(query, token)
        response = json.loads(body or b"{}")
        items = response.get("items", []) if isinstance(response, dict) else []
        raw_responses[category] = {
            "query": query,
            "status": status,
            "rate_limit_remaining": headers.get("X-RateLimit-Remaining"),
            "body": response,
        }
        if status != 200:
            incomplete_reasons.append(f"{category} search returned HTTP {status}")
            continue
        if len(items) < 40:
            incomplete_reasons.append(f"{category} search returned only {len(items)} repositories")
        for item in items[:40]:
            repo = item.get("full_name") if isinstance(item, dict) else None
            if not isinstance(repo, str) or repo in seen:
                continue
            seen.add(repo)
            selected.append((category, item))
    output.mkdir(parents=True, exist_ok=True)
    (output / "search-responses.json").write_text(
        json.dumps(raw_responses, indent=2, sort_keys=True) + "\n"
    )
    accessible: list[dict[str, object]] = []
    inaccessible: list[dict[str, str]] = []
    unscoreable: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="gitseed-m0-") as temporary:
        clone_root = Path(temporary)
        for category, item in selected:
            repo = str(item["full_name"])
            try:
                features = _collect_features(
                    repo, str(item["clone_url"]), str(item["created_at"]), clone_root
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                inaccessible.append({"repo": repo, "reason": str(error)})
                continue
            if features is None:
                unscoreable.append({"repo": repo, "reason": "no commit at three-month cutoff"})
                continue
            accessible.append(
                {
                    "repo": repo,
                    "category": category,
                    "created_at": item["created_at"],
                    "stars": item["stargazers_count"],
                    "features": features,
                }
            )
    fixture = {
        "github_token_present": bool(token),
        "collection_complete": not incomplete_reasons,
        "incomplete_reasons": incomplete_reasons,
        "selected_count": len(selected),
        "accessible": accessible,
        "inaccessible": inaccessible,
        "unscoreable": unscoreable,
    }
    (output / "samples.json").write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
