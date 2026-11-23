"""메시지 저장 + AI 분류 (UC-17).

- 분류(추론)는 DB 트랜잭션 밖에서 먼저 하고, 저장은 한 트랜잭션으로 한다.
- 모델을 쓸 수 없어도 메시지는 저장한다 ("분류 불가"). 분류 결과로 일정·회차를 바꾸지 않는다.
"""

import logging

from django.db import transaction
from django.utils import timezone

from inbox import classifier
from inbox.models import Conversation, Message

logger = logging.getLogger(__name__)


def _classify(body):
    """분류 결과 필드 dict. 실패하면 빈 dict (메시지는 그대로 저장)."""
    try:
        prediction = classifier.classify(body)
    except classifier.MessageClassifierUnavailable as e:
        logger.warning("메시지 분류를 건너뜀 (분류 불가로 저장): %s", e)
        return {}
    return {
        "predicted_label": prediction.label,
        "confidence": prediction.confidence,
        "model_version": prediction.model_version,
        "classified_at": timezone.now(),
    }


def _save(conversation, message):
    with transaction.atomic():
        message.save()
        conversation.last_message_at = message.sent_at
        conversation.save(update_fields=["last_message_at"])
    return message


def add_student_message(*, teacher, student, subject, body):
    """(시연용 A안) 수강생이 보낸 메시지로 저장하고 분류한다.

    같은 수강생·같은 제목의 대화가 있으면 거기에 이어 붙인다. student는 호출하는 쪽에서 이미
    로그인한 강사의 수강생으로 검증한 값이어야 한다 (폼 선택지 제한). 여기서도 한 번 더 확인한다.
    """
    if student.teacher_id != teacher.pk:
        raise PermissionError("다른 강사의 수강생에게는 메시지를 만들 수 없습니다.")
    message = Message(sender=Message.Sender.STUDENT, body=body, **_classify(body))
    with transaction.atomic():
        conversation, _ = Conversation.objects.get_or_create(teacher=teacher, student=student, subject=subject)
        message.conversation = conversation
        return _save(conversation, message)


def add_student_followup(*, conversation, body):
    """수강생 화면(#80)에서 같은 대화에 이어 보내는 수강생 메시지. 새 메시지와 같은 모델로 분류한다.
    conversation은 호출하는 쪽에서 로그인한 수강생 계정의 대화인지 이미 확인한 값이어야 한다."""
    message = Message(conversation=conversation, sender=Message.Sender.STUDENT, body=body, **_classify(body))
    return _save(conversation, message)


def add_teacher_reply(*, conversation, body):
    """강사 답장. 강사 메시지는 분류하지 않는다."""
    message = Message(conversation=conversation, sender=Message.Sender.TEACHER, body=body)
    return _save(conversation, message)


def mark_read(conversation):
    """대화를 열면 안 읽은 수강생 메시지를 읽음으로 바꾼다."""
    return conversation.messages.filter(sender=Message.Sender.STUDENT, read_at__isnull=True).update(
        read_at=timezone.now()
    )
