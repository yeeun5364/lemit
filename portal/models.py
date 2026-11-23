"""수강생 초대 (#84): 강사가 수강생 기록마다 짧게 유효한 일회용 QR을 만들고, 수강생 계정이 연결을 요청하면
강사가 그 계정을 확인한 뒤 승인한다. 승인하면 Student.account가 채워진다(연결은 수강생 기록에만 저장).

상태: issued(발급, 스캔 대기) → requested(연결 요청됨, 강사 확인 대기) → approved(연결됨)
      issued·requested → cancelled(강사가 취소·거절, 또는 재발급으로 대체)
      issued는 expires_at이 지나면 만료(저장 값은 그대로, is_expired로 판단)
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

INVITE_TTL_MINUTES = 10


def new_token():
    return secrets.token_urlsafe(24)


class StudentInvite(models.Model):
    class Status(models.TextChoices):
        ISSUED = "issued", "스캔 대기"
        REQUESTED = "requested", "승인 대기"
        APPROVED = "approved", "연결됨"
        CANCELLED = "cancelled", "취소"

    student = models.ForeignKey(
        "students.Student", verbose_name="수강생", on_delete=models.CASCADE, related_name="invites"
    )
    token = models.CharField("토큰", max_length=64, unique=True, default=new_token)
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.ISSUED)
    expires_at = models.DateTimeField("요청 마감")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="요청 계정",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invite_requests",
    )
    requested_at = models.DateTimeField("요청 시각", null=True, blank=True)
    decided_at = models.DateTimeField("승인·취소 시각", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "수강생 초대"
        verbose_name_plural = "수강생 초대"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["student", "-created_at"], name="invite_student_recent")]

    def __str__(self):
        return f"{self.student} · {self.get_status_display()}"

    @property
    def is_expired(self):
        """스캔 대기 중인데 요청 마감이 지났으면 만료. 이미 요청된 초대는 강사가 결정할 때까지 유지한다."""
        return self.status == self.Status.ISSUED and timezone.now() > self.expires_at

    @property
    def is_open(self):
        """수강생이 아직 연결을 요청할 수 있는 초대."""
        return self.status == self.Status.ISSUED and not self.is_expired
