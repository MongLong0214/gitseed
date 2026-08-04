from __future__ import annotations

import json

import pytest

from gitseed.cli import OllamaGrader, resolve_model, _resolve_ollama_host

MODEL_PROMPT_BYTE_CAP = 24_000


class TagsTransport:
    def __init__(self, models: list[str], expected_base: str = "http://localhost:11434") -> None:
        self.models = models
        self.expected_base = expected_base
        self.urls: list[str] = []

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        self.urls.append(url)
        return 200, {}, json.dumps({"models": [{"name": model} for model in self.models]}).encode()

    def request(
        self, method: str, url: str, data: bytes | None = None, extra_headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        assert method == "POST"
        assert url == f"{self.expected_base}/api/generate"
        return 200, {}, json.dumps({"response": json.dumps({"idea": 8, "skill": 7, "description": "useful"})}).encode()


class GenerateTransport:
    def __init__(self, response: str) -> None:
        self.response = response

    def request(
        self, method: str, url: str, data: bytes | None = None, extra_headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        return 200, {}, json.dumps({"response": self.response}).encode()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (json.dumps({"idea": "x" * 200, "skill": 7, "description": "useful"}), "idea"),
        (json.dumps({"skill": 7, "description": "useful"}), "missing idea"),
        (json.dumps({"idea": 7.5, "skill": 7, "description": "useful"}), "idea"),
        (json.dumps({"idea": 11, "skill": 7, "description": "useful"}), "idea"),
        (json.dumps({"idea": 7, "skill": 7, "description": 3}), "description"),
        ("[]", "JSON object"),
    ],
)
def test_grader_names_a_model_that_breaks_the_grade_response_contract(response: str, expected: str) -> None:
    # Given: a fake Ollama response that is JSON but cannot produce a valid grade.
    grader = OllamaGrader("qwen2.5-coder:1.5b", GenerateTransport(response), environ={})

    # When: the grader evaluates a candidate without contacting a model.
    with pytest.raises(ValueError) as raised:
        grader.evaluate("repository: example/tool\n")

    # Then: the failure names the model, contract, bounded output, and bad field.
    message = str(raised.value)
    assert "model qwen2.5-coder:1.5b" in message
    assert "JSON object with integer idea and skill from 1 to 10" in message
    assert "output shown up to 160 characters" in message
    assert expected in message
    if "x" * 200 in response:
        assert "x" * 100 in message
        assert "x" * 161 not in message


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
    grade = OllamaGrader(model, transport, environ={}).evaluate("repository: example/tool\n")
    # Then: the recorded grade carries the selected model name.
    assert grade.model == "qwen2.5-coder:32b"


def test_ollama_generation_bounds_output_and_records_bounded_prompt_version() -> None:
    # Given: one transport serving both candidate-grade and maliciousness contracts.
    class CapturingTransport:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def request(self, method, url, data=None, extra_headers=None):
            request = json.loads(data)
            self.calls.append(request)
            response = {"malicious": False} if "boolean malicious" in request["prompt"] else {
                "idea": 8,
                "skill": 7,
                "description": "useful",
            }
            return 200, {}, json.dumps({"response": json.dumps(response)}).encode()

    transport = CapturingTransport()
    grader = OllamaGrader("qwen2.5-coder:32b", transport, environ={})

    # When: both local-model generation paths run.
    grade = grader.evaluate("repository: example/tool\n")
    assert grader.flags_malicious("repository: example/tool\n") is False

    # Then: both have the same bounded deterministic generation contract.
    assert grade.prompt_version == "cli-v2-bounded"
    assert len(transport.calls) == 2
    for request in transport.calls:
        assert request["format"] == "json"
        assert request["stream"] is False
        assert request["options"] == {"temperature": 0, "num_predict": 128}
        assert len(request["prompt"].encode("utf-8")) <= MODEL_PROMPT_BYTE_CAP


def test_ollama_rejects_an_oversized_complete_prompt_without_http() -> None:
    # Given: digest bytes which become oversized after the required grading instruction.
    class NoHttpTransport:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, method, url, data=None, extra_headers=None):
            self.calls += 1
            return 200, {}, json.dumps({"response": json.dumps({"idea": 8, "skill": 7, "description": "useful"})}).encode()

    transport = NoHttpTransport()
    grader = OllamaGrader("qwen2.5-coder:32b", transport, environ={})

    # When/Then: structure is not silently trimmed and no request leaves the process.
    with pytest.raises(ValueError, match=r"model grading prompt exceeds 24000 UTF-8 bytes"):
        grader.evaluate("x" * MODEL_PROMPT_BYTE_CAP)
    assert transport.calls == 0


def test_output_cut_at_the_generation_limit_is_not_a_grade() -> None:
    # Given: Ollama stopped mid-object at its bounded generation limit.
    grader = OllamaGrader(
        "qwen2.5-coder:32b",
        GenerateTransport('{"idea": 8, "skill": 7, "description":'),
        environ={},
    )

    # When/Then: no numeric fallback turns malformed output into a grade.
    with pytest.raises(ValueError, match="not valid JSON"):
        grader.evaluate("repository: example/tool\n")


def test_ollama_host_env_variable_with_host_and_port() -> None:
    # Given: OLLAMA_HOST is set to a host:port pair.
    # When: the host is resolved.
    url = _resolve_ollama_host({"OLLAMA_HOST": "remote.example.com:8080"})
    # Then: it is wrapped with http://.
    assert url == "http://remote.example.com:8080"


def test_ollama_host_env_variable_with_full_http_url() -> None:
    # Given: OLLAMA_HOST is set to a full http:// URL.
    # When: the host is resolved.
    url = _resolve_ollama_host({"OLLAMA_HOST": "http://remote.example.com:8080"})
    # Then: it is returned as-is (trailing slash stripped if present).
    assert url == "http://remote.example.com:8080"


def test_ollama_host_env_variable_with_full_https_url() -> None:
    # Given: OLLAMA_HOST is set to a full https:// URL.
    # When: the host is resolved.
    url = _resolve_ollama_host({"OLLAMA_HOST": "https://secure.example.com:11434"})
    # Then: it is returned as-is.
    assert url == "https://secure.example.com:11434"


def test_ollama_host_defaults_to_localhost_11434() -> None:
    # Given: OLLAMA_HOST is not set.
    # When: the host is resolved.
    url = _resolve_ollama_host({})
    # Then: it defaults to localhost:11434 wrapped with http://.
    assert url == "http://localhost:11434"


def test_grader_honors_ollama_host_environment() -> None:
    # Given: OLLAMA_HOST is set to a custom endpoint.
    transport = TagsTransport(["qwen2.5-coder:7b"], expected_base="http://custom.local:8080")
    # When: the grader is initialized with that environment.
    grader = OllamaGrader("qwen2.5-coder:7b", transport, environ={"OLLAMA_HOST": "custom.local:8080"})
    # Then: it sends requests to the custom endpoint.
    grade = grader.evaluate("repository: example/tool\n")
    assert grade.model == "qwen2.5-coder:7b"
