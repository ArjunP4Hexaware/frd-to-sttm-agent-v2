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
        # The third kind (2026-08-27). Routed by an explicit folder name so
        # the existing folder-is-None / folder-is-anything-else split, which
        # every other test in this file relies on, is untouched.
        self.dicts: list[Item] = []
        self.dict_folder = "__dicts__"
        self.payloads: dict[str, bytes] = {}
        self.downloads: list[str] = []

    def add_frd(self, item_id, name, payload, etag="e1"):
        self.frds.append(Item(item_id, name, len(payload), "2026-08-20T10:00:00Z", f"https://sp/{name}", etag))
        self.payloads[item_id] = payload

    def add_sttm(self, item_id, name, payload, etag="e1"):
        self.sttms.append(Item(item_id, name, len(payload), "2026-08-20T11:00:00Z", f"https://sp/{name}", etag))
        self.payloads[item_id] = payload

    def add_dict(self, item_id, name, payload, etag="e1"):
        self.dicts.append(Item(item_id, name, len(payload), "2026-08-20T12:00:00Z",
                               f"https://sp/{name}", etag))
        self.payloads[item_id] = payload

    # -- the client surface the sync uses
    def list_documents(self, suffixes=None, folder=None):
        if folder is None:
            rows = self.frds
        elif folder == self.dict_folder:
            rows = self.dicts
        else:
            rows = self.sttms
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
    # the library's convention (learned 2026-08-22): FRD_<name> <-> STTM_<name>
    ("FRD_Community Risk.docx", "STTM_Community Risk.xlsx"),
    ("FRD_claim_intake.docx", "STTM_claim_intake.xlsx"),
    ("frd_Member_Risk", "sttm-member-risk.xlsx"),
    # mixed: a prefixed FRD and the renderer's own suffix convention
    ("FRD_Member Risk.docx", "FRD_Member Risk.sttm.xlsx"),
])
def test_name_key_matches_the_naming_conventions(a, b):
    assert name_key(a) == name_key(b) != ""


def test_prefix_filter_keeps_convention_files_and_counts_the_rest(dirs):
    """FRD_* / STTM_* are synced; files without the prefix are ignored AND
    counted (never silently dropped); pairing happens by the stem."""
    from frdsttm.sync import FRD_NAME_PREFIX, REFERENCE_NAME_PREFIX
    lib = FakeLibrary()
    lib.add_frd("f1", "FRD_member_risk.txt", frd_text("member_risk", MEMBER_COLS))
    lib.add_frd("f2", "notes about the project.txt", frd_text("claim_intake", CLAIM_COLS))
    lib.add_sttm("r1", "STTM_member_risk.xlsx", wb_bytes("member_risk", MEMBER_COLS))
    lib.add_sttm("r2", "scratch.xlsx", wb_bytes("claim_intake", CLAIM_COLS))
    frd_dir, ref_dir = dirs
    out = sync_from_sharepoint(lib, frd_dir=frd_dir, reference_dir=ref_dir,
                               reference_folder="STTMs", thresholds=_th(),
                               now_iso="2026-08-22T12:00:00+00:00",
                               frd_prefix=FRD_NAME_PREFIX, reference_prefix=REFERENCE_NAME_PREFIX)
    assert out["frd_listed"] == 1 and out["frd_ignored"] == 1
    assert out["reference_listed"] == 1 and out["reference_ignored"] == 1
    assert (frd_dir / "FRD_member_risk.txt").is_file()
    assert not (frd_dir / "notes_about_the_project.txt").exists()
    assert not (ref_dir / "scratch.xlsx").exists()
    pair = out["index"]["pairs"]["FRD_member_risk"]
    assert pair["reference"] == "STTM_member_risk.xlsx" and pair["matched_by"] == "name"


def test_no_prefix_means_no_filter(library, dirs):
    out = _sync(library, dirs)
    assert out["frd_ignored"] == 0 and out["reference_ignored"] == 0
    assert out["frd_prefix"] == "" and out["reference_prefix"] == ""


def test_name_key_keeps_distinct_documents_distinct():
    assert name_key("Member Risk FRD.docx") != name_key("Claim Intake FRD.docx")
    assert name_key("FRD.docx") == ""  # nothing identifying left: never matches


# --------------------------------------------------------------------------- #
# the third kind — vendor data dictionaries (2026-08-27)
# --------------------------------------------------------------------------- #
from test_dictionary import _field_row, _file_row, write_dictionary  # noqa: E402


def dict_bytes(tmp_path, name, columns):
    path = write_dictionary(
        tmp_path / name,
        [_file_row(f"{name}_CCYYMMDD.txt", "fields")],
        {"fields": [_field_row(i + 1, c) for i, c in enumerate(columns)]},
    )
    return path.read_bytes()


def _dirs(tmp_path):
    return tmp_path / "frd_raw", tmp_path / "sttm_reference", tmp_path / "vdd_raw"


def test_dictionaries_sync_into_their_own_volume(tmp_path):
    """Their own volume on purpose: corpus.parse_reference_dir globs every
    .xlsx in the reference directory and a DICT_ workbook is not a template."""
    frd, ref, dic = _dirs(tmp_path)
    lib = FakeLibrary()
    lib.add_frd("f1", "FRD_member_risk.txt", frd_text("member_risk", MEMBER_COLS))
    lib.add_sttm("r1", "STTM_member_risk.xlsx", wb_bytes("member_risk", MEMBER_COLS))
    lib.add_dict("d1", "DICT_member_risk.xlsx",
                 dict_bytes(tmp_path, "DICT_member_risk.xlsx", MEMBER_COLS))

    out = sync_from_sharepoint(
        lib, frd_dir=frd, reference_dir=ref, reference_folder="STTMs",
        thresholds=thresholds_from(lambda n, d: d), now_iso="2026-08-27T00:00:00+00:00",
        frd_prefix="FRD_", reference_prefix="STTM_",
        dictionary_dir=dic, dictionary_folder=lib.dict_folder, dictionary_prefix="DICT_",
    )
    assert out["dictionary_listed"] == 1
    assert out["dictionary_downloaded"] == 1
    assert (dic / "DICT_member_risk.xlsx").is_file()
    assert not (ref / "DICT_member_risk.xlsx").exists()
    index = out["index"]
    assert index["dictionary_pairs"] == {"FRD_member_risk": "DICT_member_risk.xlsx"}


def test_no_dictionary_dir_syncs_exactly_as_before(tmp_path):
    """The ordinary state until vendors return DICT_ workbooks: two folders
    listed, not three — an empty-folder probe every tick buys nothing."""
    frd, ref, _ = _dirs(tmp_path)
    lib = FakeLibrary()
    lib.add_frd("f1", "FRD_member_risk.txt", frd_text("member_risk", MEMBER_COLS))
    calls = []
    inner = lib.list_documents
    lib.list_documents = lambda suffixes=None, folder=None: (
        calls.append(folder) or inner(suffixes=suffixes, folder=folder))

    out = sync_from_sharepoint(
        lib, frd_dir=frd, reference_dir=ref, reference_folder="STTMs",
        thresholds=thresholds_from(lambda n, d: d), now_iso="2026-08-27T00:00:00+00:00",
        frd_prefix="FRD_", reference_prefix="STTM_",
    )
    assert len(calls) == 2
    assert out["dictionary_listed"] == 0
    assert out["index"]["dictionaries"] == {}


def test_dictionary_prefix_filter_counts_what_it_ignores(tmp_path):
    frd, ref, dic = _dirs(tmp_path)
    lib = FakeLibrary()
    lib.add_frd("f1", "FRD_member_risk.txt", frd_text("member_risk", MEMBER_COLS))
    lib.add_dict("d1", "DICT_member_risk.xlsx",
                 dict_bytes(tmp_path, "DICT_member_risk.xlsx", MEMBER_COLS))
    lib.add_dict("d2", "vendor notes.xlsx",
                 dict_bytes(tmp_path, "vendor notes.xlsx", MEMBER_COLS))

    out = sync_from_sharepoint(
        lib, frd_dir=frd, reference_dir=ref, reference_folder="STTMs",
        thresholds=thresholds_from(lambda n, d: d), now_iso="2026-08-27T00:00:00+00:00",
        frd_prefix="FRD_", reference_prefix="STTM_",
        dictionary_dir=dic, dictionary_folder=lib.dict_folder, dictionary_prefix="DICT_",
    )
    assert out["dictionary_listed"] == 1
    assert out["dictionary_ignored"] == 1
    assert not (dic / "vendor_notes.xlsx").exists()


def test_a_departed_dictionary_leaves_the_volume(tmp_path):
    frd, ref, dic = _dirs(tmp_path)
    lib = FakeLibrary()
    lib.add_frd("f1", "FRD_member_risk.txt", frd_text("member_risk", MEMBER_COLS))
    lib.add_dict("d1", "DICT_member_risk.xlsx",
                 dict_bytes(tmp_path, "DICT_member_risk.xlsx", MEMBER_COLS))
    kw = dict(frd_dir=frd, reference_dir=ref, reference_folder="STTMs",
              thresholds=thresholds_from(lambda n, d: d),
              frd_prefix="FRD_", reference_prefix="STTM_",
              dictionary_dir=dic, dictionary_folder=lib.dict_folder,
              dictionary_prefix="DICT_")
    sync_from_sharepoint(lib, now_iso="2026-08-27T00:00:00+00:00", **kw)
    assert (dic / "DICT_member_risk.xlsx").is_file()

    lib.dicts.clear()
    out = sync_from_sharepoint(lib, now_iso="2026-08-27T01:00:00+00:00", **kw)
    assert out["dictionary_removed"] == 1
    assert not (dic / "DICT_member_risk.xlsx").exists()
    assert out["index"]["dictionary_pairs"] == {}


def test_an_unchanged_dictionary_is_not_re_downloaded(tmp_path):
    frd, ref, dic = _dirs(tmp_path)
    lib = FakeLibrary()
    lib.add_frd("f1", "FRD_member_risk.txt", frd_text("member_risk", MEMBER_COLS))
    lib.add_dict("d1", "DICT_member_risk.xlsx",
                 dict_bytes(tmp_path, "DICT_member_risk.xlsx", MEMBER_COLS))
    kw = dict(frd_dir=frd, reference_dir=ref, reference_folder="STTMs",
              thresholds=thresholds_from(lambda n, d: d),
              frd_prefix="FRD_", reference_prefix="STTM_",
              dictionary_dir=dic, dictionary_folder=lib.dict_folder,
              dictionary_prefix="DICT_")
    sync_from_sharepoint(lib, now_iso="2026-08-27T00:00:00+00:00", **kw)
    lib.downloads.clear()
    out = sync_from_sharepoint(lib, now_iso="2026-08-27T01:00:00+00:00", **kw)
    assert out["dictionary_unchanged"] == 1
    assert lib.downloads == []
