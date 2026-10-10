from django.urls import include, path

from core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("", include("apps.accounts.urls")),
    path("health/live/", views.liveness, name="liveness"),
    path("health/ready/", views.readiness, name="readiness"),
]
handler400 = "core.views.error400"
handler403 = "core.views.error403"
handler404 = "core.views.error404"
handler500 = "core.views.error500"
