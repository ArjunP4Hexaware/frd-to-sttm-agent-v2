"""frdsttm — shared logic for the FRD-to-STTM agent.

The four pipeline notebooks (notebooks/01_frd_ingest.py ..
04_sttm_render.py) remain the entry points, in both execution modes
(plain local scripts and Databricks notebook tasks). This package holds
the logic they share:

- ``frdsttm.models`` — the pydantic extraction/contract models
  (``FrdIngestionSpec`` and children, ``GatedAmbiguity``,
  ``HumanResolution``), mirroring ``schema/sttm_extraction_schema.json``.
- ``frdsttm.local_tables`` — the local (deltalake-backed, no Spark)
  stand-in for ``spark.table`` / ``saveAsTable`` used when a notebook
  runs outside Databricks.
- ``frdsttm.mock_extractions`` — hand-authored mock ``FrdIngestionSpec``s
  for STTM_MOCK_EXTRACTION=1 runs (plumbing tests, review-app demo; not
  an extraction-quality benchmark).

Thin re-export shims remain at the old ``notebooks/_models.py`` /
``_local_tables.py`` / ``_mock_extractions.py`` paths so existing
``from _models import ...`` imports and the Databricks ``%run ./_models``
magic keep working unchanged.
"""
