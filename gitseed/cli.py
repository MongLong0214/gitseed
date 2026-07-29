"""The command-line seam between collection, screening, grading, and review."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import traceback
from uuid import uuid4
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Final, IO, Mapping, Protocol, Sequence
from urllib.parse import parse_qs, quote, urlparse

from .adapters import CallableFileReader, GitHubRepository, SystemClock
from .application import engine_version_mismatches, execute, re_evaluate, render, replay
from .artifact import ArtifactCollection, ArtifactReviewed, RunArtifact
from .collect.ratelimit import classify, parse
from .collect.search import Candidate, CollectResult, Transport, UrllibTransport, collect
from .grade.types import GradeResult
from .pipeline.run import FetchedFiles, FileFetchError, Reviewed, run
from .ports import RepositoryMetadata, RunPorts, RunRequest
from .review.actions import ActionOutcome, GitHubWriter, OutcomeStatus, Performed, perform, undo
from .review.approval import Approval, Decision, NotInteractive, collect_approval, collect_bulk_approval
from .review.commit import CommitFailed, GitCommitter, SubprocessGitCommitter, record_decisions, record_outcome
from .review.trailers import render_block
from .screen.coverage import SkippedFile, SourceCoverage
from .scoring import Recommendation, RecommendationStatus, ScoreInputs, WEIGHTS
from .storage import ObservationWriteError, SQLiteRunStore, StoredObservation


PREFERRED_OLLAMA_MODELS: Final = (
    "qwen2.5-coder:32b",
    "qwen2.5-coder:7b",
    "qwen2.5-coder:1.5b",
)
# A cold one-token reply from the local 32B model took about 23 seconds; a
# multi-file digest needs several times that budget instead of the old 30-second default.
DEFAULT_GRADE_TIMEOUT: Final = 120
DEFAULT_STORE: Final = Path(".gitseed") / "runs.db"
CLI_PROCESS_ID: Final = os.getpid()
MODEL_OUTPUT_EXCERPT_CHARS: Final = 160
EXIT_CODES: Final = "Exit codes: 0 complete; 1 invalid invocation or operational failure; 2 incomplete run."
SCORE_LIMIT: Final = "Score measures small-versus-medium size; it does not predict that a repository will take off."
SOURCE_FILE_BYTE_CAP: Final = 100_000
SOURCE_TOTAL_BYTE_CAP: Final = 500_000
SOURCE_FILE_COUNT_CAP: Final = 20
SOURCE_EXTENSIONS: Final = frozenset(
    {
        ".asm", ".bash", ".c", ".cc", ".clj", ".cljs", ".cpp", ".cs", ".css", ".cxx", ".dart", ".ex", ".exs",
        ".fs", ".fsx", ".go", ".groovy", ".h", ".hpp", ".hrl", ".html", ".java", ".js", ".jsx", ".kt", ".kts",
        ".lua", ".m", ".mjs", ".php", ".pl", ".pm", ".py", ".pyi", ".r", ".rb", ".rs", ".scala", ".sh", ".sql",
        ".swift", ".ts", ".tsx", ".vue", ".zsh",
    }
)
REVIEW_STATUS_ORDER: Final = {
    RecommendationStatus.REVIEW: 0,
    RecommendationStatus.INSUFFICIENT_EVIDENCE: 1,
    RecommendationStatus.NOT_PRIORITY: 2,
    RecommendationStatus.BLOCKED: 3,
}
# Manifests, lockfiles, and CI definitions: selected by exact filename
# regardless of extension, because the extension allow-list above has no
# `.json`/`.yaml`/`.lock`/extensionless entry and never will -- broadening it
# would let arbitrary data files ride along with source, not just the handful
# of build-time inputs a supply-chain attack actually targets. See
# `_is_priority_path` for why these are also exempt from the file-count cap.
PRIORITY_FILENAMES: Final = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "Dockerfile",
        "Makefile",
    }
)
# One level deep under .github/workflows/, matching the issue's own
# `.github/workflows/*.yml`/`*.yaml` glob.
_WORKFLOW_PATH: Final = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")


class FetchFiles(Protocol):
    """The read-only repository content seam used by the pipeline."""

    def __call__(self, candidate: Candidate) -> FetchedFiles: ...


class Grader(Protocol):
    def evaluate(self, digest: str) -> GradeResult: ...

    def flags_malicious(self, digest: str) -> bool: ...


def _resolve_ollama_host(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the Ollama host URL from OLLAMA_HOST environment variable or default.

    Supports formats: "host:port", "http://host:port", "https://host:port".
    Returns a URL base suitable for API calls (e.g., "http://host:port").
    """
    environment = os.environ if environ is None else environ
    host = environment.get("OLLAMA_HOST", "localhost:11434")

    # If it already starts with http:// or https://, use as-is
    if host.startswith(("http://", "https://")):
        return host.rstrip("/")

    # Otherwise, assume it's "host:port" and prepend http://
    return f"http://{host}"


def resolve_model(transport: Transport, *, environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    environment = os.environ if environ is None else environ
    explicit = environment.get("OLLAMA_MODEL")
    if explicit is not None:
        return explicit, "explicit OLLAMA_MODEL"
    ollama_base = _resolve_ollama_host(environment)
    tags_url = f"{ollama_base}/api/tags"
    try:
        status, _, body = transport.get(tags_url)
    except OSError as error:
        raise RuntimeError(
            "could not reach Ollama to find a grading model; looked for "
            f"{', '.join(PREFERRED_OLLAMA_MODELS)}; install one with ollama pull qwen2.5-coder:7b"
        ) from error
    if status != 200:
        raise RuntimeError(
            "could not read Ollama's installed models; looked for "
            f"{', '.join(PREFERRED_OLLAMA_MODELS)}; install one with ollama pull qwen2.5-coder:7b"
        )
    payload = json.loads(body)
    installed = {
        str(model["name"])
        for model in payload.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    }
    for model in PREFERRED_OLLAMA_MODELS:
        if model in installed:
            return model, "largest installed preferred model"
    raise RuntimeError(
        "Ollama has none of the supported grading models; looked for "
        f"{', '.join(PREFERRED_OLLAMA_MODELS)}; install one with ollama pull qwen2.5-coder:7b"
    )


class FixtureTransport:
    """Replay candidate metadata and source files without network access."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        payload = json.loads((directory / "candidates.json").read_text())
        self.items: list[dict[str, str | int]] = list(payload["candidates"])
        self.complete = bool(payload.get("complete", True))
        self.stopped_because = payload.get("stopped_because")
        self.total_count = int(payload.get("total_count", len(self.items)))
        self.incomplete_results = bool(payload.get("incomplete_results", False))
        self._files = {str(item["full_name"]): str(item.get("files", item["full_name"])) for item in self.items}
        self.calls: list[tuple[str, str]] = []

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        query = parse_qs(urlparse(url).query)
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["30"])[0])
        start = (page - 1) * per_page
        return 200, {"X-RateLimit-Remaining": "29"}, json.dumps(
            {
                "items": self.items[start:start + per_page],
                "total_count": self.total_count,
                "incomplete_results": self.incomplete_results,
            }
        ).encode()

    def fetch_files(self, candidate: Candidate) -> FetchedFiles:
        root = self.directory / self._files[candidate.repo]
        return FetchedFiles(tuple((str(path.relative_to(root)), path.read_text()) for path in sorted(root.rglob("*")) if path.is_file()))

    def search(self, query: str, limit: int) -> CollectResult:
        result = collect(
            query,
            transport=self,
            pages=(limit + 99) // 100,
            per_page=min(limit, 100),
        )
        return replace(
            result,
            candidates=result.candidates[:limit],
            complete=self.complete,
            stopped_because=self.stopped_because,
        )

    def metadata(
        self,
        candidate: Candidate,
        at: datetime,
    ) -> RepositoryMetadata:
        item = next(item for item in self.items if item["full_name"] == candidate.repo)
        return RepositoryMetadata(
            ScoreInputs(
                item.get("commit_cadence_30d"),
                item.get("contributor_count"),
                item.get("has_license"),
            )
        )

    def star(self, repo: str) -> None:
        self.calls.append(("star", repo))

    def unstar(self, repo: str) -> None:
        self.calls.append(("unstar", repo))

    def follow(self, user: str) -> None:
        self.calls.append(("follow", user))

    def unfollow(self, user: str) -> None:
        self.calls.append(("unfollow", user))


class FixtureGrader:
    """Return recorded grades keyed by the repository named in a digest."""

    def __init__(self, directory: Path) -> None:
        self.grades: dict[str, dict[str, str | int | float]] = json.loads((directory / "grades.json").read_text())

    def evaluate(self, digest: str) -> GradeResult:
        repo = digest.splitlines()[0].removeprefix("repository: ")
        if not repo:
            return GradeResult(7, 6, "fixture smoke response", "fixture", 0.0, "fixture-v1")
        return GradeResult(**self.grades[repo])

    def flags_malicious(self, digest: str) -> bool:
        return "base64.b64decode" in digest


def _is_priority_path(path: str) -> bool:
    """A manifest, lockfile, or workflow file -- fetched regardless of extension.

    These are exactly the files a supply-chain attack targets: install hooks,
    pinned dependencies, CI trigger conditions. `SOURCE_EXTENSIONS` alone
    cannot select them (GS-P0-001), and even once it can, they must not be
    the files an attacker crowds out by padding earlier tree entries
    (GS-P0-008) -- see the priority-first selection in `fetch_files` below.
    """
    return Path(path).name in PRIORITY_FILENAMES or bool(_WORKFLOW_PATH.match(path))


class GitHubClient:
    """Read repository text and execute reviewed GitHub account actions."""

    def __init__(self, transport: UrllibTransport) -> None:
        self.transport = transport

    def fetch_files(self, candidate: Candidate) -> FetchedFiles:
        repo = quote(candidate.repo, safe="/")
        status, headers, body = self.transport.get(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1")
        if error := _github_response_error(status, headers):
            raise error
        tree = json.loads(body).get("tree", [])

        discovered = 0
        eligible = 0
        priority_candidates: list[tuple[str, str, int]] = []
        regular_candidates: list[tuple[str, str, int]] = []
        skipped_policy: list[SkippedFile] = []

        # An allow-list fails closed for new binary formats; a deny-list would repeat
        # the injection-scanner mistake where the next unknown format slips through.
        for entry in tree:
            if entry.get("type") != "blob":
                continue
            discovered += 1
            path = str(entry["path"])
            size = int(entry.get("size", 0))
            url = str(entry["url"])
            priority = _is_priority_path(path)
            if not priority and (path.endswith((".min.js", ".min.css")) or Path(path).suffix.lower() not in SOURCE_EXTENSIONS):
                skipped_policy.append(SkippedFile(path, "extension is not gradeable"))
                continue
            eligible += 1
            if size > SOURCE_FILE_BYTE_CAP:
                skipped_policy.append(SkippedFile(path, f"exceeds {SOURCE_FILE_BYTE_CAP}-byte cap"))
                continue
            (priority_candidates if priority else regular_candidates).append((path, url, size))

        selected: list[tuple[str, str]] = []
        total_bytes = 0
        # Priority manifests/lockfiles/workflows are selected before the
        # count budget is applied at all, not merely early within it -- an
        # attacker who pads the first SOURCE_FILE_COUNT_CAP tree entries with
        # clean files cannot push a manifest out of the scan by position,
        # only by exceeding the byte budget every selected file still shares.
        for path, url, size in priority_candidates:
            if total_bytes + size > SOURCE_TOTAL_BYTE_CAP:
                skipped_policy.append(SkippedFile(path, f"exceeds {SOURCE_TOTAL_BYTE_CAP}-byte total-byte cap"))
                continue
            selected.append((path, url))
            total_bytes += size

        regular_selected = 0
        for path, url, size in regular_candidates:
            if regular_selected == SOURCE_FILE_COUNT_CAP:
                skipped_policy.append(SkippedFile(path, f"exceeds {SOURCE_FILE_COUNT_CAP}-file count cap"))
                continue
            if total_bytes + size > SOURCE_TOTAL_BYTE_CAP:
                skipped_policy.append(SkippedFile(path, f"exceeds {SOURCE_TOTAL_BYTE_CAP}-byte total-byte cap"))
                continue
            selected.append((path, url))
            total_bytes += size
            regular_selected += 1

        files: list[tuple[str, str]] = []
        skipped_error: list[SkippedFile] = []
        complete = True
        incomplete_because: str | None = None
        rate_limited = False
        for path, url in selected:
            blob_status, blob_headers, blob_body = self.transport.get(url)
            if error := _github_response_error(blob_status, blob_headers):
                skipped_error.append(SkippedFile(path, str(error)))
                complete = False
                incomplete_because = incomplete_because or error.run_reason
                rate_limited = rate_limited or error.rate_limited
                continue
            blob = json.loads(blob_body)
            files.append((path, base64.b64decode(blob["content"]).decode(errors="replace")))

        coverage = SourceCoverage(
            discovered_files=discovered,
            eligible_files=eligible,
            scanned_files=len(files),
            skipped_policy=tuple(skipped_policy),
            skipped_error=tuple(skipped_error),
        )
        skipped = tuple(f"{item.path}: {item.reason}" for item in (*skipped_policy, *skipped_error))
        return FetchedFiles(tuple(files), skipped, complete, incomplete_because, rate_limited, coverage)

    def star(self, repo: str) -> None:
        self._write("PUT", f"https://api.github.com/user/starred/{quote(repo, safe='/')}")

    def unstar(self, repo: str) -> None:
        self._write("DELETE", f"https://api.github.com/user/starred/{quote(repo, safe='/')}")

    def follow(self, user: str) -> None:
        self._write("PUT", f"https://api.github.com/user/following/{quote(user, safe='')}")

    def unfollow(self, user: str) -> None:
        self._write("DELETE", f"https://api.github.com/user/following/{quote(user, safe='')}")

    def _write(self, method: str, url: str) -> None:
        status, _, _ = self.transport.request(method, url, data=b"")
        if status not in (200, 204):
            raise RuntimeError(f"GitHub returned HTTP {status} for {method} {url}")


class CollectedRepository:
    def __init__(self, repository: FixtureTransport | GitHubRepository, collected: CollectResult) -> None:
        self._repository = repository
        self._collected = collected

    def search(self, query: str, limit: int) -> CollectResult:
        return self._collected

    def metadata(self, candidate: Candidate, at: datetime) -> RepositoryMetadata:
        return self._repository.metadata(candidate, at)


def _github_response_error(status: int, headers: Mapping[str, str]) -> FileFetchError | None:
    kind = classify(status, headers)
    if kind == "ok":
        return None
    if kind == "forbidden":
        return FileFetchError("GitHub access is forbidden; waiting will not help")
    if kind == "rate-limited":
        lowered = {key.lower(): value for key, value in headers.items()}
        retry_after = lowered.get("retry-after")
        if retry_after is not None:
            try:
                retry_at = datetime.now(timezone.utc) + timedelta(seconds=int(retry_after))
            except ValueError:
                return FileFetchError("GitHub secondary rate limit; wait before retrying", "GitHub secondary rate limit", True)
            reason = f"GitHub secondary rate limit; retry at {_wall_clock(retry_at)}"
            return FileFetchError(reason, reason, True)
        limit = parse(headers)
        if limit.exhausted:
            reset = ""
            if limit.reset_at is not None:
                reset = f"; quota resets at {_wall_clock(datetime.fromtimestamp(limit.reset_at, timezone.utc))}"
            reason = f"GitHub rate limit exhausted{reset}"
            return FileFetchError(reason, reason, True)
        return FileFetchError("GitHub rate limit; wait before retrying", "GitHub rate limit", True)
    return FileFetchError(f"GitHub returned HTTP {status}")


def _wall_clock(instant: datetime) -> str:
    return instant.strftime("%Y-%m-%d %H:%M:%S UTC")


class OllamaGrader:
    """The smallest local-Ollama adapter satisfying the existing grade protocol."""

    def __init__(self, model: str, transport: UrllibTransport | None = None, *, environ: Mapping[str, str] | None = None) -> None:
        self.model = model
        self.transport = UrllibTransport() if transport is None else transport
        self.ollama_base = _resolve_ollama_host(environ)

    def evaluate(self, digest: str) -> GradeResult:
        output = self._ask(
            "Return JSON with integer idea and skill from 1 to 10 plus a concise description.\n\n"
            + digest
        )
        try:
            result = json.loads(output)
        except json.JSONDecodeError as error:
            raise self._invalid_grade_response(output, "not valid JSON") from error
        if not isinstance(result, dict):
            raise self._invalid_grade_response(output, "not a JSON object")
        for field in ("idea", "skill"):
            if field not in result:
                raise self._invalid_grade_response(output, f"missing {field}")
            value = result[field]
            if type(value) is not int:
                raise self._invalid_grade_response(output, f"{field} is not an integer")
            if not 1 <= value <= 10:
                raise self._invalid_grade_response(output, f"{field} is outside 1..10")
        description = result.get("description")
        if not isinstance(description, str):
            raise self._invalid_grade_response(output, "description is not a string")
        return GradeResult(
            idea=result["idea"],
            skill=result["skill"],
            description=description,
            model=self.model,
            temperature=0.0,
            prompt_version="cli-v1",
        )

    def flags_malicious(self, digest: str) -> bool:
        result = json.loads(self._ask("Return JSON with boolean malicious.\n\n" + digest))
        return bool(result.get("malicious", False)) if isinstance(result, dict) else False

    def _invalid_grade_response(self, output: str, problem: str) -> ValueError:
        excerpt = output[:MODEL_OUTPUT_EXCERPT_CHARS]
        return ValueError(
            f"grading model {self.model} did not return the requested JSON object with integer idea and skill from 1 to 10 "
            f"plus a concise description: {problem}; it returned {excerpt!r} "
            f"(output shown up to {MODEL_OUTPUT_EXCERPT_CHARS} characters). Choose another model."
        )

    def _ask(self, prompt: str) -> str:
        body = json.dumps(
            {"model": self.model, "prompt": prompt, "format": "json", "stream": False, "options": {"temperature": 0}}
        ).encode()
        generate_url = f"{self.ollama_base}/api/generate"
        status, _, response = self.transport.request(
            "POST", generate_url, body, {"Content-Type": "application/json"}
        )
        if status != 200:
            raise RuntimeError(f"Ollama returned HTTP {status}")
        return str(json.loads(response)["response"])


class UnavailableGrader:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def evaluate(self, digest: str) -> GradeResult:
        raise RuntimeError(self.reason)

    def flags_malicious(self, digest: str) -> bool:
        raise RuntimeError(self.reason)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gitseed", epilog=EXIT_CODES)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("radar", aliases=["run"], description=SCORE_LIMIT, epilog=EXIT_CODES)
    command.add_argument("--query", help="GitHub repository search query")
    command.add_argument("--render", type=Path, help="render recorded output without re-evaluating it")
    command.add_argument("--limit", type=int, default=10, help="maximum candidates to carry forward")
    command.add_argument("--grade-timeout", type=int, default=DEFAULT_GRADE_TIMEOUT, help="seconds to wait for one model response")
    # A default write turns an accidental invocation into an external side effect.
    command.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    command.add_argument("--approve-all", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--debug", action="store_true", help="print a traceback for transport failures")
    command.add_argument("--fixtures", type=Path, help="offline candidate, source, and grade replay directory")
    command.add_argument("--artifact", type=Path, help="write a replayable record of this run")
    command.add_argument("--store", type=Path, default=DEFAULT_STORE, help="SQLite run history (default: ./.gitseed/runs.db)")
    command.add_argument("--run-id", help="identifier for this stored run")
    command.add_argument("--history", action="store_true", help="show stored runs in append order")
    command.add_argument("--corrects", help="run ID corrected by this new stored run")
    command.add_argument(
        "--source-mode",
        choices=("metadata-only", "digest", "full-source"),
        default="digest",
        help="artifact source retention mode; digest is the safe default",
    )
    render_command = commands.add_parser("render", description="Render stored output unchanged.", epilog=EXIT_CODES)
    render_command.add_argument("artifact", type=Path)
    render_command.add_argument("--json", action="store_true")
    replay_command = commands.add_parser("replay", description="Recompute stored port responses when recorded engines match.", epilog=EXIT_CODES)
    replay_command.add_argument("artifact", type=Path)
    replay_command.add_argument("--json", action="store_true")
    replay_command.add_argument("--allow-engine-mismatch", action="store_true", help="recompute with current code after reporting changed engines")
    reevaluate_command = commands.add_parser("re-evaluate", description="Recompute stored port responses with current code.", epilog=EXIT_CODES)
    reevaluate_command.add_argument("artifact", type=Path)
    reevaluate_command.add_argument("--json", action="store_true")
    explain_command = commands.add_parser("explain", description=SCORE_LIMIT, epilog=EXIT_CODES)
    explain_command.add_argument("repo")
    explain_command.add_argument("--artifact", type=Path, required=True)
    export_command = commands.add_parser("export", description="Write the canonical versioned run artifact to standard output.", epilog=EXIT_CODES)
    export_command.add_argument("artifact", type=Path)
    return parser


def _records(entries: Sequence[Reviewed]) -> list[dict[str, str | int | None]]:
    return [
        {"rank": index, "repo": entry.candidate.repo, "score": entry.score, "severity": entry.severity, "withheld": entry.withheld}
        for index, entry in enumerate(entries, start=1)
    ]


def _table(entries: Sequence[Reviewed]) -> str:
    return _table_rows(_records(entries))


def _table_rows(rows: Sequence[Mapping[str, str | int | None]]) -> str:
    columns = ["rank", "repo", "score"]
    if rows and "weight_version" in rows[0]:
        columns.extend(["weight_version", "coverage", "risk", "recommendation"])
    else:
        columns.append("severity")
    if any(row["withheld"] for row in rows):
        columns.append("withheld")
    widths = {column: max([len(column), *(len(str(row[column] if row[column] is not None else "-")) for row in rows)]) for column in columns}
    lines = ["  ".join(column.ljust(widths[column]) for column in columns)]
    for row in rows:
        lines.append("  ".join(str(row[column] if row[column] is not None else "-").ljust(widths[column]) for column in columns))
    return "\n".join(lines)


@dataclass(frozen=True)
class ReviewItem:
    entry: ArtifactReviewed
    recommendation: Recommendation

    @property
    def repo(self) -> str:
        return self.entry.candidate.repo


def rank_review_items(artifact: RunArtifact) -> tuple[ReviewItem, ...]:
    recommendations = {scored.repo: scored.recommendation for scored in artifact.scores}
    return tuple(
        sorted(
            (
                ReviewItem(entry, recommendations[entry.candidate.repo])
                for entry in artifact.result.reviewed
            ),
            key=lambda item: (
                REVIEW_STATUS_ORDER[item.recommendation.status],
                -item.recommendation.score.value,
                item.repo,
            ),
        )
    )


def _review_item_records(items: Sequence[ReviewItem]) -> list[dict[str, str | int | None]]:
    return [
        {
            "rank": index,
            "repo": item.repo,
            "score": str(item.recommendation.score.value),
            "weight_version": item.recommendation.score.version,
            "coverage": f"{len(item.recommendation.score.coverage)}/3",
            "risk": item.recommendation.risk_verdict,
            "recommendation": item.recommendation.status.value,
            "severity": item.entry.severity,
            "withheld": item.entry.withheld,
        }
        for index, item in enumerate(items, start=1)
    ]


def _radar_records(artifact: RunArtifact) -> list[dict[str, str | int | None]]:
    return _review_item_records(rank_review_items(artifact))


def _render_radar(
    artifact: RunArtifact,
    as_json: bool,
    out: IO[str],
    items: Sequence[ReviewItem] | None = None,
) -> None:
    records = _review_item_records(rank_review_items(artifact) if items is None else items)
    if as_json:
        json.dump(records, out)
        out.write("\n")
        return
    out.write(SCORE_LIMIT + "\n")
    coverage = artifact.result.grading_basis.value
    suffix = " (deterministic-only)" if coverage == "absent" else ""
    out.write(f"model coverage: {coverage}{suffix}\n")
    out.write(_candidate_coverage(artifact.collection) + "\n")
    out.write(_table_rows(records) + "\n")


def _candidate_coverage(collection: ArtifactCollection) -> str:
    total = "unknown" if collection.total_count is None else str(collection.total_count)
    if collection.complete_for_search:
        state = "complete"
    elif collection.incomplete_results:
        state = "partial: GitHub reported a partial search"
    else:
        state = "partial"
    return f"candidate coverage: {len(collection.candidates)}/{total} search results ({state})"


def _render(path: Path) -> RunArtifact:
    return render(path.read_bytes())


def _observation_summary(first: StoredObservation, current: StoredObservation) -> str:
    first_recorded = f"first recorded observation {first.observed_at.isoformat()}: {first.stars} stars"
    if current == first:
        return first_recorded + " (no growth yet)"
    change = current.stars - first.stars
    if change == 0:
        return first_recorded + f"; now {current.stars} stars (unchanged since first recorded observation)"
    return first_recorded + f"; now {current.stars} stars (changed {change:+d} stars since first recorded observation)"


def _history(path: Path, out: IO[str], err: IO[str]) -> int:
    if not path.exists():
        out.write("No stored runs.\n")
        return 0
    try:
        with SQLiteRunStore(path) as store:
            runs = store.history()
            observations = store.observations()
    except (OSError, sqlite3.Error, ValueError) as error:
        err.write(f"history failed: {error}\n")
        return 1
    if not runs:
        out.write("No stored runs.\n")
        return 0
    first_by_repo: dict[str, StoredObservation] = {}
    observations_by_run: dict[str, list[StoredObservation]] = {}
    for observation in observations:
        first_by_repo.setdefault(observation.repo, observation)
        observations_by_run.setdefault(observation.run_id, []).append(observation)
    for stored in runs:
        decisions = ", ".join(
            f"{item.repo}: {item.recommendation.status.value}"
            for item in rank_review_items(stored.artifact)
        ) or "no candidates"
        correction = "" if stored.corrects_run_id is None else f"; corrects {stored.corrects_run_id}"
        state = "complete" if stored.artifact.result.complete else "incomplete"
        observed = ", ".join(
            f"{observation.repo}: {_observation_summary(first_by_repo[observation.repo], observation)}"
            for observation in observations_by_run.get(stored.run_id, [])
        )
        observation_suffix = "" if not observed else f"; observed {observed}"
        out.write(
            f"{stored.run_id}: {state}; query {stored.artifact.request.query!r}; "
            f"decided {decisions}{correction}{observation_suffix}\n"
        )
    return 0


def _save_stored_artifact(path: Path, run_id: str, corrects_run_id: str | None, data: bytes) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with SQLiteRunStore(path) as store:
            store.save(run_id, RunArtifact.from_bytes(data), corrects_run_id)
    except ObservationWriteError as error:
        return str(error)
    return None


def _store_run(path: Path, run_id: str, corrects_run_id: str | None, artifact: RunArtifact, err: IO[str]) -> None:
    data = artifact.to_bytes()
    if os.getpid() == CLI_PROCESS_ID:
        observation_error = _save_stored_artifact(path, run_id, corrects_run_id, data)
    else:
        saved = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; from gitseed.cli import _save_stored_artifact; "
                "print(_save_stored_artifact(Path(sys.argv[1]), sys.argv[2], sys.argv[3] or None, sys.stdin.buffer.read()) or '')",
                str(path),
                run_id,
                corrects_run_id or "",
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if saved.returncode:
            raise sqlite3.OperationalError(saved.stderr.decode().strip().splitlines()[-1])
        observation_error = saved.stdout.decode().strip() or None
    if observation_error is not None:
        err.write(f"observation write failed: {observation_error}\n")


def _status(artifact: RunArtifact, err: IO[str], source: str | None = None) -> int:
    if source is not None:
        err.write(f"source: {source}\n")
    if artifact.result.complete:
        return 0
    for reason in artifact.result.incomplete_because:
        err.write(reason + "\n")
    return 2


def _explain(artifact: RunArtifact, repo: str, out: IO[str]) -> bool:
    trace = next((trace for trace in artifact.repositories if trace.candidate.repo == repo), None)
    item = next((item for item in rank_review_items(artifact) if item.repo == repo), None)
    if trace is None or item is None:
        return False
    recommendation = item.recommendation
    reviewed = item.entry
    score = recommendation.score
    values = None if trace.metadata is None else trace.metadata.score_inputs
    out.write(SCORE_LIMIT + "\n")
    out.write(f"repository: {repo}\nweight set: {score.version}\nscore: {score.value}\n")
    for feature, weight in WEIGHTS:
        value = None if values is None else getattr(values, feature.value)
        contribution = "unavailable" if value is None else str(weight if value else 0)
        out.write(f"{feature.value}: {contribution}\n")
    unavailable = ", ".join(reason.removesuffix(" unavailable") for reason in score.incomplete_because)
    out.write(f"score coverage: {len(score.coverage)}/{len(WEIGHTS)}\n")
    out.write(f"unavailable features: {unavailable or 'none'}\n")
    screened = ", ".join(reviewed.screened_files) or "0 files"
    out.write(f"security coverage: {reviewed.screening_basis.value} ({screened})\n")
    # Only present for a live GitHub adapter read; fixtures and unread
    # candidates do not model a policy budget to report against.
    if reviewed.coverage is not None:
        file_coverage = reviewed.coverage
        completeness = "complete" if file_coverage.complete_for_policy else "incomplete"
        out.write(
            f"file coverage: {file_coverage.scanned_files}/{file_coverage.eligible_files} eligible files "
            f"scanned, {file_coverage.discovered_files} discovered ({completeness}_for_policy)\n"
        )
    model_coverage = artifact.result.grading_basis.value
    suffix = " (deterministic-only)" if model_coverage == "absent" else ""
    out.write(f"model coverage: {model_coverage}{suffix}\n")
    findings = ", ".join(f"{signal.kind} at {signal.path}:{signal.line}" for signal in reviewed.findings)
    unverified = ", ".join(f"{signal.kind} at {signal.path}:{signal.line}" for signal in reviewed.unverified)
    out.write(f"security findings: {findings or 'none'}\n")
    out.write(f"unverified security claims: {unverified or 'none'}\n")
    out.write(f"risk: {recommendation.risk_verdict}\n")
    out.write(f"recommendation: {recommendation.status.value}\n")
    return True


def _action_approvals(approval: Approval, owner: str) -> list[Approval]:
    if approval.decision in (Decision.STAR, Decision.REJECT):
        return [approval]
    if approval.decision is Decision.FOLLOW:
        return [replace(approval, target=owner)]
    return [replace(approval, decision=Decision.STAR), replace(approval, target=owner, decision=Decision.FOLLOW)]


def _approvals(entries: Sequence[ReviewItem], approve_all: bool, stdin: IO[str], stdout: IO[str]) -> list[Approval]:
    owners = {item.repo: item.entry.candidate.owner for item in entries}
    if approve_all:
        collected = collect_bulk_approval(
            [item.repo for item in entries],
            _table_rows(_review_item_records(entries)),
            stdin=stdin,
            stdout=stdout,
        )
    else:
        collected = []
        for item in entries:
            approval = collect_approval(
                item.repo,
                f"{item.repo}: deterministic score {item.recommendation.score.value}; "
                f"model score {item.entry.score if item.entry.score is not None else '-'}; "
                f"risk {item.recommendation.risk_verdict}; "
                f"recommendation {item.recommendation.status.value}",
                stdin=stdin,
                stdout=stdout,
            )
            if approval is None:
                break
            collected.append(approval)
    return [action for approval in collected for action in _action_approvals(approval, owners[approval.target])]


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport | None = None,
    fetch_files: FetchFiles | None = None,
    grader: Grader | None = None,
    writer: GitHubWriter | None = None,
    committer: GitCommitter | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return 1 if error.code else 0
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    if args.command in ("render", "replay", "re-evaluate"):
        try:
            data = args.artifact.read_bytes()
            if args.command == "replay":
                mismatches = engine_version_mismatches(render(data))
                if mismatches and not args.allow_engine_mismatch:
                    err.write(
                        "replay not run: "
                        + "; ".join(str(mismatch) for mismatch in mismatches)
                        + ". Use --allow-engine-mismatch to recompute with current code.\n"
                    )
                    return 1
                artifact = re_evaluate(data) if mismatches else replay(data)
                source = (
                    "recomputed stored responses; engine versions match current code"
                    if not mismatches
                    else "recomputed stored responses with changed engines: " + "; ".join(str(mismatch) for mismatch in mismatches)
                )
            else:
                artifact = render(data) if args.command == "render" else re_evaluate(data)
                source = f"{args.command} artifact"
        except (OSError, ValueError) as error:
            err.write(f"{args.command} failed: {error}\n")
            return 1
        _render_radar(artifact, args.json, out)
        return _status(artifact, err, source)
    if args.command == "explain":
        try:
            artifact = _render(args.artifact)
        except (OSError, ValueError) as error:
            err.write(f"explain failed: {error}\n")
            return 1
        if not _explain(artifact, args.repo, out):
            err.write(f"explain failed: no repository {args.repo!r} in artifact\n")
            return 1
        return _status(artifact, err, "replayed artifact")
    if args.command == "export":
        try:
            artifact = _render(args.artifact)
        except (OSError, ValueError) as error:
            err.write(f"export failed: {error}\n")
            return 1
        out.write(artifact.to_bytes().decode())
        return _status(artifact, err, "replayed artifact")
    if args.command not in ("radar", "run"):
        return 1
    if args.history:
        if any((args.query, args.render, args.artifact, args.run_id, args.corrects)):
            err.write("invalid invocation: --history cannot be combined with run options\n")
            return 1
        return _history(args.store, out, err)
    if args.render is not None:
        if args.query is not None:
            err.write("invalid invocation: --query cannot be used with --render\n")
            return 1
        try:
            artifact = _render(args.render)
        except (OSError, ValueError) as error:
            err.write(f"radar failed: {error}\n")
            return 1
        _render_radar(artifact, args.json, out)
        return _status(artifact, err, "rendered artifact")
    if args.query is None:
        err.write("invalid invocation: --query is required unless --render is used\n")
        return 1
    if args.limit < 1:
        err.write("invalid invocation: --limit must be positive\n")
        return 1
    if args.grade_timeout < 1:
        err.write("invalid invocation: --grade-timeout must be positive\n")
        return 1

    try:
        source_in = sys.stdin if stdin is None else stdin
        fixture = FixtureTransport(args.fixtures) if args.fixtures else None
        active_transport = fixture if fixture is not None else transport or UrllibTransport(os.environ.get("GITHUB_TOKEN"))
        client = None if fixture is not None else GitHubClient(active_transport)
        active_fetch_files = fixture.fetch_files if fixture is not None else fetch_files or client.fetch_files
        if fixture is not None:
            active_grader = FixtureGrader(args.fixtures)
        elif grader is not None:
            active_grader = grader
        else:
            ollama_transport = UrllibTransport(timeout=args.grade_timeout)
            try:
                model, reason = resolve_model(ollama_transport)
            except RuntimeError as error:
                active_grader = UnavailableGrader(str(error))
            else:
                err.write(f"grading model: {model} ({reason})\n")
                active_grader = OllamaGrader(model, ollama_transport, environ=os.environ)
        repository = fixture if fixture is not None else GitHubRepository(active_transport)
        collected = repository.search(args.query, args.limit)
        recorded = execute(
            RunRequest(args.query, args.limit),
            RunPorts(
                CollectedRepository(repository, collected),
                CallableFileReader(active_fetch_files),
                active_grader,
                SystemClock(),
            ),
            source_mode=args.source_mode,
        )
        run_id = args.run_id or uuid4().hex
        if args.artifact is not None:
            args.artifact.write_bytes(recorded.to_bytes())
        review_items = rank_review_items(recorded)
        _render_radar(recorded, args.json, out, review_items)
        status = _status(recorded, err)
        if status == 2:
            if recorded.result.rate_limited and not os.environ.get("GITHUB_TOKEN"):
                err.write("GitHub allows 60 unauthenticated requests/hour; set GITHUB_TOKEN for 5,000.\n")
            _store_run(args.store, run_id, args.corrects, recorded, err)
            return 2
        if args.dry_run:
            _store_run(args.store, run_id, args.corrects, recorded, err)
            return 0
        active_writer = writer or (fixture if fixture is not None else client)
        try:
            approvals = _approvals(review_items, args.approve_all, source_in, out)
        except NotInteractive as error:
            err.write(f"approval refused: {error}\n")
            _store_run(args.store, run_id, args.corrects, recorded, err)
            return 1
        active_committer = committer or SubprocessGitCommitter(Path.cwd())
        try:
            intent_sha = record_decisions(approvals, active_committer)
        except CommitFailed as error:
            err.write(f"intent commit failed: {error}\n")
            _store_run(args.store, run_id, args.corrects, recorded, err)
            return 1
        block = render_block(approvals)
        if block:
            out.write(block)
        if intent_sha is not None:
            err.write(f"recorded intent commit {intent_sha}\n")

        performed: list[Performed] = []
        failed_at: int | None = None
        for index, approval in enumerate(approvals):
            try:
                completed = perform(active_writer, approval)
            except (OSError, RuntimeError) as error:
                err.write(f"action failed: {approval.decision.value} {approval.target}: {error}\n")
                try:
                    record_outcome(
                        ActionOutcome(approval, OutcomeStatus.CALL_FAILED, str(error)),
                        active_committer,
                    )
                except CommitFailed as commit_error:
                    err.write(f"outcome commit failed; intent remains pending: {commit_error}\n")
                failed_at = index
                break
            performed.extend(completed)
            try:
                for action in completed:
                    record_outcome(
                        ActionOutcome(action.approval, OutcomeStatus.SUCCEEDED),
                        active_committer,
                    )
            except CommitFailed as error:
                err.write(f"outcome commit failed; intent remains pending: {error}\n")
                failed_at = index
                break

        if failed_at is None:
            _store_run(args.store, run_id, args.corrects, recorded, err)
            return 0

        for approval in approvals[failed_at + 1:]:
            if approval.decision is Decision.REJECT:
                continue
            try:
                record_outcome(
                    ActionOutcome(approval, OutcomeStatus.NOT_ATTEMPTED),
                    active_committer,
                )
            except CommitFailed as error:
                err.write(f"outcome commit failed; intent remains pending: {error}\n")

        for action in reversed(performed):
            try:
                undo(active_writer, action)
            except (OSError, RuntimeError) as error:
                status = OutcomeStatus.COMPENSATION_FAILED
                detail = str(error)
                err.write(f"compensation failed: {action.action} {action.target}: {error}\n")
            else:
                status = OutcomeStatus.COMPENSATED
                detail = ""
            try:
                record_outcome(
                    ActionOutcome(action.approval, status, detail),
                    active_committer,
                )
            except CommitFailed as error:
                err.write(f"compensation outcome commit failed; intent remains pending: {error}\n")
        _store_run(args.store, run_id, args.corrects, recorded, err)
        return 1
    except (OSError, sqlite3.Error) as error:
        # Check if this is a timeout error and provide actionable guidance
        if isinstance(error, socket.timeout) or "timed out" in str(error).lower():
            err.write(f"run failed: {error}\n")
            err.write(f"fix: increase --grade-timeout (currently {args.grade_timeout} seconds)\n")
        else:
            err.write(f"run failed: {error}\n")
        # Normal CLI output must stay actionable; debug mode preserves diagnostics.
        if args.debug:
            traceback.print_exc(file=err)
        return 1
