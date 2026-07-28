"""Signal extraction: what in this repository looks dangerous, and where.

Every signal carries a citation — path, line, and the line itself. A finding a
user cannot go and look at is a finding they cannot argue with, and the seed
this project rebuilds failed exactly there: it emitted a boolean
`security_flag` whose `security_reason` sometimes said the code was fine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Sequence

from ..evidence import ClaimBasis

HIGH: Final = "high"
LOW: Final = "low"

_MAX_EXCERPT: Final = 120


@dataclass(frozen=True)
class Signal:
    """One dangerous-looking thing, with the evidence attached.

    The constructor refuses a signal that cannot cite itself. That is not
    defensive programming — an uncitable signal is the failure mode this whole
    layer exists to avoid, so it is cheaper to crash the scanner than to ship
    one.
    """

    kind: str
    severity: str
    path: str
    line: int
    excerpt: str
    basis: ClaimBasis = ClaimBasis.DETERMINISTIC

    def __post_init__(self) -> None:
        object.__setattr__(self, "basis", ClaimBasis(self.basis))
        if not self.path:
            raise ValueError("Signal.path is required — a signal must cite a file")
        if self.line < 1:
            raise ValueError(f"Signal.line must be 1-based, got {self.line}")
        if not self.excerpt.strip():
            raise ValueError("Signal.excerpt is required — a signal must quote what it saw")
        if self.severity not in (HIGH, LOW):
            raise ValueError(f"Signal.severity must be {HIGH!r} or {LOW!r}, got {self.severity!r}")


# `curl ... | sh` and its wget/bash permutations, including a `-` argument and
# `sudo` in the middle, which is how these are actually written.
_INSTALL_SCRIPT = re.compile(
    r"\b(?:curl|wget)\b[^|\n]{0,200}\|\s*(?:sudo\s+)?(?:/bin/)?(?:ba|z|da)?sh\b",
    re.IGNORECASE,
)

# A long unbroken base64 or hex run. Short ones are hashes, keys and test
# vectors; the length is what separates a checksum from a payload.
_B64_BLOB = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_HEX_BLOB = re.compile(r"(?:0x)?[0-9a-fA-F]{200,}")

# A literal IPv4 that is not a documentation or loopback address.
_RAW_IP = re.compile(r"\b(?!0\.|127\.|10\.|192\.168\.|255\.)(?:\d{1,3}\.){3}\d{1,3}\b")
_SUSPECT_HOST = re.compile(r"\b[a-z0-9-]+\.onion\b|\b(?:bit\.ly|tinyurl\.com|is\.gd)\b", re.IGNORECASE)

_POSTINSTALL = re.compile(r'"(?:post|pre)install"\s*:', re.IGNORECASE)


def _excerpt(line: str) -> str:
    stripped = line.strip()
    return stripped[:_MAX_EXCERPT] if stripped else line[:_MAX_EXCERPT]


def scan_text(path: str, text: str) -> list[Signal]:
    """Every signal in one file's text, in line order.

    Pure: no network, no subprocess, no model. Same input, same list — the
    ranking downstream is only reproducible if this is.
    """
    found: list[Signal] = []
    is_manifest = path.endswith("package.json")

    for index, line in enumerate(text.splitlines(), start=1):
        if _INSTALL_SCRIPT.search(line):
            found.append(Signal("install-script", HIGH, path, index, _excerpt(line)))
        if _B64_BLOB.search(line) or _HEX_BLOB.search(line):
            found.append(Signal("obfuscation", HIGH, path, index, _excerpt(line)))
        if is_manifest and _POSTINSTALL.search(line):
            found.append(Signal("postinstall", HIGH, path, index, _excerpt(line)))
        if _RAW_IP.search(line) or _SUSPECT_HOST.search(line):
            found.append(Signal("network", LOW, path, index, _excerpt(line)))

    return found


def scan_files(files: Sequence[tuple[str, str]]) -> list[Signal]:
    """`scan_text` over `(path, text)` pairs, in the order given."""
    return [signal for path, text in files for signal in scan_text(path, text)]
