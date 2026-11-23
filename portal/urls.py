from django.urls import path

from portal import views

app_name = "portal"

urlpatterns = [
    path("", views.my_lessons, name="lessons"),
    path("messages/", views.my_messages, name="messages"),
    path("messages/<int:pk>/", views.my_conversation, name="conversation"),
    path("join/<str:token>/", views.join, name="join"),  # QR 연결 요청 (#84), 로그인 전에도 열림
]
