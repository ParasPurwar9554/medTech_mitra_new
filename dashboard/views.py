from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.http import JsonResponse
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.core.paginator import Paginator

from core.models import Application, KnowledgePartner,KnowledgePartnerAssignment,Milestone,TRLDefinition,ApplicationTRLStage
from . import services

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.db.models import Count,F,Prefetch
from core.models import PartnerMilestoneTemplate
from core.models import TRLProgressLog


@login_required
def overview(request):
    qs = services.apply_dashboard_filters(request)
    applications_qs = qs.select_related("applicant","current_trl").order_by(F("date_received").desc(nulls_last=True),"-id",)
    paginator = Paginator(applications_qs, 100)
    page_number = request.GET.get("page", 1)
    applications_page = paginator.get_page(page_number)
    trl_qs = (
        ApplicationTRLStage.objects
        .values('trl__level', 'trl__name')
        .annotate(value=Count('application', distinct=True))
        .order_by('trl__level')
    )
    trl_data = [
        {"level": row["trl__level"], "label": row["trl__name"], "value": row["value"]}
        for row in trl_qs
    ]

    # --- TRL definitions for the Assign KP modal dropdowns ---
    all_trls_qs = TRLDefinition.objects.filter(level__gt=0).order_by("level")
    trl_definitions = list(all_trls_qs.values("id", "name", "medtech_type"))

    context = {
        "kpis": services.kpi_summary(qs),
        "status_data": services.applications_by_status(qs),
        "medtech_type_data": services.applications_by_medtech_type(qs),
        "risk_data": services.applications_by_risk_class(qs),
        "partner_data": services.applications_by_knowledge_partner(),
        "trl_data": trl_data,
        "trl_levels": [row["level"] for row in trl_data],
        "status_choices": Application.ApplicationStatus.choices,
        "medtech_type_choices": Application.MedTechType.choices,
        "risk_choices": Application.RiskClass.choices,
        "partners": KnowledgePartner.objects.filter(is_active=True),
        "active_filters": request.GET,
        "applications": applications_page,
        "all_trls": all_trls_qs,
        "trl_definitions_json": trl_definitions,
    }
    return render(request, "dashboard/overview.html", context)


@login_required
def application_detail(request, pk):
    application = get_object_or_404(
        Application.objects.select_related("applicant", "current_trl", "initial_trl", "final_trl")
       .prefetch_related(
    "tac_meetings",
    "trl_stages__partner_assignments__partner",
    "trl_stages__partner_assignments__follow_ups",
    "trl_stages__partner_assignments__milestones",
    "trl_history__trl",
    "status_history",
),
        pk=pk,
    )
    context = {
        "application": application,
        "columns": services.build_progress_tree(application),
        "trl_tracker": services.trlprogresstracker(application.reference_no),
    }
    return render(request, "dashboard/application_detail.html", context)


@login_required
def chart_data_api(request, chart_name):
    """Lightweight JSON endpoint the frontend re-polls when filters change,
    without a full page reload (used by the dashboard's AJAX filter bar)."""
    qs = services.apply_dashboard_filters(request)
    dispatch = {
        "status": services.applications_by_status,
        "medtech_type": services.applications_by_medtech_type,
        "risk": services.applications_by_risk_class,
        "trl": lambda _qs=None: services.trl_distribution(),
        "partner": lambda _qs=None: services.applications_by_knowledge_partner(),
        "kpis": services.kpi_summary,
    }
    fn = dispatch.get(chart_name)
    if not fn:
        return JsonResponse({"error": "unknown chart"}, status=404)
    return JsonResponse({"data": fn(qs)})


@require_GET
def trl_detail_ajax(request):
    application_id = request.GET.get('application_id')
    trl_id = request.GET.get('trl_id')

    if not application_id or not trl_id:
        return JsonResponse({"error": "application_id and trl_id are required"}, status=400)

    application = Application.objects.filter(id=application_id).first()
    trl = TRLDefinition.objects.filter(id=trl_id).first()

    if not application or not trl:
        return JsonResponse({"error": "Application or TRL not found"}, status=404)

    trl_tag = f"TRL-{trl.level}"

    # Find the TRL stage for this application + this TRL level
    stage = ApplicationTRLStage.objects.filter(
        application=application, trl=trl
    ).first()

    if not stage:
        return JsonResponse({
            "trl_level": trl.level,
            "trl_name": trl.name,
            "total_assigned_partners": 0,
            "assigned_partners": [],
            "partners": [],
        })

    # Get all partner assignments under this TRL stage
    assignments = KnowledgePartnerAssignment.objects.filter(
        trl_stage=stage
    ).select_related('partner').order_by('-date_allotted')
    all_assigned_partners = []
    matched_partners = []

    for assignment in assignments:
        partner = assignment.partner
        levels_list = [lvl.strip() for lvl in partner.applicable_trl_levels.split(",") if lvl.strip()]

        milestones_data = []
        milestones = Milestone.objects.filter(assignment=assignment).order_by('template__sequence')
        for m in milestones:
            milestones_data.append({
                "label": m.label or (m.template.description if m.template else ""),
                "template_name": m.template.description if m.template else "",
                "status": m.get_status_display(),
                "date_achieved": m.date_achieved.strftime("%d %b %Y") if m.date_achieved else None,
            })

        partner_data = {
            "partner_id": partner.id,
            "partner_name": partner.name,
            "short_code": partner.short_code,
            "applicable_trl_levels": partner.applicable_trl_levels,
            "assignment_status": assignment.get_status_display(),
            "milestones": milestones_data,
        }

        all_assigned_partners.append(partner_data)

        if trl_tag in levels_list:
            matched_partners.append(partner_data)

    return JsonResponse({
        "trl_level": trl.level,
        "trl_name": trl.name,
        "total_assigned_partners": assignments.count(),
        "assigned_partners": all_assigned_partners,
        "partners": matched_partners,
    })


@login_required
def trl_drilldown(request, trl_level):
    """Called when a pie slice is clicked. Returns total applications for that
    TRL level, split by knowledge partner and by milestone status."""

    partner_qs = (
        KnowledgePartnerAssignment.objects
        .filter(trl_stage__trl__level=trl_level)
        .values('partner__short_code')
        .annotate(value=Count('trl_stage__application', distinct=True))
        .order_by('partner__short_code')
    )
    partner_data = [{"label": r["partner__short_code"], "value": r["value"]} for r in partner_qs]

    milestone_qs = (
        Milestone.objects
        .filter(assignment__trl_stage__trl__level=trl_level)
        .values('status')
        .annotate(value=Count('assignment__trl_stage__application', distinct=True))
        .order_by('status')
    )
    status_labels = {"open": "Open", "in_progress": "In Progress", "closed": "Completed"}
    milestone_data = [
        {"label": status_labels.get(r["status"], r["status"]), "value": r["value"]}
        for r in milestone_qs
    ]

    return JsonResponse({"partner_data": partner_data, "milestone_data": milestone_data})


@require_GET
def partners_for_trl_ajax(request):
    """Returns knowledge partners applicable to a given TRL level, each with
    their milestone template checklist, for the 'Assign Knowledge Partner' popup."""
    trl_id = request.GET.get('trl_id')
    trl = TRLDefinition.objects.filter(id=trl_id).first()
    if not trl:
        return JsonResponse({"error": "TRL not found"}, status=404)

    def normalize(s):
        return s.strip().upper().replace(" ", "").replace("-", "")

    target = normalize(f"TRL{trl.level}")

    partners = KnowledgePartner.objects.filter(is_active=True)
    matched = []
    for p in partners:
        levels = [normalize(lvl) for lvl in p.applicable_trl_levels.split(",") if lvl.strip()]
        if target in levels:
            templates = list(
                p.milestone_templates.order_by("sequence").values("id", "sequence", "description")
            )
            matched.append({
                "id": p.id,
                "name": p.name,
                "short_code": p.short_code,
                "milestone_templates": templates,   # <-- new
            })

    return JsonResponse({"trl_id": trl.id, "trl_level": trl.level, "partners": matched})


@require_POST
def assign_trl_partner_ajax(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid data"}, status=400)

    application_id = data.get("application_id")
    trl_id = data.get("trl_id")
    partners_data = data.get("partners", [])
    initial_trl_id = data.get("initial_trl_id")
    current_trl_id = data.get("current_trl_id")

    application = Application.objects.filter(id=application_id).first()
    trl = TRLDefinition.objects.filter(id=trl_id).first()

    if not application or not trl:
        return JsonResponse({"error": "Application or TRL not found"}, status=404)
    if not partners_data:
        return JsonResponse({"error": "Select at least one knowledge partner"}, status=400)

    changed = False

    # ---- Explicit Initial TRL / Current TRL from the popup dropdowns ----
    if initial_trl_id:
        initial_trl = TRLDefinition.objects.filter(id=initial_trl_id).first()
        if initial_trl and application.initial_trl_id != initial_trl.id:
            application.initial_trl = initial_trl
            application.initial_trl_date = timezone.now().date()
            changed = True

    if current_trl_id:
        current_trl = TRLDefinition.objects.filter(id=current_trl_id).first()
        if current_trl and application.current_trl_id != current_trl.id:
            application.current_trl = current_trl
            application.current_trl_date = timezone.now().date()
            changed = True
            TRLProgressLog.objects.create(
                application=application,
                trl=current_trl,
                changed_on=timezone.now().date(),
                remarks=f"Current TRL set to {current_trl.name} via Assign Knowledge Partner action.",
                created_by=request.user if request.user.is_authenticated else None,
            )

    # ---- Fallback: if neither dropdown was used, keep application in sync
    #      with the TRL being assigned right now (first-time / lower TRL cases) ----
    if not application.initial_trl_id:
        application.initial_trl = trl
        application.initial_trl_date = timezone.now().date()
        changed = True

    if not application.current_trl_id or trl.level > application.current_trl.level:
        application.current_trl = trl
        application.current_trl_date = timezone.now().date()
        changed = True
        TRLProgressLog.objects.create(
            application=application,
            trl=trl,
            changed_on=timezone.now().date(),
            remarks=f"Current TRL set to {trl.name} via Assign Knowledge Partner action.",
            created_by=request.user if request.user.is_authenticated else None,
        )

    if changed:
        application.save()

    # ---- TRL stage + partner + milestone logic ----
    stage, stage_created = ApplicationTRLStage.objects.get_or_create(
        application=application, trl=trl,
        defaults={
            "status": ApplicationTRLStage.StageStatus.IN_PROGRESS,
            "start_date": timezone.now().date(),
        },
    )

    if stage_created:
        TRLProgressLog.objects.create(
            application=application,
            trl=trl,
            changed_on=timezone.now().date(),
            remarks=f"{trl.name} stage opened with knowledge partner assignment.",
            created_by=request.user if request.user.is_authenticated else None,
        )

    assignments_created = 0
    milestones_created = 0

    for p in partners_data:
        partner = KnowledgePartner.objects.filter(id=p.get("partner_id")).first()
        if not partner:
            continue

        assignment, was_created = KnowledgePartnerAssignment.objects.get_or_create(
            trl_stage=stage, partner=partner,
            defaults={"date_allotted": timezone.now().date()},
        )
        if was_created:
            assignments_created += 1

        for m in p.get("milestones", []):
            template_id = m.get("template_id")
            status = m.get("status") or "in_progress"

            template = PartnerMilestoneTemplate.objects.filter(id=template_id).first()
            if not template:
                continue

            if Milestone.objects.filter(assignment=assignment, template=template).exists():
                continue

            Milestone.objects.create(
                assignment=assignment,
                template=template,
                label=f"M-{template.sequence}",
                description=template.description,
                status=status,
                date_achieved=timezone.now().date() if status == "closed" else None,
            )
            milestones_created += 1

    #stage.check_and_advance()
    #stage.refresh_from_db()

    return JsonResponse({
        "success": True,
        "assignments_created": assignments_created,
        "milestones_created": milestones_created,
        "stage_status": stage.status,
        "application_initial_trl": application.initial_trl.name if application.initial_trl else None,
        "application_current_trl": application.current_trl.name if application.current_trl else None,
    })


@login_required
@require_GET
def application_milestones_ajax(request):
    user = request.user
    is_admin = user.is_superuser or user.is_admin_role

    partner = getattr(user, "partner_profile", None) if user.is_support_partner else None

    if not (is_admin or partner):
        return JsonResponse({"error": "You do not have permission."}, status=403)

    application = Application.objects.filter(id=request.GET.get("application_id")).first()
    if not application:
        return JsonResponse({"error": "Application not found."}, status=404)

    assignments = (
        KnowledgePartnerAssignment.objects
        .filter(trl_stage__application=application)
        .select_related("partner", "trl_stage__trl")
        .prefetch_related(
            Prefetch(
                "milestones",
                queryset=Milestone.objects.select_related("template")
                                          .order_by("template__sequence", "id"),
            )
        )
        .order_by("trl_stage__trl__level", "partner__name")
    )

    if not is_admin:
        assignments = assignments.filter(partner=partner)

    result = []
    for a in assignments:
        result.append({
            "assignment_id": a.id,
            "trl_name": a.trl_stage.trl.name,
            "partner_name": a.partner.name,
            "short_code": a.partner.short_code,
            "assignment_status": a.get_status_display(),
            "advisory_remarks": a.advisory_remarks,          # NEW
            "milestones": [
                {
                    "id": m.id,
                    "label": m.label,
                    "description": m.description or (m.template.description if m.template else ""),
                    "status": m.status,
                    "status_display": m.get_status_display(),
                    "date_achieved": m.date_achieved.strftime("%d %b %Y") if m.date_achieved else "-",
                }
                for m in a.milestones.all()
            ],
        })

    return JsonResponse({
        "reference_no": application.reference_no,
        "technology_name": application.technology_name,
        "is_admin": is_admin,
        "can_edit": True,
        "status_choices": [
            {"value": v, "label": l} for v, l in Milestone.MilestoneStatus.choices
        ],
        "assignments": result,
    })


def can_edit_assignment(user, assignment):
    if user.is_superuser or user.is_admin_role:
        return True
    if user.is_support_partner:
        partner = getattr(user, "partner_profile", None)
        return partner is not None and assignment.partner_id == partner.id
    return False


@login_required
@require_POST
def update_assignment_milestones(request):
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid data."}, status=400)

    assignment = KnowledgePartnerAssignment.objects.select_related("trl_stage").filter(
        id=data.get("assignment_id")
    ).first()
    if not assignment:
        return JsonResponse({"error": "Assignment not found."}, status=404)

    if not can_edit_assignment(request.user, assignment):
        return JsonResponse({"error": "You do not have permission to change this assignment."}, status=403)

    milestone_updates = data.get("milestones", [])
    remarks = data.get("remarks", "")

    valid_values = Milestone.MilestoneStatus.values
    milestone_ids = [m.get("milestone_id") for m in milestone_updates]

    # only touch milestones that really belong to this assignment
    milestones = {
        str(m.id): m for m in Milestone.objects.filter(
            id__in=milestone_ids, assignment=assignment
        )
    }

    updated = []
    for item in milestone_updates:
        milestone = milestones.get(str(item.get("milestone_id")))
        new_status = item.get("status")

        if not milestone or new_status not in valid_values:
            continue

        milestone.status = new_status
        if new_status == Milestone.MilestoneStatus.CLOSED:
            milestone.date_achieved = milestone.date_achieved or timezone.localdate()
        else:
            milestone.date_achieved = None
        milestone.save()
        updated.append(milestone)

    assignment.advisory_remarks = remarks
    assignment.save(update_fields=["advisory_remarks"])

    stage_completed = False
    stage = assignment.trl_stage
    if stage:
        stage.check_and_advance()
        stage.refresh_from_db()
        stage_completed = stage.status == stage.StageStatus.COMPLETED

    return JsonResponse({
        "success": True,
        "stage_completed": stage_completed,
        "milestones": [
            {
                "id": m.id,
                "status_display": m.get_status_display(),
                "date_achieved": m.date_achieved.strftime("%d %b %Y") if m.date_achieved else "-",
            }
            for m in updated
        ],
    })