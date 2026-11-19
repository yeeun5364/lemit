from inbox.models import Message


def unread(request):
    """하단 탭 '메시지(n)'의 안 읽은 수강생 메시지 수. 템플릿에서 쓸 때만 계산한다(callable)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    def count():
        return Message.objects.filter(
            conversation__teacher=user, sender=Message.Sender.STUDENT, read_at__isnull=True
        ).count()

    return {"inbox_unread": count}
