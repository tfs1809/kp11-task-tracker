from django.urls import path
from . import views

urlpatterns = [
    path('events/<uuid:item_id>/chat/', views.chat, name='event_chat'),
    path('events/<uuid:item_id>/chat/changes/', views.changes, name='event_chat_changes'),
    path('events/<uuid:item_id>/chat/send/', views.send, name='event_chat_send'),
    path('events/<uuid:item_id>/chat/close/', views.close, name='event_chat_close'),
    path('events/<uuid:item_id>/chat/messages/<int:message_id>/hide/', views.hide, name='event_message_hide'),
    path('events/<uuid:item_id>/chat/messages/<int:message_id>/original/', views.original, name='event_message_original'),
]
