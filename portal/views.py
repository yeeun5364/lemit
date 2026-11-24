"""S16~S18 수강생 화면 (#80). 로그인한 수강생 계정에 연결된 수강생 기록(request.student_profile)의 데이터만 보여 준다.

- 다른 수강생의 회차·대화는 조회 조건에서부터 빠지고, 주소로 직접 접근하면 404.
- 강사 계정(연결 없음)이 들어오면 404. 수강생 계정이 강사 화면에 가는 것은 portal.middleware가 막는다.
- 수강생에게는 AI 분류를 보여 주지 않는다. 분류는 강사가 메시지함에서 참고용으로 본다(UC-17).
"""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from inbox import services as inbox_services
from inbox.models import Conversation, Message
from lessons import schedule as sch
from lessons.models import Enrollment, LessonSession
from portal import invites
from portal.forms import ChatForm
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


def _my_thread(profile):
    """이 수강생과 담당 강사 사이의 모든 메시지(예전 제목 대화 포함)를 시간순으로 (#95).
    데이터는 대화별로 그대로 있다."""
    return Message.objects.filter(conversation__student=profile, conversation__teacher=profile.teacher).order_by(
        "sent_at", "id"
    )


def _with_day_breaks(thread, previous=None):
    """말풍선 목록에 날짜 구분선 표시를 붙인다. previous: 이 목록 바로 앞 메시지(자동 갱신으로 이어 붙일 때)."""
    last_day = timezone.localtime(previous.sent_at).date() if previous else None
    for m in thread:
        day = timezone.localtime(m.sent_at).date()
        m.day_break = day if day != last_day else None
        last_day = day
    return thread


def my_messages(request):
    """S17 메시지 = 담당 강사와의 대화방 하나 (#95). 제목 없이 보내고, 예전 제목 대화도 한 대화방에 시간순으로 보인다.

    새 메시지는 그 수강생의 '제목 없는 대화'(강사 × 수강생 × 제목 "" UNIQUE라 하나뿐)에 저장되고 기존 모델로 분류된다.
    받는 강사는 연결된 수강생 기록의 강사로 고정. 수강생에게는 AI 분류를 보여 주지 않는다.
    """
    profile = _profile(request)
    form = ChatForm(request.POST or None)
    if request.method == "POST" and not _closed_notice(request, profile) and form.is_valid():
        inbox_services.add_student_message(
            teacher=profile.teacher, student=profile, subject="", body=form.cleaned_data["body"]
        )
        return redirect(reverse("portal:messages") + "#chat-end")
    thread = _with_day_breaks(list(_my_thread(profile)))
    # 자동 갱신 기준은 '본 메시지 중 가장 큰 번호'. 화면 순서(보낸 시각)의 마지막 칸 번호를 쓰면, 시각과 번호 순서가
    # 다른 데이터(예전 제목 대화, 시각을 조정한 데모)에서 이미 보인 메시지가 다시 붙을 수 있다 (#96 검토)
    last_id = max((m.pk for m in thread), default=0)
    context = {"profile": profile, "form": form, "thread": thread, "last_id": last_id}
    return render(request, "portal/my_messages.html", context)


ASCII_CURSOR = re.compile(r"[0-9]{1,18}")  # 자동 갱신 커서: DB 정수 범위 안의 ASCII 숫자


def my_messages_poll(request):
    """대화방 자동 갱신 (#95): ?after=<마지막으로 본 메시지 id> 이후의 내 대화방 메시지만 말풍선 HTML로 돌려준다.

    대화방을 열어 둔 화면에서만 부른다(간격·멈춤은 static/js/app.js). 새 메시지가 없으면 빈 응답이라 가볍다.
    조회 범위는 로그인 계정에 연결된 수강생 기록의 대화뿐이라, after 값을 바꿔도 다른 수강생 메시지는 나오지 않는다.
    """
    profile = _profile(request)
    after = request.GET.get("after", "")
    # ASCII 숫자 1~18자리만 받는다. str.isdigit()은 '²' 같은 위 첨자도 참이라 int()에서 500이 났다 (#96 재검토)
    after_id = int(after) if ASCII_CURSOR.fullmatch(after) else 0
    mine = _my_thread(profile)
    # 번호 순으로 받아야 50건씩 나눠 받아도 빠지거나 겹치지 않는다. 기준은 받은 것 중 가장 큰 번호
    new = list(mine.filter(pk__gt=after_id).order_by("id")[:50])
    if not new:
        return JsonResponse({"count": 0, "last_id": after_id, "html": ""})
    previous = mine.filter(pk=after_id).first()
    html = render_to_string(
        "portal/_chat_bubbles.html", {"thread": _with_day_breaks(new, previous), "profile": profile}, request=request
    )
    return JsonResponse({"count": len(new), "last_id": max(m.pk for m in new), "html": html})


def my_conversation(request, pk):
    """예전 S18 대화 주소 (#95): 내 대화면 대화방으로 이어 준다. 다른 수강생·다른 강사의 대화는 그대로 404."""
    profile = _profile(request)
    get_object_or_404(Conversation, pk=pk, student=profile, teacher=profile.teacher)
    return redirect(reverse("portal:messages") + "#chat-end")


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
