"""
Optional Google Drive backup for enquiries.csv.

This app works fully offline with just the local CSV file. Drive sync
is an opt-in extra — if it isn't configured, every function here just
reports "not configured" and the rest of the app carries on normally.

--- How to enable it ---

1. In Google Cloud Console, create (or reuse) a project, enable the
   "Google Drive API", then create a **Service Account** and download
   its JSON key.

2. Save that key file as `service-account.json` in this project's
   root folder (next to main.py) — or save it anywhere and point
   GDRIVE_SERVICE_ACCOUNT_FILE at that path.

3. In Google Drive, create a folder for the backups (or use an
   existing one). Share that folder with the service account's email
   address — it's the "client_email" field inside the JSON key,
   something like `xxx@xxx.iam.gserviceaccount.com` — and give it
   Editor access. Without this share step, uploads will fail with a
   permission error.

4. Copy the folder's ID from its URL:
   https://drive.google.com/drive/folders/<THIS_PART_IS_THE_ID>
   and set it as the GDRIVE_FOLDER_ID environment variable.

5. Install the optional dependencies (already in requirements.txt):
       pip install -r requirements.txt

That's it — enquiries.csv is uploaded/updated in that Drive folder
every time an enquiry is created, edited, or deleted, and you can
also trigger a sync manually from the admin dashboard.
"""

import datetime as _dt
import json
import os
import threading
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
SERVICE_ACCOUNT_FILE = Path(
    os.environ.get("GDRIVE_SERVICE_ACCOUNT_FILE", str(BASE_DIR / "service-account.json"))
)
FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
STATE_FILE = BASE_DIR / ".gdrive_state.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_lock = threading.Lock()
_service = None
_service_error: Optional[str] = None
_attempted = False


def is_configured() -> bool:
    """True if the service account file and folder ID are both set."""
    return SERVICE_ACCOUNT_FILE.exists() and bool(FOLDER_ID)


def _get_service():
    """Build (once) and cache the Drive API client. Returns None if
    the optional google-api-python-client packages aren't installed,
    or the service account / folder aren't configured correctly."""
    global _service, _service_error, _attempted
    if _attempted:
        return _service
    _attempted = True

    if not is_configured():
        _service_error = "Service account file or GDRIVE_FOLDER_ID not set."
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        _service_error = (
            "google-api-python-client isn't installed — "
            "run: pip install -r requirements.txt"
        )
        return None

    try:
        creds = service_account.Credentials.from_service_account_file(
            str(SERVICE_ACCOUNT_FILE), scopes=SCOPES
        )
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
        _service_error = None
    except Exception as exc:  # noqa: BLE001 — surface any auth/build error as status text
        _service_error = str(exc)
        _service = None

    return _service


def _read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def status() -> dict:
    """A small dict describing whether Drive sync is set up, connected,
    and when it last succeeded — used by the admin dashboard."""
    configured = is_configured()
    connected = _get_service() is not None if configured else False
    state = _read_state()
    return {
        "configured": configured,
        "connected": connected,
        "error": None if connected else _service_error,
        "last_synced": state.get("last_synced"),
        "drive_file_id": state.get("file_id"),
        "folder_id": FOLDER_ID or None,
    }


def sync_csv(csv_path: Path, filename: str = "kashvi-enquiries.csv") -> dict:
    """Upload enquiries.csv to Drive, updating the same file on repeat
    calls instead of creating duplicates. Never raises — always
    returns a result dict so callers can log/report it safely."""
    with _lock:
        if not is_configured():
            return {"ok": False, "reason": "Google Drive isn't configured."}

        service = _get_service()
        if service is None:
            return {"ok": False, "reason": _service_error or "Could not connect to Google Drive."}

        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            return {"ok": False, "reason": "google-api-python-client isn't installed."}

        state = _read_state()
        file_id = state.get("file_id")

        try:
            media = MediaFileUpload(str(csv_path), mimetype="text/csv", resumable=False)

            if file_id:
                # Confirm the previously-uploaded file is still there
                # (it may have been moved/deleted by someone in Drive).
                try:
                    service.files().get(fileId=file_id, fields="id").execute()
                except Exception:
                    file_id = None

            if file_id:
                service.files().update(fileId=file_id, media_body=media).execute()
            else:
                meta = {"name": filename, "parents": [FOLDER_ID]}
                created = service.files().create(
                    body=meta, media_body=media, fields="id"
                ).execute()
                file_id = created["id"]

            state["file_id"] = file_id
            state["last_synced"] = _dt.datetime.now().isoformat(timespec="seconds")
            _write_state(state)
            return {"ok": True, "file_id": file_id, "last_synced": state["last_synced"]}

        except Exception as exc:  # noqa: BLE001 — report upload errors, don't crash the request
            return {"ok": False, "reason": str(exc)}


def sync_csv_in_background(csv_path: Path, filename: str = "kashvi-enquiries.csv") -> None:
    """Fire-and-forget sync so create/update/delete requests don't
    wait on a network round-trip to Drive."""
    if not is_configured():
        return
    threading.Thread(
        target=sync_csv, args=(csv_path, filename), daemon=True
    ).start()
