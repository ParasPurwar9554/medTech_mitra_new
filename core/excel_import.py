"""
Import data from the MedTech Mitra dashboard Excel.

Admin-managed fields intentionally ignored by this importer:
Support recommended by TAC, Initial/Current TRL fields and stages,
Assigned Knowledge Partner, and Assigned on.

Columns in the sheet (* = required):
  Reference No *, Date Received, Applicant Name *, Innovator Name,
  Applicant Type, Company Institute Name, Email, Contact Number, Address,
  Category *, Sub Category, Technology Name *, Intended Use Statement,
  Use Environment, Target Population, Area of Application,
  Risk Classification, Innovation Stage, TAC, Support recommended by TAC,
  TAC Meeting Date, Test License, Summary of The Technology, Query Text,
  Initial TRL Level, Initial Innovation Stage, Initial TRL Date,
  Current TRL Level, Current Innovation Stage, Current TRL Date,
  Assigned Knowledge Partner, Assigned on, Status, Closure Date,
  Reason for Closure, Final Response, Action Taken

What gets saved:
  - Applicant                  (name, innovator, affiliation, email, phone, address)
  - Application                (technology, risk, stages, TRL, status, closure ...)
  - TACMeeting                 (TAC, TAC Meeting Date, Final Response, Action Taken)
"""
import datetime
import re

import pandas as pd
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DataError, IntegrityError, transaction

from core.models import (
    Applicant,
    Application,
    TACMeeting,
)


# ---------------------------------------------------------------------------
# STEP 0: Settings
# ---------------------------------------------------------------------------

# Excel header (after cleaning) -> name used in this file
COLUMN_ALIASES = {
    "applicant_type": "affiliation_type",
    "company_institute_name": "affiliation_name",
    "category": "medtech_type",
    "sub_category": "medtech_subtype",
    "test_license": "test_license_received",
    "summary_of_the_technology": "summary_technology",
    "tac": "tac_ref_no",
    "final_response": "final_tac_response",
}

# Name used in this file -> Excel header the user sees (used in messages)
COLUMN_LABELS = {
    "reference_no": "Reference No",
    "applicant_name": "Applicant Name",
    "medtech_type": "Category",
    "technology_name": "Technology Name",
    "date_received": "Date Received",
    "tac_meeting_date": "TAC Meeting Date",
    "closure_date": "Closure Date",
    "innovation_stage": "Innovation Stage",
}

# Columns that MUST be in the file and MUST be filled in every row
REQUIRED_COLUMNS = ["reference_no", "applicant_name", "medtech_type", "technology_name"]

# Date columns we check for bad values
DATE_COLUMNS = ["date_received", "tac_meeting_date", "closure_date"]


# ---------------------------------------------------------------------------
# STEP 1: Small helpers for cleaning values
# ---------------------------------------------------------------------------

def _normalise(col):
    """'Reference No *' -> 'reference_no', 'Category' -> 'medtech_type'."""
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    col = col.strip("_")
    if col in COLUMN_ALIASES:
        col = COLUMN_ALIASES[col]
    return col


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
    """Turn any cell into clean text. Empty cell -> ''."""
    if _is_empty(value):
        return ""
    # 8250833696.0 -> 8250833696
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).replace("\xa0", " ").strip()
    return _fix_broken_characters(text)


def _simple(value):
    """Lowercase + keep only letters/numbers. Used only for matching."""
    text = _clean_str(value).lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _short(value, size=40):
    """Cut long text for messages: 'Portable and Radiation-free Mobile Ap...'"""
    text = " ".join(_clean_str(value).split())
    if len(text) > size:
        text = text[:size - 3] + "..."
    return text


def _first_email(value):
    """'a@x.com, b@x.com' -> 'a@x.com' (EmailField holds only one)."""
    text = _clean_str(value)
    if text == "":
        return ""
    parts = re.split(r"[,;/\s]+", text)
    return parts[0]


def _phone(value):
    """'9633474969 \\n9448908617' -> '9633474969, 9448908617'"""
    parts = re.split(r"[\s,;/]+", _clean_str(value))
    numbers = []
    for p in parts:
        if p != "":
            numbers.append(p)
    return ", ".join(numbers)


def _yes_no(value):
    """'Yes' / 'Y' / 'True' -> True, 'No' / 'N' / 'False' -> False, blank -> None."""
    value = _simple(value)
    if value.startswith("y") or value == "true" or value == "1":
        return True
    if value.startswith("n") or value == "false" or value == "0":
        return False
    return None


# ---------------------------------------------------------------------------
# STEP 2: Date parsing
# ---------------------------------------------------------------------------

def _parse_date(value):
    """
    Real Excel date cells are used as they are.
    Text dates are also read: '26/12/2023', '16.02.2024', '30.09.26'.
    If there are two dates in one cell we take the LAST one.
    Returns None if it is not a date.
    """
    if _is_empty(value):
        return None

    # Real Excel date cell
    if isinstance(value, (datetime.datetime, pd.Timestamp)):
        return value.date()
    if isinstance(value, datetime.date):
        return value

    # Text date
    found = re.findall(r"\d{1,2}\D{1,2}\d{1,2}\D{1,2}\d{2,4}", _clean_str(value))
    if len(found) == 0:
        return None

    pieces = re.split(r"\D+", found[-1])
    day = pieces[0]
    month = pieces[1]
    year = pieces[2]
    text = day + "/" + month + "/" + year

    for fmt in ["%d/%m/%Y", "%d/%m/%y"]:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# STEP 3: Map sheet text -> model choice values
# ---------------------------------------------------------------------------

def _match_choice(choices, value):
    """
    Match the cell to a Django choice by its value OR its label.
    e.g. for MedTechType: 'MM-DD', 'mmdd' or the label text all match.
    Returns the choice value, or None if nothing matches.
    """
    cell = _simple(value)
    if cell == "":
        return None
    for choice_value, label in choices.choices:
        if cell == _simple(choice_value) or cell == _simple(label):
            return choice_value
    return None


def _map_affiliation_type(value):
    if _simple(value) == "individual":
        return "individual"
    return "institute"  # startup / company / institute

def _map_gender(value):
    """Map Excel gender values to Applicant.Gender choices."""
    value = _simple(value)

    if value in ["male", "male"]:
        return Applicant.Gender.male

    if value in ["female", "female"]:
        return Applicant.Gender.female

    return Applicant.Gender.UNKNOWN


def _map_risk_class(value):
    """Accepts 'Class A', 'Class-B', or just 'C'."""
    value = _simple(value)
    for letter in ["a", "b", "c", "d"]:
        if value.startswith("class" + letter) or value == letter:
            return letter.upper()
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


def _is_known_category(value):
    """True if the Category text can be matched to a MedTech type."""
    if _match_choice(Application.MedTechType, value):
        return True
    text = _simple(value)
    for word in ["device", "assistive", "vaccine", "therapeutic", "diagnostic", "invitro", "ivd"]:
        if word in text:
            return True
    return False


def _map_medtech_type(value):
    """
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
    return Application.MedTechType.MMDD  # device / assistive / unknown


def _map_medtech_subtype(value):
    """'Device' / 'MM-1' -> MM-1 ... Blank -> 'unknown' (Not Known)."""
    if _clean_str(value) == "":
        return Application.MedTechSubCategoryType.UNKNOWN
    found = _match_choice(Application.MedTechSubCategoryType, value)
    if found:
        return found
    raise ValueError(
        "'Sub Category' value '" + _clean_str(value) + "' is not valid. "
        "Use one of: Device, Assistive, Diagnostics, Vaccine, Therapeutics, "
        "Not Known (or MM-1 to MM-5)."
    )


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


def _decide_status(row, existing, closure_date):
    """
    Closure Date filled              -> closed
    Status column matches a choice   -> use it
    otherwise keep the saved status, or in_progress for a new application.
    """
    S = Application.ApplicationStatus
    if closure_date:
        return S.CLOSED

    found = _match_choice(S, row.get("status"))
    if found:
        return found

    text = _simple(row.get("status"))
    if text == "completed" or text == "complete" or text == "done":
        return S.COMPLTED
    if text == "closed":
        return S.CLOSED
    if text == "inprogress" or text == "ongoing" or text == "open":
        return S.IN_PROGRESS

    if existing:
        return existing.status
    return S.IN_PROGRESS


# ---------------------------------------------------------------------------
# STEP 4: Applicant (find by name, else create)
# ---------------------------------------------------------------------------

def _get_or_update_applicant(row, user=None):
    name = _clean_str(row.get("applicant_name"))
    if name == "":
        name = "Unknown Applicant"

    data = {
        "innovator_name": _clean_str(row.get("innovator_name"))[:200],
        "affiliation_type": _map_affiliation_type(row.get("affiliation_type")),
        "affiliation_name": _clean_str(row.get("affiliation_name"))[:255],
        "email": _first_email(row.get("email")),
        "contact_number": _phone(row.get("contact_number"))[:50],
        "address": _clean_str(row.get("address")),
        "city": _clean_str(row.get("city"))[:255],
        "gender": _map_gender(row.get("gender")),
    }

    applicant = Applicant.objects.filter(name__iexact=name).first()
    if applicant is None:
        applicant = Applicant.objects.create(name=name[:200], created_by=user, **data)
        return applicant

    # Update only with values that are filled in
    for field, value in data.items():
        if field == "gender":
            # Update gender only if Excel has a value
            if _clean_str(row.get("gender")) != "":
                setattr(applicant, field, value)

        elif value != "" and value is not None:
            setattr(applicant, field, value)
    applicant.save()
    return applicant


# ---------------------------------------------------------------------------
# STEP 5: Build all Application values from one row
# ---------------------------------------------------------------------------

def _application_values(row, applicant, status, closure_date, warnings):
    """Build only the fields that are maintained through Excel import.

    TRL values/dates, initial and current innovation stages, and support
    recommendations are deliberately omitted so re-importing Excel cannot
    overwrite values entered manually in Django Admin.
    """
    medtech_type = _map_medtech_type(row.get("medtech_type"))
    medtech_subtype = _map_medtech_subtype(row.get("medtech_subtype"))
    use_environment = _clean_str(row.get("use_environment")).replace("_", "/")

    values = {
        "applicant": applicant,
        "date_received": _parse_date(row.get("date_received")),

        # Technology details
        "medtech_type": medtech_type,
        "medtech_subtype": medtech_subtype,
        "technology_name": _clean_str(row.get("technology_name"))[:255],
        "intended_use_statement": _clean_str(row.get("intended_use_statement")),
        "use_environment": use_environment[:255],
        "target_population": _clean_str(row.get("target_population")),
        "area_of_application": _clean_str(row.get("area_of_application")),
        "risk_classification": _map_risk_class(row.get("risk_classification")),
        "device_category": _map_device_category(row.get("device_category")),
        "innovation_stage": _map_innovation_stage(row.get("innovation_stage")),
        "summary_Technology": _clean_str(row.get("summary_technology")),

        # Support fields that are still part of the Excel import
        "support_type_requested": _clean_str(row.get("support_type_requested"))[:255],
        "support_category": _map_support_category(row.get("support_category")),

        # Test license / query
        "test_license_received": _yes_no(row.get("test_license_received")),
        "query_text": _clean_str(row.get("query_text")),

        # Status / closure
        "status": status,
        "closure_date": closure_date,
        "reason_for_closure": _clean_str(row.get("reason_for_closure")),
    }
    return values


# ---------------------------------------------------------------------------
# STEP 6: TAC meeting (TAC, TAC Meeting Date, Final Response, Action Taken)
# ---------------------------------------------------------------------------


def _save_tac_meeting(row, application, user=None):

    ref_no = _clean_str(row.get("tac_ref_no"))[:50]
    meeting_date = _parse_date(row.get("tac_meeting_date"))

    final_response = _clean_str(row.get("final_tac_response"))
    action_taken = _clean_str(row.get("action_taken"))

    # Do not create an empty TAC record.
    if not any([ref_no, meeting_date, final_response, action_taken]):
        return

    data = {}

    if ref_no:
        data["meeting_ref_no"] = ref_no

    if meeting_date:
        data["meeting_date"] = meeting_date

    if final_response:
        data["final_tac_response"] = final_response

    if action_taken:
        data["action_taken"] = action_taken

    # Find existing TAC record for this application.
    tac = TACMeeting.objects.filter(
        application=application
    ).order_by("id").first()

    if tac:
        # Update existing TAC record.
        for field, value in data.items():
            setattr(tac, field, value)

        tac.save()
    else:
        # Create TAC record only once.
        tac = TACMeeting.objects.create(
            application=application,
            **data
        )

        if user:
            tac.created_by = user
            tac.save(update_fields=["created_by"])


# ---------------------------------------------------------------------------
# STEP 7: Clear error messages
# ---------------------------------------------------------------------------

def _row_label(excel_row_num, row):
    """'Row 5 | MM/2023/Dec/11 | Applicant: Dr. Deepika Singh | Technology: Portable ...'"""
    label = "Row " + str(excel_row_num)

    ref = _clean_str(row.get("reference_no"))
    if ref == "":
        label = label + " | Reference No: (empty)"
    else:
        label = label + " | " + ref

    if _clean_str(row.get("applicant_name")) != "":
        label = label + " | Applicant: " + _short(row.get("applicant_name"), 30)

    if _clean_str(row.get("technology_name")) != "":
        label = label + " | Technology: " + _short(row.get("technology_name"))

    return label


def _explain_exception(e):
    """Turn a Python / database error into a sentence the user can act on."""
    text = str(e)
    if isinstance(e, ValueError):
        return text                                 # our own messages are already clear
    if isinstance(e, IntegrityError):
        return ("Could not save because a required value is missing or a value that "
                "must be unique is already used. Details: " + text)
    if isinstance(e, DataError):
        return ("A value is too long or has the wrong type for the database. "
                "Check the long text / number columns in this row. Details: " + text)
    if isinstance(e, ValidationError):
        return "Invalid value: " + "; ".join(e.messages)
    return "Unexpected error (" + type(e).__name__ + "): " + text


def _check_row(row, warnings):
    """
    Find values that would otherwise be silently dropped and say so.
    These are warnings: the row is still saved.
    """
    # 1. Dates that are filled but can't be read
    for col in DATE_COLUMNS:
        value = row.get(col)
        if _clean_str(value) != "" and _parse_date(value) is None:
            warnings.append("'" + COLUMN_LABELS[col] + "' has '" + _short(value, 25) +
                            "', which is not a date. Left blank. "
                            "Use DD/MM/YYYY, e.g. 26/12/2023.")

    # 2. Email that is not a valid email
    email = _first_email(row.get("email"))
    if email != "":
        try:
            validate_email(email)
        except ValidationError:
            warnings.append("'Email' has '" + _short(row.get("email"), 40) +
                            "', which is not a valid email address (example: name@gmail.com). "
                            "Saved as it is; please correct it.")

    # 3. Category text we don't recognise
    category = row.get("medtech_type")
    if not _is_known_category(category):
        warnings.append("'Category' value '" + _short(category, 30) + "' is not recognised, "
                        "saved as 'Device & Assistive Technologies'. "
                        "Use MM-DD, MM-VT or MM-AT.")

    # 4. Risk Classification text we don't recognise
    risk = row.get("risk_classification")
    risk_text = _simple(risk)
    if risk_text != "" and risk_text != "notknown" and risk_text != "unknown":
        if _map_risk_class(risk) == Application.RiskClass.UNKNOWN:
            warnings.append("'Risk Classification' value '" + _short(risk, 30) +
                            "' is not recognised, saved as 'Not Known'. "
                            "Use Class A, Class B, Class C or Class D.")

    # 5. Innovation Stage text we don't recognise
    for col in ["innovation_stage"]:
        value = row.get(col)
        if _clean_str(value) != "" and _map_innovation_stage(value) == "":
            warnings.append("'" + COLUMN_LABELS[col] + "' value '" + _short(value, 30) +
                            "' is not recognised, left blank.")


def _is_empty_row(row):
    """True if Reference No, Applicant Name and Technology Name are all empty."""
    for col in ["reference_no", "applicant_name", "technology_name"]:
        if _clean_str(row.get(col)) != "":
            return False
    return True


# ---------------------------------------------------------------------------
# STEP 8: Main import function
# ---------------------------------------------------------------------------

def import_master_excel(file_obj, user=None):
    """
    file_obj: the uploaded file (request.FILES['excel_file'])
    user:     request.user (optional)
    Returns: dict with counts, errors and warnings.
    """
    created_count = 0
    updated_count = 0
    errors = []
    warnings_all = []
    empty_rows = []

    # Read the Excel and clean the column names
    df = pd.read_excel(file_obj, sheet_name=0)
    new_columns = []
    for col in df.columns:
        new_columns.append(_normalise(col))
    df.columns = new_columns
    df = df.dropna(how="all")

    # Stop if a required column is missing
    missing_columns = []
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            missing_columns.append(COLUMN_LABELS[col])
    if len(missing_columns) > 0:
        errors.append("The Excel file is missing these required columns: " +
                      ", ".join(missing_columns) + ". Check that you uploaded the right "
                      "template and that row 1 has the column headers.")
        return {"created": 0, "updated": 0, "errors": errors, "warnings": []}

    for i, row in df.iterrows():
        excel_row_num = i + 2  # header is row 1

        # Leftover rows with no application (e.g. only "Final Response" text)
        if _is_empty_row(row):
            empty_rows.append(excel_row_num)
            continue

        label = _row_label(excel_row_num, row)

        # Required values -> error, row is NOT saved
        missing_values = []
        for col in REQUIRED_COLUMNS:
            if _clean_str(row.get(col)) == "":
                missing_values.append(COLUMN_LABELS[col])
        if len(missing_values) == 1:
            errors.append(label + " — Not saved: " + missing_values[0] +
                          " is empty. Fill in this column and import again.")
            continue
        if len(missing_values) > 1:
            errors.append(label + " — Not saved: " + ", ".join(missing_values) +
                          " are empty. Fill in these columns and import again.")
            continue

        try:
            with transaction.atomic():
                reference_no = _clean_str(row.get("reference_no"))
                if len(reference_no) > 50:
                    raise ValueError("'Reference No' is " + str(len(reference_no)) +
                                     " characters long; the maximum is 50.")

                warnings = []
                _check_row(row, warnings)

                applicant = _get_or_update_applicant(row, user)

                existing = Application.objects.filter(reference_no=reference_no).first()
                closure_date = _parse_date(row.get("closure_date"))
                status = _decide_status(row, existing, closure_date)

                defaults = _application_values(row, applicant, status, closure_date, warnings)

                obj, was_created = Application.objects.update_or_create(
                reference_no=reference_no,
                defaults={
                    **defaults,
                     **({"created_by": user} if user else {}),
                    },
                )

                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

                _save_tac_meeting(row, obj, user)

                for w in warnings:
                    warnings_all.append(label + " — " + w)

        except Exception as e:
            # Nothing from this row is saved (transaction is rolled back)
            errors.append(label + " — Not saved: " + _explain_exception(e))

    if len(empty_rows) > 0:
        warnings_all.append(str(len(empty_rows)) + " rows had no Reference No, Applicant or "
                            "Technology and were skipped (rows " + str(empty_rows[0]) +
                            "-" + str(empty_rows[-1]) + ").")

    return {
        "created": created_count,
        "updated": updated_count,
        "errors": errors,
        "warnings": warnings_all,
    }