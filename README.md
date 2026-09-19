# MedTech Mitra — Innovator Tracking & Dashboard

A Django-based system that replaces a manually-maintained Excel tracker with
a proper database, an admin console for staff data entry, a REST API, and a
filterable analytics dashboard — built for the MedTech Mitra innovator
facilitation program (Government of India / ICMR-style MedTech support
scheme).

Verified working end-to-end against the sample workbook: import command ran
cleanly, all pages (dashboard, filters, application detail, admin, API)
returned HTTP 200 in an automated smoke test.

---

## 1. Why not just keep the spreadsheet?

The source file (`Format` sheet) is 73 columns wide because it mixes five
different concerns in one row: applicant identity, the technology, the TAC
review workflow, knowledge-partner handholding, and TRL milestone tracking.
That shape makes it slow to update (everyone edits the same giant sheet),
impossible to audit (no record of who changed what, when), and hard to
report on (pivot tables over merged cells and inconsistent date formats).

This project normalizes that sheet into ~12 relational tables, adds
role-based access so different staff only touch their part of the workflow,
and gives you a live dashboard instead of a static export.

## 2. Data model

```
Applicant ──< Application >── TACMeeting
                 │
                 ├──< KnowledgePartnerAssignment >── FollowUp
                 │              │
                 │              └── KnowledgePartner ──< PartnerMilestoneTemplate
                 │
                 ├──< Milestone
                 ├──< TRLProgressLog >── TRLDefinition
                 └──< StatusChangeLog
```

| Table | Purpose |
|---|---|
| `Applicant` | Innovator / organisation identity — reusable across multiple applications |
| `Application` | One technology submission — the aggregate root the dashboard reports on |
| `TACMeeting` | Technical Advisory Committee review events for an application |
| `KnowledgePartner` | Master list: CDSCO, AMTZ, AIM-NITI Aayog, INTENT, HTA, BIS, etc. |
| `KnowledgePartnerAssignment` | Which partner is handholding which application, and its status |
| `FollowUp` | Follow-up query/meeting cycles under an assignment |
| `TRLDefinition` | Master TRL-1..9 levels with milestone descriptions |
| `TRLProgressLog` | Audit trail of an application's TRL changes over time |
| `Milestone` | Concrete milestone achieved for an application |
| `PartnerMilestoneTemplate` | Per-partner milestone checklist (from the "Partners Milestones" sheet) |
| `StatusChangeLog` | System-generated audit log of application status transitions |

Full field-level definitions are in `core/models.py`, which is heavily
commented with the reasoning behind each design choice.

## 3. Project / app structure

```
medtech_mitra/
├── config/                 # Project settings, root URLs, WSGI/ASGI
│   └── settings.py         # Env-driven: SQLite for dev, Postgres for prod
├── accounts/                # Custom User model with roles (Admin/TAC/KP/Viewer)
├── core/                    # Domain models, admin, REST API, Excel importer
│   ├── models.py
│   ├── admin.py
│   ├── serializers.py / filters.py / api_views.py / api_urls.py
│   └── management/commands/import_excel.py
├── dashboard/                # Read-only analytics layer (no models of its own)
│   ├── services.py           # All aggregation queries — unit-testable, reused by views + API
│   ├── views.py
│   └── urls.py
├── templates/                # base.html + per-app templates (server-rendered, Chart.js)
├── static/css/dashboard.css  # Design system (navy/tricolor, Fraunces + Inter)
├── requirements.txt
├── .env.example
└── manage.py
```

**Why this split:** `core` owns the data and business rules and is reusable
by anything (admin, API, management commands, future mobile app). `dashboard`
is a thin, read-only reporting layer — if requirements change, you can gut
and rebuild the dashboard without touching the system of record.

## 4. Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env — at minimum set DJANGO_SECRET_KEY; leave DJANGO_DB_ENGINE unset
# for local SQLite, or fill in the POSTGRES_* vars for Postgres.

export $(grep -v '^#' .env | xargs)   # or use python-dotenv / django-environ in production

python manage.py migrate
python manage.py createsuperuser
python manage.py import_excel /path/to/Sample_Data_Excel_for_Dashboard.xlsx
python manage.py runserver
```

Visit `http://127.0.0.1:8000/` and sign in with your superuser account.
The Django admin is at `/admin/`, the REST API at `/api/applications/`,
`/api/knowledge-partners/`, `/api/trl-definitions/`.

### Re-importing / updating data

`import_excel` uses `update_or_create` keyed on `reference_no` (applications),
`name` (knowledge partners), and `level` (TRL definitions) — so re-running it
with an updated workbook is safe and idempotent. Use `--dry-run` first to see
what would change without writing to the database:

```bash
python manage.py import_excel /path/to/latest.xlsx --dry-run
```

## 5. Roles

| Role | Access |
|---|---|
| Secretariat Admin | Full admin access, user management |
| TAC Member | View/update TAC review fields (enforce via admin permissions or a future custom view) |
| Knowledge Partner | Should only see applications assigned to their own partner (wire up in `dashboard/views.py` by filtering on `request.user.knowledge_partner`) |
| Read-only Viewer | Dashboard access only, no admin |

The `User.role` and `User.knowledge_partner` fields exist in `accounts/models.py`;
the current dashboard views don't yet filter by role — that's the natural
next step once you decide exactly which fields each role may edit.

## 6. Production deployment notes

- Set `DJANGO_DEBUG=False` and a real `DJANGO_SECRET_KEY` — this automatically
  turns on HSTS, secure cookies, and SSL redirect (see bottom of `config/settings.py`).
- Switch to PostgreSQL (`DJANGO_DB_ENGINE=postgres`) — recommended for any
  real government deployment (NIC data centre / MeghRaj cloud).
- Run behind Gunicorn + Nginx (or your ministry's existing WSGI setup);
  `gunicorn` is already in `requirements.txt`.
- Run `python manage.py collectstatic` and serve `/static/` via Nginx or
  WhiteNoise (included in requirements).
- `StatusChangeLog` gives you an audit trail out of the box; if a CERT-In or
  audit requirement asks for full request-level logging, point the `file`
  handler in `LOGGING` (settings.py) at your organisation's log pipeline.

## 7. Extending the dashboard

All chart data comes from `dashboard/services.py` — plain functions that take
an optional queryset and return `[{"label": ..., "value": ...}]`. To add a
new chart: write a new function there, wire it into `overview()` in
`dashboard/views.py`, add a `<canvas>` + a few lines of Chart.js in
`templates/dashboard/overview.html`. The same functions back the
`/api/chart/<name>/` JSON endpoint used for filter-driven re-fetching.
