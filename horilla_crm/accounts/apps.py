"""App configuration for the Accounts module."""

from django.apps import AppConfig
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    """Configuration class for the Accounts app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "horilla_crm.accounts"
    verbose_name = _("Accounts")

    demo_data = {
        "files": [
            (10, "load_data/account.json"),
        ],
        "order": 5,
    }

    def get_api_paths(self):
        """
        Return API path configurations for this app.

        Returns:
            list: List of dictionaries containing path configuration
        """
        return [
            {
                "pattern": "crm/accounts/",
                "view_or_include": "horilla_crm.accounts.api.urls",
                "name": "horilla_crm_accounts_api",
                "namespace": "horilla_crm_accounts",
            }
        ]

    def ready(self):
        try:
            from django.urls import include, path

            from horilla.urls import urlpatterns

            urlpatterns.append(
                path("accounts/", include("horilla_crm.accounts.urls")),
            )

            __import__("horilla_crm.accounts.menu")  # noqa: F401
            __import__("horilla_crm.accounts.signals")  # noqa: F401
            __import__("horilla_crm.accounts.dashboard")

        except Exception as e:
            import logging

            logging.warning("AccountsConfig.ready failed: %s", e)
        super().ready()
