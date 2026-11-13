
from django.contrib import admin, messages
from django import forms
from django.urls import path
from django.shortcuts import render, redirect
from django.db.models import F

from .forms import ExcelUploadForm
from .excel_import import import_master_excel

from .models import (
    Applicant,
    Application,
    TACMeeting,
    KnowledgePartner,
    KnowledgePartnerAssignment,
    TRLDefinition,
    PartnerMilestoneTemplate,
    Milestone,
    TRLProgressLog,
    ApplicationTRLStage,
    SupportOption,
    AssignmentChangeLog,
    ApplicationSupport,
    QuantifiableDeliverable,
    ApplicationQuantifiableDeliverable,
    QuantifiableDeliverableAttachment,
    TRLStageAttachment,
)


# ---------------------------------------------------------------------------
# Admin Site Configuration
# ---------------------------------------------------------------------------

admin.site.site_header = "MedTech Mitra Tracker"
admin.site.site_title = "MedTech Mitra Tracker"
admin.site.index_title = "Manage MedTech Mitra Dashboard"


# ---------------------------------------------------------------------------
# Support Option Admin
# ---------------------------------------------------------------------------

@admin.register(SupportOption)
class SupportOptionAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "is_active",
    )

    list_filter = (
        "is_active",
    )

    search_fields = (
        "name",
    )

    list_editable = (
        "is_active",
    )


# ---------------------------------------------------------------------------
# Application Support Attachment Inline
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Application Support Admin
# Knowledge Partner is NOT assigned to Support.
# ---------------------------------------------------------------------------

@admin.register(ApplicationSupport)
class ApplicationSupportAdmin(admin.ModelAdmin):

    list_display = (
        "application",
        "support_option",
        "status",
    )

    list_filter = (
        "status",
        "support_option",
    )

    search_fields = (
        "application__reference_no",
        "support_option__name",
    )

    autocomplete_fields = (
        "application",
        "support_option",
    )


# ---------------------------------------------------------------------------
# TAC Meeting Admin Form
# ---------------------------------------------------------------------------

class TACMeetingAdminForm(forms.ModelForm):

    class Meta:
        model = TACMeeting
        fields = "__all__"


# ---------------------------------------------------------------------------
# Application Admin Form
# ---------------------------------------------------------------------------

class ApplicationAdminForm(forms.ModelForm):

    class Meta:
        model = Application
        fields = "__all__"

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        medtech_type = None

        if self.instance and self.instance.pk:
            medtech_type = self.instance.medtech_type

        elif self.data.get("medtech_type"):
            medtech_type = self.data.get("medtech_type")

        queryset = TRLDefinition.objects.all()

        if medtech_type:
            queryset = queryset.filter(
                medtech_type__in=[medtech_type, ""]
            )

        queryset = queryset.order_by(
            "medtech_type",
            "level",
        )

        if "initial_trl" in self.fields:
            self.fields["initial_trl"].queryset = queryset

        if "current_trl" in self.fields:
            self.fields["current_trl"].queryset = queryset


# ---------------------------------------------------------------------------
# Application Inlines
# ---------------------------------------------------------------------------

class TACMeetingInline(admin.TabularInline):

    model = TACMeeting
    form = TACMeetingAdminForm
    extra = 0
    show_change_link = True


class TRLProgressLogInline(admin.TabularInline):

    model = TRLProgressLog
    extra = 0
    show_change_link = True


class ApplicationSupportInline(admin.TabularInline):

    model = ApplicationSupport
    extra = 1

    autocomplete_fields = (
        "support_option",
    )

    show_change_link = True


class ApplicationTRLStageInline(admin.TabularInline):

    model = ApplicationTRLStage
    extra = 0

    autocomplete_fields = (
        "trl",
    )

    show_change_link = True


# ---------------------------------------------------------------------------
# Application Quantifiable Deliverable Inline
#
# Each deliverable has:
# 1. Its own TRL Stage
# 2. Its own Knowledge Partner
# 3. Its own Status
# 4. Its own Remarks
# ---------------------------------------------------------------------------

class ApplicationQuantifiableDeliverableInline(admin.TabularInline):

    model = ApplicationQuantifiableDeliverable
    extra = 0

    fields = (
        "trl_stage",
        "quantifiable_deliverable",
        "knowledge_partner",
        "status",
        "remarks",
    )

    autocomplete_fields = (
        "trl_stage",
        "quantifiable_deliverable",
        "knowledge_partner",
    )

    show_change_link = True


# ---------------------------------------------------------------------------
# Applicant Admin
# ---------------------------------------------------------------------------

@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "affiliation_name",
        "affiliation_type",
        "email",
        "contact_number",
    )

    search_fields = (
        "name",
        "innovator_name",
        "affiliation_name",
        "email",
        "contact_number",
    )

    list_filter = (
        "affiliation_type",
    )


# ---------------------------------------------------------------------------
# Application Admin
# ---------------------------------------------------------------------------

@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):

    form = ApplicationAdminForm

    list_display = (
        "reference_no",
        "technology_name",
        "applicant",
        "medtech_type",
        "risk_classification",
        "status",
        "current_trl",
        "date_received",
    )

    list_filter = (
        "status",
        "medtech_type",
        "risk_classification",
        "innovation_stage",
    )

    search_fields = (
        "reference_no",
        "technology_name",
        "applicant__name",
    )

    autocomplete_fields = (
        "applicant",
        "created_by",
    )

    date_hierarchy = "date_received"

    inlines = [
        TACMeetingInline,
        ApplicationTRLStageInline,
        ApplicationSupportInline,
        ApplicationQuantifiableDeliverableInline,
    ]

    change_list_template = (
        "admin/core/application/change_list.html"
    )

    def get_changeform_initial_data(self, request):

        initial = super().get_changeform_initial_data(request)

        initial["created_by"] = request.user.pk

        return initial

    def save_model(self, request, obj, form, change):

        if not change and not obj.created_by:
            obj.created_by = request.user

        super().save_model(
            request,
            obj,
            form,
            change,
        )

    def get_ordering(self, request):

        return [
            F("date_received").desc(nulls_last=True),
            "-id",
        ]

    # -----------------------------------------------------------------------
    # Excel Import
    # -----------------------------------------------------------------------

    def get_urls(self):

        urls = super().get_urls()

        custom_urls = [
            path(
                "import-excel/",
                self.admin_site.admin_view(self.import_excel),
                name="core_application_import_excel",
            ),
        ]

        return custom_urls + urls

    def import_excel(self, request):

        if request.method == "POST":

            form = ExcelUploadForm(
                request.POST,
                request.FILES,
            )

            if form.is_valid():

                result = import_master_excel(
                    request.FILES["excel_file"]
                )

                messages.success(
                    request,
                    (
                        f"Import complete: "
                        f"{result['created']} created, "
                        f"{result['updated']} updated."
                    ),
                )

                if result["errors"]:

                    for err in result["errors"][:10]:
                        messages.warning(request, err)

                return redirect("..")

        else:

            form = ExcelUploadForm()

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Import Applications from Excel",
        }

        return render(
            request,
            "admin/core/application/import_excel.html",
            context,
        )


# ---------------------------------------------------------------------------
# Application TRL Stage Admin
#
# Knowledge Partner Assignment is no longer an inline here.
# TRL attachments remain available.
# ---------------------------------------------------------------------------

class TRLStageAttachmentInline(admin.TabularInline):

    model = TRLStageAttachment
    extra = 1

    fields = (
        "file",
        "original_name",
        "note",
        "uploader_role",
    )


@admin.register(ApplicationTRLStage)
class ApplicationTRLStageAdmin(admin.ModelAdmin):

    list_display = (
        "application",
        "trl",
        "status",
        "start_date",
        "completed_date",
    )

    list_filter = (
        "trl__medtech_type",
        "trl__level",
        "status",
    )

    autocomplete_fields = (
        "application",
        "trl",
    )

    search_fields = (
        "application__reference_no",
        "application__technology_name",
        "trl__name",
    )

    inlines = [
        TRLStageAttachmentInline,
    ]


# ---------------------------------------------------------------------------
# Knowledge Partner Admin
# ---------------------------------------------------------------------------

@admin.register(KnowledgePartner)
class KnowledgePartnerAdmin(admin.ModelAdmin):

    list_display = (
        "short_code",
        "name",
        "user",
        "applicable_trl_levels",
        "is_active",
    )

    list_filter = (
        "is_active",
    )

    search_fields = (
        "name",
        "short_code",
    )


# ---------------------------------------------------------------------------
# TRL Definition Admin
# ---------------------------------------------------------------------------

@admin.register(TRLDefinition)
class TRLDefinitionAdmin(admin.ModelAdmin):

    list_display = (
        "level",
        "name",
        "medtech_type",
    )

    list_filter = (
        "medtech_type",
    )

    ordering = (
        "medtech_type",
        "level",
    )

    search_fields = (
        "name",
    )


# ---------------------------------------------------------------------------
# Partner Milestone Template Admin
# Legacy model retained for historical records.
# ---------------------------------------------------------------------------

# @admin.register(PartnerMilestoneTemplate)
# class PartnerMilestoneTemplateAdmin(admin.ModelAdmin):

#     list_display = (
#         "partner",
#         "sequence",
#         "description",
#     )

#     list_filter = (
#         "partner",
#     )

#     search_fields = (
#         "partner__name",
#         "description",
#     )


# ---------------------------------------------------------------------------
# Legacy Milestone Inline
# ---------------------------------------------------------------------------

# class MilestoneInline(admin.TabularInline):

#     model = Milestone
#     extra = 0


# ---------------------------------------------------------------------------
# Legacy Knowledge Partner Assignment Admin
# Retained for existing records only.
# No automatic TRL advancement.
# ---------------------------------------------------------------------------

@admin.register(KnowledgePartnerAssignment)
class KnowledgePartnerAssignmentAdmin(admin.ModelAdmin):

    list_display = (
        "trl_stage",
        "partner",
        "status",
        "date_allotted",
        "date_connected",
    )

    list_filter = (
        "partner",
        "status",
    )

    search_fields = (
        "trl_stage__application__reference_no",
        "partner__name",
    )

    autocomplete_fields = (
        "trl_stage",
        "partner",
    )

    inlines = []


# ---------------------------------------------------------------------------
# TAC Meeting Admin
# ---------------------------------------------------------------------------

@admin.register(TACMeeting)
class TACMeetingAdmin(admin.ModelAdmin):

    form = TACMeetingAdminForm

    list_display = (
        "application",
        "meeting_ref_no",
        "meeting_date",
    )

    search_fields = (
        "application__reference_no",
    )

    autocomplete_fields = (
        "application",
    )


# ---------------------------------------------------------------------------
# Milestone Admin
# Legacy model retained for historical records.
# ---------------------------------------------------------------------------

# @admin.register(Milestone)
# class MilestoneAdmin(admin.ModelAdmin):

#     list_display = (
#         "assignment",
#         "label",
#         "description",
#         "remarks",
#         "status",
#         "date_achieved",
#     )

#     search_fields = (
#         "assignment__trl_stage__application__reference_no",
#         "label",
#         "description",
#     )

#     list_filter = (
#         "status",
#     )

#     autocomplete_fields = (
#         "assignment",
#         "template",
#     )


# ---------------------------------------------------------------------------
# Assignment Change Log Admin
# ---------------------------------------------------------------------------

@admin.register(AssignmentChangeLog)
class AssignmentChangeLogAdmin(admin.ModelAdmin):

    list_display = (
        "assignment",
        "field_changed",
        "old_value",
        "new_value",
        "created_at",
        "created_by",
    )

    list_filter = (
        "field_changed",
    )

    search_fields = (
        "assignment__trl_stage__application__reference_no",
        "old_value",
        "new_value",
    )


# ---------------------------------------------------------------------------
# Quantifiable Deliverable Master Admin
# ---------------------------------------------------------------------------

@admin.register(QuantifiableDeliverable)
class QuantifiableDeliverableAdmin(admin.ModelAdmin):

    list_display = (
        "deliverable",
        "medtech_type",
        "is_active",
    )

    list_filter = (
        "medtech_type",
        "is_active",
    )

    search_fields = (
        "deliverable",
    )

    list_editable = (
        "is_active",
    )


# ---------------------------------------------------------------------------
# Quantifiable Deliverable Attachment Inline
# ---------------------------------------------------------------------------

class QuantifiableDeliverableAttachmentInline(admin.TabularInline):

    model = QuantifiableDeliverableAttachment
    extra = 1

    fields = (
        "file",
        "original_name",
        "description",
    )


# ---------------------------------------------------------------------------
# Application Quantifiable Deliverable Admin
#
# Each deliverable has one TRL Stage and one Knowledge Partner.
# ---------------------------------------------------------------------------



@admin.register(ApplicationQuantifiableDeliverable)
class ApplicationQuantifiableDeliverableAdmin(admin.ModelAdmin):

    @admin.display(description="TRL Stage")
    def get_trl_stage(self, obj):
        if obj.trl_stage and obj.trl_stage.trl:
            return obj.trl_stage.trl.name

        return "-"

    list_display = (
        "application",
        "get_trl_stage",
        "quantifiable_deliverable",
        "knowledge_partner",
        "status",
        "updated_at",
    )

    list_filter = (
        "status",
        "trl_stage__trl__medtech_type",
        "trl_stage__trl__level",
        "knowledge_partner",
    )

    search_fields = (
        "application__reference_no",
        "application__technology_name",
        "quantifiable_deliverable__deliverable",
        "knowledge_partner__name",
        "knowledge_partner__short_code",
    )

    autocomplete_fields = (
        "application",
        "trl_stage",
        "quantifiable_deliverable",
        "knowledge_partner",
    )

    inlines = [
        QuantifiableDeliverableAttachmentInline,
    ]


# ---------------------------------------------------------------------------
# TRL Progress Log Admin
# ---------------------------------------------------------------------------

@admin.register(TRLProgressLog)
class TRLProgressLogAdmin(admin.ModelAdmin):

    list_display = (
        "application",
        "trl",
        "changed_on",
        "created_by",
    )

    list_filter = (
        "trl__medtech_type",
        "trl__level",
    )

    search_fields = (
        "application__reference_no",
        "application__technology_name",
    )

    autocomplete_fields = (
        "application",
        "trl",
    )


# ---------------------------------------------------------------------------
# Admin Model Ordering
# ---------------------------------------------------------------------------

MODEL_ORDER = [
    "Applicant",
    "Application",
    "TACMeeting",
    "ApplicationTRLStage",
    "TRLProgressLog",
    "SupportOption",
    "ApplicationSupport",
    "QuantifiableDeliverable",
    "ApplicationQuantifiableDeliverable",
    "KnowledgePartner",
    "KnowledgePartnerAssignment",
    "AssignmentChangeLog",
]

_original_get_app_list = admin.site.get_app_list


def custom_get_app_list(request, *args, **kwargs):

    app_list = _original_get_app_list(
        request,
        *args,
        **kwargs,
    )

    for app in app_list:

        app["models"].sort(
            key=lambda m: (
                MODEL_ORDER.index(m["object_name"])
                if m["object_name"] in MODEL_ORDER
                else len(MODEL_ORDER)
            )
        )

    return app_list


admin.site.get_app_list = custom_get_app_list
