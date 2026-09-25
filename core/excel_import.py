"""
Import Applicant + Application data from the MedTech Mitra master Excel file.

Matched to the REAL master_data.xlsx (Sheet1, 40 columns) AND to core/models.py.
Only Applicant + Application fields are imported in this pass. TAC meetings,
Knowledge Partner assignments and Follow-ups are skipped for now.

Column headers are normalised (lowercased, punctuation -> underscore), so
"Date of closure " becomes "date_of_closure" etc.
"""
import datetime
import re

import pandas as pd
from django.db import transaction

from core.models import Applicant, Application, StatusChangeLog


# ---------------------------------------------------------------------------
# STEP 1: Small helpers for cleaning values
# ---------------------------------------------------------------------------

def _normalise(col):
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    return col.strip("_")


def _is_empty(value):
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _fix_broken_characters(text):
    """
    Some cells have broken characters like 'â€¢' (should be '•'),
    'â€™' (should be ’) and 'Â®' (should be ®). This repairs them.
    """
    if "â€" not in text and "Â" not in text:
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text  # could not repair safely, keep original


def _clean_str(value):
    if _is_empty(value):
        return ""
    # Numbers like 8250833696.0 should become "8250833696"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).replace("\xa0", " ").strip()
    return _fix_broken_characters(text)


def _simple(value):
    """Lowercase + remove ALL spaces/newlines. Used only for matching."""
    return re.sub(r"\s+", "", _clean_str(value).lower())


def _first_email(value):
    """
    Some cells have 2 emails: 'a@x.com, b@x.com'.
    Applicant.email is an EmailField, so it can hold only one.
    We keep the first one (otherwise the admin edit form shows an error).
    """
    text = _clean_str(value)
    return re.split(r"[,;/\s]+", text)[0] if text else ""


# ---------------------------------------------------------------------------
# STEP 2: Date parsing
# ---------------------------------------------------------------------------

def _parse_date(value):
    """
    The sheet has 3 kinds of dates:
      1. Text with slash:  '26/12/2023', '13/3/2024'
      2. Text with dot:    '16.02.2024'
      3. Real Excel dates: Excel wrongly read '03/02/2024' as 2 March 2024
         (month and day swapped). So we swap them back -> 3 Feb 2024.
    """
    if _is_empty(value):
        return None

    # Case 3: real Excel date cell -> swap day and month back
    if isinstance(value, (datetime.datetime, datetime.date, pd.Timestamp)):
        if value.day <= 12:
            return datetime.date(value.year, value.day, value.month)
        return datetime.date(value.year, value.month, value.day)

    # Case 1 and 2: text dates in day/month/year order
    text = _clean_str(value).replace(".", "/").replace("-", "/")
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    # Last try: let pandas guess (day first)
    dt = pd.to_datetime(text, dayfirst=True, errors="coerce")
    return None if pd.isna(dt) else dt.date()


# ---------------------------------------------------------------------------
# STEP 3: Map sheet text -> model choice values (from core/models.py)
# ---------------------------------------------------------------------------

def _map_affiliation_type(value):
    # Model choices: "individual", "institute"
    if _simple(value) == "individual":
        return "individual"
    return "institute"  # startup / company / institute all map here


def _map_risk_class(value):
    # Model choices: "A", "B", "C", "D", "unknown"
    value = _simple(value)
    match = re.search(r"class([a-d])", value)
    if match:
        return match.group(1).upper()
    return Application.RiskClass.UNKNOWN


def _map_device_category(value):
    # Model choices: "predicate", "investigational"
    # Check "investigational" FIRST, because that text also has "predicate"
    value = _simple(value)
    if "investigational" in value:
        return Application.DeviceCategory.INVESTIGATIONAL
    if "predicate" in value:
        return Application.DeviceCategory.PREDICATE
    return ""


def _map_innovation_stage(value):
    # _simple() removes spaces, so "ClinicalInvestigation" (typo in sheet)
    # and "Clinical Investigation" both match.
    S = Application.InnovationStage
    value = _simple(value)
    if "conceptdevelopment" in value:
        return S.CONCEPT_DEV
    if "conceptproved" in value:
        return S.CONCEPT_PROVED
    if "benchtesting" in value:
        return S.BENCH_TESTING
    if "clinicaltesting" in value:
        return S.CLINICAL_TESTING
    if "clinicalinvestigation" in value or "performanceevaluation" in value:
        return S.CLINICAL_INVESTIGATION
    return ""


def _map_medtech_type(value):
    # Model choices: "MM-DD" (Device & Diagnostics), "MM-VT", "MM-AT".
    # There is NO "MM-IVD" choice. "Medical Device" and "In-vitro Diagnostic"
    # both belong to "Device & Diagnostics", so both map to MM-DD.
    value = _simple(value)
    if "vaccine" in value or "therapeutic" in value:
        return Application.MedTechType.MMVT
    if "assistive" in value or "assitive" in value:
        return Application.MedTechType.MMAT
    return Application.MedTechType.MMDD


def _map_test_license(value):
    # Sheet values: Yes, No, (blank for most rows)
    value = _simple(value)
    if value.startswith("y"):
        return True
    if value.startswith("n"):
        return False
    return None


def _sheet_says_closed(status_value, closure_date):
    return _simple(status_value) == "closed" or bool(closure_date)


# ---------------------------------------------------------------------------
# STEP 4: Extra columns that have NO field in the model yet.
# If you add these fields to Application later (and run migrations),
# they will be filled automatically. Until then they are skipped.
# ---------------------------------------------------------------------------

# model field name  ->  normalised Excel column name
OPTIONAL_APPLICATION_FIELDS = {
    "technology_summary": "summary_of_the_technology",
    "financial_opportunities": "financial_opportunities",
}


def _model_field_names(model):
    return {f.name for f in model._meta.get_fields()}


# ---------------------------------------------------------------------------
# STEP 5: Applicant lookup (case-insensitive name)
# ---------------------------------------------------------------------------

def _get_or_update_applicant(row, user=None):
    name = _clean_str(row.get("applicant_name")) or "Unknown Applicant"
    data = {
        "innovator_name": _clean_str(row.get("innovators_name")),
        "affiliation_type": _map_affiliation_type(row.get("applicant_type")),
        "affiliation_name": _clean_str(row.get("company_institute_name")),
        "email": _first_email(row.get("email_id")),
        "contact_number": _clean_str(row.get("contact_number")),
        "address": _clean_str(row.get("address")),
    }

    # "RAHUL UIKEY" and "Rahul Uikey" should be the same applicant
    applicant = Applicant.objects.filter(name__iexact=name).first()
    if applicant is None:
        return Applicant.objects.create(name=name, created_by=user, **data)

    # Update only with non-empty values, so a blank cell in a later row
    # does not wipe out data saved from an earlier row.
    for field, value in data.items():
        if value not in ("", None):
            setattr(applicant, field, value)
    applicant.save()
    return applicant


# ---------------------------------------------------------------------------
# STEP 6: Main import function
# ---------------------------------------------------------------------------

def import_master_excel(file_obj, user=None):
    """
    file_obj: the uploaded file (request.FILES['excel_file'])
    user:     request.user (saved as created_by on NEW records, and on
              status change logs). Optional.
    Returns: dict with counts and any row-level errors.
    """
    df = pd.read_excel(file_obj, sheet_name=0)
    df.columns = [_normalise(c) for c in df.columns]
    df = df.dropna(how="all")  # skip fully empty rows

    # Check the must-have column exists before doing anything
    if "application_reference_no" not in df.columns:
        return {
            "created": 0,
            "updated": 0,
            "errors": ["Column 'Application reference no.' not found. Wrong file?"],
        }

    app_fields = _model_field_names(Application)
    optional_fields = {
        model_field: excel_col
        for model_field, excel_col in OPTIONAL_APPLICATION_FIELDS.items()
        if model_field in app_fields
    }

    created_count = 0
    updated_count = 0
    errors = []

    for i, row in df.iterrows():
        excel_row_num = i + 2  # header is row 1
        try:
            with transaction.atomic():  # one bad row won't leave half-saved data
                reference_no = _clean_str(row.get("application_reference_no"))
                if not reference_no:
                    errors.append(f"Row {excel_row_num}: missing reference no, skipped.")
                    continue

                # ---- Applicant ----
                applicant = _get_or_update_applicant(row, user)

                # ---- Status ----
                existing = Application.objects.filter(reference_no=reference_no).first()
                closure_date = _parse_date(row.get("date_of_closure"))

                if _sheet_says_closed(row.get("status"), closure_date):
                    status = Application.ApplicationStatus.CLOSED
                elif existing:
                    # Sheet is not closed -> keep what staff set in the app
                    # (e.g. "assigned_to_kp" or "resolved"). Don't reset it.
                    status = existing.status
                else:
                    status = Application.ApplicationStatus.IN_PROGRESS

                # ---- Application ----
                defaults = {
                    "applicant": applicant,
                    "medtech_type": _map_medtech_type(row.get("medtech_type")),
                    "technology_name": _clean_str(row.get("name_of_the_technology")),
                    "intended_use_statement": _clean_str(row.get("intended_use_statement")),
                    "use_environment": _clean_str(row.get("use_environment")).replace("_", "/"),
                    "target_population": _clean_str(row.get("target_population")),
                    "area_of_application": _clean_str(row.get("area_of_application")),
                    "risk_classification": _map_risk_class(
                        row.get("risk_classification_of_medical_device_ivds")
                    ),
                    "device_category": _map_device_category(
                        row.get("type_of_medical_device_diagnostic")
                    ),
                    "innovation_stage": _map_innovation_stage(row.get("stage_of_your_innovation")),
                    "support_type_requested": _clean_str(
                        row.get("type_of_support_handholding_needed")
                    ),
                    "test_license_received": _map_test_license(
                        row.get("test_license_received_yes_no")
                    ),
                    "query_text": _clean_str(row.get("query")),
                    "date_received": _parse_date(row.get("date_of_application_reciept")),
                    "status": status,
                    "closure_date": closure_date,
                }

                # "Recorded Reason for Closure" -> final_status_notes / final_status_date
                closure_reason = _clean_str(row.get("recorded_reason_for_closure"))
                if closure_reason:
                    defaults["final_status_notes"] = closure_reason
                    defaults["final_status_date"] = closure_date

                for model_field, excel_col in optional_fields.items():
                    defaults[model_field] = _clean_str(row.get(excel_col))

                obj, was_created = Application.objects.update_or_create(
                    reference_no=reference_no,
                    defaults=defaults,
                )

                if was_created:
                    created_count += 1
                    if user:
                        obj.created_by = user
                        obj.save(update_fields=["created_by"])
                else:
                    updated_count += 1
                    # Same audit log as the admin save_model
                    if existing.status != status:
                        StatusChangeLog.objects.create(
                            application=obj,
                            from_status=existing.status,
                            to_status=status,
                            remarks="Changed by Excel import",
                            created_by=user,
                        )

        except Exception as e:
            errors.append(f"Row {excel_row_num}: {e}")

    return {
        "created": created_count,
        "updated": updated_count,
        "errors": errors,
    }