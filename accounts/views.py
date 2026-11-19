from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import FormView

from accounts.forms import LoginForm, SignupForm


class TeacherLoginView(LoginView):
    """S01 로그인. Django LoginView는 이미 공개(login_not_required)로 표시되어 있다."""

    form_class = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True  # 로그인한 사용자는 홈으로


@method_decorator(login_not_required, name="dispatch")
class SignupView(FormView):
    """S02 회원가입. 가입하면 바로 로그인하고 홈으로 이동한다."""

    form_class = SignupForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("home")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        messages.success(self.request, f"{user.name}님, 가입을 환영합니다.")
        return super().form_valid(form)


def home(request):
    """로그인 후 첫 화면 (임시). UC-05에서 월간 일정 화면(S05)으로 교체한다."""
    return render(request, "home.html")
