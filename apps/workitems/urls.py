from django.urls import path
from . import views

urlpatterns = [
    path('workitems/archive/', views.archives, name='workitems_archive'),
    path('workitems/<uuid:item_id>/edit/', views.edit, name='workitem_edit'),
    path('workitems/<uuid:item_id>/add/', views.add_assignees, name='workitem_add'),
    path('workitems/<uuid:item_id>/confirm-change/', views.workflow_confirm, name='workitem_workflow_confirm'),
    path('workitems/<uuid:item_id>/archive/', views.archive, name='workitem_archive'),
    path('workitems/<uuid:item_id>/history/', views.history, name='workitem_history'),
    path('workitems/<uuid:item_id>/assignees/<uuid:assignment_id>/', views.assignee_progress, name='workitem_assignee'),
    path('assignments/<uuid:assignment_id>/subtasks/<uuid:subtask_id>/', views.mark, name='assignment_mark'),
    path('assignments/<uuid:assignment_id>/completion/', views.completion, name='assignment_completion'),
    path('assignments/<uuid:assignment_id>/revoke/', views.revoke, name='assignment_revoke'),
    path('workitems/new/', views.issue, name='workitem_issue'),
    path('workitems/confirm/', views.confirm, name='workitem_confirm'),
    path('workitems/issued/', views.issued, name='workitems_issued'),
    path('workitems/all/', views.all_workitems, name='workitems_all'),
    path('workitems/<uuid:item_id>/', views.detail, name='workitem_detail'),
    path('workitems/<uuid:item_id>/assignees/', views.roster, name='workitem_roster'),
]
