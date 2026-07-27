from __future__ import annotations

import json

import pytest

from gitseed.cli import OllamaGrader, resolve_model


class TagsTransport:
    def __init__(self, models: list[str]) -> None:
        self.models = models
        self.urls: list[str] = []

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        self.urls.append(url)
        return 200, {}, json.dumps({"models": [{"name": model} for model in self.models]}).encode()

    def request(
        self, method: str, url: str, data: bytes | None = None, extra_headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        assert method == "POST"
        assert url == "http://localhost:11434/api/generate"
        return 200, {}, json.dumps({"response": json.dumps({"idea": 8, "skill": 7, "description": "useful"})}).encode()


def test_explicit_ollama_model_wins_without_tags_lookup() -> None:
    # Given: an operator has made an explicit model choice.
    transport = TagsTransport(["qwen2.5-coder:32b"])
    # When: the live grader resolves its model.
    model, reason = resolve_model(transport, environ={"OLLAMA_MODEL": "custom:latest"})
    # Then: the operator's choice wins before any discovery request.
    assert (model, reason) == ("custom:latest", "explicit OLLAMA_MODEL")
    assert transport.urls == []


def test_largest_preferred_installed_model_is_chosen() -> None:
    # Given: every supported grading model is installed.
    transport = TagsTransport(["qwen2.5-coder:1.5b", "qwen2.5-coder:7b", "qwen2.5-coder:32b"])
    # When: the live grader resolves its model.
    model, reason = resolve_model(transport, environ={})
    # Then: the fixed preference order selects the largest suitable model.
    assert (model, reason) == ("qwen2.5-coder:32b", "largest installed preferred model")


def test_seven_billion_is_chosen_when_thirty_two_billion_is_absent() -> None:
    # Given: only the two smaller supported models are installed.
    transport = TagsTransport(["qwen2.5-coder:1.5b", "qwen2.5-coder:7b"])
    # When: the live grader resolves its model.
    model, reason = resolve_model(transport, environ={})
    # Then: it selects the largest installed supported model.
    assert (model, reason) == ("qwen2.5-coder:7b", "largest installed preferred model")


def test_missing_preferred_models_explains_how_to_install_one() -> None:
    # Given: Ollama reports unrelated models only.
    transport = TagsTransport(["nomic-embed-text:latest"])
    # When: the live grader resolves its model.
    with pytest.raises(RuntimeError, match=r"ollama pull qwen2\.5-coder:7b"):
        resolve_model(transport, environ={})


def test_unreachable_ollama_does_not_guess_a_model() -> None:
    # Given: the local Ollama endpoint cannot be reached.
    class UnreachableTransport:
        def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
            raise OSError("connection refused")

    # When: the live grader resolves its model.
    with pytest.raises(RuntimeError, match="could not reach Ollama"):
        resolve_model(UnreachableTransport(), environ={})


def test_selected_model_is_recorded_in_the_grade() -> None:
    # Given: discovery selected the largest installed supported model.
    transport = TagsTransport(["qwen2.5-coder:32b"])
    model, _ = resolve_model(transport, environ={})
    # When: the grader evaluates a candidate.
    grade = OllamaGrader(model, transport).evaluate("repository: example/tool\n")
    # Then: the recorded grade carries the selected model name.
    assert grade.model == "qwen2.5-coder:32b"
