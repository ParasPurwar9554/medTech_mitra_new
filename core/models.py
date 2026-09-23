from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


class TimeStampedModel(models.Model):
    """Abstract base: created/updated audit fields for every table."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Master / lookup data
# ---------------------------------------------------------------------------

class KnowledgePartner(models.Model):
    name = models.CharField(max_length=150, unique=True)
    short_code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    applicable_trl_levels = models.CharField(
        max_length=100, blank=True,
        help_text="e.g. 'TRL-4,TRL-5,TRL-6' — from the KP-TRL master sheet"
    )
    is_active = models.BooleanField(default=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL,null=True, blank=True,on_delete=models.SET_NULL,related_name="partner_profile",)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.short_code


class TRLDefinition(models.Model):

    class MedTechCategoryType(models.TextChoices):
        MMDD = "MM-DD", "Device & Diagnostics"
        MMVT = "MM-VT", "Vaccines & Therapeutics"
        MMAT = "MM-AT", "Assitive Technologies"

    medtech_type = models.CharField(
        max_length=10,
        choices=MedTechCategoryType.choices,
        help_text="Leave blank if this TRL definition applies to all MedTech types.",
    )
  
    level = models.PositiveSmallIntegerField(
        choices=[(i, f"Level {i}") for i in range(1, 10)],
        validators=[MinValueValidator(1), MaxValueValidator(9)],
    )
    name = models.CharField(max_length=150)

    milestone_1 = models.CharField(max_length=255, blank=True)
    milestone_2 = models.CharField(max_length=255, blank=True)
    milestone_3 = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["medtech_type", "level"]
        unique_together = ("level", "medtech_type")  # one row per level per medtech type

    def __str__(self):
        if self.medtech_type:
            return f"{self.name} ({self.get_medtech_type_display()})"
        return self.name


class PartnerMilestoneTemplate(models.Model):
    partner = models.ForeignKey(KnowledgePartner, on_delete=models.CASCADE,
                                 related_name="milestone_templates")
    sequence = models.PositiveSmallIntegerField()
    description = models.CharField(max_length=255)

    class Meta:
        ordering = ["partner", "sequence"]
        unique_together = ("partner", "sequence")

    def __str__(self):
        return f"{self.partner.short_code} #{self.sequence}: {self.description[:40]}"


# ---------------------------------------------------------------------------
# Applicant & Application
# ---------------------------------------------------------------------------

class Applicant(TimeStampedModel):

    name = models.CharField(max_length=200)
    innovator_name = models.CharField(max_length=200, blank=True)
    affiliation_type = models.CharField(
        max_length=30,
        choices=[("individual", "Individual"), ("institute", "Institute/Company")],
        default="individual",
    )
    affiliation_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    contact_number = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["email"]), models.Index(fields=["name"])]

    def __str__(self):
        return self.name


class Application(TimeStampedModel):

    class MedTechType(models.TextChoices):
        MMDD = "MM-DD", "Device & Diagnostics"
        MMVT = "MM-VT", "Vaccines & Therapeutics"
        MMAT = "MM-AT", "Assitive Technologies"

    class RiskClass(models.TextChoices):
        CLASS_A = "A", "Class A"
        CLASS_B = "B", "Class B"
        CLASS_C = "C", "Class C"
        CLASS_D = "D", "Class D"
        UNKNOWN = "unknown", "Not Known"

    class DeviceCategory(models.TextChoices):
        PREDICATE = "predicate", "Medical device/IVD having available predicate device"
        INVESTIGATIONAL = "investigational", "Investigational Medical Device/IVD"

    class InnovationStage(models.TextChoices):
        CONCEPT_DEV = "concept_dev", "Under Concept Development"
        CONCEPT_PROVED = "concept_proved", "Concept Proved with Prototype"
        BENCH_TESTING = "bench_testing", "Bench Testing of Prototypes for Manufacturing"
        CLINICAL_TESTING = "clinical_testing", "Clinical Testing for National Program"
        CLINICAL_INVESTIGATION = "clinical_investigation", "Clinical Investigation/Performance Evaluation"

    class SupportCategory(models.TextChoices):
        ADVISORY = "advisory", "Advisory"
        HANDHOLDING = "handholding", "Handholding"

    class ApplicationStatus(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"
        CLOSED = "closed", "Completed"
        RESOLVED="resolved","Resolved"
        ASSIGNED_TO_KP = "assigned_to_kp", "Assigned to KP"

    reference_no = models.CharField(max_length=50, unique=True,)
    applicant = models.ForeignKey(Applicant, on_delete=models.PROTECT,
                                   related_name="applications")
    medtech_type = models.CharField(max_length=10, choices=MedTechType.choices)
    technology_name = models.CharField(max_length=255)
    intended_use_statement = models.TextField(blank=True)
    use_environment = models.CharField(max_length=255, blank=True)
    target_population = models.TextField(blank=True)
    area_of_application = models.TextField(blank=True)

    risk_classification = models.CharField(max_length=10, choices=RiskClass.choices,
                                            default=RiskClass.UNKNOWN)
    device_category = models.CharField(max_length=20, choices=DeviceCategory.choices,
                                        blank=True)
    innovation_stage = models.CharField(max_length=30, choices=InnovationStage.choices,
                                         blank=True)
    support_type_requested = models.CharField(max_length=255, blank=True)
    support_category = models.CharField(max_length=15, choices=SupportCategory.choices,
                                         blank=True)
    test_license_received = models.BooleanField(null=True, blank=True)
    query_text = models.TextField(blank=True)

    date_received = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=ApplicationStatus.choices,
                               default=ApplicationStatus.IN_PROGRESS, db_index=True)

    initial_trl = models.ForeignKey(TRLDefinition, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    initial_trl_date = models.DateField(null=True, blank=True)
    current_trl = models.ForeignKey(TRLDefinition, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    current_trl_date = models.DateField(null=True, blank=True)
    final_trl = models.ForeignKey(TRLDefinition, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    final_status_notes = models.TextField(blank=True)
    final_status_date = models.DateField(null=True, blank=True)
    closure_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-date_received"]
        indexes = [
            models.Index(fields=["medtech_type"]),
            models.Index(fields=["risk_classification"]),
            models.Index(fields=["date_received"]),
        ]

    def __str__(self):
        return f"{self.reference_no} - {self.technology_name}"


# ---------------------------------------------------------------------------
# TAC (Technical Advisory Committee) review workflow
# ---------------------------------------------------------------------------

class TACMeeting(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                     related_name="tac_meetings")
    meeting_ref_no = models.CharField(max_length=50, blank=True)
    meeting_date = models.DateField(null=True, blank=True)
    first_response_date = models.DateField(null=True, blank=True)
    first_meeting_outcome = models.TextField(blank=True)
    response_emailed_date = models.DateField(null=True, blank=True)
    final_tac_response = models.TextField(blank=True)
    action_taken = models.TextField(blank=True)

    class Meta:
        ordering = ["meeting_date"]

    def __str__(self):
        return f"TAC #{self.meeting_ref_no or self.pk} for {self.application.reference_no}"


class ApplicationTRLStage(TimeStampedModel):

    class StageStatus(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"

    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                     related_name="trl_stages")
    trl = models.ForeignKey(TRLDefinition, on_delete=models.PROTECT,
                             related_name="+")
    status = models.CharField(max_length=15, choices=StageStatus.choices,
                               default=StageStatus.IN_PROGRESS)
    start_date = models.DateField(default=timezone.now)
    completed_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["application", "trl__level"]
        unique_together = ("application", "trl")  # one row per TRL per app

    def __str__(self):
        return f"{self.application.reference_no} - {self.trl.name} ({self.status})"

    def check_and_advance(self):
        """If every milestone under every partner in this stage is closed,
        mark this stage completed and create/move to the next TRL stage."""
        from .models import Milestone

        print(">>> check_and_advance called for stage:", self.id, self.trl.name, self.status)

        all_milestones = Milestone.objects.filter(assignment__trl_stage=self)
   
        for m in all_milestones:
            print("   -", m.id, m.status)

        if not all_milestones.exists():
            return
        if all_milestones.exclude(status=Milestone.MilestoneStatus.CLOSED).exists():
            return
        if self.status == self.StageStatus.COMPLETED:
            return

        self.status = self.StageStatus.COMPLETED
        self.completed_date = timezone.now().date()
        self.save()

        next_trl = TRLDefinition.objects.filter(level=self.trl.level + 1).first()
        if next_trl:
            ApplicationTRLStage.objects.get_or_create(
                application=self.application, trl=next_trl,
            )
            self.application.current_trl = next_trl
            self.application.current_trl_date = timezone.now().date()
            self.application.save()
            print(">>> DONE, application current_trl set to", next_trl)
# ---------------------------------------------------------------------------
# Knowledge Partner handholding workflow
# ---------------------------------------------------------------------------

class KnowledgePartnerAssignment(TimeStampedModel):
    class SupportStatus(models.TextChoices):
        #REQUESTED = "requested", "Support Requested"
        CONNECTED = "connected", "Partner Connected"
        #IN_PROGRESS = "in_progress", "Support In Progress"
        COMPLETED = "completed", "Support Completed"

    trl_stage = models.ForeignKey(ApplicationTRLStage, on_delete=models.CASCADE,
                                   related_name="partner_assignments",null=True)   # <-- changed
    partner = models.ForeignKey(KnowledgePartner, on_delete=models.PROTECT,
                                 related_name="assignments")
    date_allotted = models.DateField(null=True, blank=True)
    date_connected = models.DateField(null=True, blank=True)
    support_requested = models.TextField(blank=True)
    support_to_be_provided = models.TextField(blank=True)
    support_provided = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=SupportStatus.choices,
                               default=SupportStatus.CONNECTED)
    advisory_remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_allotted"]

    def __str__(self):
        return f"{self.trl_stage} -> {self.partner.short_code}"


class FollowUp(TimeStampedModel):
    assignment = models.ForeignKey(KnowledgePartnerAssignment, on_delete=models.CASCADE,
                                    related_name="follow_ups")
    sequence = models.PositiveSmallIntegerField(default=1)
    query_text = models.TextField(blank=True)
    meeting_date = models.DateField(null=True, blank=True)
    response = models.TextField(blank=True)

    class Meta:
        ordering = ["assignment", "sequence"]
        unique_together = ("assignment", "sequence")


# ---------------------------------------------------------------------------
# TRL milestone progress (audit trail of technical maturity over time)
# ---------------------------------------------------------------------------

class Milestone(TimeStampedModel):
    class MilestoneStatus(models.TextChoices):
        #OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        CLOSED = "closed", "Completed"
        NOTYETSTART = "notyeststart","Not Yet Started"

    assignment = models.ForeignKey(KnowledgePartnerAssignment, on_delete=models.CASCADE,
                                    related_name="milestones")           # <-- changed
    template = models.ForeignKey(PartnerMilestoneTemplate, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    label = models.CharField(max_length=100, blank=True)   # "M-1", "M-2"...
    description = models.CharField(max_length=255, blank=True)
    action_taken = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=MilestoneStatus.choices,
                               default=MilestoneStatus.IN_PROGRESS)             # <-- changed
    date_achieved = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["assignment", "id"]


class AssignmentChangeLog(TimeStampedModel):
    """Audit trail: every milestone status change and every remarks change,
    with who made it (created_by, from TimeStampedModel) and when."""

    class FieldChanged(models.TextChoices):
        MILESTONE_STATUS = "milestone_status", "Milestone Status"
        REMARKS = "remarks", "Remarks"

    assignment = models.ForeignKey(
        KnowledgePartnerAssignment, on_delete=models.CASCADE,
        related_name="change_logs",
    )
    milestone = models.ForeignKey(
        Milestone, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="change_logs",
        help_text="Set only when field_changed is Milestone Status.",
    )
    field_changed = models.CharField(max_length=20, choices=FieldChanged.choices)
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.assignment} — {self.get_field_changed_display()} by {self.created_by}"

class TRLProgressLog(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                     related_name="trl_history")
    trl = models.ForeignKey(TRLDefinition, on_delete=models.PROTECT, related_name="+")
    changed_on = models.DateField(default=timezone.now)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["application", "changed_on"]


class StatusChangeLog(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                     related_name="status_history")
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
