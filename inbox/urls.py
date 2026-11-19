from django.urls import path

from inbox import views

app_name = "inbox"

urlpatterns = [
    path("", views.inbox_list, name="list"),
    path("new/", views.new_student_message, name="new"),
    path("<int:pk>/", views.conversation_detail, name="detail"),
    path("<int:pk>/reply/", views.reply, name="reply"),
]
