"""
frdsttm.sharepoint — Microsoft Graph transport for the SharePoint document
library that is the program's system of record for FRDs and STTMs.

Position in the pipeline. This module is a *transport seam*, deliberately
not part of stages 01-04. `01_frd_ingest` scans a directory (`RAW_DIR`) and
parses whatever supported files it finds; `04_sttm_render` writes a workbook
into an output volume. SharePoint therefore attaches at the edges:

    SharePoint library  --fetch-->  frd_raw/  -> 01 -> 02 -> 03 -> 04 -> rendered/
                                                                          |
    SharePoint library  <--publish--------------------------------------- +

Stages 01-04 are untouched by this file and stay network-free, so the test
suite keeps running offline with zero credentials. Do NOT "simplify" this by
calling Graph from inside 01: that puts a network dependency and a token
lifetime inside the parsing stage and breaks that guarantee.

Dependencies. Standard library only (`urllib.request`, `json`). Graph is
plain REST over HTTPS and needs no SDK. This is a deliberate choice, not an
oversight: the review app is deployed as a Databricks App, which installs
`review_app_react/requirements.txt` rather than `pyproject.toml`, so every
avoided dependency is one less thing that can be missing at runtime.

Auth. App-only client credentials against an Entra ID app registration:
tenant id + client id + client secret, exchanged for an app token with the
`.default` scope. The secret comes from the Databricks secret scope (or an
env var locally) and is NEVER logged, echoed, returned, or embedded in an
error message. Required Graph application permission: `Sites.Selected`
(preferred, grant per-site) or `Files.ReadWrite.All`, with admin consent.

Fail-loud posture, matching the rest of the repo. Missing configuration
raises and names both remedies (env var and secret scope). A non-2xx Graph
response raises `GraphError` carrying status, the Graph error code, and the
request id from the response headers — the three things you need to hand to
a tenant admin. There is no silent fallback to a cached or mock document in
either direction: a run that cannot reach SharePoint fails rather than
quietly ingesting a stale local copy.

Testing seam. Every network call goes through one injectable `transport`
callable with the signature
`(method, url, headers, body) -> (status, headers, bytes)`. Tests pass a
stub; nothing in the suite opens a socket.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
LOGIN_ROOT = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Graph's simple PUT upload is documented for files up to 4 MiB; anything
# larger needs a resumable upload session. We fail loudly rather than
# silently truncating -- see upload_file().
SIMPLE_UPLOAD_MAX_BYTES = 4 * 1024 * 1024


class SharePointConfigError(RuntimeError):
    """Configuration is missing or contradictory. Names the remedy."""


class GraphError(RuntimeError):
    """A Graph call returned a non-2xx response."""

    def __init__(self, status: int, url: str, code: str, message: str, request_id: str | None):
        self.status, self.url, self.code, self.request_id = status, url, code, request_id
        super().__init__(
            f"Graph {status} on {url} — {code}: {message}"
            + (f" (request-id {request_id})" if request_id else "")
        )


@dataclass(frozen=True)
class SharePointConfig:
    """Every knob. No literal belongs in call logic; see repo config doctrine.

    `client_secret` is held here only to hand to the token call. It is
    excluded from repr so it cannot leak into a traceback or a log line.
    """

    tenant_id: str
    client_id: str
    client_secret: str
    host: str            # e.g. contoso.sharepoint.com
    site_path: str       # e.g. /sites/DataOffice
    library: str         # document library (drive) display name
    frd_folder: str      # folder holding input FRDs; "" = library root
    output_folder: str   # folder rendered STTMs are published to

    def __repr__(self) -> str:  # never let the secret reach a log or traceback
        return (
            f"SharePointConfig(tenant_id={self.tenant_id!r}, client_id={self.client_id!r}, "
            f"client_secret=<redacted>, host={self.host!r}, site_path={self.site_path!r}, "
            f"library={self.library!r}, frd_folder={self.frd_folder!r}, "
            f"output_folder={self.output_folder!r})"
        )


@dataclass(frozen=True)
class SharePointItem:
    """One file in the library. `item_id` is the stable Graph id — the deck's
    "files fetched by item id from a named library"; we never crawl the site."""

    item_id: str
    name: str
    size: int
    modified: str
    web_url: str


def load_config(param, secret) -> SharePointConfig:
    """Build the config from the notebook's `_param` accessor and a `secret`
    callable that resolves the client secret (Databricks secret scope, or an
    env var locally).

    Raises SharePointConfigError naming BOTH remedies if anything required is
    absent, rather than proceeding with an empty tenant and failing later
    inside Graph with an opaque 400.
    """
    values = {
        "tenant_id": param("sharepoint_tenant_id", ""),
        "client_id": param("sharepoint_client_id", ""),
        "host": param("sharepoint_host", ""),
        "site_path": param("sharepoint_site_path", ""),
        "library": param("sharepoint_library", "Documents"),
        "frd_folder": param("sharepoint_frd_folder", ""),
        "output_folder": param("sharepoint_output_folder", ""),
    }
    client_secret = secret() or ""

    missing = [k for k in ("tenant_id", "client_id", "host", "site_path") if not values[k]]
    if not client_secret:
        missing.append("client_secret")
    if missing:
        raise SharePointConfigError(
            "SharePoint is not configured — missing: " + ", ".join(sorted(missing)) + ". "
            "Set the widgets/env vars SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID, "
            "SHAREPOINT_HOST, SHAREPOINT_SITE_PATH, and provide the client secret via "
            "the Databricks secret scope 'sttm_agent/sharepoint_client_secret' "
            "(env var SHAREPOINT_CLIENT_SECRET locally). No value is defaulted: a "
            "half-configured tenant fails inside Graph with an opaque 400."
        )
    if not values["site_path"].startswith("/"):
        raise SharePointConfigError(
            f"sharepoint_site_path must start with '/' (got {values['site_path']!r}) — "
            "Graph addresses a site as {host}:{server-relative-path}, e.g. /sites/DataOffice."
        )
    return SharePointConfig(client_secret=client_secret, **values)


def _urlopen_transport(method: str, url: str, headers: dict, body: bytes | None):
    """Default transport. The ONLY place this module opens a socket."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _raise_for_status(status: int, url: str, headers: dict, payload: bytes) -> None:
    if 200 <= status < 300:
        return
    code, message = "unknown", payload[:400].decode("utf-8", "replace")
    try:
        err = json.loads(payload).get("error", {})
        code, message = err.get("code", code), err.get("message", message)
    except (ValueError, AttributeError):
        pass  # non-JSON error body: keep the raw excerpt as the message
    request_id = headers.get("request-id") or headers.get("client-request-id")
    raise GraphError(status, url, code, message, request_id)


def acquire_token(cfg: SharePointConfig, transport=_urlopen_transport) -> str:
    """Exchange the client credentials for an app-only Graph token.

    The secret is sent in the request body and never returned, logged, or
    included in an exception message — a token/credential failure surfaces as
    the Graph error code only.
    """
    body = urllib.parse.urlencode({
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "scope": GRAPH_SCOPE,
        "grant_type": "client_credentials",
    }).encode()
    url = f"{LOGIN_ROOT}/{cfg.tenant_id}/oauth2/v2.0/token"
    status, headers, payload = transport(
        "POST", url, {"Content-Type": "application/x-www-form-urlencoded"}, body
    )
    _raise_for_status(status, url, headers, payload)
    token = json.loads(payload).get("access_token")
    if not token:
        raise GraphError(status, url, "no_access_token",
                         "token endpoint returned 2xx without an access_token", None)
    return token


class SharePointClient:
    """Thin, fail-loud Graph client scoped to one site + one document library.

    Site and drive ids are resolved once and cached on the instance: a run
    fetching five FRDs makes two resolution calls, not ten. Nothing else is
    cached — a stale document is worse than a slow one.
    """

    def __init__(self, cfg: SharePointConfig, token: str, transport=_urlopen_transport):
        self.cfg, self._token, self._transport = cfg, token, transport
        self._site_id: str | None = None
        self._drive_id: str | None = None

    # -- plumbing ---------------------------------------------------------- #

    def _call(self, method: str, url: str, *, body: bytes | None = None,
              content_type: str | None = None, parse_json: bool = True):
        headers = {"Authorization": f"Bearer {self._token}"}
        if content_type:
            headers["Content-Type"] = content_type
        status, resp_headers, payload = self._transport(method, url, headers, body)
        _raise_for_status(status, url, resp_headers, payload)
        return json.loads(payload) if parse_json and payload else payload

    @staticmethod
    def _encode_path(folder: str, name: str = "") -> str:
        """Server-relative path for Graph's `/root:/{path}:` addressing.

        Each segment is percent-encoded individually so a folder or file name
        containing a space or '&' addresses correctly while '/' stays a
        separator.
        """
        parts = [p for p in f"{folder}/{name}".split("/") if p]
        return "/".join(urllib.parse.quote(p, safe="") for p in parts)

    # -- resolution -------------------------------------------------------- #

    def site_id(self) -> str:
        if self._site_id is None:
            url = f"{GRAPH_ROOT}/sites/{self.cfg.host}:{self.cfg.site_path}"
            self._site_id = self._call("GET", url)["id"]
        return self._site_id

    def drive_id(self) -> str:
        """Resolve the document library by display name.

        Fails loudly listing what the site *does* have when the configured
        library is absent — the common cause is a renamed library or a typo,
        and guessing the default drive would silently read the wrong one.
        """
        if self._drive_id is None:
            drives = self._call("GET", f"{GRAPH_ROOT}/sites/{self.site_id()}/drives").get("value", [])
            for d in drives:
                if d.get("name") == self.cfg.library:
                    self._drive_id = d["id"]
                    break
            else:
                available = ", ".join(sorted(d.get("name", "?") for d in drives)) or "<none>"
                raise SharePointConfigError(
                    f"Document library {self.cfg.library!r} not found on "
                    f"{self.cfg.host}{self.cfg.site_path}. Available: {available}. "
                    "Set the sharepoint_library widget/env var to one of these."
                )
        return self._drive_id

    # -- read -------------------------------------------------------------- #

    def list_documents(self, suffixes: set[str] | None = None,
                       folder: str | None = None) -> list[SharePointItem]:
        """List files in one library folder (library root when blank).

        `folder` defaults to the configured FRD folder. (This override was
        removed once as YAGNI; re-added 2026-08-21 with a real caller — the
        review app's existing-STTM check lists `cfg.output_folder`.)

        Folders are skipped. When `suffixes` is given, only matching files are
        returned — the caller passes 01's SUPPORTED_SUFFIXES so the picker can
        never offer a document the pipeline cannot parse.
        """
        folder = self.cfg.frd_folder if folder is None else folder
        base = f"{GRAPH_ROOT}/drives/{self.drive_id()}"
        url = (f"{base}/root:/{self._encode_path(folder)}:/children" if folder
               else f"{base}/root/children")

        items, seen_urls = [], set()
        while url and url not in seen_urls:      # guard against a cyclic @odata.nextLink
            seen_urls.add(url)
            page = self._call("GET", url)
            for entry in page.get("value", []):
                if "folder" in entry:
                    continue
                name = entry.get("name", "")
                if suffixes is not None and Path(name).suffix.lower() not in suffixes:
                    continue
                items.append(SharePointItem(
                    item_id=entry["id"],
                    name=name,
                    size=int(entry.get("size", 0)),
                    modified=entry.get("lastModifiedDateTime", ""),
                    web_url=entry.get("webUrl", ""),
                ))
            url = page.get("@odata.nextLink")
        return sorted(items, key=lambda i: i.name.lower())

    def download_item(self, item_id: str) -> bytes:
        """Fetch one file's bytes by Graph item id."""
        url = f"{GRAPH_ROOT}/drives/{self.drive_id()}/items/{item_id}/content"
        return self._call("GET", url, parse_json=False)

    def fetch_to_dir(self, dest_dir: str | Path,
                     suffixes: set[str] | None = None) -> list[SharePointItem]:
        """Download every matching document into `dest_dir` (i.e. frd_raw).

        Writes via a `.part` temp file then renames, so a failed transfer can
        never leave a truncated .docx for 01 to parse as a valid FRD.
        """
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        fetched = []
        for item in self.list_documents(suffixes=suffixes):
            payload = self.download_item(item.item_id)
            if item.size and len(payload) != item.size:
                raise GraphError(200, item.name, "size_mismatch",
                                 f"downloaded {len(payload)} bytes, library reports {item.size}", None)
            tmp = dest / f"{item.name}.part"
            tmp.write_bytes(payload)
            tmp.replace(dest / item.name)
            fetched.append(item)
        return fetched

    # -- write ------------------------------------------------------------- #

    def upload_file(self, local_path: str | Path) -> dict:
        """Publish one file to the configured output folder, replacing any
        same-named item.

        Simple PUT upload only. Graph documents that path for files up to
        4 MiB; larger files need a resumable upload session. A rendered STTM
        workbook is ~100 KB, so the simple path is right — but we raise rather
        than let Graph reject a large file with a confusing error, and that
        raise is the marker for where the upload-session path goes if an
        oversized artifact ever appears.
        """
        src = Path(local_path)
        payload = src.read_bytes()
        if len(payload) > SIMPLE_UPLOAD_MAX_BYTES:
            raise GraphError(
                413, str(src), "file_too_large",
                f"{src.name} is {len(payload)} bytes; the simple upload path is capped at "
                f"{SIMPLE_UPLOAD_MAX_BYTES}. Implement a resumable upload session here.", None)

        path = self._encode_path(self.cfg.output_folder, src.name)
        url = f"{GRAPH_ROOT}/drives/{self.drive_id()}/root:/{path}:/content"
        return self._call("PUT", url, body=payload,
                          content_type="application/octet-stream")


def build_client(cfg: SharePointConfig, transport=_urlopen_transport) -> SharePointClient:
    """Config -> authenticated client. The one construction path."""
    return SharePointClient(cfg, acquire_token(cfg, transport), transport)
