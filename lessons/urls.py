from django.urls import path

from lessons import views

app_name = "lessons"

urlpatterns = [
    # S11 URL은 수강생 아래지만 뷰는 lessons 앱에 둔다 (SCREENS §7)
    path("students/<int:student_pk>/enrollments/new/", views.enrollment_create, name="enrollment_create"),
    path("enrollments/<int:pk>/end/", views.enrollment_end, name="enrollment_end"),
]
