"""
Import Applicant + Application data from the MedTech Mitra master Excel file.

This matches the REAL master_data.xlsx headers (38-column sheet), not a
simplified template. Only Applicant + Application fields are imported in
this first pass — TAC meetings, Knowledge Partner assignments, and Follow-ups
are intentionally skipped for now and can be added in a later import step.

Column headers are normalised (lowercased, punctuation -> underscore) so
minor spelling/spacing differences in the sheet don't break the match.
"""
import re
import pandas as pd

from core.models import Applicant, Application


def _normalise(col):
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    return col.strip("_")


def _clean_str(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).replace("\xa0", " ").strip()


def _parse_date(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        cleaned = str(value).replace("\xa0", " ").strip()
        dt = pd.to_datetime(cleaned, dayfirst=True, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.date()
    except Exception:
        return None


def _map_affiliation_type(value):
    value = _clean_str(value).lower()
    if value == "individual":
        return "individual"
    return "institute"  # startup / company / institute all map here


def _map_risk_class(value):
    value = _clean_str(value).lower()
    if "not known" in value:
        return "unknown"
    match = re.search(r"class\s*([a-d])", value)
    if match:
        return match.group(1).upper()
    return "unknown"


def _map_device_category(value):
    value = _clean_str(value).lower()
    if "investigational" in value:
        return "investigational"
    if "predicate" in value:
        return "predicate"
    return ""


def _map_innovation_stage(value):
    value = _clean_str(value).lower()
    if "concept development" in value:
        return "concept_dev"
    if "concept proved" in value:
        return "concept_proved"
    if "bench testing" in value:
        return "bench_testing"
    if "clinical testing" in value:
        return "clinical_testing"
    if "clinical investigation" in value or "performance evaluation" in value:
        return "clinical_investigation"
    return ""


def _map_medtech_type(value):
    value = _clean_str(value).lower()
    if "diagnostic" in value or "ivd" in value:
        return "MM-IVD"
    if "device" in value:
        return "MM-DD"
    return ""


def _map_test_license(value):
    value = _clean_str(value).lower()
    if value.startswith("y"):
        return True
    if value.startswith("n"):
        return False
    return None


def import_master_excel(file_obj):
    """
    file_obj: the uploaded file (request.FILES['excel_file'])
    Returns: dict with counts and any row-level errors.
    """
    df = pd.read_excel(file_obj)
    df.columns = [_normalise(c) for c in df.columns]

    created_count = 0
    updated_count = 0
    errors = []

    for i, row in df.iterrows():
        excel_row_num = i + 2  # header is row 1
        try:
            reference_no = _clean_str(row.get("application_reference_no"))
            if not reference_no:
                errors.append(f"Row {excel_row_num}: missing reference no, skipped.")
                continue

            # ---- Applicant ----
            applicant_name = _clean_str(row.get("applicant_name")) or "Unknown Applicant"
            applicant, _ = Applicant.objects.update_or_create(
                name=applicant_name,
                defaults={
                    "innovator_name": _clean_str(row.get("innovators_name")),
                    "affiliation_type": _map_affiliation_type(row.get("applicant_type")),
                    "affiliation_name": _clean_str(row.get("company_institute_name")),
                    "email": _clean_str(row.get("email_id")),
                    "contact_number": _clean_str(row.get("contact_number")),
                    "address": _clean_str(row.get("address")),
                },
            )

            # ---- Application ----
            closure_date = _parse_date(row.get("date_of_closure"))
            status = "closed" if closure_date else "in_progress"

            defaults = {
                "applicant": applicant,
                "medtech_type": _map_medtech_type(row.get("medtech_type")),
                "technology_name": _clean_str(row.get("name_of_the_technology")),
                "intended_use_statement": _clean_str(row.get("intended_use_statement")),
                "use_environment": _clean_str(row.get("use_environment")),
                "target_population": _clean_str(row.get("target_population")),
                "area_of_application": _clean_str(row.get("area_of_application")),
                "risk_classification": _map_risk_class(row.get("risk_classification_of_medical_device_ivds")),
                "device_category": _map_device_category(row.get("type_of_medical_device_diagnostic")),
                "innovation_stage": _map_innovation_stage(row.get("stage_of_your_innovation")),
                "support_type_requested": _clean_str(row.get("type_of_support_handholding_needed")),
                "test_license_received": _map_test_license(row.get("test_license_received_yes_no")),
                "query_text": _clean_str(row.get("query")),
                "date_received": _parse_date(row.get("date_of_application_reciept")),
                "status": status,
                "closure_date": closure_date,
            }

            obj, was_created = Application.objects.update_or_create(
                reference_no=reference_no,
                defaults=defaults,
            )

            if was_created:
                created_count += 1
            else:
                updated_count += 1

        except Exception as e:
            errors.append(f"Row {excel_row_num}: {e}")

    return {
        "created": created_count,
        "updated": updated_count,
        "errors": errors,
    }