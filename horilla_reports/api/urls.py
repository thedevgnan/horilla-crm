"""
URL patterns for horilla_reports API
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from horilla_reports.api.views import ReportFolderViewSet, ReportViewSet

router = DefaultRouter()
router.register(r"reports", ReportViewSet)
router.register(r"report-folders", ReportFolderViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
