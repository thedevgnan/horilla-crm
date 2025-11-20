"""
URL patterns for horilla_crm.reports API
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from horilla_reports.api.views import (
    ReportViewSet,
    ReportFolderViewSet,
)

router = DefaultRouter()
router.register(r"reports", ReportViewSet)
router.register(r"report-folders", ReportFolderViewSet)

urlpatterns = [
    path("", include(router.urls)),
]