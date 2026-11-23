from django.urls import path

from portal import teacher_views as invite_views
from students import views

app_name = "students"

urlpatterns = [
    path("", views.StudentListView.as_view(), name="list"),
    path("new/", views.StudentCreateView.as_view(), name="create"),
    path("<int:pk>/", views.StudentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.StudentUpdateView.as_view(), name="edit"),
    path("<int:pk>/archive/", views.archive, name="archive"),
    path("<int:pk>/restore/", views.restore, name="restore"),
    # 수강생 계정 연결 (#84): QR 초대 발급·승인·취소, 연결 해제 (강사 화면)
    path("<int:pk>/invite/", invite_views.issue, name="invite_issue"),
    path("<int:pk>/invite/<int:invite_pk>/approve/", invite_views.approve, name="invite_approve"),
    path("<int:pk>/invite/<int:invite_pk>/cancel/", invite_views.cancel, name="invite_cancel"),
    path("<int:pk>/unlink/", invite_views.unlink, name="unlink"),
]
