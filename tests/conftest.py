"""Suite-wide fixtures.

`_isolate_audit_store` (autouse): every governed route in the review app
records an audit event (review_app_react/backend/audit.py). Point the
event store at the test's tmp_path so the suite never writes into the
repo's local_dev_fixtures/ — and so a test can read back exactly the
events its own requests produced via `audit.list_events()`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "review_app_react" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture(autouse=True)
def _isolate_audit_store(tmp_path, monkeypatch):
    try:
        import audit  # noqa: PLC0415 — backend module; absent only if fastapi extras are missing
    except ImportError:
        yield
        return
    monkeypatch.setattr(audit, "LOCAL_ROOT", tmp_path / "audit_store")
    yield
