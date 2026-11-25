"""강사별 외부 API 키 (#102, ERD 참고).

- 원문은 저장하지 않고 SHA-256 해시만 둔다 (api.keys).
- 범위(scopes)는 코드 목록(JSON). 코드와 이름은 api.scopes.
- 만료는 필수다. 만료 전에는 같은 키 값 그대로 연장할 수 있고, 만료·폐기된 키는 다시 살릴 수 없다.
"""

import datetime

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from api import scopes as scope_codes

USAGE_TOUCH_INTERVAL = datetime.timedelta(minutes=1)  # 마지막 사용 시각은 1분에 한 번만 갱신 (DB 쓰기 줄이기)


class ApiKeyQuerySet(models.QuerySet):
    def usable(self, now=None):
        """폐기되지 않고 아직 만료되지 않은 키."""
        now = now or timezone.now()
        return self.filter(revoked_at__isnull=True, expires_at__gt=now)


class ApiKey(models.Model):
    MAX_USABLE_PER_USER = 5
    EXPIRY_CHOICES = [(30, "30일"), (90, "90일"), (180, "180일"), (365, "1년")]
    DEFAULT_EXPIRY_DAYS = 90
    EXPIRES_SOON = datetime.timedelta(days=14)

    class Status(models.TextChoices):
        ACTIVE = "active", "사용 중"
        EXPIRED = "expired", "만료"
        REVOKED = "revoked", "폐기"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys", verbose_name="강사"
    )
    name = models.CharField("이름", max_length=50, help_text="어디에 쓰는 키인지 (예: Claude 에이전트)")
    prefix = models.CharField("키 앞부분", max_length=16, help_text="목록에서 키를 구분하기 위한 앞부분. 원문이 아님")
    key_hash = models.CharField("키 해시", max_length=64, unique=True)
    scopes = models.JSONField("범위", default=list)
    created_at = models.DateTimeField("만든 시각", auto_now_add=True)
    expires_at = models.DateTimeField("만료 시각")
    last_used_at = models.DateTimeField("마지막 사용", null=True, blank=True)
    revoked_at = models.DateTimeField("폐기 시각", null=True, blank=True)

    objects = ApiKeyQuerySet.as_manager()

    class Meta:
        verbose_name = "API 키"
        verbose_name_plural = "API 키"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(expires_at__gt=F("created_at")), name="api_key_expires_after_created"),
        ]

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    def status(self, now=None):
        now = now or timezone.now()
        if self.revoked_at is not None:
            return self.Status.REVOKED
        if self.expires_at <= now:
            return self.Status.EXPIRED
        return self.Status.ACTIVE

    def get_status_display(self):
        return self.Status(self.status()).label

    @property
    def is_usable(self):
        return self.status() == self.Status.ACTIVE

    @property
    def expires_soon(self):
        return self.is_usable and self.expires_at - timezone.now() <= self.EXPIRES_SOON

    @property
    def scope_labels(self):
        return [scope_codes.label(s) for s in self.scopes]

    def has_scope(self, scope):
        return scope in self.scopes

    def touch(self, now=None):
        """마지막 사용 시각을 1분에 한 번만 DB에 쓴다. 여러 worker가 동시에 와도 조건부 UPDATE라 한 번만 바뀐다."""
        now = now or timezone.now()
        if self.last_used_at is not None and now - self.last_used_at < USAGE_TOUCH_INTERVAL:
            return
        stale = Q(last_used_at__isnull=True) | Q(last_used_at__lt=now - USAGE_TOUCH_INTERVAL)
        ApiKey.objects.filter(stale, pk=self.pk).update(last_used_at=now)
        self.last_used_at = now
