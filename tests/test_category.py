from __future__ import annotations

from dataclasses import replace

import pytest

from gitseed.category import CATEGORY_PACKS, CategoryPack, Evidence, EvidenceRequirement, classify
from gitseed.evidence import ClaimBasis


def test_unreadable_evidence_is_absent_not_an_uncategorized_match() -> None:
    # Given: the only evidence needed by this category could not be read.
    pack = CategoryPack(
        name="python",
        version="v1",
        evidence=(EvidenceRequirement("files", "pyproject.toml"),),
    )
    unavailable = (Evidence("files", frozenset(), ClaimBasis.ABSENT),)

    # When: deterministic categorization is attempted.
    result = classify(pack, unavailable)

    # Then: absence says what could not be read instead of calling it a non-match.
    assert result.category is None
    assert result.basis is ClaimBasis.ABSENT
    assert result.missing_evidence == ("files",)
    assert result.render() == (
        "category: absent\n"
        "pack: python v1\n"
        "unavailable evidence: files\n"
    )

    read_but_unmatched = classify(
        pack,
        (Evidence("files", frozenset({"README.md"}), ClaimBasis.DETERMINISTIC),),
    )
    assert read_but_unmatched.category is None
    assert read_but_unmatched.basis is ClaimBasis.DETERMINISTIC
    assert read_but_unmatched.missing_evidence == ()


def test_model_only_evidence_is_uncategorized_until_the_basis_changes() -> None:
    # Given: a model opinion claims the same file as a deterministic collector.
    pack = CategoryPack(
        name="python",
        version="v1",
        evidence=(EvidenceRequirement("files", "pyproject.toml"),),
    )
    model_only = Evidence(
        "files", frozenset({"pyproject.toml"}), ClaimBasis.MODEL
    )

    # When: the model basis is mutated to deterministic evidence.
    unverified = classify(pack, (model_only,))
    verified = classify(
        pack,
        (replace(model_only, basis=ClaimBasis.DETERMINISTIC),),
    )

    # Then: only the deterministic mutation may assign the category.
    assert unverified.category is None
    assert unverified.basis is ClaimBasis.MODEL
    assert verified.category == "python"
    assert verified.basis is ClaimBasis.DETERMINISTIC


def test_pack_rejects_evidence_the_collector_cannot_produce() -> None:
    # Given/When: a pack asks the collector for a non-existent evidence source.
    with pytest.raises(ValueError, match="webhooks"):
        CategoryPack(
            name="webhook-driven",
            version="v1",
            evidence=(EvidenceRequirement("webhooks", "receiver"),),
        )


def test_classifier_is_deterministic_for_the_same_evidence_and_pack_version() -> None:
    # Given: one valid pack and the deterministic evidence it requires.
    pack = CategoryPack(
        name="python",
        version="v1",
        evidence=(EvidenceRequirement("files", "pyproject.toml"),),
    )
    evidence = (Evidence("files", frozenset({"pyproject.toml"}), ClaimBasis.DETERMINISTIC),)

    # When: the classifier receives the identical input twice.
    first = classify(pack, evidence)
    second = classify(pack, evidence)

    # Then: category and pack version are identical.
    assert first == second
    assert first.category == second.category == "python"
    assert first.pack_version == second.pack_version == "v1"


def test_categories_from_different_pack_versions_are_not_comparable() -> None:
    # Given: equivalent evidence under two versions of the same category pack.
    evidence = (Evidence("files", frozenset({"pyproject.toml"}), ClaimBasis.DETERMINISTIC),)
    first = classify(
        CategoryPack("python", "v1", (EvidenceRequirement("files", "pyproject.toml"),)),
        evidence,
    )
    second = classify(
        CategoryPack("python", "v2", (EvidenceRequirement("files", "pyproject.toml"),)),
        evidence,
    )

    # When/Then: changing the pack version changes identity and blocks ordering.
    assert first != second
    with pytest.raises(TypeError, match="pack versions"):
        sorted((first, second))


def test_initial_category_packs_validate() -> None:
    assert tuple(pack.name for pack in CATEGORY_PACKS) == (
        "coding-agents",
        "mcp",
        "local-ai",
    )


def test_coding_agents_requires_an_independent_source_signal() -> None:
    pack = CATEGORY_PACKS[0]
    agents_only = (
        Evidence("files", frozenset({"AGENTS.md"}), ClaimBasis.DETERMINISTIC),
        Evidence("source", frozenset(), ClaimBasis.DETERMINISTIC),
    )
    strong = (
        Evidence("files", frozenset({"AGENTS.md"}), ClaimBasis.DETERMINISTIC),
        Evidence("source", frozenset({"agent-runtime"}), ClaimBasis.DETERMINISTIC),
    )

    weak = classify(pack, agents_only)
    matched = classify(pack, strong)

    assert weak.category is None
    assert weak.basis is ClaimBasis.DETERMINISTIC
    assert matched.category == "coding-agents"
    assert matched.basis is ClaimBasis.DETERMINISTIC


def test_same_version_packs_keep_distinct_identities() -> None:
    evidence = (Evidence("files", frozenset({"README.md"}), ClaimBasis.DETERMINISTIC),)
    mcp = classify(CategoryPack("mcp", "v1", (EvidenceRequirement("files", "README.md"),)), evidence)
    local_ai = classify(CategoryPack("local-ai", "v1", (EvidenceRequirement("files", "README.md"),)), evidence)

    assert mcp.pack != local_ai.pack
    assert (mcp.pack.name, mcp.pack.version) == ("mcp", "v1")
