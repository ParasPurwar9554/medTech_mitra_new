import csv
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count, F, Prefetch, Q
from django.http import (FileResponse,Http404,HttpResponse,JsonResponse,)
from django.shortcuts import (get_object_or_404,redirect,render,)
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.models import (
    Application,
    ApplicationQuantifiableDeliverable,
    ApplicationSupport,
    ApplicationTRLStage,
    KnowledgePartner,
    KnowledgePartnerAssignment,
    PartnerMilestoneTemplate,
    QuantifiableDeliverable,
    QuantifiableDeliverableAttachment,
    SupportOption,
    TACMeeting,
    TRLDefinition,
)

from . import services

logger = logging.getLogger(__name__)
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024   # 10 MB

@login_required
def applications(request):

    # 1. Base queryset (role-based access)
    base_qs = services.apply_dashboard_filters(
        request,
        include_medtech_type=False
    )

    # 2. MedTech category selection
    valid_types = [
        value for value, label in Application.MedTechType.choices
    ]

    selected_medtech_type = request.GET.get("medtech_type", "")

    if selected_medtech_type not in valid_types:
        selected_medtech_type = valid_types[0] if valid_types else ""

    # 3. MedTech category tabs with counts
    medtech_tabs = []

    for value, label in Application.MedTechType.choices:
        medtech_tabs.append({
            "value": value,
            "label": label,
            "count": base_qs.filter(medtech_type=value).count(),
        })

    # 4. Apply MedTech category filter
    qs = base_qs

    if selected_medtech_type:
        qs = qs.filter(medtech_type=selected_medtech_type)

    # 5. Search filter
    search_query = request.GET.get("q", "").strip()

    if search_query:
        qs = qs.filter(
            Q(reference_no__icontains=search_query) |
            Q(technology_name__icontains=search_query) |
            Q(applicant__name__icontains=search_query)
        )

    # 6. Subcategory filter
    valid_subtypes = [
        value for value, label
        in Application.MedTechSubCategoryType.choices
    ]

    selected_subtype = request.GET.get("subtype", "")

    if selected_subtype not in valid_subtypes:
        selected_subtype = ""

    if selected_subtype:
        qs = qs.filter(medtech_subtype=selected_subtype)

    # 7. Support options filter
    support_options = SupportOption.objects.filter(
        is_active=True
    ).order_by("name")

    valid_support_ids = {
        str(option.id) for option in support_options
    }

    selected_support_option = request.GET.get("support_option", "")

    if selected_support_option not in valid_support_ids:
        selected_support_option = ""

    if selected_support_option:
        qs = qs.filter(
            application_supports__support_option_id=selected_support_option
        ).distinct()

    # 8. Quantifiable deliverables filter
    quantifiable_deliverables = QuantifiableDeliverable.objects.filter(
        is_active=True
    ).order_by("deliverable")

    valid_deliverable_ids = {
        str(item.id) for item in quantifiable_deliverables
    }

    selected_deliverable = request.GET.get("deliverable", "")

    if selected_deliverable not in valid_deliverable_ids:
        selected_deliverable = ""

    if selected_deliverable:
        qs = qs.filter(
            quantifiable_deliverables__quantifiable_deliverable_id=
            selected_deliverable
        ).distinct()

    # 9. Risk classification filter
    risk_classification_choices = Application.RiskClass.choices

    selected_risk_classification = request.GET.get(
        "risk_classification", ""
    )

    if selected_risk_classification:
        qs = qs.filter(
            risk_classification=selected_risk_classification
        )

    # 10. Generate MedTech tab URLs
    tab_params = request.GET.copy()
    tab_params.pop("medtech_type", None)
    tab_params.pop("page", None)

    for tab in medtech_tabs:
        params = tab_params.copy()
        params["medtech_type"] = tab["value"]
        tab["url"] = f"?{params.urlencode()}"

    # --------------------------------------------------
    # 11. Quantifiable deliverables queryset
    # --------------------------------------------------

    deliverable_qs = (
        ApplicationQuantifiableDeliverable.objects
        .select_related(
            "quantifiable_deliverable",
            "trl_stage__trl",
            "knowledge_partner",
        )
        .prefetch_related("attachments")
        .order_by(
            "trl_stage__trl__level",
            "id",
        )
    )

    # Only Knowledge Partner users see their own assignments
    if request.user.is_support_partner:

        if request.user.knowledge_partner_id:
            deliverable_qs = deliverable_qs.filter(
                knowledge_partner_id=request.user.knowledge_partner_id
            )
        else:
            deliverable_qs = deliverable_qs.none()

    # --------------------------------------------------
    # 12. Optimize application queries
    # --------------------------------------------------

    applications_qs = (
        qs.select_related(
            "applicant",
            "initial_trl",
            "current_trl",
        )
        .prefetch_related(
            Prefetch(
                "tac_meetings",
                queryset=TACMeeting.objects.order_by(
                    "-meeting_date",
                    "-id",
                ),
            ),
            Prefetch(
                "application_supports",
                queryset=ApplicationSupport.objects.select_related(
                    "support_option"
                ).order_by("id"),
            ),
            Prefetch(
                "trl_stages",
                queryset=ApplicationTRLStage.objects.select_related(
                    "trl"
                ).order_by("-trl__level", "-id"),
            ),
            Prefetch(
                "quantifiable_deliverables",
                queryset=deliverable_qs,
            ),
        )
        .order_by(
            F("date_received").desc(nulls_last=True),
            "-id",
        )
    )

    # 13. Pagination
    paginator = Paginator(applications_qs, 20)

    applications_page = paginator.get_page(
        request.GET.get("page", 1)
    )

    # 14. Admin check
    is_admin = (
        request.user.is_superuser
        or request.user.is_admin_role
    )

    # 15. Context
    context = {
        "kpis": services.kpi_summary(base_qs),
        "medtech_type_data": services.applications_by_medtech_type(qs),
        "risk_data": services.applications_by_risk_class(qs),
        "status_choices": Application.ApplicationStatus.choices,
        "medtech_type_choices": Application.MedTechType.choices,
        "subtype_choices": Application.MedTechSubCategoryType.choices,
        "medtech_tabs": medtech_tabs,
        "selected_medtech_type": selected_medtech_type,
        "selected_subtype": selected_subtype,
        "search_query": search_query,
        "support_options": support_options,
        "selected_support_option": selected_support_option,
        "quantifiable_deliverables": quantifiable_deliverables,
        "selected_deliverable": selected_deliverable,
        "partners": KnowledgePartner.objects.filter(is_active=True),
        "active_filters": request.GET,
        "applications": applications_page,
        "is_admin": is_admin,
        "risk_classification_choices": risk_classification_choices,
        "selected_risk_classification": selected_risk_classification,
    }

    return render(
        request,
        "dashboard/applications.html",
        context,
    )


@login_required
def overview(request):

    if request.user.is_support_partner:
        return redirect("dashboard:applications")
    qs = services.apply_dashboard_filters(
        request,
        include_trl=True,
        include_medtech_type=True,
        include_subtype=True,
        include_support=True,
        include_deliverable=True,
    )

    # KPI totals.
    total_applications = qs.count()
    total_in_progress = qs.filter(status=Application.ApplicationStatus.IN_PROGRESS).count()
    total_closed = qs.filter(status=Application.ApplicationStatus.CLOSED).count()
    total_completed = qs.filter(status=Application.ApplicationStatus.COMPLTED).count()
    category_portfolio = services.category_wise_portfolio(qs)
    support_rows = (
        ApplicationSupport.objects
        .filter(application__in=qs)
        .values("support_option__name")
        .annotate(total=Count("application_id", distinct=True))
        .order_by("-total")
    )

    support_chart = {
        "labels": [row["support_option__name"] for row in support_rows],
        "values": [row["total"] for row in support_rows],
    }

    # Deliverable-wise status progress.
    deliverable_rows = (
        ApplicationQuantifiableDeliverable.objects
        .filter(application__in=qs)
        .values("quantifiable_deliverable__deliverable")
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            in_progress=Count("id", filter=Q(status="in_progress")),
            pending=Count("id", filter=Q(status="pending")),
        )
        .order_by("quantifiable_deliverable__deliverable")
    )

    deliverable_progress = []
    for row in deliverable_rows:
        total = row["total"]
        deliverable_progress.append({
            "name": row["quantifiable_deliverable__deliverable"],
            "total": total,
            "completed": row["completed"],
            "in_progress": row["in_progress"],
            "pending": row["pending"],
            "completed_percent": round(row["completed"] * 100 / total, 1),
            "in_progress_percent": round(row["in_progress"] * 100 / total, 1),
            "pending_percent": round(row["pending"] * 100 / total, 1),
        })

    # Filter dropdown data.
    support_options = SupportOption.objects.filter(is_active=True).order_by("name")
    quantifiable_deliverables = QuantifiableDeliverable.objects.filter(is_active=True).order_by("deliverable")
    partners = KnowledgePartner.objects.filter(is_active=True).order_by("name")

    context = {
        "total_applications": total_applications,
        "total_in_progress": total_in_progress,
        "total_closed": total_closed,
        "total_completed": total_completed,
        "category_portfolio": category_portfolio,
        "support_chart": support_chart,
        "deliverable_progress": deliverable_progress,
        "status_choices": Application.ApplicationStatus.choices,
        "medtech_type_choices": Application.MedTechType.choices,
        "subtype_choices": Application.MedTechSubCategoryType.choices,
        "risk_choices": Application.RiskClass.choices,
        "trl_level_choices": range(1, 10),
        "support_options": support_options,
        "quantifiable_deliverables": quantifiable_deliverables,
        "partners": partners,
    }

    return render(request, "dashboard/dashboard.html", context)


@login_required
def application_detail(request, pk):
    application = get_object_or_404(
        Application.objects
        .select_related(
            "applicant",
            "current_trl",
            "initial_trl",
        )
        .prefetch_related(
            "tac_meetings",
            "trl_stages__partner_assignments__partner",
            "trl_stages__partner_assignments__milestones",

            # Support Requested
            Prefetch(
                "application_supports",
                queryset=ApplicationSupport.objects
                .select_related("support_option")
                .order_by("id"),
            ),

            # Quantifiable Deliverables
            Prefetch(
                "quantifiable_deliverables",
                queryset=ApplicationQuantifiableDeliverable.objects
                .select_related(
                    "quantifiable_deliverable",
                    "trl_stage__trl",
                    "knowledge_partner",
                )
                .prefetch_related("attachments")
                .order_by("trl_stage__trl__level", "id"),
            ),
        ),
        pk=pk,
    )

    context = {
        "application": application,
    }

    return render(
        request,
        "dashboard/application_detail.html",
        context,
    )


@login_required
def export_applications(request):
    try:
        # 1. Use the SAME filters as the dashboard
        qs = services.apply_dashboard_filters(request)

        # 2. Load related data in bulk and use the same order as the table
        applications = (
            qs.select_related("applicant", "initial_trl", "current_trl")
              .prefetch_related("trl_stages__partner_assignments__partner")
              .order_by(F("date_received").desc(nulls_last=True), "-id")
        )

        # 3. Prepare the file download
        filename = f"applications_{timezone.localdate():%Y-%m-%d}.csv"
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("\ufeff")  # makes Excel read special characters correctly

        writer = csv.writer(response)

        # 4. Header row
        writer.writerow([
            "Reference No",
            "Innovator",
            "Technology",
            "Status",
            "Category",
            "Risk Class",
            "Knowledge Partner(s)",
            "Initial TRL",
            "Current TRL",
            "Date Received",
        ])

        # 5. One row per application
        for app in applications:
            partners = sorted({
                pa.partner.short_code
                for stage in app.trl_stages.all()
                for pa in stage.partner_assignments.all()
                if pa.partner
            })

            writer.writerow([
                app.reference_no,
                app.applicant.name if app.applicant else "",
                app.technology_name,
                app.get_status_display(),
                app.get_medtech_type_display(),
                app.get_risk_classification_display() if app.risk_classification else "",
                ", ".join(partners),
                f"TRL {app.initial_trl.level}" if app.initial_trl else "",
                f"TRL {app.current_trl.level}" if app.current_trl else "",
                app.date_received.strftime("%d-%m-%Y") if app.date_received else "",
            ])

        return response

    except Exception:
        logger.exception("Export failed for user %s", request.user.pk)
        error_text = "Sorry, the export failed. Please try again or contact support."

        # Request came from jQuery: send the error as JSON
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"error": error_text}, status=500)

        # Normal request: redirect with a Django message (old behaviour)
        messages.error(request, error_text)
        url = reverse("dashboard:overview")
        if request.GET:
            url += "?" + request.GET.urlencode()
        return redirect(url)