"""메시지함 (UC-17, ERD 2-10): 강사와 수강생이 메일처럼 주고받는 대화와 메시지.

- 대화(Conversation) 하나 = 강사 1 × 수강생 1 × 제목 1. 메시지(Message)가 시간순으로 쌓인다.
- AI 분류는 **수강생 메시지에만** 저장한다. 분류는 참고용이며 일정·상태를 자동으로 바꾸지 않는다.
- MVP(A안)에서는 수강생 계정이 없어서 강사가 시연용으로 수강생 메시지를 대신 등록한다.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from inbox.classifier import LOW_CONFIDENCE
from ml.labels import LABEL_DISPLAY, LABELS


class Conversation(models.Model):
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="강사", on_delete=models.PROTECT, related_name="conversations"
    )
    student = models.ForeignKey(
        "students.Student", verbose_name="수강생", on_delete=models.PROTECT, related_name="conversations"
    )
    # 비어 있으면 그 수강생과 강사의 '대화방'(#95). 수강생 화면은 제목 없이 이 대화 하나로 보낸다.
    # 강사 × 수강생 × 제목 UNIQUE라 제목 없는 대화도 수강생마다 하나뿐이다. 예전 제목 대화는 그대로 둔다.
    subject = models.CharField("제목", max_length=100, blank=True)
    last_message_at = models.DateTimeField("마지막 메시지", default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "대화"
        verbose_name_plural = "대화"
        ordering = ["-last_message_at", "-id"]
        indexes = [models.Index(fields=["teacher", "-last_message_at"], name="conv_teacher_last")]
        constraints = [
            # 강사 × 수강생 × 제목 = 대화 하나 (ERD 2-10). 동시 요청이 와도 DB가 중복을 막고,
            # get_or_create는 IntegrityError가 나면 이미 만들어진 대화를 다시 찾아 쓴다 (PR #38 리뷰).
            models.UniqueConstraint(fields=["teacher", "student", "subject"], name="conversation_unique_subject"),
        ]

    def __str__(self):
        return f"{self.student} · {self.subject}" if self.subject else str(self.student)

    def clean(self):
        # 저장 직전 재검증 (ERD 2-5): 대화의 수강생은 같은 강사의 수강생이어야 한다
        if self.student_id and self.teacher_id and self.student.teacher_id != self.teacher_id:
            raise ValidationError({"student": "내 수강생만 선택할 수 있습니다."})


class Message(models.Model):
    class Sender(models.TextChoices):
        STUDENT = "student", "수강생"
        TEACHER = "teacher", "강사"

    class Label(models.TextChoices):
        CANCELLATION = "CANCELLATION", LABEL_DISPLAY["CANCELLATION"]
        MAKEUP = "MAKEUP", LABEL_DISPLAY["MAKEUP"]
        RESCHEDULE = "RESCHEDULE", LABEL_DISPLAY["RESCHEDULE"]
        GENERAL = "GENERAL", LABEL_DISPLAY["GENERAL"]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField("보낸 사람", max_length=10, choices=Sender.choices)
    body = models.TextField("내용", max_length=1000)
    sent_at = models.DateTimeField("보낸 시각", default=timezone.now)
    read_at = models.DateTimeField("읽은 시각", null=True, blank=True)  # null이면 안 읽음 (수강생 메시지만 의미 있음)
    # AI 분류 (수강생 메시지만). 모델을 쓸 수 없으면 비워 두고 메시지는 그대로 저장한다.
    predicted_label = models.CharField("AI 분류", max_length=20, choices=Label.choices, blank=True)
    confidence = models.FloatField("확신도", null=True, blank=True)
    model_version = models.CharField("모델 버전", max_length=50, blank=True)
    classified_at = models.DateTimeField("분류 시각", null=True, blank=True)

    class Meta:
        verbose_name = "메시지"
        verbose_name_plural = "메시지"
        ordering = ["sent_at", "id"]
        indexes = [models.Index(fields=["conversation", "sent_at"], name="msg_conv_sent")]
        constraints = [
            # 강사가 보낸 메시지에는 AI 분류 필드 4개를 모두 비운다 (ERD 2-10, PR #38 리뷰)
            models.CheckConstraint(
                condition=Q(sender="student")
                | Q(predicted_label="", confidence__isnull=True, model_version="", classified_at__isnull=True),
                name="message_teacher_not_classified",
            ),
            models.CheckConstraint(
                condition=Q(confidence__isnull=True) | Q(confidence__gte=0, confidence__lte=1),
                name="message_confidence_0_1",
            ),
        ]

    def __str__(self):
        return f"[{self.get_sender_display()}] {self.body[:30]}"

    @property
    def is_from_student(self):
        return self.sender == self.Sender.STUDENT

    @property
    def is_classified(self):
        return bool(self.predicted_label)

    @property
    def confidence_percent(self):
        return round(self.confidence * 100) if self.confidence is not None else None

    @property
    def is_low_confidence(self):
        return self.confidence is not None and self.confidence < LOW_CONFIDENCE


assert {c.value for c in Message.Label} == set(LABELS), "Message.Label은 ml/labels.py의 LABELS와 같아야 한다"
