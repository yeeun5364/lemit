"""레슨권·회차·라벨 (ERD Enrollment·LessonSession·Label).

- 잔여 회차는 저장하지 않고 회차 상태로 계산한다 (ERD 2-2). 계산은 EnrollmentQuerySet.with_usage() 한 곳에만 둔다.
- 필드는 MVP 핵심 때 미리 만들어 두고(라벨·장소·보강·결석 차감), 화면 기능은 단계별로 붙인다 (ERD §4).
- Enrollment·LessonSession에는 강사 필드가 없고 student.teacher를 따라가 소유자를 확인한다 (ERD 2-5).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, F, Q


class Label(models.Model):
    """레슨권 색 구분 라벨 (ERD 2-9, UC-16). 화면은 MVP 후속, 모델만 먼저 만든다."""

    class Color(models.TextChoices):
        RED = "red", "빨강"
        ORANGE = "orange", "주황"
        YELLOW = "yellow", "노랑"
        GREEN = "green", "초록"
        TEAL = "teal", "청록"
        BLUE = "blue", "파랑"
        PURPLE = "purple", "보라"
        BROWN = "brown", "갈색"

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="강사", on_delete=models.CASCADE, related_name="labels"
    )
    name = models.CharField("이름", max_length=20)
    color = models.CharField("색", max_length=10, choices=Color.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "라벨"
        verbose_name_plural = "라벨"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["teacher", "name"], name="label_unique_name_per_teacher"),
        ]

    def __str__(self):
        return self.name


# 차감 회차 = 완료(보강 포함) + 차감한 결석 (ERD 2-2, README 회차 규칙)
DEDUCTED = Q(sessions__status="completed") | Q(sessions__status="absent", sessions__absence_deducted=True)


class EnrollmentQuerySet(models.QuerySet):
    def for_teacher(self, teacher):
        return self.filter(student__teacher=teacher)

    def active(self):
        return self.filter(status=Enrollment.Status.ACTIVE)

    def with_usage(self):
        """used_count(차감 회차)와 remaining_count(잔여 회차)를 붙인다. 잔여는 0 미만이 될 수 있다(초과 진행)."""
        return self.annotate(used_count=Count("sessions", filter=DEDUCTED)).annotate(
            remaining_count=F("total_count") - F("used_count")
        )

    def exhausted(self):
        """잔여 0 이하인 레슨권. 자동 종료하지 않고 '소진'으로 표시만 한다 (UC-03, #2)."""
        return self.with_usage().filter(remaining_count__lte=0)


class Enrollment(models.Model):
    """레슨권 (예: 피아노 개인 레슨 8회). 1:1 레슨이라 레슨명을 여기에 둔다 (ERD 2-3)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "진행 중"
        ENDED = "ended", "종료"

    student = models.ForeignKey(
        "students.Student",
        verbose_name="수강생",
        on_delete=models.PROTECT,  # 수강생은 삭제 대신 보관
        related_name="enrollments",
    )
    label = models.ForeignKey(
        Label,
        verbose_name="라벨",
        on_delete=models.SET_NULL,  # 라벨을 지우면 '라벨 없음'(회색), 레슨권은 유지
        null=True,
        blank=True,
        related_name="enrollments",
    )
    title = models.CharField("레슨명", max_length=50)
    total_count = models.PositiveSmallIntegerField("총 회차", validators=[MinValueValidator(1)])
    start_date = models.DateField("시작일")
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.ACTIVE)
    memo = models.TextField("메모", blank=True, max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EnrollmentQuerySet.as_manager()

    class Meta:
        verbose_name = "레슨권"
        verbose_name_plural = "레슨권"
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(total_count__gte=1),
                name="enrollment_total_count_gte_1",
                violation_error_message="총 회차는 1 이상이어야 합니다.",
            ),
        ]

    def __str__(self):
        return f"{self.student} · {self.title}"

    def clean(self):
        # 저장 직전 재검증 (ERD 2-5): 라벨은 같은 강사의 것만
        if self.label_id and self.student_id and self.label.teacher_id != self.student.teacher_id:
            raise ValidationError({"label": "내 라벨만 선택할 수 있습니다."})

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE


class LessonSession(models.Model):
    """수업 1회 (회차). 상태 4개, 보강은 상태가 아니라 makeup_for 연결 (ERD 2-1). 화면은 UC-04부터."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "예정"
        COMPLETED = "completed", "완료"
        CANCELLED = "cancelled", "휴강"
        ABSENT = "absent", "결석"

    enrollment = models.ForeignKey(
        Enrollment,
        verbose_name="레슨권",
        on_delete=models.PROTECT,  # 진행 이력이 사라지지 않게
        related_name="sessions",
    )
    place = models.CharField("장소", max_length=100, blank=True)
    start_at = models.DateTimeField("시작")
    end_at = models.DateTimeField("종료")
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    absence_deducted = models.BooleanField("결석 차감", default=False)
    makeup_for = models.OneToOneField(
        "self",
        verbose_name="보강 대상 회차",
        on_delete=models.SET_NULL,  # 원래 회차가 없어져도 보강 기록은 유지
        null=True,
        blank=True,
        related_name="makeup",
    )
    reason = models.TextField("사유", blank=True, max_length=500)
    completed_at = models.DateTimeField("완료 처리 시각", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "회차"
        verbose_name_plural = "회차"
        ordering = ["start_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=F("start_at")),
                name="session_end_after_start",
                violation_error_message="종료 시각은 시작 시각보다 늦어야 합니다.",
            ),
            # 차감 표시는 결석에만 의미가 있다. 다른 상태에서 true로 남으면 잔여 계산이 어긋나지 않게 DB에서 막는다.
            models.CheckConstraint(
                condition=Q(absence_deducted=False) | Q(status="absent"),
                name="session_deducted_only_if_absent",
                violation_error_message="결석 회차만 차감 여부를 가질 수 있습니다.",
            ),
        ]
        indexes = [
            models.Index(fields=["enrollment", "start_at"], name="session_enrollment_start"),
            models.Index(fields=["start_at"], name="session_start"),
        ]

    def __str__(self):
        return f"{self.enrollment} · {self.start_at:%Y-%m-%d %H:%M}"

    def clean(self):
        # 보강 대상은 같은 레슨권 강사의 휴강 또는 미차감 결석만 (ERD §3, #2)
        original = self.makeup_for
        if original is None:
            return
        if original.pk and original.pk == self.pk:
            raise ValidationError({"makeup_for": "자기 자신을 보강 대상으로 지정할 수 없습니다."})
        if self.enrollment_id and original.enrollment.student.teacher_id != self.enrollment.student.teacher_id:
            raise ValidationError({"makeup_for": "내 회차만 보강 대상으로 지정할 수 있습니다."})
        allowed = original.status == self.Status.CANCELLED or (
            original.status == self.Status.ABSENT and not original.absence_deducted
        )
        if not allowed:
            raise ValidationError({"makeup_for": "휴강이나 차감하지 않은 결석 회차만 보강할 수 있습니다."})
