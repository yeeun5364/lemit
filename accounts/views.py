import time
from collections import Counter

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import FormView

from accounts.forms import LemitPasswordResetForm, LoginForm, SignupForm


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


# ---- UC-15 비밀번호 찾기(S03)·변경(S04): Django 기본 뷰에 템플릿·문구만 붙인다 ----
# 재설정 뷰 4개는 Django가 이미 공개(login_not_required)로 표시해 두었다 (SCREENS §1).

RESET_EMAIL_SESSION_KEY = "password_reset_entered_email"  # 비밀번호 찾기에 입력한 이메일 (#78, 안내 화면 표시용)
# 입력한 이메일은 오타를 확인·고치는 짧은 시간 동안만 둔다. 공용 기기에서 나중에 같은 화면을 열어도 보이지 않게
# (PR #82 리뷰 P3). 재설정 링크 유효 시간(1시간)과는 별개다.
RESET_EMAIL_TTL_SECONDS = 10 * 60


def _remember_reset_email(request, email):
    request.session[RESET_EMAIL_SESSION_KEY] = {"email": email, "at": time.time()}


def _recent_reset_email(request):
    """10분 안에 입력한 이메일. 지났거나 형식이 다르면 세션에서 지우고 빈 문자열."""
    saved = request.session.get(RESET_EMAIL_SESSION_KEY)
    if isinstance(saved, dict) and time.time() - saved.get("at", 0) <= RESET_EMAIL_TTL_SECONDS:
        return saved.get("email", "")
    request.session.pop(RESET_EMAIL_SESSION_KEY, None)
    return ""


class PasswordResetView(auth_views.PasswordResetView):
    """메일 발송이 실패해도 Django 기본 폼이 예외를 잡아 로그만 남기므로(django.contrib.auth 로거),
    가입 여부와 관계없이 같은 안내 화면으로 간다. 비상시에는 운영자가 /admin/에서 재설정한다."""

    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")
    form_class = LemitPasswordResetForm

    def get_initial(self):
        """안내 화면의 '이메일 다시 입력'으로 돌아오면 방금 입력한 이메일을 채워 오타만 고치게 한다 (#78).
        세션에 있는 값은 이 브라우저에서 사용자가 직접 입력한 문자열뿐이라 가입 여부와 무관하다."""
        initial = super().get_initial()
        if self.request.GET.get("retry"):
            email = _recent_reset_email(self.request)
            if email:
                initial["email"] = email
        return initial

    def form_valid(self, form):
        """요청 횟수 제한 (#7, PR #68 '재설정 요청 횟수 제한 없음'): 같은 이메일·IP로 너무 자주 요청하면
        메일을 보내지 않는다. 화면은 같은 안내로 보내 가입 여부도, 막혔다는 사실도 드러내지 않는다."""
        from accounts import throttle
        from accounts.models import LoginAttempt

        # 안내 화면에 '입력한 이메일'을 보여 주려고 저장한다 (#78). 가입·제한·발송 실패와 관계없이 항상 같은 값
        _remember_reset_email(self.request, form.cleaned_data.get("email", ""))
        # 한도 확인과 사용 기록을 잠금 안에서 한 번에 한다(동시 요청도 한도까지만 메일 발송, PR #70 리뷰 P2)
        if throttle.reserve(LoginAttempt.Kind.RESET, self.request, form.cleaned_data.get("email")) is None:
            return redirect(self.get_success_url())
        return super().form_valid(form)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    """입력한 이메일을 다시 보여 주어 오타를 확인하게 한다 (#78).
    사용자가 입력한 그대로라 가입 여부는 드러나지 않는다."""

    template_name = "accounts/password_reset_done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["entered_email"] = _recent_reset_email(self.request)
        return context


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


class PasswordChangeView(auth_views.PasswordChangeView):
    """S04 비밀번호 변경. 바꾼 뒤에도 로그인은 유지된다(Django가 세션 해시를 갱신)."""

    template_name = "accounts/password_change_form.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "비밀번호를 변경했습니다.")
        return response


DASHBOARD_MESSAGES = 5  # 대시보드 '확인할 메시지'에 보여 줄 대화 수


def home(request):
    """대시보드 (#45). 로그인한 강사의 실제 데이터만 보여준다.

    - 전체 수강생: 수강 중(보관 제외)인 내 수강생 수
    - 확인할 메시지: 안 읽은 수강생 메시지 수, 그 메시지가 있는 대화(최근순)와 대화별 최근 AI 분류
    - 오늘 수업·오늘의 일정: 오늘(Asia/Seoul) 시작하는 내 회차 (보관 수강생 제외, 일정 화면과 같은 기준, UC-05)
    """
    from django.utils import timezone

    from inbox.models import Message
    from inbox.selectors import conversations_for, with_display
    from lessons.schedule import sessions_by_day
    from students.models import Student

    teacher = request.user
    today = timezone.localdate()
    today_sessions = sessions_by_day(teacher, today, today).get(today, [])
    attention = conversations_for(teacher).filter(unread__gt=0)  # 확인할 대화 (안 읽은 메시지 있음)
    # 건수·분류별 개수는 분류 값 한 열만 가져와 센다 (대화 본문 전체를 불러오지 않음, Codex 리뷰)
    labels = list(attention.values_list("latest_label", flat=True))
    label_counts = Counter(label for label in labels if label)
    unread_messages = Message.objects.filter(
        conversation__teacher=teacher, sender=Message.Sender.STUDENT, read_at__isnull=True
    ).count()
    return render(
        request,
        "home.html",
        {
            "active_students": Student.objects.for_teacher(teacher).active().count(),
            "today": today,
            "today_sessions": today_sessions,
            "today_scheduled": sum(s.status == "scheduled" for s in today_sessions),
            "unread_messages": unread_messages,
            "attention_total": len(labels),
            "attention": with_display(attention[:DASHBOARD_MESSAGES]),
            "label_summary": [
                (value, label, label_counts[value]) for value, label in Message.Label.choices if value in label_counts
            ],
        },
    )
