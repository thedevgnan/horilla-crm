from django.forms import JSONField
import django_filters

from horilla_generics.filters import HorillaFilterSet
from horilla_reports.models import Report

from .models import Report  # Ensure your Report model is imported


class ReportFilter(HorillaFilterSet):
    class Meta:
        model = Report
        fields = '__all__'
        exclude = ['additional_info']
        search_fields = ['name']
       