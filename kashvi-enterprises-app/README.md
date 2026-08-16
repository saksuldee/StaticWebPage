# Kashvi Enterprises — Website + Enquiry API

A single-page site for Kashvi Enterprises with a FastAPI backend that
saves every enquiry form submission to `enquiries.csv`.

## What happens when someone submits the form

1. The browser sends the form fields to `POST /api/enquiry`.
2. FastAPI validates them (name, a phone number of at least 7 digits,
   service, optional message) and appends a row to `enquiries.csv`
   with a timestamp.
3. The page then opens WhatsApp with the same details pre-filled, so
   the enquiry is both logged and sent straight to your phone.

If the server can't be reached, the form still opens WhatsApp so the
enquiry isn't lost — it just won't have a CSV row for that one.

## Project layout

```
kashvi-app/
├── main.py             FastAPI app (API + serves the site + admin)
├── google_drive.py      optional Google Drive backup module
├── requirements.txt
├── enquiries.csv         created automatically on first run
├── service-account.json  your Drive credentials (you provide this — see below)
├── admin_page/
│   └── admin.html       follow-up report dashboard (password protected)
└── static/
    ├── index.html
    └── images/
```

## Run it

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

uvicorn main:app --reload
```

Open **http://127.0.0.1:8000** — that's the site, served by FastAPI
itself. The enquiry form on that page posts straight to this same
server.

## Admin — follow-up report

Open **http://127.0.0.1:8000/admin**. Your browser will prompt for a
username and password:

```
Username: admin
Password: kashvi-admin
```

**Change these before deploying anywhere public** — set the
`ADMIN_USER` and `ADMIN_PASSWORD` environment variables:

```bash
export ADMIN_USER=kashvi
export ADMIN_PASSWORD="a-much-stronger-password"
uvicorn main:app --reload
```

The dashboard shows:
- Totals: all enquiries, a breakdown of **New / Contacted / Resolved**,
  and how many are **Due / Overdue** for follow-up
- A count for the last 7 days
- A searchable, filterable table of every enquiry (search by name or
  phone, filter by service or status)
- A **status dropdown per row** — mark an enquiry Contacted or
  Resolved as you follow up, and it's saved immediately
- A **Fitted checkbox per row** — tick it once the job's actually
  done on-site (separate from "Resolved", since an enquiry can be
  closed out without the fitting having happened, or vice versa)
- A **Next Follow-up date per row** — set when you next need to
  check back in; rows with a follow-up date on or before today (and
  not yet Resolved) are highlighted and marked **OVERDUE**
- One-tap **Call** and **WhatsApp** buttons per row
- A **Delete button per row** (with a confirmation prompt) to permanently
  remove an enquiry from `enquiries.csv`
- An **Export CSV** button to download the raw file
- A **Google Drive status pill** — shows whether backup is connected,
  and a **Sync now** button to trigger one on demand (see below)

## Endpoints

| Method | Path                     | Auth | What it does                                    |
|--------|--------------------------|------|--------------------------------------------------|
| GET    | `/`                      | —    | The website                                      |
| POST   | `/api/enquiry`           | —    | Saves one enquiry to `enquiries.csv`             |
| GET    | `/admin`                 | ✅   | Follow-up report dashboard                       |
| GET    | `/api/enquiries`         | ✅   | Returns all saved enquiries as JSON              |
| PATCH  | `/api/enquiries/{id}`    | ✅   | Updates status, fitted, and/or next follow-up    |
| DELETE | `/api/enquiries/{id}`    | ✅   | Permanently deletes one enquiry                  |
| GET    | `/api/enquiries/export`  | ✅   | Downloads `enquiries.csv`                        |
| GET    | `/api/drive/status`      | ✅   | Whether Google Drive backup is connected         |
| POST   | `/api/drive/sync`        | ✅   | Triggers an immediate Drive backup               |
| GET    | `/api/health`            | —    | Basic health check                               |

`PATCH /api/enquiries/{id}` accepts any subset of these fields — only
what you send gets changed:

```json
{"status": "Contacted", "fitted": true, "next_followup": "2026-08-25"}
```

### Example: submit an enquiry directly

```bash
curl -X POST http://127.0.0.1:8000/api/enquiry \
  -H "Content-Type: application/json" \
  -d '{"name":"Anita Rao","phone":"9998887770","service":"RO Purifiers","message":"Flow has gone very slow."}'
```

### Example: mark it fitted and set a follow-up date

```bash
curl -X PATCH http://127.0.0.1:8000/api/enquiries/1 \
  -u admin:kashvi-admin \
  -H "Content-Type: application/json" \
  -d '{"fitted": true, "next_followup": "2026-08-25"}'
```

### `enquiries.csv` columns

```
id, timestamp, name, phone, service, message, status, fitted, next_followup
```

- `status` — one of `New`, `Contacted`, `Resolved`. Starts at `New`.
- `fitted` — `Yes` or `No`. Whether the job's been physically done.
- `next_followup` — a `YYYY-MM-DD` date, or blank if none is set.

All three are editable from the admin dashboard. If you're upgrading
from an older version of this app, the extra columns are added
automatically the first time the server starts — existing rows get
`fitted = No` and an empty `next_followup`, nothing is lost.

Open it in Excel, Google Sheets, or any spreadsheet app — it's
appended to on every submission, so it doubles as a running log of
leads.

## Google Drive backup (optional)

`enquiries.csv` can auto-upload to a Google Drive folder every time an
enquiry is created, edited, or deleted — a live backup that survives
even if the server's disk doesn't. This is entirely optional; without
it, the app works exactly as described above using just the local
file.

**Setup (one-time):**

1. In [Google Cloud Console](https://console.cloud.google.com/), open
   or create a project, then enable the **Google Drive API**
   (APIs & Services → Enable APIs → search "Google Drive API").
2. Go to **APIs & Services → Credentials → Create Credentials →
   Service Account**. Give it any name, no special roles needed.
3. Open the service account you just made → **Keys → Add Key →
   Create new key → JSON**. This downloads a `.json` file.
4. Rename that file `service-account.json` and put it in this
   project's root folder, next to `main.py`.
   (Or keep it wherever you like and point the
   `GDRIVE_SERVICE_ACCOUNT_FILE` environment variable at it.)
5. In Google Drive, create a folder for the backups (or pick an
   existing one). **Share that folder** with the service account's
   email — it's the `client_email` field inside the JSON file, looks
   like `something@your-project.iam.gserviceaccount.com` — and give
   it **Editor** access. This step is easy to miss and is the most
   common reason sync fails.
6. Copy the folder's ID from its Google Drive URL:
   `https://drive.google.com/drive/folders/`**`THIS_PART`**
7. Set it as an environment variable and start the server:

```bash
export GDRIVE_FOLDER_ID="paste-the-folder-id-here"
uvicorn main:app --reload
```

That's it. The admin dashboard header shows a **Drive** status pill —
green dot means it's connected, and it displays when it last synced.
Click **Sync now** any time to force an immediate backup instead of
waiting for the next enquiry change.

**Environment variables:**

| Variable                     | Default                  | What it's for                          |
|-------------------------------|---------------------------|-----------------------------------------|
| `GDRIVE_SERVICE_ACCOUNT_FILE` | `./service-account.json`  | Path to the service account key         |
| `GDRIVE_FOLDER_ID`            | *(none)*                  | The target Drive folder's ID            |

If the service account file is missing or `GDRIVE_FOLDER_ID` isn't
set, the status pill just reads "Drive backup: not set up" and
everything else keeps working locally — no errors, nothing blocks.

`service-account.json` contains a private key — never commit it to
git or share it. It's already excluded via `.gitignore`.

## Deploying

This one process serves both the API and the website, so there's
nothing extra to wire up — deploy it anywhere that runs a Python app
(a VPS, Render, Railway, Fly.io, etc.) with:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

For production, put it behind a real webserver (nginx/Caddy) with
HTTPS, and consider swapping the CSV for a proper database once
enquiry volume grows.
