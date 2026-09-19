"""
Import the legacy MedTech Mitra Excel tracker into the normalized Django schema.

Usage:
    python manage.py import_excel /path/to/Sample_Data_Excel_for_Dashboard.xlsx

The source workbook has 5 sheets:
    Format                  -> one row per application (73 columns, header on row 2)
    KP-TRL                  -> master: which Knowledge Partner handles which TRL levels
    TRL-Milestones DD_AT    -> master: TRL 1-9 definitions + milestone descriptions
    Partners Milestones     -> master: per-partner milestone checklist
    Sheet7                  -> master: additional partner milestone phrasing (reference only)

This command is intentionally defensive: government spreadsheets accumulated
over years have inconsistent date formats, stray whitespace, and blank rows.
Every parse step degrades gracefully (logs a warning, keeps the field blank)
rather than crashing the whole import over one bad cell.
"""
import datetime as dt
import re

import openpyxl
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    Applicant, Application, TACMeeting, KnowledgePartner,
    KnowledgePartnerAssignment, TRLDefinition, PartnerMilestoneTemplate, Milestone,
    TRLProgressLog,
)

DATE_RE_SLASH = re.compile(r"^\s*(\d{1,2})[./](\d{1,2})[./](\d{2,4})\s*$")


def parse_date(value):
    """Best-effort date parser for the mixed formats found in the sheet
    (Excel dates, 'dd/mm/yyyy', 'dd.mm.yyyy', with stray trailing spaces)."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        s = value.strip()
        m = DATE_RE_SLASH.match(s)
        if m:
            d, mo, y = m.groups()
            y = int(y)
            if y < 100:
                y += 2000
            try:
                return dt.date(y, int(mo), int(d))
            except ValueError:
                return None
    return None


def clean_str(value):
    if value is None:
        return ""
    return str(value).strip()


def parse_bool(value):
    s = clean_str(value).lower()
    if s in ("yes", "y", "true", "1"):
        return True
    if s in ("no", "n", "false", "0"):
        return False
    return None


MEDTECH_TYPE_MAP = {
    "medical device": Application.MedTechType.DEVICE,
    "in-vitro diagnostic": Application.MedTechType.IVD,
}

RISK_MAP = {
    "class a": Application.RiskClass.CLASS_A,
    "class b": Application.RiskClass.CLASS_B,
    "class c": Application.RiskClass.CLASS_C,
    "class d": Application.RiskClass.CLASS_D,
    "not known": Application.RiskClass.UNKNOWN,
}

DEVICE_CATEGORY_MAP = {
    "medical device or ivd having available predicate device": Application.DeviceCategory.PREDICATE,
    "medical device/ivd having available predicate device": Application.DeviceCategory.PREDICATE,
    "investigational medical device/ivd": Application.DeviceCategory.INVESTIGATIONAL,
}

STAGE_MAP = {
    "under concept development": Application.InnovationStage.CONCEPT_DEV,
    "concept proved with prototype": Application.InnovationStage.CONCEPT_PROVED,
    "bench testing of prototypes for manufacturing testing": Application.InnovationStage.BENCH_TESTING,
    "clinical testing for national program": Application.InnovationStage.CLINICAL_TESTING,
    "clinical investigation/performance/clinical performance evaluation": Application.InnovationStage.CLINICAL_INVESTIGATION,
}

SUPPORT_CATEGORY_MAP = {
    "advisory": Application.SupportCategory.ADVISORY,
    "handholding": Application.SupportCategory.HANDHOLDING,
}


def fuzzy_map(raw, mapping, default=""):
    key = clean_str(raw).lower()
    if not key:
        return default
    if key in mapping:
        return mapping[key]
    for k, v in mapping.items():
        if key.startswith(k[:20]):
            return v
    return default


def normalize_partner_name(name):
    """Collapse whitespace/dash variants like 'ICMR- INTENT' vs 'ICMR-INTENT'
    vs 'ICMR-INTENT, DHR-HTA' so we don't create duplicate partner rows."""
    return re.sub(r"\s+", " ", clean_str(name)).strip(" -,")


def get_or_create_partner(raw_name, partners_cache):
    """Look up (or create) a KnowledgePartner by name, reusing a cache keyed
    by normalized lowercase name to avoid short_code collisions across rows."""
    name = normalize_partner_name(raw_name)
    if not name:
        return None
    key = name.lower()
    if key in partners_cache:
        return partners_cache[key]

    base_code = re.sub(r"[^A-Za-z0-9]+", "", name)[:20].upper() or name[:20].upper()
    code = base_code
    suffix = 1
    while KnowledgePartner.objects.filter(short_code=code).exclude(name=name).exists():
        suffix += 1
        code = f"{base_code[:17]}{suffix}"

    partner, _ = KnowledgePartner.objects.get_or_create(
        name=name, defaults={"short_code": code}
    )
    partners_cache[key] = partner
    partners_cache[partner.short_code.lower()] = partner
    return partner


class Command(BaseCommand):
    help = "Import the legacy MedTech Mitra Excel tracker into the database."

    def add_arguments(self, parser):
        parser.add_argument("excel_path", type=str)
        parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing to DB")

    def handle(self, *args, **options):
        path = options["excel_path"]
        dry_run = options["dry_run"]
        wb = openpyxl.load_workbook(path, data_only=True)

        with transaction.atomic():
            partners = self.import_knowledge_partners(wb)
            trl_defs = self.import_trl_definitions(wb)
            self.import_partner_milestones(wb, partners)
            created, skipped = self.import_applications(wb, partners, trl_defs)

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING(
                    f"DRY RUN — would create {created} applications ({skipped} rows skipped). No changes saved."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"Imported {created} applications ({skipped} rows skipped)."
                ))

    # -- Master data -----------------------------------------------------

    def import_knowledge_partners(self, wb):
        partners = {}
        if "KP-TRL" not in wb.sheetnames:
            return partners
        ws = wb["KP-TRL"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue
            name = normalize_partner_name(row[0])
            trl_applicable = clean_str(row[1]) if len(row) > 1 else ""
            short_code = re.sub(r"[^A-Za-z0-9]+", "", name.split("-")[0]).upper()[:20] or name[:20].upper()
            partner, _ = KnowledgePartner.objects.update_or_create(
                name=name,
                defaults={"short_code": short_code, "applicable_trl_levels": trl_applicable},
            )
            partners[name.lower()] = partner
            partners[short_code.lower()] = partner
        self.stdout.write(f"  Knowledge partners: {len(set(p.pk for p in partners.values()))}")
        return partners

    def import_trl_definitions(self, wb):
        trl_defs = {}
        if "TRL-Milestones DD_AT" not in wb.sheetnames:
            return trl_defs
        ws = wb["TRL-Milestones DD_AT"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue
            label = clean_str(row[0])  # e.g. "TRL-1 Ideation"
            m = re.match(r"TRL-(\d+)", label)
            if not m:
                continue
            level = int(m.group(1))
            m1 = clean_str(row[1]) if len(row) > 1 else ""
            m2 = clean_str(row[2]) if len(row) > 2 else ""
            m3 = clean_str(row[3]) if len(row) > 3 else ""
            trl, _ = TRLDefinition.objects.update_or_create(
                level=level,
                defaults={"name": label, "milestone_1": m1, "milestone_2": m2, "milestone_3": m3},
            )
            trl_defs[level] = trl
        self.stdout.write(f"  TRL definitions: {len(trl_defs)}")
        return trl_defs

    def import_partner_milestones(self, wb, partners):
        if "Partners Milestones" not in wb.sheetnames:
            return
        ws = wb["Partners Milestones"]
        count = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[1]:
                continue
            partner = get_or_create_partner(row[1], partners)
            if not partner:
                continue
            milestones = [clean_str(c) for c in row[2:] if clean_str(c)]
            for i, desc in enumerate(milestones, start=1):
                PartnerMilestoneTemplate.objects.update_or_create(
                    partner=partner, sequence=i, defaults={"description": desc[:255]}
                )
                count += 1
        self.stdout.write(f"  Partner milestone templates: {count}")

    # -- Applications -----------------------------------------------------

    def import_applications(self, wb, partners, trl_defs):
        ws = wb["Format"]
        header_row = 2
        headers = [clean_str(c.value) for c in ws[header_row]]

        def col(name_fragment):
            """Find a column index whose header contains this fragment (case-insensitive,
            whitespace-normalised) — resilient to the sheet's inconsistent line breaks."""
            frag = name_fragment.lower()
            for i, h in enumerate(headers):
                if frag in re.sub(r"\s+", " ", h.lower()):
                    return i
            return None

        idx = {
            "sno": col("s.no"),
            "ref_no": col("application reference"),
            "applicant_name": col("applicant name"),
            "affiliation": col("company/ institute name"),
            "innovator_name": col("innovators name"),
            "email": col("email id"),
            "contact": col("contact number"),
            "medtech_type": col("medtech type"),
            "tech_name": col("name of the technology"),
            "intended_use": col("intended use statement"),
            "use_env": col("use environment"),
            "target_pop": col("target population"),
            "area": col("area of application"),
            "risk": col("risk classification"),
            "device_cat": col("type of medical device/diagnostic"),
            "stage": col("stage of your innovation"),
            "support_needed": col("type of support"),
            "test_license": col("test license received"),
            "query": col("query"),
            "address": col("address"),
            "date_received": col("date of application reciept"),
            "tac_ref": col("tac meeting ref"),
            "tac_date": col("tac meeting date"),
            "first_meeting_outcome": col("first meeting (outcome)"),
            "final_tac_response": col("final response"),
            "action_taken": col("action taken"),
            "kp_assigned": col("assigned knowledge partner"),
            "closure_date": col("date of closure"),
            "kp_responsible": col("knowledge partner responsible"),
            "kp_date_allotted": col("date of application allotment"),
            "support_category": col("support category"),
            "kp_connected": col("knowledge partner connected"),
            "kp_connected_date": col("knowledge partner connected date"),
            "support_provided": col("support provided"),
            "support_status": col("support status"),
            "initial_trl": col("initial trl"),
            "initial_trl_date": col("initial trl date"),
            "current_trl": col("current trl"),
            "current_trl_date": col("current trl date"),
            "final_trl": col("final trl"),
            "final_status": col("final status of application"),
            "final_status_date": col("final status date"),
        }

        def get(row, key):
            i = idx.get(key)
            if i is None or i >= len(row):
                return None
            return row[i]

        def trl_from_text(text):
            s = clean_str(text)
            m = re.search(r"TRL[-\s]?(\d+)", s, re.IGNORECASE)
            if m:
                return trl_defs.get(int(m.group(1)))
            return None

        created, skipped = 0, 0
        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            ref_no = clean_str(get(row, "ref_no"))
            applicant_name = clean_str(get(row, "applicant_name"))
            if not ref_no or not applicant_name:
                skipped += 1
                continue

            email = clean_str(get(row, "email"))
            affiliation_name = clean_str(get(row, "affiliation"))
            applicant_lookup = {"email": email} if email else {"name": applicant_name}
            applicant, _ = Applicant.objects.update_or_create(
                **applicant_lookup,
                defaults=dict(
                    name=applicant_name,
                    innovator_name=clean_str(get(row, "innovator_name")),
                    affiliation_name=affiliation_name,
                    affiliation_type="individual" if affiliation_name.lower() in ("", "individual") else "institute",
                    contact_number=clean_str(get(row, "contact")),
                    address=clean_str(get(row, "address")),
                ),
            )

            final_status_text = clean_str(get(row, "final_status"))
            closure_date = parse_date(get(row, "closure_date"))
            if closure_date and final_status_text:
                status = Application.ApplicationStatus.CLOSED_SUCCESS \
                    if "concluded" in final_status_text.lower() or "success" in final_status_text.lower() \
                    else Application.ApplicationStatus.CLOSED_OTHER
            elif clean_str(get(row, "kp_assigned")):
                status = Application.ApplicationStatus.IN_PROGRESS
            elif clean_str(get(row, "tac_ref")):
                status = Application.ApplicationStatus.UNDER_TAC_REVIEW
            else:
                status = Application.ApplicationStatus.RECEIVED

            application, _ = Application.objects.update_or_create(
                reference_no=ref_no,
                defaults=dict(
                    applicant=applicant,
                    medtech_type=fuzzy_map(get(row, "medtech_type"), MEDTECH_TYPE_MAP, Application.MedTechType.DEVICE),
                    technology_name=clean_str(get(row, "tech_name")),
                    intended_use_statement=clean_str(get(row, "intended_use")),
                    use_environment=clean_str(get(row, "use_env")),
                    target_population=clean_str(get(row, "target_pop")),
                    area_of_application=clean_str(get(row, "area")),
                    risk_classification=fuzzy_map(get(row, "risk"), RISK_MAP, Application.RiskClass.UNKNOWN),
                    device_category=fuzzy_map(get(row, "device_cat"), DEVICE_CATEGORY_MAP, ""),
                    innovation_stage=fuzzy_map(get(row, "stage"), STAGE_MAP, ""),
                    support_type_requested=clean_str(get(row, "support_needed")),
                    support_category=fuzzy_map(get(row, "support_category"), SUPPORT_CATEGORY_MAP, ""),
                    test_license_received=parse_bool(get(row, "test_license")),
                    query_text=clean_str(get(row, "query")),
                    date_received=parse_date(get(row, "date_received")),
                    status=status,
                    initial_trl=trl_from_text(get(row, "initial_trl")),
                    initial_trl_date=parse_date(get(row, "initial_trl_date")),
                    current_trl=trl_from_text(get(row, "current_trl")) or trl_from_text(get(row, "initial_trl")),
                    current_trl_date=parse_date(get(row, "current_trl_date")),
                    final_trl=trl_from_text(get(row, "final_trl")),
                    final_status_notes=final_status_text,
                    final_status_date=parse_date(get(row, "final_status_date")),
                    closure_date=closure_date,
                ),
            )

            # TAC meeting
            if clean_str(get(row, "tac_ref")) or parse_date(get(row, "tac_date")):
                TACMeeting.objects.update_or_create(
                    application=application,
                    meeting_ref_no=clean_str(get(row, "tac_ref")),
                    defaults=dict(
                        meeting_date=parse_date(get(row, "tac_date")),
                        first_meeting_outcome=clean_str(get(row, "first_meeting_outcome")),
                        final_tac_response=clean_str(get(row, "final_tac_response")),
                        action_taken=clean_str(get(row, "action_taken")),
                    ),
                )

            # Knowledge partner assignment
            kp_name = clean_str(get(row, "kp_assigned")) or clean_str(get(row, "kp_responsible"))
            if kp_name:
                # sheet sometimes lists multiple partners comma/slash separated; take first
                first_name = re.split(r"[,/]", kp_name)[0].strip()
                partner = get_or_create_partner(first_name, partners)
                if partner:
                    KnowledgePartnerAssignment.objects.update_or_create(
                        application=application, partner=partner,
                        defaults=dict(
                            date_allotted=parse_date(get(row, "kp_date_allotted")),
                            date_connected=parse_date(get(row, "kp_connected_date")),
                            support_provided=clean_str(get(row, "support_provided")),
                            status=(
                                KnowledgePartnerAssignment.SupportStatus.COMPLETED
                                if closure_date else
                                KnowledgePartnerAssignment.SupportStatus.CONNECTED
                                if clean_str(get(row, "kp_connected"))
                                else KnowledgePartnerAssignment.SupportStatus.REQUESTED
                            ),
                        ),
                    )

            # TRL history: log initial + current as two audit points if present
            if application.initial_trl and application.initial_trl_date:
                TRLProgressLog.objects.update_or_create(
                    application=application, trl=application.initial_trl,
                    changed_on=application.initial_trl_date, defaults={}
                )
            if application.current_trl and application.current_trl_date:
                TRLProgressLog.objects.update_or_create(
                    application=application, trl=application.current_trl,
                    changed_on=application.current_trl_date, defaults={}
                )

            created += 1

        return created, skipped
