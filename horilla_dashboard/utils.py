"""Utility functions for horilla_dashboard app."""

import json
import logging
import traceback
import uuid

from django.core.paginator import Paginator
from django.db.models import Q

from horilla_utils.methods import get_section_info_for_model
from horilla_utils.middlewares import _thread_local

logger = logging.getLogger(__name__)


class DefaultDashboardGenerator:
    """
    Simple dashboard generator for specific predefined models
    """

    extra_models = []

    def __init__(self, user, company=None):
        self.user = user
        self.company = company

        try:

            self.models = self.get_models()

        except ImportError:
            logger.warning("CRM models not found, using empty model list")
            self.models = []

    def get_models(self):
        """
        Child apps override this to return model list.
        """
        return self.extra_models

    def get_queryset(self, model_class):
        """Get filtered queryset for a model"""
        queryset = model_class.objects.all()

        app_label = model_class._meta.app_label
        model_name = model_class._meta.model_name

        has_view_all = self.user.has_perm(f"{app_label}.view_{model_name}")
        has_view_own = self.user.has_perm(f"{app_label}.view_own_{model_name}")

        if has_view_all:
            return queryset

        if has_view_own:

            if hasattr(model_class, "company") and self.company:
                queryset = queryset.filter(company=self.company)

            if hasattr(model_class, "OWNER_FIELDS"):
                owner_fields = model_class.OWNER_FIELDS
                if owner_fields and len(owner_fields) > 0:

                    q_objects = Q()
                    for field_name in owner_fields:
                        if hasattr(model_class, field_name):
                            q_objects |= Q(**{field_name: self.user})

                    if q_objects:
                        queryset = queryset.filter(q_objects)

            return queryset

        return queryset.none()

    def has_model_permission(self, model_class):
        """Check if user has either view or view_own permission for a model"""
        app_label = model_class._meta.app_label
        model_name = model_class._meta.model_name

        has_view_all = self.user.has_perm(f"{app_label}.view_{model_name}")
        has_view_own = self.user.has_perm(f"{app_label}.view_own_{model_name}")

        return has_view_all or has_view_own

    def generate_kpi_data(self):
        """Generate simple count KPIs"""
        kpis = []

        for model_info in self.models[:4]:
            try:
                model_class = model_info["model"]

                if self.has_model_permission(model_class):
                    count = self.get_queryset(model_info["model"]).count()

                    section_info = get_section_info_for_model(model_class)

                    kpi = {
                        "title": f"Total {model_info['name']}",
                        "value": count,
                        "icon": model_info["icon"],
                        "color": model_info["color"],
                        "url": section_info["url"],
                        "section": section_info["section"],
                        "type": "count",
                    }
                    kpis.append(kpi)

            except Exception as e:
                traceback.print_exc()
                logger.warning("Failed to generate KPI for %s:", e)

        return kpis

    def generate_chart_data(self):
        """Generate business-specific filtered charts"""
        charts = []

        for model_info in self.models[:5]:
            try:
                model_class = model_info["model"]

                if not self.has_model_permission(model_class):
                    continue

                queryset = self.get_queryset(model_class)
                count = queryset.count()

                if count == 0:
                    continue

                chart_func = model_info.get("chart_func")

                if callable(chart_func):
                    chart = chart_func(self, queryset, model_info)
                    charts.append(chart)

            except Exception as e:
                traceback.print_exc()
                logger.warning("Failed to generate chart for : %s", e)

        return charts

    def get_date_field(self, model_class):
        """Get the first date field from model"""
        for field in model_class._meta.fields:
            if field.get_internal_type() in ["DateField", "DateTimeField"]:
                return field.name

        return None

    def generate_table_data(self):
        tables = []
        for model_info in self.models:
            try:
                model_class = model_info["model"]
                if not self.has_model_permission(model_class):
                    continue

                table_func = model_info.get("table_func")
                if callable(table_func):
                    table = table_func(self, model_info)
                    if table:
                        tables.append(table)
            except Exception as e:
                traceback.print_exc()
                logger.warning("Failed to generate table for : %s", e)

        return tables

    def build_table_context(
        self,
        model_info,
        title,
        filter_kwargs,
        no_record_msg,
        view_id,
        request=None,
        table_fields=None,
    ):
        """
        Build table context with pagination for infinite scroll
        """

        try:
            request = getattr(_thread_local, "request", None)
            qs = self.get_queryset(model_info["model"])
            if filter_kwargs:
                qs = qs.filter(**filter_kwargs)

            sort_field = request.GET.get("sort", None) if request else None
            sort_direction = request.GET.get("direction", "asc") if request else "asc"
            if sort_field:
                prefix = "-" if sort_direction == "desc" else ""
                try:
                    qs = qs.order_by(f"{prefix}{sort_field}")
                except:
                    qs = qs.order_by("id")
            else:
                date_field = self.get_date_field(model_info["model"])
                order_field = f"-{date_field}" if date_field else "-pk"
                qs = qs.order_by(order_field)

            page = request.GET.get("page", 1) if request else 1
            paginator = Paginator(qs, 10)
            try:
                page_obj = paginator.get_page(page)
            except:
                page_obj = paginator.get_page(1)

            has_next = page_obj.has_next()
            next_page = page_obj.next_page_number() if has_next else None

            if table_fields is None:
                table_fields_func = model_info.get("table_fields_func")
                if callable(table_fields_func):
                    table_fields = table_fields_func(model_info["model"])
                else:
                    table_fields = None

            if not table_fields:
                return None

            columns = [(f["verbose_name"], f["name"]) for f in table_fields]
            filtered_ids = list(qs.values_list("id", flat=True))

            first_col_field = table_fields[0]["name"] if table_fields else None

            col_attrs = {}
            if first_col_field and hasattr(model_info["model"], "get_detail_url"):
                if self.has_model_permission(model_info["model"]):
                    col_attrs = {
                        first_col_field: {
                            "hx-get": f"{{get_detail_url}}?section=sales",
                            "hx-target": "#mainContent",
                            "hx-swap": "outerHTML",
                            "hx-push-url": "true",
                            "hx-select": "#mainContent",
                            "hx-select-oob": "#sideMenuContainer",
                            "class": "hover:text-primary-600",
                            "style": "cursor:pointer;",
                        }
                    }

            return {
                "id": f"table_{model_info['model']._meta.model_name}_{uuid.uuid4().hex[:8]}",
                "title": title,
                "queryset": page_obj.object_list,
                "columns": columns,
                "view_id": view_id,
                "model_name": model_info["model"]._meta.model_name,
                "model_verbose_name": model_info["model"]._meta.verbose_name_plural,
                "total_records_count": qs.count(),
                "bulk_select_option": False,
                "bulk_export_option": False,
                "bulk_update_option": False,
                "bulk_delete_enabled": False,
                "clear_session_button_enabled": False,
                "enable_sorting": True,
                "visible_actions": [],
                "action_method": None,
                "additional_action_button": [],
                "custom_bulk_actions": [],
                "search_url": "",
                "search_params": request.GET.urlencode() if request else "",
                "filter_fields": [],
                "filter_set_class": None,
                "table_class": True,
                "table_width": False,
                "table_height": False,
                "table_height_as_class": "h-[300px]",
                "header_attrs": {},
                "col_attrs": col_attrs,
                "selected_ids": filtered_ids,
                "selected_ids_json": json.dumps(filtered_ids),
                "current_sort": sort_field if request else "",
                "current_direction": sort_direction if request else "",
                "main_url": "",
                "view_type": "dashboard",
                "no_record_section": True,
                "no_record_msg": no_record_msg,
                "no_record_add_button": {},
                "page_obj": page_obj,
                "has_next": has_next,
                "next_page": next_page,
            }
        except Exception as e:
            logger.warning("Failed to generate table for : %s", e)
            return None
