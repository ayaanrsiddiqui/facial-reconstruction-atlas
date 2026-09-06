# Facial Reconstruction Atlas

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![FastAPI](https://img.shields.io/badge/backend-FastAPI-009485.svg)
![Deployed on Vercel](https://img.shields.io/badge/deployed-Vercel-000000.svg)

A searchable, filterable reference application for facial plastic surgery reconstructive
cases. Clinicians browse a case series by anatomic defect location, repair method, and
flap or graft characteristics, with each case linking to a staged photographic series
documenting the reconstruction from pre-operative through healed.

Built as a research tool for the UVA Health Department of Otolaryngology.

**[Live demo →](https://facial-reconstruction-atlas.vercel.app)** (seeded with the 12
fabricated cases described below — nothing real)

> **This is a public, code-only copy.**
> No real case data, patient imagery, or institutional configuration is included here.
> The repository ships with a small set of **fabricated** sample cases so the
> application runs end to end out of the box. See [Sample data](#sample-data).

## Screenshots

| Anatomy explorer | Case detail |
| --- | --- |
| ![Clickable face diagram with filters and search](docs/screenshots/search-view.png) | ![Case detail modal with staged photo carousel and comments](docs/screenshots/case-detail.png) |

## Contents

- [Features](#features)
- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Deployment](#deployment)
- [Sample data](#sample-data)
- [How the case log is parsed](#how-the-case-log-is-parsed)
- [Anatomy diagrams and the region editor](#anatomy-diagrams-and-the-region-editor)
- [API reference](#api-reference)
- [Security](#security)
- [License](#license)

## Features

- **Multi-criteria filtering** across defect region, sub-location, defect size,
  full-thickness status, repair method, flap type, graft type, and donor site.
- **Interactive anatomic region selector** — clickable face diagram with drill-down
  sub-diagrams for the nose, periorbital region, and lip.
- **Free-text search** spanning every case field, including surgical notes.
- **Staged photo series** per case (pre-op, defect, flap drawn, flap raised, flap
  closed, healed), served through a validated proxy rather than direct storage URLs.
- **Accounts, favorites, and comments** so reviewers can bookmark cases and leave
  clinical notes.
- **Spreadsheet validation tool** that pre-checks a case log before import.

## Tech stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Backend | [FastAPI](https://fastapi.tiangolo.com/) on Python 3.12+ | Single `app.py`, no ORM |
| Database | SQLite | Rebuilt from the spreadsheet at build time, opened read-only at runtime on Vercel |
| Frontend | Vanilla HTML/CSS/JS | One `index.html` + `script.js`, no framework, no build step |
| Spreadsheet parsing | [openpyxl](https://openpyxl.readthedocs.io/) | `import_patient_log.py` |
| Placeholder imagery | [Pillow](https://python-pillow.org/) | Draws case thumbnails and the face-reference illustration |
| Auth | PBKDF2-HMAC-SHA256 + cookie sessions | No third-party auth dependency |
| Hosting | [Vercel](https://vercel.com/) (Python serverless runtime) | Auto-deploys on push via the GitHub integration |

There's no test suite yet — `validate_spreadsheet.py` is the closest thing, acting as a
data-quality gate for anything going into the database.

## Architecture

```
Browser (vanilla JS SPA)
      │  HTTPS
      ▼
FastAPI application server ──▶ SQLite      (case metadata)
                           └─▶ image store (staged photography)
```

The application server is the only path to case media. Images are never exposed as
direct file URLs — every request passes through `/api/image/...`, which validates the
path against a fixed root and an extension allowlist before streaming bytes. That
single chokepoint is what makes per-user authorization and access logging attachable as
one layer rather than a re-architecture.

**Build-time vs. runtime** is the other load-bearing split. Vercel's Python runtime is
read-only once deployed, so the SQLite database can't be written to on every cold start.
Instead:

```
patient_log.xlsx ──▶ import_patient_log.py ──▶ build.py ──▶ metadata.db, mock_o_drive/
   (build time only, via generate_placeholders.py + init_database())
```

`build.py` runs once per deploy (see [Deployment](#deployment)), sets
`ENTDATABASE_ALLOW_DB_WRITES=1` for that process only, generates the placeholder
imagery, and seeds `metadata.db`. At request time, `database_writes_allowed()`
(`app.py`) checks the Vercel-provided `VERCEL` environment variable and opens SQLite in
immutable, read-only mode unless writes are explicitly re-enabled — so a stray write
attempt against the read-only deployment bundle fails loudly instead of silently
targeting a throwaway filesystem.

That still leaves one case: a deployment that *does* re-enable writes but points
`ENTDATABASE_DB_PATH` somewhere that turns out not to be writable after all (a
misconfigured volume, a permissions mismatch). `init_database()` treats that as
recoverable rather than fatal — on a fresh path it first tries to copy the
build-produced `metadata.db` into place (`_bootstrap_db_if_needed()`, avoiding a full
spreadsheet re-import on every cold start), and if the write attempt fails with a
read-only or permissions error either way, it logs a warning, flips the app into
read-only mode for the rest of that process, and continues serving reads instead of
taking the whole app down. Startup failing shouldn't mean the login page 500s.

## Project structure

| File | Role |
| --- | --- |
| `app.py` | FastAPI application: API, auth, media proxy, anatomy definitions |
| `import_patient_log.py` | Reads the Excel case log into structured seed records |
| `validate_spreadsheet.py` | Read-only pre-flight check on a candidate case log |
| `field_options.py` | Filter dropdown vocabularies |
| `generate_placeholders.py` | Generates stand-in case images and the face reference illustration |
| `build.py` | Deployment build step (images + database) |
| `region-editor.html` | Visual editor for authoring the anatomy diagram polygons |
| `index.html` / `script.js` / `styles.css` | Single-page frontend |

## Getting started

Requires Python 3.12+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Use the bundled fabricated sample data
cp sample_data/patient_log.xlsx .

python generate_placeholders.py
python -c "from app import init_database; init_database()"

uvicorn app:app --reload --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001>.

## Deployment

The [live demo](https://facial-reconstruction-atlas.vercel.app) runs on Vercel's Python
serverless runtime, configured entirely by two files:

- **`vercel.json`** registers `app.py` as the function Vercel builds and routes all
  traffic to.
- **`pyproject.toml`**'s `[tool.vercel.scripts]` block points Vercel's build step at
  `build.py`, which stages the fabricated sample spreadsheet (mirroring the `cp
  sample_data/patient_log.xlsx .` step above), runs `generate_placeholders.py`, and
  seeds `metadata.db` — all before the function ever serves a request.

To deploy your own copy: import this repository into Vercel (or run `vercel --prod`
from a clone) — no environment variables are required for the demo data to work. Once
connected, `git push` to the default branch triggers a rebuild and redeploy
automatically.

Relevant environment variables, all optional:

| Variable | Set by | Default | Purpose |
| --- | --- | --- | --- |
| `ENTDATABASE_DB_PATH` | You | `metadata.db` beside `app.py` | Absolute or relative path to the SQLite database file |
| `ENTDATABASE_IMAGE_ROOT` | You | `mock_o_drive/` beside `app.py` | Directory the case images are served from. Resolved to an absolute path once at import; `/api/image/...` refuses to serve anything outside it |
| `ENTDATABASE_ALLOWED_ORIGINS` | You | `http://127.0.0.1:8001,http://localhost:8001` | Comma-separated CORS origin allowlist. Credentials are allowed, so this must stay an explicit list — never `*` |
| `ENTDATABASE_DEV_TOOLS` | You | unset | Set to `1` to expose the region editor (`/region-editor`, `/api/dev/face-regions`). Unset, those routes return 404; set, they still require a logged-in user |
| `ENTDATABASE_ALLOW_DB_WRITES` | `build.py` | unset | Opts the build process back into writes despite `VERCEL` being set |
| `ENTDATABASE_DEMO_MODE` | You | unset | Public demo only — see below. Never set on an internal deployment |
| `ENTDATABASE_XLSX_PATH` | You | auto-detected | Points the importer at a specific spreadsheet instead of the auto-detected default |
| `VERCEL` | Vercel itself | — | Detected by `app.py` to switch SQLite to read-only mode at runtime |

The [live demo](https://facial-reconstruction-atlas.vercel.app) runs this specific set,
which is what turns the login-walled internal tool into something anyone can click
around without an account:

```
ENTDATABASE_DEMO_MODE=1
ENTDATABASE_ALLOW_DB_WRITES=1
ENTDATABASE_DB_PATH=/tmp/metadata.db
```

`ENTDATABASE_DEMO_MODE` drops the login requirement on read endpoints and lets
anonymous visitors favorite cases under a shared demo account, while still requiring a
real (if throwaway) account to post a comment — see `require_user`/`demo_mode_enabled`
in `app.py`. `/tmp` on a serverless instance is per-instance and ephemeral, so demo
favorites and comments reset whenever the instance recycles; that's intended for a
public demo, not a bug. An internal deployment should leave `ENTDATABASE_DEMO_MODE`
unset entirely — every data endpoint then requires a real login.

## Sample data

`sample_data/patient_log.xlsx` contains **12 entirely fabricated cases**. They are
invented for demonstration and bear no relationship to any real patient, case, or
institution. They exist so that a fresh clone runs and every filter has something to
match.

To use a different case log, drop the `.xlsx` in the project root as `patient_log.xlsx`,
or point at it explicitly:

```bash
export ENTDATABASE_XLSX_PATH="/path/to/your_case_log.xlsx"
```

Then validate it before wiring it in:

```bash
python validate_spreadsheet.py "/path/to/your_case_log.xlsx"
```

The check is read-only. It reports dropped rows, unrecognized regions, vocabulary not
yet in `field_options.py`, stray stage markers, and cases with no matching image folder.
A clean run ends in `PASS`; anything else ends in `REVIEWED` with a list to look at.
Only a missing required column is a hard failure.

## How the case log is parsed

`import_patient_log.py` is the whole data pipeline. Notable behavior:

- **Columns match by header text, not position.** Reordering columns is safe, and
  matching tolerates whitespace and capitalization differences.
- **Ten columns are required.** If any is missing, import fails immediately naming the
  missing columns rather than silently importing partial data.
- **Free-text defect locations are parsed** into structured region / sub-location pairs.
  A case listing multiple sites gets an entry for each. Unrecognized text is still
  imported and remains keyword-searchable, filed under region `Unknown`, with a warning.
- **Repair methods are normalized** to five canonical categories via keyword and synonym
  matching; unmatched text is preserved verbatim with a warning.
- **Stage columns are photo flags.** An `x` or `y` means that stage's image exists under
  a fixed filename; anything else means no image for that stage.
- **Incomplete rows are imported and visibly flagged** in the UI rather than presented
  as complete.
- **Duplicate case identifiers** are dropped with a warning; the first occurrence wins.

New vocabulary — an unfamiliar flap, graft, or sub-location — imports fine but will not
appear in the filter dropdowns until added to `field_options.py`.

## Anatomy diagrams and the region editor

Region polygons are stored as SVG paths inside `index.html`, between marker comments
(`<!-- FACE_REGIONS_START -->` and friends) — one block per diagram (the full face, plus
nose/periorbital/lip drill-downs). `app.py` exposes those blocks as structured shapes at
`GET /api/dev/face-regions` and can rewrite them from `POST /api/dev/face-regions`; both
are what `region-editor.html` (served at `/region-editor`) uses under the hood, so
editing polygons visually is really just editing `index.html` through an API instead of
by hand. Because it is a local authoring tool rather than a production feature, all three routes
are hidden unless `ENTDATABASE_DEV_TOOLS=1`, returning 404 otherwise; with the flag set
they still require a logged-in user. Saving is additionally disabled wherever
`ENTDATABASE_ALLOW_DB_WRITES`/`VERCEL` says writes aren't allowed.

`static/face-reference.jpg` is a **generated line-art illustration**, not a photograph.
`generate_placeholders.py` draws it fresh on every run — like the real case log, no
actual reference photo is ever committed here (see `.gitignore`). Its layout is sized to
match the coordinate space the polygons were authored against. Swap in your own
reference image and re-run the region editor to realign.

## API reference

Every route lives in `app.py`; interactive, always-current documentation (request/response
schemas included) is auto-generated by FastAPI at
[`/docs`](https://facial-reconstruction-atlas.vercel.app/docs) on any running instance.
The short version:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/anatomy` | Diagram and region definitions for the face/nose/periorbital/lip selectors (auth required) |
| `GET /api/filters` | Dropdown vocabulary, plus which values actually appear in the current database (auth required) |
| `GET /api/search` | Filtered/full-text case search (auth required) |
| `GET /api/cases/{folder_name}` | Full case detail, including images (auth required) |
| `GET /api/image/{folder_name}/{filename}` | Path-validated case photo proxy (auth required) |
| `POST /api/auth/register` / `login` / `logout`, `GET /api/auth/me` | Cookie-session accounts |
| `POST /api/cases/{folder_name}/favorite`, `GET /api/favorites` | Per-user favorites (auth required) |
| `GET/POST /api/cases/{folder_name}/comments`, `DELETE /api/comments/{id}` | Case comments (auth required to write, own-comment-only delete) |
| `GET/POST /api/dev/face-regions` | Read/rewrite the SVG region markup — backs the region editor. 404 unless `ENTDATABASE_DEV_TOOLS=1`, and auth required even then |

## Security

Implemented:

- Media path validation — traversal-hostile segments rejected, resolved paths verified
  to sit under the image root, extension allowlist enforced.
- PBKDF2-HMAC-SHA256 password storage (200,000 iterations, per-user salt,
  constant-time verification).
- Parameterized SQL throughout; user-generated text HTML-escaped before rendering.
- Session tokens in `HttpOnly`, `SameSite=Lax` cookies.
- Ownership checks on destructive actions.
- Authenticated by default — every read endpoint (search, case detail, images, filters,
  anatomy, comments) requires a session; there is no exception list.
- CORS restricted to an explicit origin allowlist (`ENTDATABASE_ALLOWED_ORIGINS`) with
  credentials enabled; methods and headers narrowed.
- Development-only routes (the region editor) hidden behind `ENTDATABASE_DEV_TOOLS` and
  additionally login-gated.

Not implemented — this is a prototype, and these are known gaps rather than oversights:

- No SSO, no role-based access control, no access logging.
- No server-side session expiry, no rate limiting on authentication.
- Open account registration — any visitor can create an account and thereby reach every
  case. Authentication is enforced, but authorization is all-or-nothing.

**Do not deploy this as-is against real patient data.** Any clinical deployment needs
institutional review, an appropriate hosting environment, and the gaps above closed.

## License

MIT — see `LICENSE`.
