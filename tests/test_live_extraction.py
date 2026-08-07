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
