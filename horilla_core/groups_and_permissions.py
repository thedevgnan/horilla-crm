from functools import cached_property

from django.apps import apps
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from horilla.registry.permission_registry import PERMISSION_EXEMPT_MODELS
from horilla_core.decorators import htmx_required, permission_required_or_denied
from horilla_core.models import FieldPermission, HorillaUser, Role
from horilla_generics.views import HorillaListView, HorillaTabView


class PermissionUtils:
    """Utility class to handle common permission-related logic."""

    FIXED_ORDER = [
        "add",
        "change",
        "view",
        "delete",
        "view_own",
        "change_own",
        "can_import",
    ]

    PERMISSION_MAP = {
        "add": "Create",
        "change": "Change",
        "view": "View",
        "delete": "Delete",
        "view_own": "View Own",
        "change_own": "Change Own",
        "can_import": "Import",
    }

    @staticmethod
    def get_model_permissions(app_label, model_name, permissions=None):
        """Retrieve permissions for a specific model."""
        if permissions is None:
            permissions = Permission.objects.filter(
                content_type__app_label=app_label,
                content_type__model=model_name.lower(),
            )
        simplified_permissions = []
        for key in PermissionUtils.FIXED_ORDER:
            expected_codename = f"{key}_{model_name.lower()}"
            perm = permissions.filter(codename=expected_codename).first()
            if perm:
                simplified_permissions.append(
                    {
                        "id": perm.id,
                        "codename": perm.codename,
                        "label": PermissionUtils.PERMISSION_MAP[key],
                    }
                )

        standard_codenames = [
            f"{key}_{model_name.lower()}" for key in PermissionUtils.FIXED_ORDER
        ]
        custom_permissions = permissions.exclude(codename__in=standard_codenames)

        for perm in custom_permissions:
            label = perm.name if perm.name else perm.codename.replace("_", " ").title()

            simplified_permissions.append(
                {
                    "id": perm.id,
                    "codename": perm.codename,
                    "label": label,
                }
            )

        return simplified_permissions

    @staticmethod
    def get_all_models_data(user=None, role=None, search_query=None):
        """Retrieve all models with their permissions, optionally checking user or role permissions."""

        all_models = []
        for model in apps.get_models():
            model_name = model.__name__
            app_label = model._meta.app_label

            if model_name in PERMISSION_EXEMPT_MODELS:
                continue

            if search_query:
                verbose_name = model._meta.verbose_name.title().lower()
                verbose_name_plural = model._meta.verbose_name_plural.title().lower()
                search_lower = search_query.lower()

                if not (
                    search_lower in verbose_name
                    or search_lower in verbose_name_plural
                    or search_lower in model_name.lower()
                    or search_lower in app_label.lower()
                ):
                    continue

            permissions = PermissionUtils.get_model_permissions(app_label, model_name)
            if permissions:
                model_data = {
                    "app_label": app_label,
                    "model_name": model_name,
                    "verbose_name": model._meta.verbose_name.title(),
                    "verbose_name_plural": model._meta.verbose_name_plural.title(),
                    "permissions": permissions,
                    "is_managed": model._meta.managed,
                }
                if user or role:
                    all_permissions_checked = True
                    has_any_permission = False
                    for perm in permissions:
                        has_perm = (
                            user.user_permissions.filter(id=perm["id"]).exists()
                            if user
                            else role.permissions.filter(id=perm["id"]).exists()
                        )
                        perm["has_perm"] = has_perm
                        if has_perm:
                            has_any_permission = True
                        else:
                            all_permissions_checked = False
                    model_data["select_all_checked"] = (
                        all_permissions_checked
                        and has_any_permission
                        and len(permissions) > 0
                    )
                all_models.append(model_data)
        return sorted(
            all_models, key=lambda m: (m["is_managed"], m["app_label"], m["model_name"])
        )


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class ModelFieldsModalView(LoginRequiredMixin, TemplateView):
    """
    View to display model fields in a modal for field-level permissions
    Supports both role-based and user-based contexts
    """

    template_name = "permissions/field_permissions_modal.html"

    def get(self, request, app_label, model_name, *args, **kwargs):

        role_id = kwargs.get("role_id")
        user_id = kwargs.get("user_id")
        context_type = request.GET.get("context", "role")

        selected_user_ids = None
        selected_users_count = 0
        if context_type == "bulk":
            user_ids_str = request.GET.get("selected_user_ids", "")
            if user_ids_str:
                try:
                    selected_user_ids = [
                        int(uid.strip())
                        for uid in user_ids_str.split(",")
                        if uid.strip()
                    ]
                    selected_users_count = len(selected_user_ids)
                except (ValueError, AttributeError):
                    selected_user_ids = None

        role = None
        user = None

        if role_id:
            try:
                role = get_object_or_404(Role, id=role_id)
            except:
                messages.error(request, _("Role does not exist"))
                return HttpResponse("<script>$('#reloadButton').click();</script>")

        if user_id:
            try:
                user = get_object_or_404(HorillaUser, id=user_id)
            except:
                messages.error(request, _("User does not exist"))
                return HttpResponse("<script>$('#reloadButton').click();</script>")

        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            messages.error(request, _("Model not found"))
            return HttpResponse("")

        if not model._meta.managed:
            messages.info(
                request, _("Field-level permissions are not available for this model.")
            )
            return HttpResponse(
                "<script>closeModal(); $('#reloadButton').click();$('#reloadMessagesButton').click();</script>"
            )

        content_type = ContentType.objects.get_for_model(model)

        existing_permissions = {}
        role_inherited_permissions = {}

        if role:
            field_perms = FieldPermission.objects.filter(
                role=role, content_type=content_type
            )
            for perm in field_perms:
                existing_permissions[perm.field_name] = perm.permission_type

        elif user:
            user_field_perms = FieldPermission.objects.filter(
                user=user, content_type=content_type
            )
            for perm in user_field_perms:
                existing_permissions[perm.field_name] = perm.permission_type

            if hasattr(user, "role") and user.role:
                role_field_perms = FieldPermission.objects.filter(
                    role=user.role, content_type=content_type
                )
                for perm in role_field_perms:
                    role_inherited_permissions[perm.field_name] = perm.permission_type
                    if perm.field_name not in existing_permissions:
                        existing_permissions[perm.field_name] = perm.permission_type

        allowed_fields = getattr(model, "field_permissions", None)
        if not isinstance(allowed_fields, (list, tuple, set)):
            allowed_fields = None

        model_defaults = getattr(model, "default_field_permissions", {})

        fields = []

        for field in model._meta.get_fields():
            if field.many_to_many or field.one_to_many or field.one_to_one:
                continue

            field_name = field.name

            if allowed_fields is not None and field_name not in allowed_fields:
                continue

            verbose_name = getattr(field, "verbose_name", field_name).title()

            if field_name in existing_permissions:
                current_permission = existing_permissions[field_name]
            elif field_name in model_defaults:
                current_permission = model_defaults[field_name]
            else:
                current_permission = "readwrite"

            # current_permission = existing_permissions.get(field_name, "readwrite")

            fields.append(
                {
                    "name": field_name,
                    "verbose_name": verbose_name,
                    "field_type": field.__class__.__name__,
                    "current_permission": current_permission,
                }
            )

        context = {
            "role": role,
            "user": user,
            "model": model,
            "model_name": model_name,
            "app_label": app_label,
            "verbose_name": model._meta.verbose_name.title(),
            "fields": fields,
            "context_type": context_type,
            "role_id": role_id,
            "user_id": user_id,
            "is_bulk": context_type == "bulk",
            "selected_user_ids": (
                ",".join(map(str, selected_user_ids)) if selected_user_ids else ""
            ),
            "selected_users_count": selected_users_count,
        }

        return HttpResponse(render_to_string(self.template_name, context, request))


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class SaveBulkFieldPermissionsView(LoginRequiredMixin, View):
    """
    Save field permissions for multiple users at once (bulk assignment)
    """

    def post(self, request, *args, **kwargs):

        user_ids_str = request.POST.get("user_ids", "")
        if not user_ids_str:
            messages.error(
                request, _("No users selected. Please select at least one user.")
            )
            return HttpResponse(
                "<script>closeModal(); $('#reloadMessagesButton').click();</script>"
            )

        try:
            user_ids = [
                int(uid.strip()) for uid in user_ids_str.split(",") if uid.strip()
            ]
        except (ValueError, AttributeError):
            messages.error(request, _("Invalid user selection."))
            return HttpResponse(
                "<script>closeModal(); $('#reloadMessagesButton').click();</script>"
            )

        if not user_ids:
            messages.error(request, _("Please select at least one user."))
            return HttpResponse(
                "<script>closeModal(); $('#reloadMessagesButton').click();</script>"
            )

        app_label = request.POST.get("app_label")
        model_name = request.POST.get("model_name")

        try:
            model = apps.get_model(app_label, model_name)
            content_type = ContentType.objects.get_for_model(model)
        except LookupError:
            messages.error(request, _("Model not found"))
            return HttpResponse(
                "<script>closeModal(); $('#reloadMessagesButton').click();</script>"
            )

        field_permissions = {}
        for key, value in request.POST.items():
            if key.startswith("field-"):
                field_name = key.replace("field-", "")
                field_permissions[field_name] = value

        if not field_permissions:
            messages.warning(request, _("No field permissions to save."))
            return HttpResponse(
                "<script>closeModal(); $('#reloadMessagesButton').click();</script>"
            )

        try:
            users = HorillaUser.objects.filter(id__in=user_ids, is_superuser=False)

            if not users.exists():
                messages.error(request, _("No valid users found."))
                return HttpResponse(
                    "<script>closeModal(); $('#reloadMessagesButton').click();</script>"
                )

            for user in users:
                for field_name, permission_type in field_permissions.items():
                    FieldPermission.objects.update_or_create(
                        user=user,
                        content_type=content_type,
                        field_name=field_name,
                        defaults={"permission_type": permission_type},
                    )

            messages.success(
                request,
                _(
                    "Successfully saved {count} field permission(s) for {user_count} user(s) on {model}."
                ).format(
                    count=len(field_permissions),
                    user_count=users.count(),
                    model=model._meta.verbose_name.title(),
                ),
            )

            return HttpResponse(
                "<script>closeModal(); $('#reloadMessagesButton').click();</script>"
            )

        except Exception as e:
            messages.error(
                request,
                _("Error saving field permissions: {error}").format(error=str(e)),
            )
            return HttpResponse(
                "<script>closeModal(); $('#reloadMessagesButton').click();</script>"
            )


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class UpdateFieldPermissionView(LoginRequiredMixin, View):
    """
    Update field-level permission for a user or role
    """

    def post(self, request, *args, **kwargs):

        role_id = kwargs.get("role_id")
        user_id = kwargs.get("user_id")
        app_label = kwargs.get("app_label")
        model_name = kwargs.get("model_name")
        field_name = kwargs.get("field_name")
        permission_type = request.POST.get("permission_type")

        if not permission_type in ["readonly", "readwrite", "hidden"]:
            return JsonResponse(
                {"success": False, "message": "Invalid permission type"}
            )

        try:
            model = apps.get_model(app_label, model_name)
            content_type = ContentType.objects.get_for_model(model)
        except LookupError:
            return JsonResponse({"success": False, "message": "Model not found"})

        try:
            if role_id:
                role = get_object_or_404(Role, id=role_id)
                field_perm, created = FieldPermission.objects.update_or_create(
                    role=role,
                    content_type=content_type,
                    field_name=field_name,
                    defaults={"permission_type": permission_type},
                )
                target_name = role.role_name
            elif user_id:
                user = get_object_or_404(HorillaUser, id=user_id)
                field_perm, created = FieldPermission.objects.update_or_create(
                    user=user,
                    content_type=content_type,
                    field_name=field_name,
                    defaults={"permission_type": permission_type},
                )
                target_name = user.get_full_name()
            else:
                return JsonResponse(
                    {
                        "success": False,
                        "message": "Either role_id or user_id must be provided",
                    }
                )

            action = "created" if created else "updated"
            messages.success(
                request,
                f"Field permission for '{field_name}' {action} successfully for {target_name}",
            )

        except Exception as e:
            return JsonResponse(
                {"success": False, "message": f"Error updating permission: {str(e)}"}
            )


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class SaveAllFieldPermissionsView(LoginRequiredMixin, View):
    """
    Save all field permissions at once when user clicks 'Save Changes'
    """

    def post(self, request, *args, **kwargs):

        role_id = kwargs.get("role_id")
        user_id = kwargs.get("user_id")
        app_label = request.POST.get("app_label")
        model_name = request.POST.get("model_name")

        try:
            model = apps.get_model(app_label, model_name)
            content_type = ContentType.objects.get_for_model(model)
        except LookupError:
            messages.error(request, _("Model not found"))
            return HttpResponse("<script>closeModal();</script>")

        field_permissions = {}
        for key, value in request.POST.items():
            if key.startswith("field-"):
                field_name = key.replace("field-", "")
                field_permissions[field_name] = value

        try:
            saved_count = 0
            if role_id:
                role = get_object_or_404(Role, id=role_id)
                for field_name, permission_type in field_permissions.items():
                    FieldPermission.objects.update_or_create(
                        role=role,
                        content_type=content_type,
                        field_name=field_name,
                        defaults={"permission_type": permission_type},
                    )
                    saved_count += 1
                target_name = role.role_name

            elif user_id:
                user = get_object_or_404(HorillaUser, id=user_id)
                for field_name, permission_type in field_permissions.items():
                    FieldPermission.objects.update_or_create(
                        user=user,
                        content_type=content_type,
                        field_name=field_name,
                        defaults={"permission_type": permission_type},
                    )
                    saved_count += 1
                target_name = user.get_full_name()
            else:
                messages.error(request, _("Either role or user must be specified"))
                return HttpResponse("<script>closeModal();</script>")

            messages.success(
                request,
                f"Successfully saved {saved_count} field permissions for {target_name}",
            )

            return HttpResponse(
                "<script>closeModal(); $('#reloadButton').click();$('#reloadMessagesButton').click();</script>"
            )

        except Exception as e:
            messages.error(request, f"Error saving field permissions: {str(e)}")
            return HttpResponse("<script>closeModal();</script>")


@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class GroupPermissionView(LoginRequiredMixin, TemplateView):
    template_name = "permissions/group_perm_view.html"


@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class GroupPermissionTabView(LoginRequiredMixin, HorillaTabView):
    """
    Tab view for permission
    """

    view_id = "group-permission-view"
    background_class = "bg-primary-100 rounded-md"

    @cached_property
    def tabs(self):
        if self.request.user.has_perm("horilla_core.view_company"):
            return [
                {
                    "title": _("Groups"),
                    "url": reverse_lazy("horilla_core:group_tab"),
                    "target": "group-view-content",
                    "id": "group-detail-view",
                },
                {
                    "title": _("Permissions"),
                    "url": reverse_lazy("horilla_core:permission_tab"),
                    "target": "permission-view-content",
                    "id": "permission-detail-view",
                },
                {
                    "title": _("Super Users"),
                    "url": reverse_lazy("horilla_core:super_user_tab"),
                    "target": "super-user-view-content",
                    "id": "super-user-detail-view",
                },
            ]
        return []


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class GroupTab(LoginRequiredMixin, TemplateView):
    template_name = "permissions/group.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["roles"] = Role.objects.all().order_by("role_name")
        context["all_models"] = PermissionUtils.get_all_models_data()
        return context


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class RolePermissionsView(LoginRequiredMixin, TemplateView):
    template_name = "permissions/group_role_detail.html"

    def get(self, request, *args, **kwargs):
        role_id = kwargs.get("role_id")
        try:
            role = get_object_or_404(Role, id=role_id)
        except:
            messages.error(request, _("Role does not exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role_id = self.kwargs.get("role_id")

        role = get_object_or_404(Role, id=role_id)

        context["role"] = role
        context["role_id"] = role_id
        context["all_models"] = PermissionUtils.get_all_models_data(role=role)

        return context


# search for models


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class SearchRoleModelsView(LoginRequiredMixin, TemplateView):
    """
    View to search and filter models in role permissions view
    """

    template_name = "permissions/search_permission/role_models_list.html"

    def get(self, request, role_id, *args, **kwargs):
        try:
            role = get_object_or_404(Role, id=role_id)
        except:
            messages.error(request, _("Role does not exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        search_query = request.GET.get("search", "").strip()

        context = {
            "role": role,
            "all_models": PermissionUtils.get_all_models_data(
                role=role, search_query=search_query
            ),
        }
        return HttpResponse(render_to_string(self.template_name, context, request))


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class SearchUserModelsView(LoginRequiredMixin, TemplateView):
    """
    View to search and filter models in role permissions view
    """

    template_name = "permissions/search_permission/user_models_list.html"

    def get(self, request, user_id, *args, **kwargs):
        try:
            user = get_object_or_404(HorillaUser, id=user_id)
        except:
            messages.error(request, _("User does not exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        search_query = request.GET.get("search", "").strip()

        context = {
            "user": user,
            "all_models": PermissionUtils.get_all_models_data(
                user=user, search_query=search_query
            ),
        }
        return HttpResponse(render_to_string(self.template_name, context, request))


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class SearchAssignModelsView(LoginRequiredMixin, TemplateView):
    """
    Search view for assign permissions form (no specific user/role)
    """

    template_name = "permissions/search_permission/assign_models_list.html"

    def get(self, request, *args, **kwargs):
        search_query = request.GET.get("search", "").strip()

        context = {
            "all_models": PermissionUtils.get_all_models_data(
                search_query=search_query
            ),
        }
        return HttpResponse(render_to_string(self.template_name, context, request))


# end search


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class RoleMembersView(LoginRequiredMixin, TemplateView):
    template_name = "permissions/role_members.html"

    def get(self, request, *args, **kwargs):
        role_id = kwargs.get("role_id")
        try:
            role = get_object_or_404(Role, id=role_id)
        except:
            messages.error(request, _("Role does not exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role_id = self.kwargs.get("role_id")
        role = get_object_or_404(Role, id=role_id)

        columns = [
            ("Employee", "get_avatar_with_name"),
            ("Email", "email"),
        ]

        actions = [
            {
                "action": "Delete",
                "src": "assets/icons/a4.svg",
                "img_class": "w-4 h-4",
                "attrs": """
                    hx-post="{get_delete_user_from_role}"
                    hx-target="#deleteModeBox"
                    hx-swap="innerHTML"
                    hx-trigger="confirmed"
                    hx-on:click="hxConfirm(this,'Are you sure you want to delete the user from this role?')"
                    hx-on::after-request="$('#reloadMessagesButton').click();"
                """,
            }
        ]

        list_view = HorillaListView(
            model=HorillaUser,
            view_id=f"role-members-{role_id}",
            search_url=reverse_lazy(
                "horilla_core:role_members_view", kwargs={"role_id": role_id}
            ),
            main_url=reverse_lazy(
                "horilla_core:role_members_view", kwargs={"role_id": role_id}
            ),
            columns=columns,
            table_width=True,
            table_height=False,
            table_height_as_class="h-[400px]",
            bulk_select_option=False,
            bulk_export_option=False,
            bulk_update_option=False,
            bulk_delete_enabled=False,
            clear_session_button_enabled=False,
            list_column_visibility=False,
            enable_sorting=True,
            save_to_list_option=False,
            actions=actions,
        )

        list_view.request = self.request
        list_view.kwargs = self.kwargs
        list_view.get_queryset = lambda: HorillaUser.objects.filter(
            role=role
        ).select_related("role")
        list_view.object_list = list_view.get_queryset()
        context.update(list_view.get_context_data())
        context["role"] = role
        context["model_verbose_name"] = f"{role.role_name} Role Members"
        context["no_record_msg"] = f'No members found in the "{role.role_name}" role.'
        return context


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class PermissionTab(LoginRequiredMixin, TemplateView):
    """
    Template view for permission tab
    """

    template_name = "permissions/permission.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = (
            getattr(self.request, "active_company", None) or self.request.user.company
        )
        users = HorillaUser.objects.filter(is_superuser=False, company=company)
        paginator = Paginator(users, 10)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)
        context["users"] = page_obj
        context["page_obj"] = page_obj
        return context


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class UpdateUserPermissionsView(LoginRequiredMixin, View):
    """
    Toggle permission for a specific user when checkbox is clicked.
    """

    def post(self, request, user_id):
        try:
            user = get_object_or_404(HorillaUser, id=user_id)
        except:
            messages.error(request, _("User does not exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        perm_id = request.POST.get("permission_id")
        checked = request.POST.get("checked") == "true"

        try:
            permission = Permission.objects.get(id=perm_id)
        except Permission.DoesNotExist:
            return JsonResponse({"success": False, "message": "Permission not found"})

        if checked:
            user.user_permissions.add(permission)
            messages.success(
                request,
                f"Permission '{permission.name}' added to {user.get_full_name()}.",
            )
        else:
            user.user_permissions.remove(permission)
            messages.success(
                request,
                f"Permission '{permission.name}' removed from {user.get_full_name()}.",
            )

        return HttpResponse("<script>$('#reloadMessagesButton').click();</script>")


# user search


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class LoadUserPermissionsView(LoginRequiredMixin, TemplateView):
    """
    View to load permissions for a specific user
    """

    template_name = "permissions/user_permissions.html"

    def get(self, request, user_id, *args, **kwargs):
        try:
            user = get_object_or_404(HorillaUser, id=user_id)
        except:
            messages.error(self.request, _("User Does not Exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")
        context = {
            "user": user,
            "all_models": PermissionUtils.get_all_models_data(user=user),
        }
        return HttpResponse(render_to_string(self.template_name, context, request))


# user search end


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class LoadMoreUsersView(LoginRequiredMixin, TemplateView):
    """
    View to load more users for infinite scrolling with search functionality
    """

    template_name = "permissions/user_list.html"

    def get(self, request, *args, **kwargs):
        search_query = request.GET.get("search", "").strip()

        users = HorillaUser.objects.filter(is_superuser=False)

        if search_query:
            search_words = search_query.split()

            q_object = Q()
            for word in search_words:
                q_object &= (
                    Q(username__icontains=word)
                    | Q(first_name__icontains=word)
                    | Q(last_name__icontains=word)
                )

            users = users.filter(q_object)

        paginator = Paginator(users, 10)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        context = {
            "users": page_obj,
            "page_obj": page_obj,
            "search_query": search_query,
        }

        return HttpResponse(render_to_string(self.template_name, context, request))


@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class UpdateRolePermissionsView(LoginRequiredMixin, View):
    """
    Toggle permission for a role and its members when checkbox is clicked.
    """

    def post(self, request, role_id):
        role = get_object_or_404(Role, id=role_id)
        perm_id = request.POST.get("permission_id")
        checked = request.POST.get("checked") == "true"

        try:
            permission = Permission.objects.get(id=perm_id)
        except Permission.DoesNotExist:
            return JsonResponse({"success": False, "message": "Permission not found"})

        members = HorillaUser.objects.filter(role=role)
        if checked:
            role.permissions.add(permission)
            for member in members:
                member.user_permissions.add(permission)
            messages.success(request, "Permission added successfully.")
        else:
            role.permissions.remove(permission)
            for member in members:
                member.user_permissions.remove(permission)
            messages.success(request, "Permission removed successfully.")

        return HttpResponse("<script>$('#reloadMessagesButton').click();</script>")


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class AssignUsersView(LoginRequiredMixin, View):
    """
    Optimized view to handle assigning permissions to users.
    """

    template_name = "permissions/assign_perm_form.html"

    def get(self, request, *args, **kwargs):
        context = {
            "all_models": PermissionUtils.get_all_models_data(
                user=None,  # Optionally pass user if you want pre-check
            )
        }
        if request.headers.get("HX-Request"):
            return HttpResponse(render_to_string(self.template_name, context, request))
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        user_ids = request.POST.getlist("users")
        permission_ids = request.POST.getlist("permissions")

        if not user_ids or not permission_ids:
            errors = {}
            if not user_ids:
                errors["users"] = [_("Please select at least one user.")]
            if not permission_ids:
                errors["permissions"] = [_("Please select at least one permission.")]
            context = {
                "all_models": PermissionUtils.get_all_models_data(),
                "form": {"errors": errors},
            }
            return HttpResponse(render_to_string(self.template_name, context, request))

        users = HorillaUser.objects.filter(id__in=user_ids, is_superuser=False)
        permissions = Permission.objects.filter(id__in=permission_ids)

        try:
            for user in users:
                user.user_permissions.add(*permissions)

            messages.success(
                request,
                _(
                    "Successfully assigned {permissions_count} permission(s) to {users_count} user(s)."
                ).format(
                    permissions_count=permissions.count(),
                    users_count=users.count(),
                ),
            )

            if request.headers.get("HX-Request"):
                return HttpResponse(
                    "<script>closeContentModal(); location.reload();</script>"
                )
            return redirect("horilla_core:permission_tab")

        except Exception as e:
            messages.error(
                request, _("Error assigning permissions: {error}").format(error=str(e))
            )
            return self.get(request, *args, **kwargs)


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class UpdateRoleModelPermissionsView(LoginRequiredMixin, View):
    """
    Toggle all permissions for a specific model for a role when select all checkbox is clicked.
    """

    def post(self, request, role_id):
        try:
            role = get_object_or_404(Role, id=role_id)
        except:
            messages.error(self.request, _("Role Does not Exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        model_name = request.POST.get("model_name")
        app_label = request.POST.get("app_label")
        checked = request.POST.get("checked") == "true"

        if not model_name or not app_label:
            return JsonResponse(
                {"success": False, "message": "Model information not provided"}
            )

        try:
            permissions = PermissionUtils.get_model_permissions(app_label, model_name)
            if not permissions:
                return JsonResponse(
                    {"success": False, "message": "No permissions found for this model"}
                )

            permission_objects = Permission.objects.filter(
                id__in=[p["id"] for p in permissions]
            )
            members = HorillaUser.objects.filter(role=role)

            if checked:
                role.permissions.add(*permission_objects)
                for member in members:
                    member.user_permissions.add(*permission_objects)
                messages.success(request, f"All permissions added for {model_name}.")
            else:
                role.permissions.remove(*permission_objects)
                for member in members:
                    member.user_permissions.remove(*permission_objects)
                messages.success(request, f"All permissions removed for {model_name}.")

            return HttpResponse("<script>$('#reloadMessagesButton').click();</script>")

        except Exception as e:
            return JsonResponse(
                {"success": False, "message": f"Error updating permissions: {str(e)}"}
            )


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class UpdateRoleAllPermissionsView(LoginRequiredMixin, View):
    """
    Toggle ALL permissions for a role when master select all checkbox is clicked.
    """

    def post(self, request, role_id):
        try:
            role = get_object_or_404(Role, id=role_id)
        except:
            messages.error(self.request, _("Role Does not Exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")
        checked = request.POST.get("checked") == "true"

        try:
            all_permissions = []
            for model in apps.get_models():
                model_name = model.__name__
                if model_name in PERMISSION_EXEMPT_MODELS:
                    continue
                permissions = PermissionUtils.get_model_permissions(
                    model._meta.app_label, model_name
                )
                all_permissions.extend(
                    Permission.objects.filter(id__in=[p["id"] for p in permissions])
                )

            if not all_permissions:
                return JsonResponse(
                    {"success": False, "message": "No permissions found"}
                )

            members = HorillaUser.objects.filter(role=role)
            if checked:
                role.permissions.add(*all_permissions)
                for member in members:
                    member.user_permissions.add(*all_permissions)
                messages.success(
                    request, f"All permissions granted to {role.role_name} role."
                )
            else:
                role.permissions.remove(*all_permissions)
                for member in members:
                    member.user_permissions.remove(*all_permissions)
                messages.success(
                    request, f"All permissions revoked from {role.role_name} role."
                )

            return HttpResponse("<script>$('#reloadMessagesButton').click();</script>")

        except Exception as e:
            return JsonResponse(
                {"success": False, "message": f"Error updating permissions: {str(e)}"}
            )


@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class UpdateUserModelPermissionsView(LoginRequiredMixin, View):
    """
    Toggle all permissions for a specific model for a user when select all checkbox is clicked.
    """

    def post(self, request, user_id):
        try:
            user = get_object_or_404(HorillaUser, id=user_id)
        except:
            messages.error(self.request, _("User Does not Exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        model_name = request.POST.get("model_name")
        app_label = request.POST.get("app_label")
        checked = request.POST.get("checked") == "true"

        if not model_name or not app_label:
            return JsonResponse(
                {"success": False, "message": "Model information not provided"}
            )

        try:
            permissions = PermissionUtils.get_model_permissions(app_label, model_name)
            if not permissions:
                return JsonResponse(
                    {"success": False, "message": "No permissions found for this model"}
                )

            permission_objects = Permission.objects.filter(
                id__in=[p["id"] for p in permissions]
            )

            if checked:
                user.user_permissions.add(*permission_objects)
                messages.success(
                    request,
                    f"All permissions added for {model_name} to user {user.username}.",
                )
            else:
                user.user_permissions.remove(*permission_objects)
                messages.success(
                    request,
                    f"All permissions removed for {model_name} from user {user.username}.",
                )

            # Return success response
            return HttpResponse(status=200)

        except Exception as e:
            return JsonResponse(
                {"success": False, "message": f"Error updating permissions: {str(e)}"}
            )


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class UpdateUserAllPermissionsView(LoginRequiredMixin, View):
    """
    Toggle ALL permissions for a user when master select all checkbox is clicked.
    """

    def post(self, request, user_id):
        try:
            user = get_object_or_404(HorillaUser, id=user_id)
        except:
            messages.error(self.request, _("User Does not Exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        checked = request.POST.get("checked") == "true"

        try:
            all_permissions = []
            for model in apps.get_models():
                model_name = model.__name__
                if model_name in PERMISSION_EXEMPT_MODELS:
                    continue
                permissions = PermissionUtils.get_model_permissions(
                    model._meta.app_label, model_name
                )
                all_permissions.extend(
                    Permission.objects.filter(id__in=[p["id"] for p in permissions])
                )

            if not all_permissions:
                return JsonResponse(
                    {"success": False, "message": "No permissions found"}
                )

            if checked:
                user.user_permissions.add(*all_permissions)
                messages.success(
                    request, f"All permissions granted to user {user.username}."
                )
            else:
                user.user_permissions.remove(*all_permissions)
                messages.success(
                    request, f"All permissions revoked from user {user.username}."
                )

            # Return success response
            return HttpResponse(status=200)

        except Exception as e:
            return JsonResponse(
                {"success": False, "message": f"Error updating permissions: {str(e)}"}
            )


@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class BulkUpdateUserModelPermissionsView(LoginRequiredMixin, View):
    """
    Toggle all permissions for a specific model for multiple users when select all checkbox is clicked.
    """

    def post(self, request):
        user_ids = request.POST.getlist("users")
        model_name = request.POST.get("model_name")
        app_label = request.POST.get("app_label")
        checked = request.POST.get("checked") == "true"

        if not user_ids:
            return JsonResponse({"success": False, "message": "No users selected"})

        if not model_name or not app_label:
            return JsonResponse(
                {"success": False, "message": "Model information not provided"}
            )

        try:
            users = HorillaUser.objects.filter(id__in=user_ids, is_superuser=False)
            if not users.exists():
                return JsonResponse(
                    {"success": False, "message": "No valid users found"}
                )

            permissions = PermissionUtils.get_model_permissions(app_label, model_name)
            if not permissions:
                return JsonResponse(
                    {"success": False, "message": "No permissions found for this model"}
                )

            permission_objects = Permission.objects.filter(
                id__in=[p["id"] for p in permissions]
            )

            for user in users:
                if checked:
                    user.user_permissions.add(*permission_objects)
                else:
                    user.user_permissions.remove(*permission_objects)

            if checked:
                messages.success(
                    request,
                    f"All {model_name} permissions added to {users.count()} user(s).",
                )
            else:
                messages.success(
                    request,
                    f"All {model_name} permissions removed from {users.count()} user(s).",
                )

            return HttpResponse(status=200)

        except Exception as e:
            return JsonResponse(
                {"success": False, "message": f"Error updating permissions: {str(e)}"}
            )


@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class BulkUpdateUserAllPermissionsView(LoginRequiredMixin, View):
    """
    Toggle ALL permissions for multiple users when master select all checkbox is clicked.
    """

    def post(self, request):
        user_ids = request.POST.getlist("users")
        checked = request.POST.get("checked") == "true"

        if not user_ids:
            return JsonResponse({"success": False, "message": "No users selected"})

        try:
            users = HorillaUser.objects.filter(id__in=user_ids, is_superuser=False)
            if not users.exists():
                return JsonResponse(
                    {"success": False, "message": "No valid users found"}
                )

            all_permissions = []
            for model in apps.get_models():
                model_name = model.__name__
                if model_name in PERMISSION_EXEMPT_MODELS:
                    continue
                permissions = PermissionUtils.get_model_permissions(
                    model._meta.app_label, model_name
                )
                all_permissions.extend(
                    Permission.objects.filter(id__in=[p["id"] for p in permissions])
                )

            if not all_permissions:
                return JsonResponse(
                    {"success": False, "message": "No permissions found"}
                )

            for user in users:
                if checked:
                    user.user_permissions.add(*all_permissions)
                else:
                    user.user_permissions.remove(*all_permissions)

            return HttpResponse(status=200)

        except Exception as e:
            return JsonResponse(
                {"success": False, "message": f"Error updating permissions: {str(e)}"}
            )


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class SuperUserTab(LoginRequiredMixin, HorillaListView):
    """
    List view of the super user tab
    """

    model = HorillaUser
    view_id = "super_user_list"
    list_column_visibility = False
    bulk_select_option = False

    columns = [
        (_("First Name"), "get_avatar_with_name"),
        "role",
        (_("Super User Status"), "super_user_status_col"),
    ]

    def get_queryset(self):
        queryset = super().get_queryset()
        company = (
            getattr(self.request, "active_company", None) or self.request.user.company
        )
        queryset = queryset.filter(is_superuser=True, company=company)
        return queryset


@method_decorator(htmx_required, name="dispatch")
@method_decorator(
    permission_required_or_denied(
        [
            "auth.view_permission",
            "auth.view_group",
            "auth.change_permission",
            "auth.change_group",
        ]
    ),
    name="dispatch",
)
class ToggleSuperuserView(LoginRequiredMixin, View):
    """
    Toggle superuser status for a user.
    """

    def post(self, request, *args, **kwargs):
        user_id = kwargs.get("pk")
        User = get_user_model()
        try:
            user = get_object_or_404(User, pk=user_id)
        except:
            messages.error(self.request, _("User Does not Exist"))
            return HttpResponse("<script>$('#reloadButton').click();</script>")

        if user.is_superuser:
            user.is_superuser = False
            user.save()
            messages.success(
                request,
                f"Superuser status of {user.get_full_name()} removed successfully",
            )

        return HttpResponse("<script>htmx.trigger('#reloadButton','click')</script>")
