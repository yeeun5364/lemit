from django.contrib.auth.views import LogoutView
from django.urls import path

from accounts import views

urlpatterns = [
    path("login/", views.TeacherLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),  # POST만 허용 (Django 5), 로그인 필요
    path("signup/", views.SignupView.as_view(), name="signup"),
    # S03 비밀번호 찾기 (UC-15, 공개)
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password-reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("password-reset/complete/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    # S04 비밀번호 변경 (로그인 필요)
    path("password-change/", views.PasswordChangeView.as_view(), name="password_change"),
]
