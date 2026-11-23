"""S16~S18 수강생 화면 (#80). 로그인한 수강생 계정에 연결된 수강생 기록(request.student_profile)의 데이터만 보여 준다.

- 다른 수강생의 회차·대화는 조회 조건에서부터 빠지고, 주소로 직접 접근하면 404.
- 강사 계정(연결 없음)이 들어오면 404. 수강생 계정이 강사 화면에 가는 것은 portal.middleware가 막는다.
- 수강생에게는 AI 분류를 보여 주지 않는다. 분류는 강사가 메시지함에서 참고용으로 본다(UC-17).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from inbox import services as inbox_services
from inbox.models import Conversation, Message
from lessons import schedule as sch
from lessons.models import Enrollment, LessonSession
from portal import invites
from portal.forms import FollowUpForm, NewMessageForm
from portal.models import StudentInvite

PAST_LIMIT = 10


def _profile(request):
    profile = getattr(request, "student_profile", None)
    if profile is None:
        raise Http404("수강생 계정이 아닙니다.")
    return profile


def _closed_notice(request, profile):
    if not profile.is_active:
        messages.error(request, "수강이 종료된 상태라 메시지를 보낼 수 없습니다. 담당 강사에게 직접 연락해 주세요.")
        return True
    return False


def my_lessons(request):
    """S16 내 수업: 월간 달력(#92) + 고른 날의 수업, 다가오는 회차와 지난 회차, 진행 중 레슨권의 잔여 회차.

    ?date=YYYY-MM-DD로 달과 날짜를 고른다(잘못된 값은 오늘). 달력·그날 목록 모두 로그인 계정에 연결된
    수강생 기록의 회차만 조회하고, 다른 요청 값(student 등)은 쓰지 않는다. 읽기 전용(일정 추가·수정 없음).
    """
    profile = _profile(request)
    now = timezone.now()
    today = timezone.localdate()
    selected = sch.parse_date(request.GET.get("date"), today)
    weeks = sch.month_weeks(selected)
    days = sch.student_sessions_by_day(profile, weeks[0][0], weeks[-1][-1])

    def link(d):
        return f"?date={d.isoformat()}#calendar"

    def link_if_allowed(d):
        """parse_date가 받는 범위(1900~2999년) 밖으로는 이동 링크를 만들지 않는다 (PR #93 리뷰 P3)."""
        return link(d) if sch.parse_date(d.isoformat(), None) else None

    calendar = {
        "grid": sch.month_grid(selected, today, days, link),
        "weekdays": sch.WEEKDAYS,
        "selected": selected,
        "today": today,
        "day_sessions": days.get(selected, []),
        "prev_url": link_if_allowed(sch.add_months(selected, -1)),
        "next_url": link_if_allowed(sch.add_months(selected, 1)),
        "today_url": link(today),
    }
    sessions = LessonSession.objects.filter(enrollment__student=profile).select_related("enrollment__label")
    upcoming = list(sessions.filter(start_at__gte=now).order_by("start_at", "id"))
    past = list(sessions.filter(start_at__lt=now).order_by("-start_at", "-id")[:PAST_LIMIT])
    enrollments = (
        Enrollment.objects.with_usage()
        .filter(student=profile, status=Enrollment.Status.ACTIVE)
        .order_by("-start_date", "id")
    )
    context = {"profile": profile, "upcoming": upcoming, "past": past, "enrollments": enrollments, **calendar}
    return render(request, "portal/my_lessons.html", context)


def my_messages(request):
    """S17 내 메시지: 담당 강사와의 대화 목록 + 새 메시지. 받는 강사는 연결된 수강생 기록의 강사로 고정."""
    profile = _profile(request)
    form = NewMessageForm(request.POST or None)
    if request.method == "POST" and not _closed_notice(request, profile) and form.is_valid():
        message = inbox_services.add_student_message(
            teacher=profile.teacher,
            student=profile,
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
        )
        messages.success(request, f"담당 강사({profile.teacher.name})에게 메시지를 보냈습니다.")
        return redirect("portal:conversation", pk=message.conversation_id)
    conversations = list(
        Conversation.objects.filter(student=profile, teacher=profile.teacher).prefetch_related(
            Prefetch("messages", queryset=Message.objects.order_by("sent_at", "id"))
        )
    )
    for c in conversations:
        thread = list(c.messages.all())
        c.last_message = thread[-1] if thread else None
    return render(
        request, "portal/my_messages.html", {"profile": profile, "form": form, "conversations": conversations}
    )


def my_conversation(request, pk):
    """S18 대화: 강사 답장 확인, 같은 대화에 이어 보내기."""
    profile = _profile(request)
    conversation = get_object_or_404(Conversation, pk=pk, student=profile, teacher=profile.teacher)
    form = FollowUpForm(request.POST or None)
    if request.method == "POST" and not _closed_notice(request, profile) and form.is_valid():
        inbox_services.add_student_followup(conversation=conversation, body=form.cleaned_data["body"])
        messages.success(request, "메시지를 보냈습니다.")
        return redirect("portal:conversation", pk=conversation.pk)
    thread = conversation.messages.order_by("sent_at", "id")
    context = {"profile": profile, "conversation": conversation, "thread": thread, "form": form}
    return render(request, "portal/conversation.html", context)


@login_not_required
def join(request, token):
    """S19 QR 연결 요청 (#84). 휴대폰으로 QR을 스캔하면 여기로 온다.

    로그인 전이면 로그인·가입으로 보내고(돌아올 주소 유지), 로그인하면 '연결 요청'을 누를 수 있다.
    요청만으로는 아무것도 볼 수 없고, 강사가 계정을 확인해 승인해야 '내 수업'이 열린다.
    """
    invite = StudentInvite.objects.select_related("student__teacher", "requested_by").filter(token=token).first()
    user = request.user
    if invite is None:
        return render(
            request,
            "portal/join.html",
            {"error": "올바르지 않은 초대입니다. 강사에게 QR을 다시 받아 주세요."},
            status=404,
        )
    if user.is_authenticated and invite.student.account_id == user.pk:
        return redirect("portal:lessons")
    if request.method == "POST":
        if not user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        try:
            invite = invites.request_link(token, user)
        except invites.InviteError as e:
            messages.error(request, str(e))
        return redirect("portal:join", token=token)
    context = {"invite": invite, "next": request.path}
    if user.is_authenticated:
        mine = invite.status == StudentInvite.Status.REQUESTED and invite.requested_by_id == user.pk
        context["requested"] = mine
        if not mine:
            context["refusal"] = invites.account_refusal(user) or (
                None if invite.is_open else "만료됐거나 이미 사용된 초대입니다. 강사에게 새 QR을 받아 주세요."
            )
    elif not invite.is_open:
        context["refusal"] = "만료됐거나 이미 사용된 초대입니다. 강사에게 새 QR을 받아 주세요."
    return render(request, "portal/join.html", context)
