from apps.accounts.models import User

from .policies import require_active_actor


def own_profile(actor):
    require_active_actor(actor)
    return User.objects.filter(pk=actor.pk).values("id", "full_name", "email").get()
