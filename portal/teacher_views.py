"""강사가 수강생 상세(S10)에서 쓰는 초대·연결 관리 (#84). 주소는 students/ 아래(강사 화면)라
수강생 계정은 미들웨어에서 막히고, 강사는 자기 수강생의 초대만 다룰 수 있다(다른 강사는 404)."""

import segno
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import get_owned_or_404
from portal import invites
from portal.models import StudentInvite
from students.models import Student


def _back(student):
    return redirect(reverse("students:detail", args=[student.pk]) + "#account-link")


def _owned_invite(request, pk, invite_pk):
    return get_object_or_404(
        StudentInvite.objects.select_related("student", "requested_by"),
        pk=invite_pk,
        student__pk=pk,
        student__teacher=request.user,
    )


def invite_panel(request, student):
    """수강생 상세의 '수강생 계정 연결' 카드에 넣을 값. 열린 초대가 있으면 QR(SVG)과 주소도."""
    invite = student.invites.select_related("requested_by").first()
    panel = {"invite": invite, "linked_account": student.account}
    if invite is not None and invite.is_open:
        url = request.build_absolute_uri(reverse("portal:join", args=[invite.token]))
        panel["join_url"] = url
        panel["qr_svg"] = segno.make(url, error="m").svg_inline(scale=5, border=2, title="수강생 연결 QR")
    return panel


@require_POST
def issue(request, pk):
    student = get_owned_or_404(Student, request.user, pk=pk)
    try:
        invites.issue(student)
    except invites.InviteError as e:
        messages.error(request, str(e))
    else:
        messages.success(
            request, "연결 QR을 만들었습니다. 10분 안에 수강생이 휴대폰으로 스캔해 연결을 요청해야 합니다."
        )
    return _back(student)


@require_POST
def approve(request, pk, invite_pk):
    invite = _owned_invite(request, pk, invite_pk)
    try:
        done = invites.approve(invite)
    except invites.InviteError as e:
        messages.error(request, str(e))
    else:
        messages.success(
            request, f"{done.requested_by.email} 계정을 '{invite.student.name}' 수강생 기록에 연결했습니다."
        )
    return _back(invite.student)


@require_POST
def cancel(request, pk, invite_pk):
    invite = _owned_invite(request, pk, invite_pk)
    was_requested = invite.status == StudentInvite.Status.REQUESTED
    invites.cancel(invite)
    messages.success(request, "연결 요청을 거절했습니다." if was_requested else "초대를 취소했습니다.")
    return _back(invite.student)


@require_POST
def unlink(request, pk):
    student = get_owned_or_404(Student, request.user, pk=pk)
    try:
        invites.unlink(student)
    except invites.InviteError as e:
        messages.error(request, str(e))
    else:
        messages.success(
            request, "수강생 계정 연결을 해제했습니다. 그 계정은 이제 이 수강생의 수업·메시지를 볼 수 없습니다."
        )
    return _back(student)
