from rest_framework import serializers

from .models import Application, KnowledgePartner, TRLDefinition


class ApplicationListSerializer(serializers.ModelSerializer):
    applicant_name = serializers.CharField(source="applicant.name", read_only=True)
    current_trl_level = serializers.IntegerField(source="current_trl.level", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Application
        fields = [
            "id", "reference_no", "technology_name", "applicant_name",
            "medtech_type", "risk_classification", "status", "status_display",
            "current_trl_level", "date_received", "closure_date",
        ]


class KnowledgePartnerSerializer(serializers.ModelSerializer):
    active_applications = serializers.IntegerField(read_only=True)

    class Meta:
        model = KnowledgePartner
        fields = ["id", "name", "short_code", "applicable_trl_levels", "active_applications"]


class TRLDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TRLDefinition
        fields = ["level", "name"]
