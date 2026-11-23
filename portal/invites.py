"""수강생 초대·연결 규칙 (#84). 화면(뷰)은 이 함수만 부르고, 거부 사유는 InviteError 문장으로 보여 준다.

동시 요청 규칙: 연결을 바꾸는 쓰기(발급·승인·연결 해제)는 수강생 행을 먼저 잠그고(lessons.services.lock_student),
초대 행도 잠근 뒤 최신 상태로 다시 확인한다. 연결은 Student.account(OneToOne, DB UNIQUE)에만 저장하므로
같은 요청을 두 번 승인하거나 여러 초대를 동시에 승인해도 연결은 하나만 생긴다.
"""

import datetime

from django.db import transaction
from django.utils import timezone

from lessons.services import lock_student
from portal.models import INVITE_TTL_MINUTES, StudentInvite
from students.models import Student


class InviteError(Exception):
    """화면에 그대로 보여 줄 거부 사유."""


def account_refusal(user):
    """이 계정을 수강생 계정으로 연결할 수 없는 이유. 연결할 수 있으면 None."""
    if user.is_staff or user.is_superuser:
        return "관리자 계정은 수강생 계정으로 연결할 수 없습니다."
    if user.students.exists():
        return "수강생을 관리하는 강사 계정은 수강생 계정으로 연결할 수 없습니다. 다른 계정으로 로그인하세요."
    if Student.objects.filter(account=user).exists():
        return "이 계정은 이미 다른 수강생 기록에 연결돼 있습니다."
    return None


def _close_pending(student, *, keep=None):
    """수강생의 대기 중 초대(발급·요청)를 취소로. keep은 남길 초대."""
    qs = StudentInvite.objects.filter(
        student=student, status__in=[StudentInvite.Status.ISSUED, StudentInvite.Status.REQUESTED]
    )
    if keep is not None:
        qs = qs.exclude(pk=keep.pk)
    return qs.update(status=StudentInvite.Status.CANCELLED, decided_at=timezone.now())


def issue(student):
    """새 일회용 초대를 발급한다. 이전 대기 초대는 취소된다(재발급). 이미 연결된 기록이나 보관된 기록은 거부."""
    with transaction.atomic():
        locked = lock_student(student.pk)
        if not locked.is_active:
            raise InviteError("보관된 수강생에게는 초대를 만들 수 없습니다. 보관을 해제한 뒤 다시 시도하세요.")
        if locked.account_id:
            raise InviteError("이미 수강생 계정이 연결돼 있습니다. 다른 계정으로 바꾸려면 먼저 연결을 해제하세요.")
        _close_pending(locked)
        expires = timezone.now() + datetime.timedelta(minutes=INVITE_TTL_MINUTES)
        return StudentInvite.objects.create(student=locked, expires_at=expires)


def request_link(token, user):
    """수강생 계정이 초대(QR)로 연결을 요청한다. 같은 계정이 다시 요청하면 그대로 둔다."""
    with transaction.atomic():
        invite = (
            StudentInvite.objects.select_for_update().select_related("student__teacher").filter(token=token).first()
        )
        if invite is None:
            raise InviteError("올바르지 않은 초대입니다. 강사에게 QR을 다시 받아 주세요.")
        if invite.status == StudentInvite.Status.REQUESTED and invite.requested_by_id == user.pk:
            return invite
        if not invite.is_open:
            raise InviteError("만료됐거나 이미 사용된 초대입니다. 강사에게 새 QR을 받아 주세요.")
        if not invite.student.is_active or invite.student.account_id:
            raise InviteError("이 초대로는 더 이상 연결할 수 없습니다. 강사에게 새 QR을 받아 주세요.")
        refusal = account_refusal(user)
        if refusal:
            raise InviteError(refusal)
        invite.status = StudentInvite.Status.REQUESTED
        invite.requested_by = user
        invite.requested_at = timezone.now()
        invite.save(update_fields=["status", "requested_by", "requested_at"])
        return invite


def approve(invite):
    """강사가 요청 계정을 확인하고 승인한다. 수강생 기록에 계정을 연결하고 다른 대기 초대는 취소."""
    with transaction.atomic():
        student = lock_student(invite.student_id)
        current = StudentInvite.objects.select_for_update(of=("self",)).select_related("requested_by").get(pk=invite.pk)
        if current.status != StudentInvite.Status.REQUESTED or current.requested_by is None:
            raise InviteError("승인할 요청이 없습니다. 화면을 다시 확인하세요.")
        if student.account_id:
            raise InviteError("이미 수강생 계정이 연결돼 있습니다.")
        refusal = account_refusal(current.requested_by)
        if refusal:
            raise InviteError(refusal)
        student.account = current.requested_by
        student.save(update_fields=["account", "updated_at"])
        now = timezone.now()
        current.status = StudentInvite.Status.APPROVED
        current.decided_at = now
        current.save(update_fields=["status", "decided_at"])
        _close_pending(student, keep=current)
        return current


def cancel(invite):
    """강사가 대기 중 초대를 취소하거나 요청을 거절한다. 이미 승인·취소된 초대는 그대로."""
    with transaction.atomic():
        current = StudentInvite.objects.select_for_update().get(pk=invite.pk)
        if current.status in (StudentInvite.Status.ISSUED, StudentInvite.Status.REQUESTED):
            current.status = StudentInvite.Status.CANCELLED
            current.decided_at = timezone.now()
            current.save(update_fields=["status", "decided_at"])
        return current


def unlink(student):
    """잘못 연결한 경우 강사가 연결을 해제한다. 해제된 계정은 바로 수강생 화면에 접근할 수 없다."""
    with transaction.atomic():
        locked = lock_student(student.pk)
        if not locked.account_id:
            raise InviteError("연결된 수강생 계정이 없습니다.")
        locked.account = None
        locked.save(update_fields=["account", "updated_at"])
        _close_pending(locked)
        return locked
