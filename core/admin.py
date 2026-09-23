from django.contrib import admin
from .forms import ExcelUploadForm
from .excel_import import import_master_excel
from django.contrib import admin
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import F
from django import forms
from ckeditor.widgets import CKEditorWidget
admin.site.site_header = "MedTech Mitra Secretariat"
admin.site.site_title = "MedTech Mitra Secretariat"
admin.site.index_title = "Welcome to the Dashboard"

from .models import (
    Applicant, Application, TACMeeting, KnowledgePartner,
    KnowledgePartnerAssignment, FollowUp, TRLDefinition,
    PartnerMilestoneTemplate, Milestone, TRLProgressLog, StatusChangeLog,
    ApplicationTRLStage,AssignmentChangeLog
)


# ---------------------------------------------------------------------------
# Inlines — must be defined BEFORE any Admin class that references them
# ---------------------------------------------------------------------------

class TACMeetingInline(admin.TabularInline):
    model = TACMeeting
    extra = 0


class TRLProgressLogInline(admin.TabularInline):
    model = TRLProgressLog
    extra = 0


class MilestoneInline(admin.TabularInline):
    """Shown inside KnowledgePartnerAssignmentAdmin: milestones for that partner assignment."""
    model = Milestone
    extra = 0


class KnowledgePartnerAssignmentInline(admin.TabularInline):
    """Shown inside ApplicationTRLStageAdmin: which partners are on this TRL stage."""
    model = KnowledgePartnerAssignment
    extra = 0


class ApplicationTRLStageInline(admin.TabularInline):
    """Shown inside ApplicationAdmin: the horizontal TRL1..TRL9 boxes for this application."""
    model = ApplicationTRLStage
    extra = 0


# ---------------------------------------------------------------------------
# Admin classes
# ---------------------------------------------------------------------------

@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):
    list_display = ("name", "affiliation_name", "affiliation_type", "email", "contact_number")
    search_fields = ("name", "innovator_name", "affiliation_name", "email")
    list_filter = ("affiliation_type",)

class ApplicationAdminForm(forms.ModelForm):
    query_text = forms.CharField(widget=CKEditorWidget(), required=False)

    class Meta:
        model = Application
        fields = "__all__"

@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    form = ApplicationAdminForm 
    list_display = (
        "reference_no", "technology_name", "applicant", "medtech_type",
        "risk_classification", "status", "current_trl", "date_received",
    )
    list_filter = ("status", "medtech_type", "risk_classification", "innovation_stage")
    search_fields = ("reference_no", "technology_name", "applicant__name")
    autocomplete_fields = ("applicant", "initial_trl", "current_trl", "final_trl","created_by")
    date_hierarchy = "date_received"
    inlines = [TACMeetingInline, ApplicationTRLStageInline, TRLProgressLogInline]

    #def save_model(self, request, obj, form, change):
       # if not change:  # only when creating a NEW object, not editing
           # obj.created_by = request.user
       # super().save_model(request, obj, form, change)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial['created_by'] = request.user.pk
        return initial

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)   
        
    change_list_template = "admin/core/application/change_list.html"

    def get_ordering(self, request):
        return [F("date_received").desc(nulls_last=True), "-id"]

    def save_model(self, request, obj, form, change):
        """Log status transitions for audit purposes when changed via admin."""
        if change:
            old = Application.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
            if old and old != obj.status:
                StatusChangeLog.objects.create(
                    application=obj, from_status=old, to_status=obj.status,
                    created_by=request.user,
                )
        super().save_model(request, obj, form, change)

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
            form = ExcelUploadForm(request.POST, request.FILES)
            if form.is_valid():
                result = import_master_excel(request.FILES["excel_file"])
                messages.success(
                    request,
                    f"Import complete: {result['created']} created, {result['updated']} updated."
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
        return render(request, "admin/core/application/import_excel.html", context)

@admin.register(ApplicationTRLStage)
class ApplicationTRLStageAdmin(admin.ModelAdmin):
    list_display = ("application", "trl", "status", "start_date", "completed_date")
    list_filter = ("trl", "status")
    autocomplete_fields = ("application",)
    search_fields = ("application__reference_no", "application__technology_name")
    inlines = [KnowledgePartnerAssignmentInline]


@admin.register(KnowledgePartner)
class KnowledgePartnerAdmin(admin.ModelAdmin):
    list_display = ("short_code", "name", "user","applicable_trl_levels", "is_active")
    search_fields = ("name", "short_code")


@admin.register(TRLDefinition)
class TRLDefinitionAdmin(admin.ModelAdmin):
    list_display = ("level", "name", "medtech_type")
    list_filter = ("medtech_type",)
    ordering = ("medtech_type", "level")
    search_fields = ("name",)


@admin.register(PartnerMilestoneTemplate)
class PartnerMilestoneTemplateAdmin(admin.ModelAdmin):
    list_display = ("partner", "sequence", "description")
    list_filter = ("partner",)


@admin.register(KnowledgePartnerAssignment)
class KnowledgePartnerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("trl_stage", "partner", "status", "date_allotted", "date_connected")
    list_filter = ("partner", "status")
    search_fields = (
    "trl_stage__application__reference_no",
    "partner__name",
    )
    autocomplete_fields = ("trl_stage",)
    inlines = [MilestoneInline]

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        # after saving inline milestones, re-check this assignment's TRL stage
        assignment = form.instance
        if assignment.trl_stage_id:
            assignment.trl_stage.check_and_advance()


@admin.register(FollowUp)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = ("assignment", "sequence", "meeting_date")


@admin.register(TACMeeting)
class TACMeetingAdmin(admin.ModelAdmin):
    list_display = ("application", "meeting_ref_no", "meeting_date")
    search_fields = ("application__reference_no",)
    autocomplete_fields = ("application",)


@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ("assignment", "label", "description", "status", "date_achieved")
    search_fields = (
    "assignment__trl_stage__application__reference_no",
    "label",
    "description",
    )
    list_filter = ("status",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        obj.assignment.trl_stage.check_and_advance()


@admin.register(TRLProgressLog)
class TRLProgressLogAdmin(admin.ModelAdmin):
    list_display = ("application", "trl", "changed_on")
    search_fields = ("application__reference_no",)

@admin.register(StatusChangeLog)
class StatusChangeLogAdmin(admin.ModelAdmin):
    list_display = ("application", "from_status", "to_status", "created_at", "created_by")
    readonly_fields = [f.name for f in StatusChangeLog._meta.fields]

    def has_add_permission(self, request):
        return False  # audit log: system-generated only

@admin.register(AssignmentChangeLog)
class AssignmentChangeLogAdmin(admin.ModelAdmin):
    list_display = ("assignment", "field_changed", "old_value", "new_value", "created_by", "created_at")
    list_filter = ("field_changed", "created_by")
    search_fields = (
        "assignment__trl_stage__application__reference_no",
        "assignment__partner__name",
    )
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in AssignmentChangeLog._meta.fields]  # log entries are never edited by hand

    def has_add_permission(self, request):
        return False   # entries are only created by the save view, never typed in by hand    


# ---------------------------------------------------------------------------
# Custom order of models in the admin sidebar / home page
# ---------------------------------------------------------------------------

MODEL_ORDER = ["Applicant", "Application", "ApplicationTRLStage","KnowledgePartnerAssignment","Milestone","KnowledgePartner","PartnerMilestoneTemplate","TRLDefinition"]

_original_get_app_list = admin.site.get_app_list

def custom_get_app_list(request, *args, **kwargs):
    app_list = _original_get_app_list(request, *args, **kwargs)

    for app in app_list:
        app["models"].sort(
            key=lambda m: MODEL_ORDER.index(m["object_name"])
            if m["object_name"] in MODEL_ORDER
            else len(MODEL_ORDER)   # all other models go after these three
        )
    return app_list

admin.site.get_app_list = custom_get_app_list    