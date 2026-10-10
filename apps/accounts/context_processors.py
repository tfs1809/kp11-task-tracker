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
            return {"can_administer": has_permission(actor, "accounts.manage_users")}
    except PermissionDenied:
        return {"can_administer": False}
