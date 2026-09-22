"""
Aggregation/reporting queries for the dashboard.

Kept separate from views.py so the same functions can be:
  - unit-tested independently of HTTP
  - reused by both the HTML dashboard views and any future API/export command
"""
from django.db.models import Avg, Count, F, ExpressionWrapper, DurationField
from django.db.models.functions import TruncMonth
from datetime import date

from core.models import Application, KnowledgePartner, TRLDefinition,KnowledgePartnerAssignment,Milestone


def kpi_summary(queryset=None):
    qs = queryset if queryset is not None else Application.objects.all()
    #total = qs.count()
    in_progress = qs.filter(status=Application.ApplicationStatus.IN_PROGRESS).count()
    closed = qs.filter(status=Application.ApplicationStatus.CLOSED).count()
    resolved = qs.filter(status=Application.ApplicationStatus.RESOLVED).count()
    assign_to_kp = qs.filter(status=Application.ApplicationStatus.ASSIGNED_TO_KP).count()
    assigned_partners = KnowledgePartnerAssignment.objects.values("partner").distinct().count()

    return {
        "total_application":in_progress+closed+resolved+assign_to_kp,
        "in_progress": in_progress + assign_to_kp,
        "closed":closed,
        "resolved":resolved,
        "assign_to_kp":assign_to_kp
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


def trl_distribution():
    """How many applications currently sit at each TRL level — the core
    'technology maturity funnel' chart for a MedTech program dashboard."""
    rows = (
        Application.objects.filter(current_trl__isnull=False)
        .values("current_trl__level", "current_trl__name")
        .annotate(count=Count("id"))
        .order_by("current_trl__level")
    )
    return [{"label": r["current_trl__name"] or f"TRL-{r['current_trl__level']}", "value": r["count"]} for r in rows]


def get_base_queryset(user):
    """Return only the applications this user is allowed to see."""
    # Delegated Admin, Secretariat, superuser: all records
    if user.is_superuser or user.is_admin_role or user.is_secretariat:
        print("IN delegated")
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

def apply_dashboard_filters(request):
    qs = get_base_queryset(request.user)
    status = request.GET.get("status")
    medtech_type = request.GET.get("medtech_type")
    risk = request.GET.get("risk_classification")
    partner = request.GET.get("partner")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    by_reference = request.GET.get("by_reference")

    if by_reference:
        qs = qs.filter(reference_no__icontains=by_reference)
    if status:
        qs = qs.filter(status=status)
    if medtech_type:
        qs = qs.filter(medtech_type=medtech_type)
    if risk:
        qs = qs.filter(risk_classification=risk)
    if partner:
        qs = qs.filter(trl_stages__partner_assignments__partner__short_code=partner)
    if date_from:
        qs = qs.filter(date_received__gte=date_from)
    if date_to:
        qs = qs.filter(date_received__lte=date_to)
    return qs.distinct()


def build_progress_tree(application):
    trl_logs = sorted(application.trl_history.all(), key=lambda log: log.changed_on)
    assignments = sorted(
        KnowledgePartnerAssignment.objects.filter(trl_stage__application=application),
        key=lambda a: a.date_allotted or a.created_at.date(),
    )

    def milestones_for(a):
        return sorted(a.milestones.all(), key=lambda m: m.date_achieved or date.max)

    columns = []
    used_ids = set()

    for i, log in enumerate(trl_logs):
        next_date = trl_logs[i + 1].changed_on if i + 1 < len(trl_logs) else None
        cycle_assignments = [
            a for a in assignments
            if (a.date_allotted or a.created_at.date()) >= log.changed_on
            and (next_date is None or (a.date_allotted or a.created_at.date()) < next_date)
        ]
        used_ids.update(a.id for a in cycle_assignments)
        columns.append({
            "trl": log.trl,
            "done": True,
            "current": application.current_trl_id == log.trl_id and i == len(trl_logs) - 1,
            "assignments": [
                {"assignment": a, "done": a.status == "completed", "milestones": milestones_for(a)}
                for a in cycle_assignments
            ],
        })

    if application.current_trl and (not trl_logs or trl_logs[-1].trl_id != application.current_trl_id):
        remaining = [a for a in assignments if a.id not in used_ids]
        columns.append({
            "trl": application.current_trl,
            "done": True,
            "current": True,
            "assignments": [
                {"assignment": a, "done": a.status == "completed", "milestones": milestones_for(a)}
                for a in remaining
            ],
        })

    return columns


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




    
