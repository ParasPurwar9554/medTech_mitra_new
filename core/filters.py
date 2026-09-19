import django_filters as df

from .models import Application, KnowledgePartner


class ApplicationFilter(df.FilterSet):
    date_from = df.DateFilter(field_name="date_received", lookup_expr="gte")
    date_to = df.DateFilter(field_name="date_received", lookup_expr="lte")

    partner = df.ModelChoiceFilter(
        field_name="trl_stages__partner_assignments__partner",
        queryset=KnowledgePartner.objects.all(),
        label="Knowledge Partner",
    )

    class Meta:
        model = Application
        fields = {
            "status": ["exact"],
            "medtech_type": ["exact"],
            "risk_classification": ["exact"],
            "innovation_stage": ["exact"],
        }