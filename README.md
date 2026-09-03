# Facial Reconstruction Atlas

A searchable, filterable reference application for facial plastic surgery reconstructive
cases. Clinicians browse a case series by anatomic defect location, repair method, and
flap or graft characteristics, with each case linking to a staged photographic series
documenting the reconstruction from pre-operative through healed.

Built as a research tool for the UVA Health Department of Otolaryngology.

> **This is a public, code-only copy.**
> No real case data, patient imagery, or institutional configuration is included here.
> The repository ships with a small set of **fabricated** sample cases so the
> application runs end to end out of the box. See [Sample data](#sample-data).

---

## What it does

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

### Files

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

## The anatomy diagram

Region polygons are stored as SVG paths inside `index.html`, between marker comments
(`<!-- FACE_REGIONS_START -->` and friends). `region-editor.html` is a visual tool for
drawing and repositioning them; it reads the current shapes, lets you edit them on the
reference image, and writes the updated markup back.

`static/face-reference.jpg` is a **generated line-art illustration**, not a photograph.
`generate_placeholders.py` draws it fresh on every run — like the real case log, no
actual reference photo is ever committed here (see `.gitignore`). Its layout is sized to
match the coordinate space the polygons were authored against. Swap in your own
reference image and re-run the region editor to realign.

## Security

Implemented:

- Media path validation — traversal-hostile segments rejected, resolved paths verified
  to sit under the image root, extension allowlist enforced.
- PBKDF2-HMAC-SHA256 password storage (200,000 iterations, per-user salt,
  constant-time verification).
- Parameterized SQL throughout; user-generated text HTML-escaped before rendering.
- Session tokens in `HttpOnly`, `SameSite=Lax` cookies.
- Ownership checks on destructive actions.

Not implemented — this is a prototype, and these are known gaps rather than oversights:

- Authentication currently gates interactive features (favorites, comments) but not
  case search, case detail, or image retrieval.
- No SSO, no role-based access control, no access logging.
- No server-side session expiry, no rate limiting on authentication.
- Permissive CORS and open account registration.

**Do not deploy this as-is against real patient data.** Any clinical deployment needs
institutional review, an appropriate hosting environment, and the gaps above closed.

## License

MIT — see `LICENSE`.
