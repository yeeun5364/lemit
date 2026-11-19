from django.contrib.auth.views import LogoutView
from django.urls import path

from accounts import views

urlpatterns = [
    path("login/", views.TeacherLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),  # POST만 허용 (Django 5), 로그인 필요
    path("signup/", views.SignupView.as_view(), name="signup"),
]
