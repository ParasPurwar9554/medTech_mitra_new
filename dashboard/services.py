"""
Aggregation/reporting queries for the dashboard.

Kept separate from views.py so the same functions can be:
  - unit-tested independently of HTTP
  - reused by both the HTML dashboard views and any future API/export command
"""
from django.db.models import Avg, Count, F, ExpressionWrapper, DurationField,Q
from django.db.models.functions import TruncMonth
from datetime import date
from django.utils.dateparse import parse_date
from django.db.models import Q, Count
from core.models import Application, KnowledgePartner, TRLDefinition,KnowledgePartnerAssignment,Milestone

def _to_date(text):
    """'2024-03-15' -> date(2024, 3, 15). Returns None if empty or wrong."""
    try:
        return parse_date(text.strip()) if text else None
    except ValueError:
        return None

    
def kpi_summary(queryset=None):
    qs = queryset if queryset is not None else Application.objects.all()
    #total = qs.count()
    in_progress = qs.filter(status=Application.ApplicationStatus.IN_PROGRESS).count()
    closed = qs.filter(status=Application.ApplicationStatus.CLOSED).count()
    completed = qs.filter(status=Application.ApplicationStatus.COMPLTED).count()
    #assign_to_kp = qs.filter(status=Application.ApplicationStatus.ASSIGNED_TO_KP).count()
    assigned_partners = KnowledgePartnerAssignment.objects.values("partner").distinct().count()

    return {
        "total_application":in_progress+closed + completed,
        "in_progress": in_progress,
        "closed":closed,
        "completed":completed
    }


def applications_by_status(queryset=None):
    qs = queryset if queryset is not None else Application.objects.all()
    rows = qs.values("status").annotate(count=Count("id")).order_by("-count")
    label_map = dict(Application.ApplicationStatus.choices)
    return [{"label": label_map.get(r["status"], r["status"]), "value": r["count"]} for r in rows]


def applications_by_medtech_type(queryset=None):
    qs = queryset if queryset is not None else Application.objects.all()
    rows = qs.values("medtech_type").annotate(count=Count("id")).order_by("-count")
    label_map = dict(Application.MedTechType.choices)
    return [{"label": label_map.get(r["medtech_type"], r["medtech_type"]), "value": r["count"]} for r in rows]


def applications_by_risk_class(queryset=None):
    qs = queryset if queryset is not None else Application.objects.all()
    rows = qs.values("risk_classification").annotate(count=Count("id")).order_by("risk_classification")
    label_map = dict(Application.RiskClass.choices)
    return [{"label": label_map.get(r["risk_classification"], r["risk_classification"]), "value": r["count"]} for r in rows]


def applications_by_knowledge_partner():
    rows = (
        KnowledgePartner.objects.annotate(count=Count("assignments", distinct=True))
        .values("short_code", "count")
        .order_by("-count")
    )
    return [{"label": r["short_code"], "value": r["count"]} for r in rows]


def get_base_queryset(user):
    """Return only the applications this user is allowed to see."""
    # Delegated Admin, Secretariat, superuser: all records
    if user.is_superuser or user.is_admin_role or user.is_secretariat:
        return Application.objects.all()

    # Knowledge Partner: only applications assigned to their partner
    if user.is_support_partner and user.knowledge_partner_id:
        return Application.objects.filter(
            trl_stages__partner_assignments__partner_id=user.knowledge_partner_id
        )

    # Innovator: only their own applications
    if user.is_innovator and user.applicant_id:
        print("is_innovator")
        return Application.objects.filter(applicant_id=user.applicant_id)

    # Anyone else: nothing
    return Application.objects.none()

def apply_dashboard_filters(request, include_trl=True):
    qs = get_base_queryset(request.user)
    status = request.GET.get("status")
    medtech_type = request.GET.get("medtech_type")
    risk = request.GET.get("risk_classification")
    partner = request.GET.get("partner")
    date_from = _to_date(request.GET.get("date_from"))
    date_to = _to_date(request.GET.get("date_to"))
    by_reference = request.GET.get("by_reference")
    trl_from = request.GET.get("trl_from", "")
    trl_to = request.GET.get("trl_to", "")

    if by_reference:
        qs = qs.filter(
            Q(reference_no__icontains=by_reference) |
            Q(applicant__name__icontains=by_reference) |
            Q(technology_name__icontains=by_reference)
        )
    if status:
        qs = qs.filter(status=status)
    if medtech_type:
        qs = qs.filter(medtech_type=medtech_type)
    if risk:
        qs = qs.filter(risk_classification=risk)
    if partner:
        qs = qs.filter(trl_stages__partner_assignments__partner__short_code=partner)
    if date_from or date_to:
        received_q = Q()
        meeting_q = Q()
        if date_from:
            received_q &= Q(date_received__gte=date_from)
            meeting_q &= Q(tac_meetings__meeting_date__gte=date_from)
        if date_to:
            received_q &= Q(date_received__lte=date_to)
            meeting_q &= Q(tac_meetings__meeting_date__lte=date_to)

        qs = qs.filter(received_q | meeting_q)

    # TRL filters (skipped when building the movement table)
    if include_trl:
        if trl_from.isdigit():
            qs = qs.filter(initial_trl__level=int(trl_from))
        if trl_to.isdigit():
            qs = qs.filter(current_trl__level=int(trl_to))

    return qs.distinct()

def get_trl_movement(request):
    # All filters EXCEPT the TRL ones
    qs = apply_dashboard_filters(request)

    rows = list(
        qs.filter(initial_trl__isnull=False, current_trl__isnull=False)
          .values("initial_trl__level", "current_trl__level")
          .annotate(total=Count("id", distinct=True))
          .order_by("initial_trl__level", "current_trl__level")
    )

    params = request.GET.copy()
    params.pop("page", None)

    for row in rows:
        row["from_level"] = row["initial_trl__level"]
        row["to_level"] = row["current_trl__level"]
        row["steps"] = row["to_level"] - row["from_level"]

        params["trl_from"] = row["from_level"]
        params["trl_to"] = row["to_level"]
        row["url"] = "?" + params.urlencode()

    return rows


def trlprogresstracker(refnumber):
    application = Application.objects.filter(reference_no=refnumber).first()
    if not application or not application.current_trl:
        return []

    current_level = application.current_trl.level
    initial_level = application.initial_trl.level if application.initial_trl else 1
    partners = KnowledgePartner.objects.filter(is_active=True)

    specific_trls = TRLDefinition.objects.filter(
        medtech_type=application.medtech_type
    ).order_by('level')
    generic_trls = TRLDefinition.objects.filter(
        medtech_type=""
    ).order_by('level')

    trls_by_level = {}
    for trl in generic_trls:
        trls_by_level[trl.level] = trl
    for trl in specific_trls:
        trls_by_level[trl.level] = trl

    all_trls = [trls_by_level[lvl] for lvl in sorted(trls_by_level) if lvl > 0]

    tracker = []
    for trl in all_trls:
        if trl.level < initial_level:
            status = "disabled"
        elif trl.level < current_level:
            status = "completed"
        elif trl.level == current_level:
            status = "current"
        else:
            status = "pending"

        trl_tag = f"TRL-{trl.level}"
        matched_partners = []

        if status != "disabled":
            for partner in partners:
                levels_list = [lvl.strip() for lvl in partner.applicable_trl_levels.split(",") if lvl.strip()]
                if trl_tag not in levels_list:
                    continue

                assignment = KnowledgePartnerAssignment.objects.filter(
                    trl_stage__application=application, partner=partner
                ).order_by('-date_allotted').first()

                milestones_data = []
                if assignment:
                    milestones = Milestone.objects.filter(assignment=assignment).order_by('template__sequence')
                    for m in milestones:
                        milestones_data.append({
                            "label": m.label or (m.template.description if m.template else ""),
                            "is_achieved": m.status == Milestone.MilestoneStatus.CLOSED,
                            "date_achieved": m.date_achieved,
                        })

                matched_partners.append({
                    "short_code": partner.short_code,
                    "name": partner.name,
                    "assignment_status": assignment.get_status_display() if assignment else "Not Assigned",
                    "milestones": milestones_data,
                })

        tracker.append({
            "id": trl.id,
            "level": trl.level,
            "name": trl.name,
            "status": status,
            "partners": matched_partners,
        })

    return tracker




    
