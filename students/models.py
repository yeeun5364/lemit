import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

PHONE_CHARS = re.compile(r"\+?[0-9 ()\-]+")
PHONE_DIGITS_MIN = 9  # 가장 짧은 국내 번호: 02-123-4567
PHONE_DIGITS_MAX = 15  # 국제 표준(E.164) 최대 자릿수


def validate_phone(value):
    """연락처: 숫자·공백·-·()만, +는 맨 앞에만(국제번호 허용), 숫자는 9~15자리 (PR #37 리뷰).

    문자 종류만 보면 '--------'처럼 숫자가 없는 값도 통과하므로 숫자 자릿수를 따로 센다.
    """
    digits = sum(ch in "0123456789" for ch in value)
    if not PHONE_CHARS.fullmatch(value) or not PHONE_DIGITS_MIN <= digits <= PHONE_DIGITS_MAX:
        raise ValidationError(
            "연락처는 숫자 9~15자리로 입력하세요. -, 공백, 괄호와 맨 앞의 +(국제번호)는 쓸 수 있습니다. "
            "예: 010-1234-5678, +82 10-1234-5678",
            code="invalid_phone",
        )


class StudentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_teacher(self, teacher):
        """로그인한 강사의 수강생만 (ERD 2-5). 뷰에서는 accounts.permissions 도우미와 같은 기준."""
        return self.filter(teacher=teacher)


class Student(models.Model):
    """수강생 (ERD Student, UC-02). 개인정보는 최소한만 받는다 (생년월일 대신 미성년자 여부, ERD 2-7)."""

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="강사",
        on_delete=models.PROTECT,  # 강사 계정을 지워도 수강생·수업 기록이 함께 사라지지 않게
        related_name="students",
    )
    name = models.CharField("이름", max_length=50)
    phone = models.CharField("연락처", max_length=20, blank=True, validators=[validate_phone])
    is_minor = models.BooleanField("미성년자", default=False)
    guardian_name = models.CharField("보호자 이름", max_length=50, blank=True)
    guardian_phone = models.CharField("보호자 연락처", max_length=20, blank=True, validators=[validate_phone])
    memo = models.TextField("메모", blank=True, max_length=1000)
    is_active = models.BooleanField("수강 중", default=True)  # False = 보관(수강 종료)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StudentQuerySet.as_manager()

    class Meta:
        verbose_name = "수강생"
        verbose_name_plural = "수강생"
        ordering = ["name", "id"]
        constraints = [
            # 미성년자면 보호자 연락처 필수 (ERD 2-7). 폼 검증과 별개로 DB에서도 막는다.
            models.CheckConstraint(
                condition=Q(is_minor=False) | ~Q(guardian_phone=""),
                name="student_minor_requires_guardian_phone",
                violation_error_message="미성년자는 보호자 연락처가 필요합니다.",
            ),
        ]
        indexes = [models.Index(fields=["teacher", "is_active", "name"], name="student_teacher_active_name")]

    def __str__(self):
        return self.name
