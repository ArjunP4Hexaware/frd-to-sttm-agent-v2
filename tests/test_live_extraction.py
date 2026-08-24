"""Live-extraction transport tests (frdsttm.live_extraction) — pure
functions plus a stubbed streaming client; no network, no real SDK calls."""

import json
from types import SimpleNamespace

import pytest

from frdsttm.live_extraction import (
    build_extraction_prompt,
    extract_live,
    parse_extraction_response,
)
from frdsttm.models import FrdIngestionSpec

VALID_SPEC_JSON = json.dumps({
    "project": {"project_id": "1007412"},
    "feeds": [{"feed_name": "cv_risk", "file_name_patterns": ["cv_risk_file.csv"]}],
})


# --------------------------------------------------------------------------- #
# prompt assembly
# --------------------------------------------------------------------------- #

def test_prompt_embeds_schema_descriptions_and_content():
    prompt = build_extraction_prompt("FRD BODY MARKER 12345")
    # The schema (with field descriptions -- that's where the guidance
    # lives) and the document must both be in the prompt.
    assert "ONLY a single JSON object" in prompt
    assert '"file_name_patterns"' in prompt
    assert "Project identifier exactly as stated" in prompt  # a Field description
    assert prompt.rstrip().endswith("FRD BODY MARKER 12345")


def test_prompt_accepts_explicit_schema():
    prompt = build_extraction_prompt("doc", schema={"type": "object", "properties": {"zz_custom": {}}})
    assert '"zz_custom"' in prompt
    assert '"file_name_patterns"' not in prompt


# --------------------------------------------------------------------------- #
# response parsing / validation
# --------------------------------------------------------------------------- #

def test_parse_valid_json_returns_spec():
    spec = parse_extraction_response(VALID_SPEC_JSON, "doc1")
    assert isinstance(spec, FrdIngestionSpec)
    assert spec.feeds[0].feed_name == "cv_risk"


def test_parse_tolerates_markdown_fences():
    fenced = f"```json\n{VALID_SPEC_JSON}\n```"
    spec = parse_extraction_response(fenced, "doc1")
    assert spec.project.project_id == "1007412"


def test_parse_extra_field_raises_naming_field():
    bad = json.dumps({"feeds": [], "invented_field": 1})
    with pytest.raises(RuntimeError) as ei:
        parse_extraction_response(bad, "doc1")
    msg = str(ei.value)
    assert "doc1" in msg
    assert "invented_field" in msg  # extra="forbid" names the offender
    assert "not retried" in msg


def test_parse_invalid_json_raises():
    with pytest.raises(RuntimeError, match="doc1"):
        parse_extraction_response("this is not json {", "doc1")


# --------------------------------------------------------------------------- #
# extract_live against a stubbed streaming client
# --------------------------------------------------------------------------- #

class _FakeStream:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._msg


class _FakeClient:
    """Mirrors the one SDK surface extract_live touches:
    client.messages.stream(**kwargs) -> context manager -> final message."""

    def __init__(self, msg):
        self.calls = []
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.calls.append(kwargs)
                return _FakeStream(msg)

        self.messages = _Messages()


def _final_message(stop_reason="end_turn", text=VALID_SPEC_JSON):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


def test_extract_live_happy_path_and_config_passthrough():
    client = _FakeClient(_final_message())
    resp = extract_live(client, "doc1", "the document body",
                        model="claude-test-model", max_tokens=64000,
                        system_prompt="SYSPROMPT")
    # Response mirrors the mock branch's shape (downstream contract).
    assert resp.stop_reason == "end_turn"
    assert isinstance(resp.parsed_output, FrdIngestionSpec)
    assert resp.usage.input_tokens == 100
    # Config-driven model/max_tokens reach the API call unaltered.
    (kwargs,) = client.calls
    assert kwargs["model"] == "claude-test-model"
    assert kwargs["max_tokens"] == 64000
    assert kwargs["system"] == "SYSPROMPT"
    assert "the document body" in kwargs["messages"][0]["content"]
    assert '"file_name_patterns"' in kwargs["messages"][0]["content"]


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_extract_live_unusable_stop_reason_skips_parse(stop):
    # Truncated/refused output must NOT be parsed -- parsed_output is None
    # and the caller's existing assertion owns the error message.
    client = _FakeClient(_final_message(stop_reason=stop, text="{truncated"))
    resp = extract_live(client, "doc1", "doc", model="m", max_tokens=10,
                        system_prompt="s")
    assert resp.stop_reason == stop
    assert resp.parsed_output is None


def test_extract_live_validation_failure_propagates():
    client = _FakeClient(_final_message(text=json.dumps({"nope": True})))
    with pytest.raises(RuntimeError, match="nope"):
        extract_live(client, "doc1", "doc", model="m", max_tokens=10,
                     system_prompt="s")


# --------------------------------------------------------------------------- #
# provider seam: Databricks-served Claude (2026-08-24)
# --------------------------------------------------------------------------- #
class _FakeAnthropic:
    """Records the kwargs the SDK would have been constructed with. No SDK
    import, no network, no credential anywhere in this file."""

    last_kwargs = None

    def __init__(self, **kwargs):
        _FakeAnthropic.last_kwargs = kwargs


class _FakeModule:
    Anthropic = _FakeAnthropic


class _FakeConfig:
    def __init__(self, host="https://adb-123.azuredatabricks.net/"):
        self.host = host

    def authenticate(self):
        return {"Authorization": "Bearer dbx-token"}


def test_databricks_model_name_prefixes_a_first_party_id():
    from frdsttm.live_extraction import databricks_model_name

    assert databricks_model_name("claude-opus-4-8") == "databricks-claude-opus-4-8"


def test_databricks_model_name_leaves_an_already_prefixed_id_alone():
    """So `model` can be pinned explicitly without being double-prefixed."""
    from frdsttm.live_extraction import databricks_model_name

    assert databricks_model_name("databricks-claude-opus-5") == "databricks-claude-opus-5"


def test_anthropic_provider_builds_a_plain_client():
    """The first-party path must stay byte-identical to before: no base_url,
    no headers, no api_key override -- the SDK resolves ANTHROPIC_API_KEY."""
    from frdsttm.live_extraction import build_live_client

    build_live_client("anthropic", max_retries=2, anthropic_module=_FakeModule)

    assert _FakeAnthropic.last_kwargs == {"max_retries": 2}


def test_unset_provider_also_builds_a_plain_client():
    from frdsttm.live_extraction import build_live_client

    build_live_client("", max_retries=3, anthropic_module=_FakeModule)

    assert _FakeAnthropic.last_kwargs == {"max_retries": 3}


def test_databricks_provider_points_at_the_workspace_endpoint():
    from frdsttm.live_extraction import build_live_client

    build_live_client("databricks", max_retries=2, anthropic_module=_FakeModule,
                      workspace_config=_FakeConfig())
    kw = _FakeAnthropic.last_kwargs

    # trailing slash on the host must not produce a double slash
    assert kw["base_url"] == "https://adb-123.azuredatabricks.net/serving-endpoints/anthropic"
    assert kw["default_headers"] == {"Authorization": "Bearer dbx-token"}
    assert kw["max_retries"] == 2


def test_databricks_provider_sends_no_real_api_key():
    """The whole point: no ANTHROPIC_API_KEY is read, held, or sent. The
    literal "unused" is what Databricks' own documentation passes."""
    from frdsttm.live_extraction import build_live_client, DATABRICKS_UNUSED_API_KEY

    build_live_client("databricks", max_retries=1, anthropic_module=_FakeModule,
                      workspace_config=_FakeConfig())

    assert _FakeAnthropic.last_kwargs["api_key"] == DATABRICKS_UNUSED_API_KEY == "unused"
