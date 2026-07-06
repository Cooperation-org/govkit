"""
Convenience mixins for org-scoped class-based views.

OrgContextMiddleware already resolves request.org / request.membership. These mixins are
for feature agents who prefer CBVs and want role gating without repeating boilerplate.
"""

from django.core.exceptions import PermissionDenied


class OrgScopedViewMixin:
    """Assumes OrgContextMiddleware ran (URL has org_slug). Exposes self.org / self.membership."""

    @property
    def org(self):
        return self.request.org

    @property
    def membership(self):
        return self.request.membership


class RequireRoleMixin(OrgScopedViewMixin):
    """Gate a view to one or more membership roles. Set `required_roles`."""

    required_roles = ()

    def dispatch(self, request, *args, **kwargs):
        membership = request.membership
        is_super = request.user.is_authenticated and request.user.is_superuser
        if not is_super:
            if membership is None or membership.role not in self.required_roles:
                raise PermissionDenied("Insufficient role for this action.")
        return super().dispatch(request, *args, **kwargs)
