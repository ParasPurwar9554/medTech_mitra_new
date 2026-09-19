from django.db.models import Count, Q
from rest_framework import viewsets

from .filters import ApplicationFilter
from .models import Application, KnowledgePartner, TRLDefinition
from .serializers import ApplicationListSerializer, KnowledgePartnerSerializer, TRLDefinitionSerializer


class ApplicationViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API for application records, filterable by status/type/risk/
    knowledge-partner/date-range. Backs the dashboard's drill-down table and
    any external e-Gov integrations."""
    queryset = Application.objects.select_related("applicant", "current_trl").all()
    serializer_class = ApplicationListSerializer
    filterset_class = ApplicationFilter
    search_fields = ["reference_no", "technology_name", "applicant__name"]


class KnowledgePartnerViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = KnowledgePartnerSerializer

    def get_queryset(self):
        return KnowledgePartner.objects.annotate(
            active_applications=Count(
                "assignments",
                filter=~Q(assignments__status="completed"),
                distinct=True,
            )
        )


class TRLDefinitionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TRLDefinition.objects.all()
    serializer_class = TRLDefinitionSerializer
