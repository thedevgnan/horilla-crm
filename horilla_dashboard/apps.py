"""App configuration for dashboard app."""

from django.apps import AppConfig
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _


class HorillaDashboardConfig(AppConfig):
    """
    HorillaDashboardConfig App Configuration
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "horilla_dashboard"
    verbose_name = _("Dashboard")

    def get_api_paths(self):
        """
        Return API path configurations for this app.

        Returns:
            list: List of dictionaries containing path configuration
        """
        return [
            {
                "pattern": "/horilla_dashboard/",
                "view_or_include": "horilla_dashboard.api.urls",
                "name": "horilla_dashboard_api",
                "namespace": "horilla_dashboard",
            }
        ]

    def ready(self):
        try:
            # Auto-register this app's URLs and add to installed apps
            from django.urls import include, path

            from horilla.urls import urlpatterns

            # Add app URLs to main urlpatterns
            urlpatterns.append(
                path("dashboard/", include("horilla_dashboard.urls")),
            )

            __import__("horilla_dashboard.menu")
            __import__("horilla_dashboard.signals")
        except Exception as e:
            import logging

            logging.warning("HorillaDashboardConfig.ready failed: %s", e)
        super().ready()
