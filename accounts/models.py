from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):

    class Role(models.TextChoices):
        DELEGATED_ADMIN = "1", "Delegated Admin"
        MM_SECRETARIAT = "2", "MM Secretariat"
        INNOVATOR = "3", "Innovator"
        SUPPORT_PARTNER = "4", "Support Partner"

    role = models.CharField(
        max_length=30,
        choices=Role.choices,
        default=Role.INNOVATOR,
    )

    knowledge_partner = models.ForeignKey(
        "core.KnowledgePartner",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff_users",
        help_text="Only set for users with role=Support Partner",
    )

    applicant = models.OneToOneField(
        "core.Applicant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="user",
        help_text="Only set for users with role=Innovator",
    )

    @property
    def is_admin_role(self):
        return self.role == self.Role.DELEGATED_ADMIN

    @property
    def is_secretariat(self):
        return self.role == self.Role.MM_SECRETARIAT

    @property
    def is_innovator(self):
        return self.role == self.Role.INNOVATOR

    @property
    def is_support_partner(self):
        return self.role == self.Role.SUPPORT_PARTNER