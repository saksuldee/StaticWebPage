"""
Kashvi Enterprises — website backend.

Serves the static site, saves enquiry form submissions to a CSV file,
and provides a small password-protected admin page to review and
follow up on them.

Run:
    pip install -r requirements.txt
    uvicorn main:app --reload

Then open:
    http://127.0.0.1:8000        the website
    http://127.0.0.1:8000/admin  the follow-up report (login required)

Default admin login is admin / kashvi-admin — change it via the
ADMIN_USER / ADMIN_PASSWORD environment variables before deploying.
"""

import csv
import os
import re
import secrets
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import google_drive as gdrive

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ADMIN_DIR = BASE_DIR / "admin_page"
CSV_PATH = BASE_DIR / "enquiries.csv"

CSV_HEADERS = [
    "id", "timestamp", "name", "phone", "service", "message",
    "status", "fitted", "next_followup",
]
STATUSES = ("New", "Contacted", "Resolved")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "kashvi-admin")

# csv writes happen from request handlers which may run concurrently;
# a simple lock keeps rows/rewrites from interleaving.
_csv_lock = threading.Lock()

app = FastAPI(title="Kashvi Enterprises Enquiry API", version="1.1.0")

# Allow the form to be called from any origin (useful if the frontend
# is ever hosted separately from this API). Tighten this in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Simple HTTP Basic Auth gate for the admin/report endpoints."""
    correct_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_pass = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


class Enquiry(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    phone: str = Field(..., min_length=7, max_length=20)
    service: str = Field(..., min_length=1, max_length=80)
    message: str = Field("", max_length=1000)

    @field_validator("name", "service", "message")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if len(digits) < 7:
            raise ValueError("Phone number looks too short.")
        return v.strip()


class EnquiryUpdate(BaseModel):
    """Partial update — only the fields the admin actually changed are sent."""
    status: Optional[Literal["New", "Contacted", "Resolved"]] = None
    fitted: Optional[bool] = None
    next_followup: Optional[str] = Field(None, max_length=10)

    @field_validator("next_followup")
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        try:
            date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError("next_followup must be a YYYY-MM-DD date.") from exc
        return v


def ensure_csv() -> None:
    """Create the CSV file with headers if missing, or upgrade it in
    place if it was written by an older version of this app that had
    fewer columns."""
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_HEADERS)
        return

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f), [])

    if header == CSV_HEADERS:
        return

    # Older file with a different column set — migrate it, filling any
    # new columns with sensible defaults so nothing is lost.
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        old_rows = list(csv.DictReader(f))

    migrated = [
        {
            "id": r.get("id", ""),
            "timestamp": r.get("timestamp", ""),
            "name": r.get("name", ""),
            "phone": r.get("phone", ""),
            "service": r.get("service", ""),
            "message": r.get("message", ""),
            "status": r.get("status", "New"),
            "fitted": r.get("fitted", "No"),
            "next_followup": r.get("next_followup", ""),
        }
        for r in old_rows
    ]
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(migrated)


def read_rows() -> list[dict]:
    ensure_csv()
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(rows: list[dict]) -> None:
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


@app.on_event("startup")
def on_startup() -> None:
    ensure_csv()


# ---------------------------------------------------------------------
# Public: enquiry form submits here
# ---------------------------------------------------------------------
@app.post("/api/enquiry")
def create_enquiry(enquiry: Enquiry):
    """Append a new enquiry to enquiries.csv with status 'New'."""
    try:
        with _csv_lock:
            rows = read_rows()
            existing_ids = [int(r["id"]) for r in rows if r.get("id", "").isdigit()]
            next_id = max(existing_ids, default=0) + 1
            row = {
                "id": str(next_id),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "name": enquiry.name,
                "phone": enquiry.phone,
                "service": enquiry.service,
                "message": enquiry.message,
                "status": "New",
                "fitted": "No",
                "next_followup": "",
            }
            rows.append(row)
            write_rows(rows)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Could not save enquiry.") from exc

    gdrive.sync_csv_in_background(CSV_PATH)
    return {"status": "ok", "message": "Enquiry saved.", "id": next_id}


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------
# Admin / follow-up report — all protected by HTTP Basic Auth
# ---------------------------------------------------------------------
@app.get("/api/enquiries")
def list_enquiries(username: str = Depends(verify_admin)):
    """Return all saved enquiries, most recent first."""
    rows = read_rows()
    rows.reverse()
    return rows


@app.patch("/api/enquiries/{enquiry_id}")
def update_enquiry(
    enquiry_id: int,
    update: EnquiryUpdate,
    username: str = Depends(verify_admin),
):
    """Update follow-up status, fitted flag, and/or next follow-up date
    for one enquiry. Only fields included in the request body change."""
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update.")

    with _csv_lock:
        rows = read_rows()
        target = next((r for r in rows if r.get("id") == str(enquiry_id)), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Enquiry not found.")

        if "status" in fields:
            target["status"] = fields["status"]
        if "fitted" in fields:
            target["fitted"] = "Yes" if fields["fitted"] else "No"
        if "next_followup" in fields:
            target["next_followup"] = fields["next_followup"] or ""

        write_rows(rows)

    gdrive.sync_csv_in_background(CSV_PATH)
    return {"status": "ok", "id": enquiry_id, "updated": list(fields.keys())}


@app.delete("/api/enquiries/{enquiry_id}")
def delete_enquiry(enquiry_id: int, username: str = Depends(verify_admin)):
    """Permanently remove one enquiry from enquiries.csv."""
    with _csv_lock:
        rows = read_rows()
        target = next((r for r in rows if r.get("id") == str(enquiry_id)), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Enquiry not found.")
        rows = [r for r in rows if r.get("id") != str(enquiry_id)]
        write_rows(rows)

    gdrive.sync_csv_in_background(CSV_PATH)
    return {"status": "ok", "deleted_id": enquiry_id}


@app.get("/api/enquiries/export")
def export_enquiries(username: str = Depends(verify_admin)):
    """Download the raw CSV file."""
    ensure_csv()
    return FileResponse(
        CSV_PATH,
        media_type="text/csv",
        filename="kashvi-enquiries.csv",
    )


@app.get("/api/drive/status")
def drive_status(username: str = Depends(verify_admin)):
    """Whether Google Drive backup is configured and its last sync time."""
    return gdrive.status()


@app.post("/api/drive/sync")
def drive_sync_now(username: str = Depends(verify_admin)):
    """Trigger an immediate, synchronous backup of enquiries.csv to Drive."""
    ensure_csv()
    result = gdrive.sync_csv(CSV_PATH)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("reason", "Sync failed."))
    return result


@app.get("/admin")
def admin_page(username: str = Depends(verify_admin)):
    """Password-protected follow-up report dashboard."""
    return FileResponse(ADMIN_DIR / "admin.html")


# Serve the public site itself (index.html, images, etc.) at "/".
# Mounted last so the routes above take priority.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
