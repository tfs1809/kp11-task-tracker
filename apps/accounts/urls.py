from django.urls import path

from . import views

urlpatterns = [
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("accounts/activate/", views.activation_view, name="activate"),
    path("accounts/recovery/", views.recovery_view, name="recovery"),
    path("accounts/reset/", views.reset_view, name="reset"),
    path("accounts/password/", views.password_change_view, name="password_change"),
    path("accounts/profile/", views.profile_view, name="profile"),
    path("administration/", views.administration_view, name="administration"),
    path("administration/invite/", views.invitation_view, name="invite"),
    path("administration/employees/<uuid:user_id>/", views.employee_view, name="employee"),
    path("administration/employees/<uuid:user_id>/resend/", views.resend_view, name="resend"),
    path("administration/roles/new/", views.role_view, name="role_new"),
    path("administration/roles/<uuid:role_id>/", views.role_view, name="role"),
    path("administration/groups/new/", views.group_view, name="group_new"),
    path("administration/groups/<uuid:group_id>/", views.group_view, name="group"),
]
