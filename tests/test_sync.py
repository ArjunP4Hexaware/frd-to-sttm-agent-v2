"""frdsttm.sync — SharePoint → volumes → corpus index, incrementally.

Offline: a fake client scripted with in-memory listings; all documents are
SYNTHETIC (per the no-client-documents rule). Covers the contract the
slides make: first run = bulk load, later runs pull only what changed,
departed files leave the volumes, every document carries a fingerprint,
and a reviewer-uploaded `<doc>.sttm.xlsx` pairs with its FRD by name.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pytest
from openpyxl import Workbook

from frdsttm.corpus import CORPUS_INDEX_NAME, load_corpus_index
from frdsttm.similarity import name_key, thresholds_from
from frdsttm.sync import (
    MANIFEST_NAME,
    load_manifest,
    manifest_by_local_name,
    reindex,
    safe_name,
    sync_from_sharepoint,
)


# --------------------------------------------------------------------------- #
# synthetic library
# --------------------------------------------------------------------------- #
@dataclass
class Item:
    item_id: str
    name: str
    size: int
    modified: str
    web_url: str
    etag: str = ""


def frd_text(doc: str, cols: list[str]) -> bytes:
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


def wb_bytes(table: str, cols: list[str]) -> bytes:
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
ROSTER_COLS = ["ROSTER_ID", "NPI_NUMBER", "TERM_DATE"]


class FakeLibrary:
    """Two folders of items + payloads; mutable so tests can 'upload' and
    'edit' between syncs. download_item counts calls so the incremental
    contract is provable."""

    def __init__(self):
        self.frds: list[Item] = []
        self.sttms: list[Item] = []
        self.payloads: dict[str, bytes] = {}
        self.downloads: list[str] = []

    def add_frd(self, item_id, name, payload, etag="e1"):
        self.frds.append(Item(item_id, name, len(payload), "2026-08-20T10:00:00Z", f"https://sp/{name}", etag))
        self.payloads[item_id] = payload

    def add_sttm(self, item_id, name, payload, etag="e1"):
        self.sttms.append(Item(item_id, name, len(payload), "2026-08-20T11:00:00Z", f"https://sp/{name}", etag))
        self.payloads[item_id] = payload

    # -- the client surface the sync uses
    def list_documents(self, suffixes=None, folder=None):
        rows = self.frds if folder is None else self.sttms
        return [i for i in rows if suffixes is None or ("." + i.name.rsplit(".", 1)[-1]).lower() in suffixes]

    def download_item(self, item_id):
        self.downloads.append(item_id)
        return self.payloads[item_id]


@pytest.fixture()
def library():
    lib = FakeLibrary()
    lib.add_frd("f1", "Member Risk FRD.txt", frd_text("member_risk", MEMBER_COLS))
    lib.add_frd("f2", "Claim Intake FRD.txt", frd_text("claim_intake", CLAIM_COLS))
    lib.add_sttm("r1", "Member Risk FRD.sttm.xlsx", wb_bytes("member_risk", MEMBER_COLS))
    return lib


@pytest.fixture()
def dirs(tmp_path):
    return tmp_path / "frd_raw", tmp_path / "sttm_reference"


def _th():
    return thresholds_from(lambda n, d: d)


def _sync(lib, dirs, now="2026-08-22T12:00:00+00:00"):
    frd_dir, ref_dir = dirs
    return sync_from_sharepoint(lib, frd_dir=frd_dir, reference_dir=ref_dir,
                                reference_folder="STTMs", thresholds=_th(), now_iso=now)


# --------------------------------------------------------------------------- #
# first sync = bulk load
# --------------------------------------------------------------------------- #
def test_first_sync_lands_everything_and_pairs_by_name(library, dirs):
    frd_dir, ref_dir = dirs
    out = _sync(library, dirs)
    assert out["frd_downloaded"] == 2 and out["reference_downloaded"] == 1
    assert out["skipped"] == []
    assert (frd_dir / "Member_Risk_FRD.txt").is_file()          # sanitised names
    assert (ref_dir / "Member_Risk_FRD.sttm.xlsx").is_file()
    assert (ref_dir / CORPUS_INDEX_NAME).is_file() and (ref_dir / MANIFEST_NAME).is_file()
    assert list(frd_dir.glob("*.part")) == []

    index = out["index"]
    pair = index["pairs"]["Member_Risk_FRD"]
    assert pair["reference"] == "Member_Risk_FRD.sttm.xlsx"
    assert pair["matched_by"] == "name" and pair["confidence"] == "high"
    assert index["unmapped"] == ["Claim_Intake_FRD"]
    # every document carries a fingerprint of its bytes
    assert len(index["frds"]["Member_Risk_FRD"]["content_sha256"]) == 64
    assert len(index["references"]["Member_Risk_FRD.sttm.xlsx"]["content_sha256"]) == 64

    manifest = load_manifest(ref_dir)
    assert manifest["synced_at"] == "2026-08-22T12:00:00+00:00"
    by_name = manifest_by_local_name(manifest, "frd")
    assert by_name["Member_Risk_FRD.txt"]["web_url"] == "https://sp/Member Risk FRD.txt"


# --------------------------------------------------------------------------- #
# later syncs = incremental
# --------------------------------------------------------------------------- #
def test_unchanged_items_are_not_downloaded_again(library, dirs):
    _sync(library, dirs)
    library.downloads.clear()
    out = _sync(library, dirs, now="2026-08-22T12:15:00+00:00")
    assert library.downloads == []
    assert out["frd_downloaded"] == 0 and out["frd_unchanged"] == 2
    assert out["reference_downloaded"] == 0 and out["reference_unchanged"] == 1


def test_changed_etag_triggers_a_redownload_and_new_fingerprint(library, dirs):
    frd_dir, ref_dir = dirs
    first = _sync(library, dirs)
    sha_before = first["index"]["frds"]["Claim_Intake_FRD"]["content_sha256"]
    # The FRD is revised in SharePoint: same name, new content, new eTag.
    library.payloads["f2"] = frd_text("claim_intake", CLAIM_COLS + ["ADJUDICATION_FLAG"])
    library.frds[1].etag = "e2"
    library.frds[1].size = len(library.payloads["f2"])
    library.downloads.clear()
    out = _sync(library, dirs)
    assert library.downloads == ["f2"]
    assert out["frd_downloaded"] == 1 and out["frd_unchanged"] == 1
    assert out["index"]["frds"]["Claim_Intake_FRD"]["content_sha256"] != sha_before
    assert b"ADJUDICATION_FLAG" in (frd_dir / "Claim_Intake_FRD.txt").read_bytes()


def test_reviewer_upload_is_picked_up_next_sync_and_pairs_by_name(library, dirs):
    """The slide-2 loop: a person uploads `<doc>.sttm.xlsx`; the next sync
    lands it in sttm_reference and the FRD stops being unmapped."""
    first = _sync(library, dirs)
    assert first["index"]["unmapped"] == ["Claim_Intake_FRD"]
    library.add_sttm("r2", "Claim Intake FRD.sttm.xlsx", wb_bytes("claim_intake", CLAIM_COLS))
    out = _sync(library, dirs)
    assert out["reference_downloaded"] == 1
    assert out["index"]["unmapped"] == []
    assert out["index"]["pairs"]["Claim_Intake_FRD"]["matched_by"] == "name"


def test_departed_items_leave_the_volume_but_hand_staged_files_stay(library, dirs):
    frd_dir, ref_dir = dirs
    _sync(library, dirs)
    # Someone staged a file by hand (not via the sync): it must survive.
    (frd_dir / "hand_staged.txt").write_bytes(frd_text("hand_staged", ROSTER_COLS))
    library.frds = [i for i in library.frds if i.item_id != "f2"]  # removed from the library
    out = _sync(library, dirs)
    assert out["frd_removed"] == 1
    assert not (frd_dir / "Claim_Intake_FRD.txt").exists()
    assert (frd_dir / "hand_staged.txt").exists()
    assert "hand_staged" in out["index"]["frds"]           # indexed like any other
    assert "Claim_Intake_FRD" not in out["index"]["frds"]


def test_one_failed_download_is_reported_not_fatal(library, dirs):
    frd_dir, _ref_dir = dirs
    del library.payloads["f2"]  # download raises KeyError
    out = _sync(library, dirs)
    assert out["frd_downloaded"] == 1
    assert [s["name"] for s in out["skipped"]] == ["Claim Intake FRD.txt"]
    assert (frd_dir / "Member_Risk_FRD.txt").is_file()
    assert "Member_Risk_FRD" in out["index"]["pairs"]


def test_truncated_download_is_reported_and_not_written(library, dirs):
    frd_dir, _ = dirs
    library.frds[0].size = 999_999  # library claims more bytes than arrive
    out = _sync(library, dirs)
    assert any("999999" in s["error"] for s in out["skipped"])
    assert not (frd_dir / "Member_Risk_FRD.txt").exists()


def test_corrupt_manifest_means_a_full_resync_and_says_so(library, dirs):
    _frd_dir, ref_dir = dirs
    _sync(library, dirs)
    (ref_dir / MANIFEST_NAME).write_text("{not json")
    library.downloads.clear()
    out = _sync(library, dirs)
    assert "manifest_reset" in out
    assert sorted(library.downloads) == ["f1", "f2", "r1"]
    json.loads((ref_dir / MANIFEST_NAME).read_text())  # rewritten clean


# --------------------------------------------------------------------------- #
# reindex (no network) + helpers
# --------------------------------------------------------------------------- #
def test_reindex_builds_from_the_volumes_alone(library, dirs):
    frd_dir, ref_dir = dirs
    _sync(library, dirs)
    (ref_dir / CORPUS_INDEX_NAME).unlink()
    index, skipped = reindex(frd_dir, ref_dir, _th(), "2026-08-22T13:00:00+00:00")
    assert skipped == []
    assert index["generated_at"] == "2026-08-22T13:00:00+00:00"
    assert set(index["frds"]) == {"Member_Risk_FRD", "Claim_Intake_FRD"}
    assert load_corpus_index(ref_dir)["pairs"] == index["pairs"]


def test_reindex_reports_an_unparsable_frd(tmp_path):
    frd_dir, ref_dir = tmp_path / "frd_raw", tmp_path / "sttm_reference"
    frd_dir.mkdir()
    (frd_dir / "broken.docx").write_bytes(b"not a docx")
    (frd_dir / "ok.txt").write_bytes(frd_text("ok", ROSTER_COLS))
    index, skipped = reindex(frd_dir, ref_dir, _th(), "now")
    assert [s["name"] for s in skipped] == ["broken.docx"]
    assert list(index["frds"]) == ["ok"]


def test_safe_name_never_escapes_the_volume():
    assert safe_name("../../etc/evil.docx") == "evil.docx"
    assert safe_name("Community Risk FRD.docx") == "Community_Risk_FRD.docx"


@pytest.mark.parametrize("a,b", [
    ("Community Risk FRD.docx", "Community Risk FRD.sttm.xlsx"),
    ("Community_Risk_FRD", "community-risk-STTM.xlsx"),
    ("claim_intake_frd", "claim_intake_sttm.xlsx"),
])
def test_name_key_matches_the_naming_conventions(a, b):
    assert name_key(a) == name_key(b) != ""


def test_name_key_keeps_distinct_documents_distinct():
    assert name_key("Member Risk FRD.docx") != name_key("Claim Intake FRD.docx")
    assert name_key("FRD.docx") == ""  # nothing identifying left: never matches
