"""The command-line seam between collection, screening, grading, and review."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable, IO, Protocol, Sequence
from urllib.parse import parse_qs, quote, urlparse

from .collect.search import Candidate, CollectResult, Transport, UrllibTransport, collect
from .grade.types import GradeResult
from .pipeline.run import Reviewed, ranked, run
from .review.actions import GitHubWriter, perform
from .review.approval import Approval, Decision, NotInteractive, collect_approval, collect_bulk_approval
from .review.trailers import render_block


class FetchFiles(Protocol):
    """The read-only repository content seam used by the pipeline."""

    def __call__(self, candidate: Candidate) -> Sequence[tuple[str, str]]: ...


class Grader(Protocol):
    def evaluate(self, digest: str) -> GradeResult: ...

    def flags_malicious(self, digest: str) -> bool: ...


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

    def fetch_files(self, candidate: Candidate) -> Sequence[tuple[str, str]]:
        root = self.directory / self._files[candidate.repo]
        return [(str(path.relative_to(root)), path.read_text()) for path in sorted(root.rglob("*")) if path.is_file()]

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

    def fetch_files(self, candidate: Candidate) -> Sequence[tuple[str, str]]:
        repo = quote(candidate.repo, safe="/")
        status, _, body = self.transport.get(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1")
        if status != 200:
            raise RuntimeError(f"GitHub returned HTTP {status} for {candidate.repo}")
        tree = json.loads(body).get("tree", [])
        files: list[tuple[str, str]] = []
        for entry in tree:
            if entry.get("type") != "blob" or int(entry.get("size", 0)) > 100_000:
                continue
            blob_status, _, blob_body = self.transport.get(str(entry["url"]))
            if blob_status != 200:
                raise RuntimeError(f"GitHub returned HTTP {blob_status} for {entry['path']}")
            blob = json.loads(blob_body)
            files.append((str(entry["path"]), base64.b64decode(blob["content"]).decode(errors="replace")))
            if len(files) == 20:
                break
        return files

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
    # A default write turns an accidental invocation into an external side effect.
    command.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    command.add_argument("--approve-all", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--fixtures", type=Path, help="offline candidate, source, and grade replay directory")
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
    args = _parser().parse_args(argv)
    if args.command != "run":
        return 1
    if args.limit < 1:
        _parser().error("--limit must be positive")

    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    source_in = sys.stdin if stdin is None else stdin
    fixture = FixtureTransport(args.fixtures) if args.fixtures else None
    active_transport = fixture if fixture is not None else transport or UrllibTransport(os.environ.get("GITHUB_TOKEN"))
    client = None if fixture is not None else GitHubClient(active_transport)
    active_fetch_files = fixture.fetch_files if fixture is not None else fetch_files or client.fetch_files
    active_grader = FixtureGrader(args.fixtures) if fixture is not None else grader or OllamaGrader(os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"))
    active_writer = fixture if fixture is not None else writer or client
    collected = collect(args.query, transport=active_transport, pages=(args.limit + 99) // 100, per_page=min(args.limit, 100))
    if fixture is not None and not fixture.complete:
        collected.complete = False
        collected.stopped_because = str(fixture.stopped_because or "fixture reported an incomplete collection")
    collected.candidates = collected.candidates[:args.limit]
    result = run(collected, fetch_files=active_fetch_files, grader=active_grader)
    entries = ranked(result)
    if args.json:
        json.dump(_records(entries), out)
        out.write("\n")
    else:
        out.write(_table(entries) + "\n")
    if not result.complete:
        for reason in result.incomplete_because:
            err.write(reason + "\n")
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
