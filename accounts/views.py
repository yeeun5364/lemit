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


DASHBOARD_MESSAGES = 5  # 대시보드 '확인할 메시지'에 보여 줄 대화 수


def home(request):
    """대시보드 (#45). 로그인한 강사의 실제 데이터만 보여준다.

    - 전체 수강생: 수강 중(보관 제외)인 내 수강생 수
    - 확인할 메시지: 안 읽은 수강생 메시지 수, 그 메시지가 있는 대화(최근순)와 대화별 최근 AI 분류
    - 오늘 수업·오늘의 일정: 회차·일정 기능(UC-04·05, #11·#12)이 없어 '준비 중'으로 표시한다. 숫자를 지어내지 않는다.
    """
    from inbox.models import Message
    from inbox.selectors import conversations_for, with_display
    from students.models import Student

    teacher = request.user
    attention = with_display(conversations_for(teacher).filter(unread__gt=0))  # 확인할 대화 (안 읽은 메시지 있음)
    unread_messages = Message.objects.filter(
        conversation__teacher=teacher, sender=Message.Sender.STUDENT, read_at__isnull=True
    ).count()
    label_counts = {}
    for c in attention:
        if c.latest_label:
            label_counts[c.latest_label] = label_counts.get(c.latest_label, 0) + 1
    return render(
        request,
        "home.html",
        {
            "active_students": Student.objects.for_teacher(teacher).active().count(),
            "unread_messages": unread_messages,
            "attention_total": len(attention),
            "attention": attention[:DASHBOARD_MESSAGES],
            "label_summary": [
                (value, label, label_counts[value]) for value, label in Message.Label.choices if value in label_counts
            ],
        },
    )
