from django.urls import path

from portal import views

app_name = "portal"

urlpatterns = [
    path("", views.my_lessons, name="lessons"),
    path("messages/", views.my_messages, name="messages"),
    path("messages/<int:pk>/", views.my_conversation, name="conversation"),
]
