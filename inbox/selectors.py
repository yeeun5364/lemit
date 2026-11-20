"""메시지함 조회 쿼리 (메시지함 목록·대화 상세 왼쪽 목록·대시보드 메시지 요약이 함께 쓴다).

모든 조회는 로그인한 강사의 대화로 좁힌다 (accounts.permissions.scope_to_teacher).
"""

from django.db.models import Count, OuterRef, Q, Subquery

from accounts.permissions import scope_to_teacher
from inbox.models import Conversation, Message

LABEL_DISPLAY = dict(Message.Label.choices)


def conversations_for(teacher, label=""):
    """강사의 대화 목록 (최근순). 대화마다 가장 최근 수강생 메시지의 분류·확신도·본문과 안 읽은 수를 붙인다.

    label을 주면 가장 최근 수강생 메시지의 AI 분류가 그 값인 대화만 남긴다.
    """
    latest_student_msg = Message.objects.filter(conversation=OuterRef("pk"), sender=Message.Sender.STUDENT).order_by(
        "-sent_at", "-id"
    )
    qs = (
        scope_to_teacher(Conversation.objects.select_related("student"), teacher)
        .annotate(
            latest_label=Subquery(latest_student_msg.values("predicted_label")[:1]),
            latest_confidence=Subquery(latest_student_msg.values("confidence")[:1]),
            latest_body=Subquery(latest_student_msg.values("body")[:1]),
            unread=Count("messages", filter=Q(messages__sender=Message.Sender.STUDENT, messages__read_at__isnull=True)),
        )
        .order_by("-last_message_at", "-id")
    )
    if label:
        qs = qs.filter(latest_label=label)
    return qs


def with_display(conversations):
    """템플릿에서 쓸 분류 표시 이름을 붙여 리스트로 돌려준다."""
    conversations = list(conversations)
    for c in conversations:
        c.latest_display = LABEL_DISPLAY.get(c.latest_label, "")
    return conversations
