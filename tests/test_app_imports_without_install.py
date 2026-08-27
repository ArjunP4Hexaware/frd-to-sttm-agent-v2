"""The review-app backend must import WITHOUT the frdsttm package installed.

The deployed Databricks App installs review_app_react/requirements.txt and
never pip-installs this repo, so `src/` reaches sys.path only through the
bootstrap lines in the backend modules that import frdsttm. Locally the
editable install (`pip install -e .`) hides any gap in that bootstrap — which
is exactly how the App crashed at start-up on 2026-08-27
(`app -> corpus_routes -> demo -> frdsttm`, ModuleNotFoundError) after a
green local suite.

This test reproduces the container: a subprocess strips every `.../src`
entry the editable install added to sys.path, then imports `app` from the
backend directory the way `python review_app_react/backend/app.py` would.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "review_app_react" / "backend"

_PROBE = r"""
import sys
sys.path[:] = [p for p in sys.path if not p.rstrip('/').endswith('/src')]
for name in list(sys.modules):
    if name == 'frdsttm' or name.startswith('frdsttm.'):
        del sys.modules[name]
import app  # noqa: F401
print('OK')
# Importing app pulls in deltalake/pyarrow (via local_tables); on the py3.14
# venv a NORMAL interpreter exit can then deadlock in their finalizers — the
# same hang the notebooks dodge with os._exit(0). One full-suite run stalled
# >5 min on 2026-08-27 before this line existed; the rerun took 7 s.
sys.stdout.flush()
import os; os._exit(0)
"""


def test_backend_imports_with_src_stripped_from_sys_path():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=BACKEND,
        env={"PATH": "/usr/bin:/bin", "STTM_SYNC_ON_STARTUP": "0", "HOME": str(REPO)},
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0 and proc.stdout.strip().endswith("OK"), (
        "the backend does not import without the editable install — "
        "a module imports frdsttm before src/ is on sys.path:\n" + proc.stderr[-2000:]
    )
