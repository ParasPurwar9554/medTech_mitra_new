"""
Import data from the MedTech Mitra dashboard Excel template (Sheet1, 29 columns).

Columns in the sheet (* = required):
  Reference No *, Date Received, Applicant Name *, Innovator Name,
  Affiliation Type, Affiliation Name, Email, Contact Number, Address,
  MedTech Type *, Technology Name *, Intended Use Statement, Use Environment,
  Target Population, Area of Application, Risk Classification,
  Device Category, Innovation Stage, Support Type Requested, Support Category,
  Test License Received, Query Text, Status, Initial TRL Level,
  Initial TRL Date, Current TRL Level, Current TRL Date, Closure Date,
  Reason for Closure

What gets saved:
  - Applicant        (name, innovator, affiliation, email, phone, address)
  - Application      (all other columns; TRL levels are linked to TRLDefinition)
  - TRLProgressLog   (when the current TRL is set or changes)

Column headers are normalised (lowercased, punctuation -> underscore,
the "*" is dropped), so "Reference No *" becomes "reference_no".
"""
import datetime
import re

import pandas as pd
from django.db import transaction

from core.models import (
    Applicant,
    Application,
    TRLDefinition,
    TRLProgressLog,
)


# Columns that MUST be in the file, or we stop the import
REQUIRED_COLUMNS = ["reference_no", "applicant_name", "medtech_type", "technology_name","medtech_subtype"]


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
    """Repair broken characters like 'â€¢' -> '•' and 'Â®' -> '®'."""
    if "â€" not in text and "Â" not in text:
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _clean_str(value):
    if _is_empty(value):
        return ""
    # 8250833696.0 -> "8250833696"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).replace("\xa0", " ").strip()
    return _fix_broken_characters(text)


def _simple(value):
    """Lowercase + keep only letters/numbers. Used only for matching."""
    return re.sub(r"[^a-z0-9]", "", _clean_str(value).lower())


def _first_email(value):
    """'a@x.com, b@x.com' -> 'a@x.com' (EmailField holds only one)."""
    text = _clean_str(value)
    return re.split(r"[,;/\s]+", text)[0] if text else ""


def _phone(value):
    """'9633474969 \\n9448908617' -> '9633474969, 9448908617'"""
    parts = re.split(r"[\s,;/]+", _clean_str(value))
    return ", ".join(p for p in parts if p)


def _yes_no(value):
    """'Yes' / 'Y' / 'True' -> True, 'No' / 'N' / 'False' -> False, blank -> None."""
    value = _simple(value)
    if value.startswith("y") or value in ("true", "1"):
        return True
    if value.startswith("n") or value in ("false", "0"):
        return False
    return None


def _trl_level(value):
    """'TRL 4' / '4' / 4.0 -> 4. Blank or not a number -> None."""
    match = re.search(r"\d+", _clean_str(value))
    return int(match.group()) if match else None


# ---------------------------------------------------------------------------
# STEP 2: Date parsing
# ---------------------------------------------------------------------------

def _parse_date(value):
    """
    The template uses real Excel date cells, so most dates come in as
    datetime objects and are used AS THEY ARE (no day/month swap).

    If someone types a date as text instead, we still read it:
      '26/12/2023', '16.02.2024', '30.09.26', '22/09/2026; 30/09/2026'
      (when there are two dates in one cell we take the LAST one)
    Returns None if it is not a date.
    """
    if _is_empty(value):
        return None

    # Real Excel date cell
    if isinstance(value, (datetime.datetime, pd.Timestamp)):
        return value.date()
    if isinstance(value, datetime.date):
        return value

    # Text date: find every "day ? month ? year" piece, keep the LAST one
    found = re.findall(r"\d{1,2}\D{1,2}\d{1,2}\D{1,2}\d{2,4}", _clean_str(value))
    if not found:
        return None
    day, month, year = re.split(r"\D+", found[-1])
    text = f"{day}/{month}/{year}"

    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# STEP 3: Map sheet text -> model choice values
# ---------------------------------------------------------------------------

def _match_choice(choices, value):
    """
    Try to match the cell to a Django choice by its value OR its label.
    e.g. for MedTechType, 'MM-DD', 'mmdd' or the label text all match.
    Returns the choice value, or None if nothing matches.
    """
    cell = _simple(value)
    if not cell:
        return None
    for choice_value, label in choices.choices:
        if cell in (_simple(choice_value), _simple(label)):
            return choice_value
    return None


def _map_affiliation_type(value):
    if _simple(value) == "individual":
        return "individual"
    return "institute"  # startup / company / institute


def _map_risk_class(value):
    # Accepts 'Class A', 'Class-B', or just 'C'
    value = _simple(value)
    match = re.search(r"class([a-d])", value) or re.fullmatch(r"([a-d])", value)
    if match:
        return match.group(1).upper()
    return Application.RiskClass.UNKNOWN


def _map_device_category(value):
    found = _match_choice(Application.DeviceCategory, value)
    if found:
        return found
    # Check "investigational" FIRST, because that text also has "predicate"
    value = _simple(value)
    if "investigational" in value:
        return Application.DeviceCategory.INVESTIGATIONAL
    if "predicate" in value:
        return Application.DeviceCategory.PREDICATE
    return ""


def _map_innovation_stage(value):
    found = _match_choice(Application.InnovationStage, value)
    if found:
        return found
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
    """
    Model choices:
      MM-DD = Device & Assistive Technologies
      MM-VT = Vaccines & Therapeutics
      MM-AT = Diagnostics
    """
    found = _match_choice(Application.MedTechType, value)
    if found:
        return found
    value = _simple(value)
    if "vaccine" in value or "therapeutic" in value:
        return Application.MedTechType.MMVT
    if "diagnostic" in value or "invitro" in value or "ivd" in value:
        return Application.MedTechType.MMAT
    return Application.MedTechType.MMDD  # medical device / assistive / unknown


def _map_support_category(value):
    """'Advisory' -> advisory, 'Handholding' / 'Hand holding' -> handholding."""
    found = _match_choice(Application.SupportCategory, value)
    if found:
        return found
    value = _simple(value)
    if "advis" in value:
        return Application.SupportCategory.ADVISORY
    if "hand" in value:
        return Application.SupportCategory.HANDHOLDING
    return ""


def _find_trl(level, medtech_type):
    """
    Initial/Current TRL are links to TRLDefinition (level + medtech type).
    1. Try the row for this level AND this medtech type.
    2. If not found, try a row with this level and a BLANK medtech type
       (a TRL that applies to all types).
    Returns the TRLDefinition, or None (we never use another type's TRL).
    """
    if level is None:
        return None
    trl = TRLDefinition.objects.filter(level=level, medtech_type=medtech_type).first()
    if trl is None:
        trl = TRLDefinition.objects.filter(level=level, medtech_type="").first()
    return trl


def _decide_status(row, existing, closure_date):
    """
    'Status' column matches a status choice  -> use it
    a 'Closure Date' is filled               -> closed
    otherwise keep what staff already set in the app, or in_progress for new.
    """
    S = Application.ApplicationStatus
    if closure_date:
        return S.CLOSED

    found = _match_choice(S, row.get("status"))
    if found:
        return found

    # Common spellings that may not match the choice labels exactly
    text = _simple(row.get("status"))
    if text in ("completed", "complete", "done"):
        return S.COMPLTED
    if text == "closed":
        return S.CLOSED
    if text in ("inprogress", "ongoing", "open"):
        return S.IN_PROGRESS

    if existing:
        return existing.status
    return S.IN_PROGRESS


# ---------------------------------------------------------------------------
# STEP 4: Applicant lookup (case-insensitive name)
# ---------------------------------------------------------------------------

def _get_or_update_applicant(row, user=None):
    name = _clean_str(row.get("applicant_name")) or "Unknown Applicant"
    data = {
        "innovator_name": _clean_str(row.get("innovator_name")),
        "affiliation_type": _map_affiliation_type(row.get("affiliation_type")),
        "affiliation_name": _clean_str(row.get("affiliation_name")),
        "email": _first_email(row.get("email")),
        "contact_number": _phone(row.get("contact_number")),
        "address": _clean_str(row.get("address")),
    }

    applicant = Applicant.objects.filter(name__iexact=name).first()
    if applicant is None:
        return Applicant.objects.create(name=name, created_by=user, **data)

    # Only overwrite with non-empty values
    for field, value in data.items():
        if value not in ("", None):
            setattr(applicant, field, value)
    applicant.save()
    return applicant

def _map_medtech_subtype(value):
    found = _match_choice(Application.MedTechSubCategoryType, value)
    if found:
        return found
    raise ValueError(
        f"Invalid MedTech Sub Category: '{value}'. "
        f"Allowed: MM-1, MM-2, MM-3, MM-4, MM-5, Device, Assistive, "
        f"Diagnostics, Vaccine, Therapeutics, Not Known"
    )


# ---------------------------------------------------------------------------
# STEP 5: Build all Application values from one row
# ---------------------------------------------------------------------------

def _application_values(row, applicant, status, closure_date, warnings):
    medtech_type = _map_medtech_type(row.get("medtech_type"))
    medtech_subtype = _map_medtech_subtype(row.get("medtech_subtype"))
    print("++++++++++: ",medtech_subtype)

    # --- TRL: turn the level number into a TRLDefinition row ---
    initial_level = _trl_level(row.get("initial_trl_level"))
    current_level = _trl_level(row.get("current_trl_level"))
    initial_trl = _find_trl(initial_level, medtech_type)
    current_trl = _find_trl(current_level, medtech_type)
    if initial_level and initial_trl is None:
        warnings.append(f"Initial TRL level {initial_level} not found in TRL master, left blank.")
    if current_level and current_trl is None:
        warnings.append(f"Current TRL level {current_level} not found in TRL master, left blank.")

    values = {
        "applicant": applicant,
        "date_received": _parse_date(row.get("date_received")),

        # --- Technology details ---
        "medtech_type": medtech_type,
        "medtech_subtype":medtech_subtype,
        "technology_name": _clean_str(row.get("technology_name")),
        "intended_use_statement": _clean_str(row.get("intended_use_statement")),
        "use_environment": _clean_str(row.get("use_environment")).replace("_", "/"),
        "target_population": _clean_str(row.get("target_population")),
        "area_of_application": _clean_str(row.get("area_of_application")),
        "risk_classification": _map_risk_class(row.get("risk_classification")),
        "device_category": _map_device_category(row.get("device_category")),
        "innovation_stage": _map_innovation_stage(row.get("innovation_stage")),

        # --- Support ---
        "support_type_requested": _clean_str(row.get("support_type_requested")),
        "support_category": _map_support_category(row.get("support_category")),

        # --- Test license / query ---
        "test_license_received": _yes_no(row.get("test_license_received")),
        "query_text": _clean_str(row.get("query_text")),

        # --- TRL (Technology Readiness Level) ---
        "initial_trl": initial_trl,
        "initial_trl_date": _parse_date(row.get("initial_trl_date")),
        "current_trl": current_trl,
        "current_trl_date": _parse_date(row.get("current_trl_date")),

        # --- Status / closure ---
        "status": status,
        "closure_date": closure_date,
        "reason_for_closure": _clean_str(row.get("reason_for_closure")),
    }

    # Closure reason also goes to final_status_notes / final_status_date
    if values["reason_for_closure"]:
        values["final_status_notes"] = values["reason_for_closure"]
        values["final_status_date"] = closure_date

    return values


# ---------------------------------------------------------------------------
# STEP 6: Main import function
# ---------------------------------------------------------------------------

def import_master_excel(file_obj, user=None):
    """
    file_obj: the uploaded file (request.FILES['excel_file'])
    user:     request.user (optional)
    Returns: dict with counts and any row-level errors.
    """
    df = pd.read_excel(file_obj, sheet_name=0)
    df.columns = [_normalise(c) for c in df.columns]
    #print("df Columns: ", df.columns)
    df = df.dropna(how="all")
    #print("df Dropna: ", df)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return {
            "created": 0,
            "updated": 0,
            "errors": [f"Columns not found: {', '.join(missing)}. Wrong file?"],
        }

    created_count = 0
    updated_count = 0
    errors = []

    for i, row in df.iterrows():
        excel_row_num = i + 2  # header is row 1
        try:
            with transaction.atomic():
                reference_no = _clean_str(row.get("reference_no"))
                if not reference_no:
                    errors.append(f"Row {excel_row_num}: missing Reference No, skipped.")
                    continue
                if not _clean_str(row.get("technology_name")):
                    errors.append(f"Row {excel_row_num}: missing Technology Name, skipped.")
                    continue

                applicant = _get_or_update_applicant(row, user)

                existing = Application.objects.filter(reference_no=reference_no).first()
                closure_date = _parse_date(row.get("closure_date"))
                status = _decide_status(row, existing, closure_date)

                warnings = []
                defaults = _application_values(
                    row, applicant, status, closure_date, warnings)
                for w in warnings:
                    errors.append(f"Row {excel_row_num}: {w}")

                # Blank TRL cell -> keep the TRL already saved in the app
                old_trl_id = existing.current_trl_id if existing else None
                for field in ("initial_trl", "current_trl"):
                    if defaults[field] is None and existing:
                        defaults.pop(field)

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

                # Save TRL history when the current TRL is new or changed
                if obj.current_trl_id and obj.current_trl_id != old_trl_id:
                    TRLProgressLog.objects.create(
                        application=obj,
                        trl=obj.current_trl,
                        changed_on=obj.current_trl_date or datetime.date.today(),
                        remarks="Set by Excel import",
                        created_by=user,
                    )

        except Exception as e:
            errors.append(f"Row {excel_row_num}: {e}")

    return {
        "created": created_count,
        "updated": updated_count,
        "errors": errors,
    }
