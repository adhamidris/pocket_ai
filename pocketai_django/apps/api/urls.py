from django.urls import path

from . import views
from .chat_portal import events, send_message, stream_send, submit_csat

app_name = "api"

urlpatterns = [
    path("health/", views.placeholder, name="health"),
    path("chat/messages/", send_message, name="chat-messages"),
    path("chat/stream/send/", stream_send, name="chat-stream-send"),
    path("chat/events/", events, name="chat-events"),
    path("chat/csat/", submit_csat, name="chat-csat"),
]
