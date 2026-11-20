from django.db.models import (Avg,Count,F,ExpressionWrapper,DurationField,Q,Exists,OuterRef,)
from django.db.models.functions import TruncMonth
from datetime import date
from django.utils.dateparse import parse_date
from django.db.models import Q, Count
from core.models import Application

def _to_date(text):
    """'2024-03-15' -> date(2024, 3, 15). Returns None if empty or wrong."""
    try:
        return parse_date(text.strip()) if text else None
    except ValueError:
        return None

def kpi_summary(queryset=None):
    qs = queryset if queryset is not None else Application.objects.all()
    total = qs.count()
    in_progress = qs.filter(status=Application.ApplicationStatus.IN_PROGRESS).count()
    closed = qs.filter(status=Application.ApplicationStatus.CLOSED).count()
    completed = qs.filter(status=Application.ApplicationStatus.COMPLTED).count()
    return {
        "total_application": total,
        "in_progress": in_progress,
        "closed": closed,
        "completed": completed,
    }


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


def get_user_applications(user):
    qs = Application.objects.all()

    # Superuser and Admin roles can see all applications
    if user.is_superuser or user.role in (
        user.Role.DELEGATED_ADMIN,
        user.Role.MM_SECRETARIAT,
    ):
        return qs

    # Support Partner
    if user.role == user.Role.SUPPORT_PARTNER:

        if not user.knowledge_partner_id:
            return qs.none()

        return qs.filter(
            quantifiable_deliverables__knowledge_partner_id=
            user.knowledge_partner_id
        ).distinct()

    # Innovator
    if user.role == user.Role.INNOVATOR:

        if not user.applicant_id:
            return qs.none()

        return qs.filter(
            applicant_id=user.applicant_id
        )

    return qs.none()


def apply_dashboard_filters(
    request,
    include_trl=True,
    include_medtech_type=True,
    include_subtype=False,
    include_support=False,
    include_deliverable=False,
):
    qs = get_user_applications(request.user)

    status = request.GET.get("status", "")
    medtech_type = request.GET.get("medtech_type", "")
    subtype = request.GET.get("subtype", "")
    risk = request.GET.get("risk_classification", "")
    partner = request.GET.get("partner", "")
    support_option = request.GET.get("support_option", "")
    deliverable = request.GET.get("deliverable", "")
    date_from = _to_date(request.GET.get("date_from"))
    date_to = _to_date(request.GET.get("date_to"))
    by_reference = request.GET.get("by_reference", "").strip()
    trl_from = request.GET.get("trl_from", "")
    trl_to = request.GET.get("trl_to", "")

    if by_reference:
        qs = qs.filter(
            Q(reference_no__icontains=by_reference) |
            Q(applicant__name__icontains=by_reference) |
            Q(technology_name__icontains=by_reference)
        )

    if status:
        valid_statuses = dict(Application.ApplicationStatus.choices)

        if status in valid_statuses:
            qs = qs.filter(status=status)

    if include_medtech_type and medtech_type:
        valid_types = dict(Application.MedTechType.choices)

        if medtech_type in valid_types:
            qs = qs.filter(medtech_type=medtech_type)

    if include_subtype and subtype:
        valid_subtypes = dict(Application.MedTechSubCategoryType.choices)

        if subtype in valid_subtypes:
            qs = qs.filter(medtech_subtype=subtype)

    if risk:
        valid_risks = dict(Application.RiskClass.choices)

        if risk in valid_risks:
            qs = qs.filter(risk_classification=risk)

    if partner:
        qs = qs.filter(trl_stages__partner_assignments__partner__short_code=partner)

    if include_support and support_option.isdigit():
        qs = qs.filter(
            application_supports__support_option_id=int(support_option)
        )

    if include_deliverable and deliverable.isdigit():
        qs = qs.filter(
            quantifiable_deliverables__quantifiable_deliverable_id=int(
                deliverable
            )
        )

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

    if include_trl:
        if trl_from.isdigit():
            qs = qs.filter(initial_trl__level=int(trl_from))

        if trl_to.isdigit():
            qs = qs.filter(current_trl__level=int(trl_to))

    return qs.distinct()


def category_wise_portfolio(queryset=None):
    qs = queryset if queryset is not None else Application.objects.all()
    category_data = []
    for category_value, category_label in Application.MedTechType.choices:
        category_qs = qs.filter(medtech_type=category_value)
        received = category_qs.count()
        tac_discussed = category_qs.filter(tac_meetings__isnull=False).distinct().count()
        closed = category_qs.filter(status=Application.ApplicationStatus.CLOSED).count()

        completed = category_qs.filter(status=Application.ApplicationStatus.COMPLTED).count()
        in_process = category_qs.filter(status=Application.ApplicationStatus.IN_PROGRESS).count()

        closed_percent = closed / received * 100 if received else 0
        completed_percent = completed / received * 100 if received else 0
        progress_percent = in_process / received * 100 if received else 0

        category_data.append({
            "value": category_value,
            "name": category_label,
            "received": received,
            "tac_discussed": tac_discussed,
            "closed": closed,
            "completed": completed,
            "in_process": in_process,
            "closed_percent": round(closed_percent, 2),
            "completed_percent": round(completed_percent, 2),
            "progress_percent": round(progress_percent, 2),
        })

    return category_data





    
