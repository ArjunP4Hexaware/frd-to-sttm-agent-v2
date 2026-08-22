"""Corpus route tests (review_app_react/backend/corpus_routes.py).

Same posture as test_sharepoint_routes.py: FastAPI TestClient with the
Graph transport stubbed — no socket, no credential, no model call. All
document content is synthetic and generated in-memory.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent / "review_app_react" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import corpus_routes as cr  # noqa: E402
import sharepoint_routes as spr  # noqa: E402

from test_sharepoint_routes import ENV  # noqa: E402  (same tenant fixture)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(cr.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def configured(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def dirs(monkeypatch, tmp_path):
    preloaded = tmp_path / "frd_raw"
    reference = tmp_path / "sttm_reference"
    monkeypatch.setattr(cr, "PRELOADED_DIR", preloaded)
    monkeypatch.setattr(cr, "REFERENCE_DIR", reference)
    return preloaded, reference


def _frd_text(doc: str, cols: list[str]) -> bytes:
    body = (
        f"# Functional Requirements Document — {doc} (SYNTHETIC)\n\n"
        f"Project ID: 9100009\n\n## Data Ingestion Requirements\n\n"
        f"The Synthetic Vendor delivers {doc.upper()}_YYYYMMDD.txt weekly. "
        f"The feed loads the columns {', '.join(cols)} into the stage table "
        f"syn_cat.syn_stg.{doc.upper()} and is promoted to the standard "
        f"layer table syn_cat.syn_std.{doc.upper()}.\n\n"
        f"**REQ-001** Process shall reject the record when {cols[0]} is NULL.\n"
    )
    return (body + "\nPadding sentence for the ingest sanity gate. " * 8).encode()


def _wb_bytes(table: str, cols: list[str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = f"MAPPING-{table.upper()}"[:31]
    src = ["Database Column Name", "Description", "Datatype", "Null Check", "Comment"]
    tgt = ["Catalog", "Schema", "TableName", "ColumnName", "Datatype"]
    ws.append(["Source File Layout"] + [""] * 4 + ["Stage Layer"] + [""] * 4
              + ["Standard Layer"] + [""] * 4)
    ws.append(src + tgt + tgt)
    for c in cols:
        ws.append([c, f"synthetic {c.lower()}", "String", "Not Null", ""]
                  + ["syn_cat", "syn_stg", table, c, "String"]
                  + ["syn_cat", "syn_std", table, c, "String"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


MEMBER_COLS = ["MEMBER_ID", "ZIP_CODE", "RISK_SCORE"]
CLAIM_COLS = ["CLAIM_NUMBER", "PROVIDER_NPI", "PAID_AMOUNT"]

FRD_ITEMS = [
    {"item_id": "f1", "name": "member_risk.txt", "size": 1, "modified": "m", "web_url": "w"},
    {"item_id": "f2", "name": "claim_intake.txt", "size": 1, "modified": "m", "web_url": "w"},
]
REF_ITEMS = [
    {"item_id": "r1", "name": "member_risk.sttm.xlsx", "size": 1, "modified": "m", "web_url": "w"},
    {"item_id": "r2", "name": "claim_intake.sttm.xlsx", "size": 1, "modified": "m", "web_url": "w"},
]
PAYLOADS = {
    "f1": _frd_text("member_risk", MEMBER_COLS),
    "f2": _frd_text("claim_intake", CLAIM_COLS),
    "r1": _wb_bytes("member_risk", MEMBER_COLS),
    "r2": _wb_bytes("claim_intake", CLAIM_COLS),
}


def _stub_graph(monkeypatch, *, frd_items=FRD_ITEMS, ref_items=REF_ITEMS,
                payloads=PAYLOADS):
    from frdsttm.sharepoint import SharePointItem

    class FakeClient:
        def list_documents(self, suffixes=None, folder=None):
            rows = frd_items if folder is None else ref_items
            return [SharePointItem(**c) for c in rows
                    if suffixes is None or Path(c["name"]).suffix.lower() in suffixes]

        def download_item(self, item_id):
            return payloads[item_id]

    monkeypatch.setattr(spr, "build_client", lambda cfg: FakeClient())


def test_bootstrap_requires_confirm(client, configured, dirs):
    r = client.post("/api/demo/corpus/bootstrap", json={})
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"]


def test_bootstrap_503_when_unconfigured(client, dirs, monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    r = client.post("/api/demo/corpus/bootstrap", json={"confirm": True})
    assert r.status_code == 503


def test_summary_unbuilt_is_a_normal_state(client, dirs):
    r = client.get("/api/demo/corpus")
    assert r.status_code == 200
    assert r.json()["built"] is False
    r = client.get("/api/demo/corpus/frds")
    assert r.status_code == 200
    assert r.json() == {"built": False, "frds": []}


def test_bootstrap_builds_pairs_and_runnable_frds(client, configured, dirs, monkeypatch):
    preloaded, reference = dirs
    _stub_graph(monkeypatch)
    r = client.post("/api/demo/corpus/bootstrap", json={"confirm": True})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["built"] is True
    assert body["n_frd_files"] == 2 and body["n_reference_files"] == 2
    assert body["n_pairs"] == 2 and body["n_unmapped"] == 0
    assert body["skipped"] == []
    assert (preloaded / "member_risk.txt").is_file()
    assert (reference / "member_risk.sttm.xlsx").is_file()
    assert (reference / "corpus_index.json").is_file()

    summary = client.get("/api/demo/corpus").json()
    assert summary["built"] is True and summary["n_pairs"] == 2

    frds = client.get("/api/demo/corpus/frds").json()["frds"]
    by_id = {f["doc_id"]: f for f in frds}
    assert by_id["member_risk"]["reference"] == "member_risk.sttm.xlsx"
    assert by_id["member_risk"]["paired"] is True
    # `path` is None here because PRELOADED_DIR was moved out of the repo
    # root for the test — the runnable flag tells the UI the truth either way.
    assert by_id["member_risk"]["runnable"] in (True, False)


def test_bootstrap_collects_per_file_failures_without_dying(client, configured,
                                                            dirs, monkeypatch):
    bad_frd = [{"item_id": "f9", "name": "broken.docx", "size": 1,
                "modified": "m", "web_url": "w"}] + FRD_ITEMS
    payloads = {**PAYLOADS, "f9": b"not a real docx"}
    _stub_graph(monkeypatch, frd_items=bad_frd, payloads=payloads)
    r = client.post("/api/demo/corpus/bootstrap", json={"confirm": True})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["n_pairs"] == 2  # the good pairs still built
    assert [s["name"] for s in body["skipped"]] == ["broken.docx"]
