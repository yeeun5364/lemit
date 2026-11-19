from django.urls import path

from students import views

app_name = "students"

urlpatterns = [
    path("", views.StudentListView.as_view(), name="list"),
    path("new/", views.StudentCreateView.as_view(), name="create"),
    path("<int:pk>/", views.StudentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.StudentUpdateView.as_view(), name="edit"),
    path("<int:pk>/archive/", views.archive, name="archive"),
    path("<int:pk>/restore/", views.restore, name="restore"),
]
