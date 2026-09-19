from django.conf import settings
from django.db import models
from django.utils import timezone


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
    """CDSCO, AMTZ, AIM-NITI Aayog, INTENT, HTA, BIS, etc."""
    name = models.CharField(max_length=150, unique=True)
    short_code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    applicable_trl_levels = models.CharField(
        max_length=100, blank=True,
        help_text="e.g. 'TRL-4,TRL-5,TRL-6' — from the KP-TRL master sheet"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.short_code


class TRLDefinition(models.Model):
    """Master table for the 9 Technology Readiness Levels and their milestones.
    Kept per MedTech type since milestone wording differs between Device,
    Vaccine, and Assistive Technology tracks."""

    class MedTechCategoryType(models.TextChoices):
        MMDD = "MM-DD", "Device & Diagnostics"
        MMVT = "MM-VT", "Vaccines & Therapeutics"
        MMAT = "MM-AT", "Assitive Technologies"

    medtech_type = models.CharField(
        max_length=10,
        choices=MedTechCategoryType.choices,
        blank=True,
        help_text="Leave blank if this TRL definition applies to all MedTech types.",
    )
    level = models.PositiveSmallIntegerField()  # 1..9
    name = models.CharField(max_length=150)  # "TRL-1 Ideation"

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
    """Master milestone checklist per Knowledge Partner (from 'Partners Milestones' sheet)."""
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
    """The innovator / organisation. Kept separate so one person/org can
    submit multiple applications over time without duplicating identity data."""

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
    """One innovation/technology submitted for MedTech Mitra support.
    This is the aggregate root the dashboard reports against."""

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
    """One row per TRL level that an application has entered.
    This is the 'box' you click on in the horizontal TRL1->TRL9 tracker.
    """

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
        print(">>> milestone count:", all_milestones.count())
        for m in all_milestones:
            print("   -", m.id, m.status)

        if not all_milestones.exists():
            print(">>> STOP: no milestones")
            return
        if all_milestones.exclude(status=Milestone.MilestoneStatus.CLOSED).exists():
            print(">>> STOP: some milestone not closed")
            return
        if self.status == self.StageStatus.COMPLETED:
            print(">>> STOP: stage already completed")
            return

        print(">>> ADVANCING NOW")
        self.status = self.StageStatus.COMPLETED
        self.completed_date = timezone.now().date()
        self.save()

        next_trl = TRLDefinition.objects.filter(level=self.trl.level + 1).first()
        print(">>> next_trl found:", next_trl)
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
    """A follow-up query/meeting cycle for a KP assignment (can repeat: 1st, 2nd, ...)."""
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


class TRLProgressLog(TimeStampedModel):
    """Audit trail: every time an application's TRL changes, log it.
    This is what powers the 'TRL progression over time' dashboard chart
    and gives a government auditor a defensible history."""
    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                     related_name="trl_history")
    trl = models.ForeignKey(TRLDefinition, on_delete=models.PROTECT, related_name="+")
    changed_on = models.DateField(default=timezone.now)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["application", "changed_on"]


class StatusChangeLog(TimeStampedModel):
    """Generic audit trail for Application.status transitions — required for
    a government system so every change is traceable to a user and timestamp."""
    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                     related_name="status_history")
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
