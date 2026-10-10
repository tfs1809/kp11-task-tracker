from django.core.exceptions import PermissionDenied

from apps.access.policies import has_permission
from apps.access.services import actor_transaction


def navigation(request):
    user = getattr(request, "user", None)
    # Host отклоняется CommonMiddleware раньше AuthenticationMiddleware.
    if user is None or not user.is_authenticated:
        return {"can_administer": False}
    try:
        with actor_transaction(user.pk, session_auth_version=request.session.get("auth_version")) as actor:
            from apps.workitems.policies import can_issue
            return {"can_administer": has_permission(actor, "accounts.manage_users"), "can_issue": can_issue(actor), "can_view_issued": has_permission(actor, "workitems.view_own"), "can_view_all": has_permission(actor, "workitems.view_all")}
    except PermissionDenied:
        return {"can_administer": False}
