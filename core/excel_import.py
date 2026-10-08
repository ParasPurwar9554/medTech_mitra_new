"""
Import data from the MedTech Mitra master Excel file (Sheet1, 56 columns).

What gets saved:
  - Applicant        (name, innovator, type, company, email, phone, address)
  - Application      (all application, test-license, closure, KP and
                      follow-up-call columns)
  - TACMeeting       (TAC ref no, meeting date, first response, response
                      date, final response, action taken)
  - StatusChangeLog  (when the status changes on re-import)

Column headers are normalised (lowercased, punctuation -> underscore), so
"Date of Closure" becomes "date_of_closure" etc.
"""
import datetime
import re

import pandas as pd
from django.db import transaction

from core.models import Applicant, Application, StatusChangeLog, TACMeeting


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
    """Lowercase + remove ALL spaces/newlines. Used only for matching."""
    return re.sub(r"\s+", "", _clean_str(value).lower())


def _get(row, *names):
    """
    Return the first column that exists and has a value.
    Useful when a column was renamed in a newer sheet, e.g.
    'Email Id' (old) vs 'Email Id (RED-Email Failed)' (new).
    """
    for name in names:
        value = row.get(name)
        if not _is_empty(value) and _clean_str(value) != "":
            return value
    return None


def _first_email(value):
    """'a@x.com, b@x.com' -> 'a@x.com' (EmailField holds only one)."""
    text = _clean_str(value)
    return re.split(r"[,;/\s]+", text)[0] if text else ""


def _phone(value):
    """'9633474969 \\n9448908617' -> '9633474969, 9448908617'"""
    parts = re.split(r"[\s,;/]+", _clean_str(value))
    return ", ".join(p for p in parts if p)


def _yes_no(value):
    """'Yes' -> True, 'No' / 'No ' / 'Np' -> False, blank -> None."""
    value = _simple(value)
    if value.startswith("y"):
        return True
    if value.startswith("n"):
        return False
    return None


# ---------------------------------------------------------------------------
# STEP 2: Date parsing
# ---------------------------------------------------------------------------

def _parse_date(value):
    """
    The sheet has these kinds of dates:
      1. Text with slash:  '26/12/2023', '13/3/2024', '31/1//2024'
      2. Text with dot:    '16.02.2024', '30.09.26', '1.10.26'
         (typos like '12:08.2025' and '26.02,2026' are also fixed)
      3. Two dates in one cell: '22/09/2026; 30/09/2026' or
         '10.02.2026, 26.02.2026' -> we take the LAST (latest) one
      4. Real Excel dates: Excel wrongly read '03/02/2024' as 2 March 2024
         (month and day swapped). So we swap them back -> 3 Feb 2024.
    Returns None if it is not a date.
    """
    if _is_empty(value):
        return None

    # Case 4: real Excel date cell -> swap day and month back
    if isinstance(value, (datetime.datetime, datetime.date, pd.Timestamp)):
        if value.day <= 12:
            return datetime.date(value.year, value.day, value.month)
        return datetime.date(value.year, value.month, value.day)

    # Find every "day ? month ? year" piece in the text, keep the LAST one
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

def _map_affiliation_type(value):
    if _simple(value) == "individual":
        return "individual"
    return "institute"  # startup / company / institute


def _map_risk_class(value):
    match = re.search(r"class([a-d])", _simple(value))
    if match:
        return match.group(1).upper()
    return Application.RiskClass.UNKNOWN


def _map_device_category(value):
    # Check "investigational" FIRST, because that text also has "predicate"
    value = _simple(value)
    if "investigational" in value:
        return Application.DeviceCategory.INVESTIGATIONAL
    if "predicate" in value:
        return Application.DeviceCategory.PREDICATE
    return ""


def _map_innovation_stage(value):
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
    # "Medical Device" and "In-vitro Diagnostic" both -> MM-DD
    value = _simple(value)
    if "vaccine" in value or "therapeutic" in value:
        return Application.MedTechType.MMVT
    if "assistive" in value or "assitive" in value:
        return Application.MedTechType.MMAT
    return Application.MedTechType.MMDD


def _decide_status(row, existing, closure_date):
    """
    'Final Status' = Closed  (or a closure date)  -> closed
    'Status'       = completed                    -> completed
    otherwise keep what staff already set in the app, or in_progress for new.
    """
    S = Application.ApplicationStatus
    if _simple(row.get("final_status")) == "closed" or closure_date:
        return S.CLOSED
    if _simple(row.get("status")) == "completed":
        return S.COMPLTED
    if _simple(row.get("status")) == "closed":
        return S.CLOSED
    if existing:
        return existing.status
    return S.IN_PROGRESS


# ---------------------------------------------------------------------------
# STEP 4: Applicant lookup (case-insensitive name)
# ---------------------------------------------------------------------------

def _get_or_update_applicant(row, user=None):
    name = _clean_str(row.get("applicant_name")) or "Unknown Applicant"
    data = {
        "innovator_name": _clean_str(row.get("innovators_name")),
        "affiliation_type": _map_affiliation_type(row.get("applicant_type")),
        "affiliation_name": _clean_str(row.get("company_institute_name")),
        "email": _first_email(_get(row, "email_id_red_email_failed", "email_id")),
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


# ---------------------------------------------------------------------------
# STEP 5: Build all Application values from one row
# ---------------------------------------------------------------------------

def _application_values(row, applicant, status, closure_date):
    # 'Date of Interaction/Status' can be a date OR some text
    interaction_raw = row.get("date_of_interaction_status")
    interaction_date = _parse_date(interaction_raw)

    values = {
        "applicant": applicant,
        "allocated_to": _clean_str(row.get("alocation")),

        # --- Technology details ---
        "medtech_type": _map_medtech_type(row.get("medtech_type")),
        "technology_name": _clean_str(row.get("name_of_the_technology")),
        "intended_use_statement": _clean_str(row.get("intended_use_statement")),
        "use_environment": _clean_str(row.get("use_environment")).replace("_", "/"),
        "target_population": _clean_str(row.get("target_population")),
        "area_of_application": _clean_str(row.get("area_of_application")),
        "risk_classification": _map_risk_class(
            row.get("risk_classification_of_medical_device_ivds")),
        "device_category": _map_device_category(
            row.get("type_of_medical_device_diagnostic")),
        "summary_Technology": _clean_str(row.get("summary_of_the_technology")),
        "innovation_stage": _map_innovation_stage(row.get("stage_of_your_innovation")),
        "support_type_requested": _clean_str(row.get("type_of_support_handholding_needed")),

        # --- Test license ---
        "test_license_received": _yes_no(row.get("test_license_received_yes_no")),
        "test_license_status": _clean_str(row.get("test_license")),
        "tl_facilitated": _clean_str(row.get("tl_facilitated_to_be_updated_by_aarti_sahu")),
        "test_license_copy_link": _clean_str(row.get("test_license_copy_link")),

        # --- Other info ---
        "sorting_remarks": _clean_str(row.get("remarks_temp_for_sorting")),
        "financial_opportunities": _clean_str(row.get("financial_opportunities")),
        "query_text": _clean_str(row.get("query")),
        "date_received": _parse_date(row.get("date_of_application_reciept")),

        # --- Status / closure ---
        "status": status,
        "closure_date": closure_date,
        "tac_closed_in": _clean_str(row.get("tac_no_in_which_the_application_was_closed")),
        "reason_for_closure": _clean_str(
            _get(row, "remarks_reason", "recorded_reason_for_closure")),

        # --- Knowledge partner (stored as text for now) ---
        "kp_assigned_screening": _clean_str(row.get("kp_assigned_for_initial_screening_advisory")),
        "kp_assigned_handholding": _clean_str(row.get("kp_assigned_for_handholding")),
        "remarks": _clean_str(row.get("remarks")),

        # --- Follow-up call ---
        "need_to_call": _yes_no(row.get("need_to_call_yes_no")),
        "date_of_interaction": interaction_date,
        "interaction_status_text": "" if interaction_date else _clean_str(interaction_raw),
        "current_technology_stage": _clean_str(row.get("current_stage_of_your_technology")),
        "support_given": _yes_no(row.get("whether_the_asked_support_is_given_to_you_yes_no")),
        "still_working_on_technology": _yes_no(
            row.get("does_the_innovator_still_working_on_the_technology_yes_no")),
        "requires_support": _yes_no(
            row.get("whether_they_require_any_suport_from_medtech_mitra_yes_no")),
        "informed_can_come_back": _yes_no(row.get(
            "infomed_that_they_can_come_back_and_seek_help_at_any_stage_and_since_you_do_not_need_help_right_now")),
        "kind_of_support_required": _clean_str(row.get("kind_of_support_required")),
        "closure_remarks": _clean_str(row.get("closure_reason_remarks_if_any")),
    }

    # Closure reason also goes to final_status_notes / final_status_date
    if values["reason_for_closure"]:
        values["final_status_notes"] = values["reason_for_closure"]
        values["final_status_date"] = closure_date

    return values


# ---------------------------------------------------------------------------
# STEP 6: TAC meeting (one per application + TAC ref no)
# ---------------------------------------------------------------------------

def _save_tac_meeting(row, application, user=None):
    first_resp_raw = row.get("first_responce_meeting_slot_additional_info")
    first_resp_date = _parse_date(first_resp_raw)

    data = {
        "meeting_date": _parse_date(row.get("tac_meeting_date")),
        "first_response_date": first_resp_date,
        # if 'First Response' is text (not a date) keep it as outcome
        "first_meeting_outcome": "" if first_resp_date else _clean_str(first_resp_raw),
        "response_emailed_date": _parse_date(row.get("date_of_response")),
        "final_tac_response": _clean_str(row.get("final_response")),
        "action_taken": _clean_str(row.get("action_taken")),
    }
    ref_no = _clean_str(row.get("tac_meeting_ref_no"))

    # Nothing about TAC in this row -> skip
    if not ref_no and not any(data.values()):
        return

    tac = TACMeeting.objects.filter(application=application, meeting_ref_no=ref_no).first()
    if tac is None:
        TACMeeting.objects.create(
            application=application, meeting_ref_no=ref_no, created_by=user, **data
        )
        return

    for field, value in data.items():
        if value not in ("", None):
            setattr(tac, field, value)
    tac.save()


# ---------------------------------------------------------------------------
# STEP 7: Main import function
# ---------------------------------------------------------------------------

def import_master_excel(file_obj, user=None):
    """
    file_obj: the uploaded file (request.FILES['excel_file'])
    user:     request.user (optional)
    Returns: dict with counts and any row-level errors.
    """
    df = pd.read_excel(file_obj, sheet_name=0)
    df.columns = [_normalise(c) for c in df.columns]
    df = df.dropna(how="all")

    if "application_reference_no" not in df.columns:
        return {
            "created": 0,
            "updated": 0,
            "errors": ["Column 'Application reference no.' not found. Wrong file?"],
        }

    created_count = 0
    updated_count = 0
    errors = []

    for i, row in df.iterrows():
        excel_row_num = i + 2  # header is row 1
        try:
            with transaction.atomic():
                reference_no = _clean_str(row.get("application_reference_no"))
                if not reference_no:
                    errors.append(f"Row {excel_row_num}: missing reference no, skipped.")
                    continue

                applicant = _get_or_update_applicant(row, user)

                existing = Application.objects.filter(reference_no=reference_no).first()
                closure_date = _parse_date(row.get("date_of_closure"))
                status = _decide_status(row, existing, closure_date)

                defaults = _application_values(row, applicant, status, closure_date)

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
                    if existing.status != status:
                        StatusChangeLog.objects.create(
                            application=obj,
                            from_status=existing.status,
                            to_status=status,
                            remarks="Changed by Excel import",
                            created_by=user,
                        )

                _save_tac_meeting(row, obj, user)

        except Exception as e:
            errors.append(f"Row {excel_row_num}: {e}")

    return {
        "created": created_count,
        "updated": updated_count,
        "errors": errors,
    }