from django.urls import path
from . import views

urlpatterns = [
    path('workitems/new/', views.issue, name='workitem_issue'),
    path('workitems/confirm/', views.confirm, name='workitem_confirm'),
    path('workitems/issued/', views.issued, name='workitems_issued'),
    path('workitems/all/', views.all_workitems, name='workitems_all'),
    path('workitems/<uuid:item_id>/', views.detail, name='workitem_detail'),
    path('workitems/<uuid:item_id>/assignees/', views.roster, name='workitem_roster'),
]
