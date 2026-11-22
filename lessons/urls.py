from django.urls import path

from lessons import views

app_name = "lessons"

urlpatterns = [
    # S11 URL은 수강생 아래지만 뷰는 lessons 앱에 둔다 (SCREENS §7)
    path("students/<int:student_pk>/enrollments/new/", views.enrollment_create, name="enrollment_create"),
    path("enrollments/<int:pk>/end/", views.enrollment_end, name="enrollment_end"),
    path("enrollments/<int:pk>/label/", views.enrollment_label, name="enrollment_label"),
    path("labels/", views.label_list, name="label_list"),
    path("labels/new/", views.label_create, name="label_create"),
    path("labels/<int:pk>/edit/", views.label_edit, name="label_edit"),
    path("labels/<int:pk>/delete/", views.label_delete, name="label_delete"),
    path("schedule/", views.schedule, name="schedule"),
    path("sessions/new/", views.session_create, name="session_create"),
    path("sessions/<int:pk>/", views.session_detail, name="session_detail"),
    path("sessions/<int:pk>/edit/", views.session_edit, name="session_edit"),
    path("sessions/<int:pk>/delete/", views.session_delete, name="session_delete"),
    path("sessions/<int:pk>/complete/", views.session_complete, name="session_complete"),
    path("sessions/<int:pk>/uncomplete/", views.session_uncomplete, name="session_uncomplete"),
    path("sessions/<int:pk>/cancel/", views.session_cancel, name="session_cancel"),
    path("sessions/<int:pk>/uncancel/", views.session_uncancel, name="session_uncancel"),
    path("sessions/<int:pk>/makeup/", views.session_makeup, name="session_makeup"),
]
