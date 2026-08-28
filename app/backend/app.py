"""FastAPI backend: the picker, runs, answers, the workbook.

Local:    STTM_APP_MODE=local  python app/backend/app.py   (pipeline in-process)
Deployed: Databricks App (app.yaml) — the pipeline runs as the bundle job.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import runs
import settings
import storage
from frdsttm import corpus, pipeline
from frdsttm.frd_parsing import SUPPORTED_SUFFIXES

app = FastAPI(title="FRD to STTM Agent")


def _who(request: Request) -> str:
    for h in ("x-forwarded-email", "x-forwarded-preferred-username", "x-forwarded-user"):
        v = request.headers.get(h)
        if v:
            return v
    return "local"


# --------------------------------------------------------------------------- #
# config + documents
# --------------------------------------------------------------------------- #
@app.get("/api/config")
def config() -> dict:
    return {"mode": settings.MODE, "provider": settings.PROVIDER, "model": settings.MODEL,
            "catalog": settings.CATALOG, "schema": settings.SCHEMA, "volumes": settings.VOLUMES,
            "data_root": settings.VOLUME_ROOT if settings.IS_DATABRICKS else str(settings.LOCAL_ROOT)}


def _index():
    try:
        return corpus.load_index(settings.PATHS.reference)
    except corpus.CorpusIndexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/documents")
def documents() -> dict:
    storage.pull_documents()
    index = _index()
    if index is None:
        return {"built": False, "documents": [], "unpaired_vdds": [], "unpaired_references": [],
                "vdd_errors": {}, "generated_at": None}
    docs = [{"doc_id": d, **e, "vdd_summary": index["vdds"].get(e["vdd"] or ""),
             "sttm_summary": index["references"].get(e["sttm"] or "")}
            for d, e in sorted(index["documents"].items())]
    return {"built": True, "documents": docs, "unpaired_vdds": index["unpaired_vdds"],
            "unpaired_references": index["unpaired_references"], "vdd_errors": index["vdd_errors"],
            "generated_at": index["generated_at"]}


_reindex = {"state": "idle", "error": None, "url": None, "finished_at": None}


@app.post("/api/reindex", status_code=202)
def reindex(request: Request) -> dict:
    if _reindex["state"] == "running":
        raise HTTPException(status_code=409, detail="a reindex is already running")
    _reindex.update(state="running", error=None, url=None, finished_at=None)

    def work():
        try:
            if settings.IS_DATABRICKS:
                import jobs
                job_run_id, url = jobs.start("reindex", triggered_by=_who(request))
                _reindex["url"] = url
                jobs.wait(job_run_id)
                storage.pull_documents(force=True)
            else:
                pipeline.reindex(settings.PATHS)
            _reindex["state"] = "done"
        except Exception as exc:  # noqa: BLE001
            _reindex.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            _reindex["finished_at"] = runs._now()
    threading.Thread(target=work, daemon=True).start()
    return dict(_reindex)


@app.get("/api/reindex")
def reindex_state() -> dict:
    return dict(_reindex)


_KIND_DIR = {"frd": "frds", "vdd": "vdds", "sttm": "reference"}
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.get("/api/documents/{doc_id}/{kind}")
def download_document(doc_id: str, kind: str):
    index = _index() or {"documents": {}}
    entry = index["documents"].get(doc_id)
    if entry is None or kind not in _KIND_DIR:
        raise HTTPException(status_code=404, detail="no such document")
    name = entry.get(kind)
    if not name:
        raise HTTPException(status_code=404, detail=f"this FRD has no {kind}")
    path = getattr(settings.PATHS, _KIND_DIR[kind]) / Path(name).name
    if not path.is_file():
        storage.pull_documents()
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{name} is indexed but not present — reindex")
    return FileResponse(str(path), filename=path.name,
                        media_type=_XLSX if path.suffix == ".xlsx" else _DOCX)


@app.post("/api/documents/upload", status_code=201)
async def upload_document(kind: str, file: UploadFile = File(...)) -> dict:
    """Drop an FRD / VDD / approved STTM into its volume, then reindex."""
    if kind not in _KIND_DIR:
        raise HTTPException(status_code=400, detail="kind must be frd | vdd | sttm")
    name = Path(file.filename or "").name
    suffix = Path(name).suffix.lower()
    ok = suffix in SUPPORTED_SUFFIXES if kind == "frd" else suffix == ".xlsx"
    if not name or not ok:
        raise HTTPException(status_code=400, detail=f"unexpected file type for a {kind}: {name!r}")
    storage.push_document(_KIND_DIR[kind], name, await file.read())
    return {"kind": kind, "name": name}


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #
class StartRun(BaseModel):
    doc_id: str


class Answer(BaseModel):
    question_id: str
    value: str


@app.get("/api/runs")
def list_runs() -> dict:
    return {"runs": runs.list_all()}


@app.post("/api/runs", status_code=201)
def start_run(body: StartRun, request: Request) -> dict:
    index = _index()
    entry = (index or {"documents": {}})["documents"].get(body.doc_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{body.doc_id!r} is not in the index — reindex first")
    if not entry["generatable"]:
        raise HTTPException(status_code=400, detail=f"{body.doc_id} cannot be generated: {entry['reason']}")
    try:
        run_id = runs.start(body.doc_id, by=_who(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id, "doc_id": body.doc_id}


def _run_or_404(run_id: str) -> dict:
    try:
        return runs.view(run_id)
    except FileNotFoundError as exc:
        live = runs.active(run_id)
        if live:
            return {"run_id": run_id, "doc_id": live.get("doc_id"), "status": "extracting",
                    "live": live, "preview": [], "summary": None}
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    return _run_or_404(run_id)


@app.post("/api/runs/{run_id}/answers")
def post_answer(run_id: str, body: Answer, request: Request) -> dict:
    try:
        run = runs.answer(run_id, body.question_id, body.value, by=_who(request))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=400, detail="no such question on this run")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": run["status"], "questions": run["assessment"]["questions"]}


@app.post("/api/runs/{run_id}/render", status_code=202)
def post_render(run_id: str, request: Request) -> dict:
    try:
        runs.render(run_id, by=_who(request))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id, "live": runs.active(run_id)}


@app.get("/api/runs/{run_id}/workbook")
def workbook(run_id: str):
    run = _run_or_404(run_id)
    name = (run.get("render") or {}).get("workbook")
    path = settings.PATHS.run_dir(run_id) / name if name else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="no workbook has been generated for this run")
    return FileResponse(str(path), filename=path.name, media_type=_XLSX)


@app.get("/api/runs/{run_id}/report")
def report(run_id: str):
    run = _run_or_404(run_id)
    return Response(content=pipeline.report_md(run), media_type="text/markdown")


# --------------------------------------------------------------------------- #
# frontend
# --------------------------------------------------------------------------- #
class _NoStoreHTML(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", _NoStoreHTML(directory=str(_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(app, host=os.environ.get("STTM_BIND_HOST", "0.0.0.0"),
                port=int(os.environ.get("DATABRICKS_APP_PORT", 8000)))
