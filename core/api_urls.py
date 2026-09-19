from rest_framework.routers import DefaultRouter

from .api_views import ApplicationViewSet, KnowledgePartnerViewSet, TRLDefinitionViewSet

router = DefaultRouter()
router.register("applications", ApplicationViewSet, basename="application")
router.register("knowledge-partners", KnowledgePartnerViewSet, basename="knowledge-partner")
router.register("trl-definitions", TRLDefinitionViewSet, basename="trl-definition")

urlpatterns = router.urls
