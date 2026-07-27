"""The command-line seam between collection, screening, grading, and review."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Final, IO, Mapping, Protocol, Sequence
from urllib.parse import parse_qs, quote, urlparse

from .adapters import CallableFileReader, GitHubRepository, SystemClock
from .application import execute, replay
from .collect.ratelimit import classify, parse
from .collect.search import Candidate, CollectResult, Transport, UrllibTransport, collect
from .grade.types import GradeResult
from .pipeline.run import FetchedFiles, FileFetchError, Reviewed, ranked, run
from .ports import RepositoryMetadata, RunPorts, RunRequest
from .review.actions import GitHubWriter, perform
from .review.approval import Approval, Decision, NotInteractive, collect_approval, collect_bulk_approval
from .review.trailers import render_block
from .scoring import ScoreInputs


PREFERRED_OLLAMA_MODELS: Final = (
    "qwen2.5-coder:32b",
    "qwen2.5-coder:7b",
    "qwen2.5-coder:1.5b",
)
OLLAMA_TAGS_URL: Final = "http://localhost:11434/api/tags"
# A cold one-token reply from the local 32B model took about 23 seconds; a
# multi-file digest needs several times that budget instead of the old 30-second default.
DEFAULT_GRADE_TIMEOUT: Final = 120
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


class FetchFiles(Protocol):
    """The read-only repository content seam used by the pipeline."""

    def __call__(self, candidate: Candidate) -> FetchedFiles: ...


class Grader(Protocol):
    def evaluate(self, digest: str) -> GradeResult: ...

    def flags_malicious(self, digest: str) -> bool: ...


def resolve_model(transport: Transport, *, environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    environment = os.environ if environ is None else environ
    explicit = environment.get("OLLAMA_MODEL")
    if explicit is not None:
        return explicit, "explicit OLLAMA_MODEL"
    try:
        status, _, body = transport.get(OLLAMA_TAGS_URL)
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
        self._files = {str(item["full_name"]): str(item.get("files", item["full_name"])) for item in self.items}
        self.calls: list[tuple[str, str]] = []

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        query = parse_qs(urlparse(url).query)
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["30"])[0])
        start = (page - 1) * per_page
        return 200, {"X-RateLimit-Remaining": "29"}, json.dumps({"items": self.items[start:start + per_page]}).encode()

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
        result.complete = self.complete
        result.stopped_because = self.stopped_because
        result.candidates = result.candidates[:limit]
        return result

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
        return GradeResult(**self.grades[repo])

    def flags_malicious(self, digest: str) -> bool:
        return False


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
        selected: list[tuple[str, str]] = []
        skipped: list[str] = []
        total_bytes = 0
        # An allow-list fails closed for new binary formats; a deny-list would repeat
        # the injection-scanner mistake where the next unknown format slips through.
        for entry in tree:
            if entry.get("type") != "blob":
                continue
            path = str(entry["path"])
            size = int(entry.get("size", 0))
            if path.endswith((".min.js", ".min.css")) or Path(path).suffix.lower() not in SOURCE_EXTENSIONS:
                skipped.append(f"{path}: extension is not gradeable")
            elif size > SOURCE_FILE_BYTE_CAP:
                skipped.append(f"{path}: exceeds {SOURCE_FILE_BYTE_CAP}-byte cap")
            elif len(selected) == SOURCE_FILE_COUNT_CAP:
                skipped.append(f"{path}: exceeds {SOURCE_FILE_COUNT_CAP}-file count cap")
            elif total_bytes + size > SOURCE_TOTAL_BYTE_CAP:
                skipped.append(f"{path}: exceeds {SOURCE_TOTAL_BYTE_CAP}-byte total-byte cap")
            else:
                selected.append((path, str(entry["url"])))
                total_bytes += size

        files: list[tuple[str, str]] = []
        complete = True
        incomplete_because: str | None = None
        rate_limited = False
        for path, url in selected:
            blob_status, blob_headers, blob_body = self.transport.get(url)
            if error := _github_response_error(blob_status, blob_headers):
                skipped.append(f"{path}: {error}")
                complete = False
                incomplete_because = incomplete_because or error.run_reason
                rate_limited = rate_limited or error.rate_limited
                continue
            blob = json.loads(blob_body)
            files.append((path, base64.b64decode(blob["content"]).decode(errors="replace")))
        return FetchedFiles(tuple(files), tuple(skipped), complete, incomplete_because, rate_limited)

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

    def __init__(self, model: str, transport: UrllibTransport | None = None) -> None:
        self.model = model
        self.transport = UrllibTransport() if transport is None else transport

    def evaluate(self, digest: str) -> GradeResult:
        result = self._ask(
            "Return JSON with integer idea and skill from 1 to 10 plus a concise description.\n\n"
            + digest
        )
        return GradeResult(
            idea=int(result["idea"]),
            skill=int(result["skill"]),
            description=str(result["description"]),
            model=self.model,
            temperature=0.0,
            prompt_version="cli-v1",
        )

    def flags_malicious(self, digest: str) -> bool:
        return bool(self._ask("Return JSON with boolean malicious.\n\n" + digest).get("malicious", False))

    def _ask(self, prompt: str) -> dict[str, object]:
        body = json.dumps(
            {"model": self.model, "prompt": prompt, "format": "json", "stream": False, "options": {"temperature": 0}}
        ).encode()
        status, _, response = self.transport.request(
            "POST", "http://localhost:11434/api/generate", body, {"Content-Type": "application/json"}
        )
        if status != 200:
            raise RuntimeError(f"Ollama returned HTTP {status}")
        return json.loads(json.loads(response)["response"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gitseed")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run")
    command.add_argument("--query", required=True, help="GitHub repository search query")
    command.add_argument("--limit", type=int, default=10, help="maximum candidates to carry forward")
    command.add_argument("--grade-timeout", type=int, default=DEFAULT_GRADE_TIMEOUT, help="seconds to wait for one model response")
    # A default write turns an accidental invocation into an external side effect.
    command.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    command.add_argument("--approve-all", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--debug", action="store_true", help="print a traceback for transport failures")
    command.add_argument("--fixtures", type=Path, help="offline candidate, source, and grade replay directory")
    command.add_argument("--artifact", type=Path, help="write a replayable record of this run")
    replay_command = commands.add_parser("replay")
    replay_command.add_argument("artifact", type=Path)
    replay_command.add_argument("--json", action="store_true")
    return parser


def _records(entries: Sequence[Reviewed]) -> list[dict[str, str | int | None]]:
    return [
        {"rank": index, "repo": entry.candidate.repo, "score": entry.score, "severity": entry.severity, "withheld": entry.withheld}
        for index, entry in enumerate(entries, start=1)
    ]


def _table(entries: Sequence[Reviewed]) -> str:
    rows = _records(entries)
    columns = ["rank", "repo", "score", "severity"]
    if any(row["withheld"] for row in rows):
        columns.append("withheld")
    widths = {column: max(len(column), *(len(str(row[column] if row[column] is not None else "-")) for row in rows)) for column in columns}
    lines = ["  ".join(column.ljust(widths[column]) for column in columns)]
    for row in rows:
        lines.append("  ".join(str(row[column] if row[column] is not None else "-").ljust(widths[column]) for column in columns))
    return "\n".join(lines)


def _action_approvals(approval: Approval, owner: str) -> list[Approval]:
    if approval.decision in (Decision.STAR, Decision.REJECT):
        return [approval]
    if approval.decision is Decision.FOLLOW:
        return [replace(approval, target=owner)]
    return [replace(approval, decision=Decision.STAR), replace(approval, target=owner, decision=Decision.FOLLOW)]


def _approvals(entries: Sequence[Reviewed], approve_all: bool, stdin: IO[str], stdout: IO[str]) -> list[Approval]:
    owners = {entry.candidate.repo: entry.candidate.owner for entry in entries}
    if approve_all:
        collected = collect_bulk_approval(list(owners), _table(entries), stdin=stdin, stdout=stdout)
    else:
        collected = []
        for entry in entries:
            approval = collect_approval(
                entry.candidate.repo,
                f"{entry.candidate.repo}: score {entry.score if entry.score is not None else '-'}; severity {entry.severity}",
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
    if args.command == "replay":
        try:
            artifact = replay(args.artifact.read_bytes())
        except (OSError, ValueError) as error:
            err.write(f"replay failed: {error}\n")
            return 1
        entries = ranked(artifact.result)
        if args.json:
            json.dump(_records(entries), out)
            out.write("\n")
        else:
            out.write(_table(entries) + "\n")
        if artifact.result.complete:
            return 0
        for reason in artifact.result.incomplete_because:
            err.write(reason + "\n")
        return 2
    if args.command != "run":
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
            model, reason = resolve_model(ollama_transport)
            err.write(f"grading model: {model} ({reason})\n")
            active_grader = OllamaGrader(model, ollama_transport)
        active_writer = writer or (fixture if fixture is not None else client)
        if args.artifact is None:
            collected = collect(args.query, transport=active_transport, pages=(args.limit + 99) // 100, per_page=min(args.limit, 100))
            if fixture is not None and not fixture.complete:
                collected.complete = False
                collected.stopped_because = str(fixture.stopped_because or "fixture reported an incomplete collection")
            collected.candidates = collected.candidates[:args.limit]
            result = run(collected, fetch_files=active_fetch_files, grader=active_grader)
        else:
            recorded = execute(
                RunRequest(args.query, args.limit),
                RunPorts(
                    fixture if fixture is not None else GitHubRepository(active_transport),
                    CallableFileReader(active_fetch_files),
                    active_grader,
                    SystemClock(),
                ),
            )
            args.artifact.write_bytes(recorded.to_bytes())
            result = recorded.result
        entries = ranked(result)
        if args.json:
            json.dump(_records(entries), out)
            out.write("\n")
        else:
            out.write(_table(entries) + "\n")
        if not result.complete:
            for reason in result.incomplete_because:
                err.write(reason + "\n")
            if result.rate_limited and not os.environ.get("GITHUB_TOKEN"):
                err.write("GitHub allows 60 unauthenticated requests/hour; set GITHUB_TOKEN for 5,000.\n")
            return 2
        if args.dry_run:
            return 0
        try:
            approvals = _approvals(entries, args.approve_all, source_in, out)
        except NotInteractive as error:
            err.write(f"approval refused: {error}\n")
            return 1
        for approval in approvals:
            perform(active_writer, approval)
        block = render_block(approvals)
        if block:
            out.write(block)
        return 0
    except OSError as error:
        err.write(f"run failed: {error}\n")
        # Normal CLI output must stay actionable; debug mode preserves diagnostics.
        if args.debug:
            traceback.print_exc(file=err)
        return 1
