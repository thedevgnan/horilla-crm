from django.apps import apps
from django.db import models

from horilla.registry.feature import FEATURE_REGISTRY

# Define your horilla_mail helper methods here


def limit_content_types():
    """
    Limit ContentType choices to only models that have
    'reports_includable = True'.
    """
    includable_models = []
    for model in FEATURE_REGISTRY["report_models"]:
        includable_models.append(model._meta.model_name.lower())

    return models.Q(model__in=includable_models)
