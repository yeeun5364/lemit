"""API 키 권한 범위 (#102).

메시지 발송은 수강생에게 실제로 전달되어 되돌릴 수 없으므로 일정 변경과 따로 둔다.
DB에는 코드 목록(JSON)으로 저장하므로 나중에 더 잘게 나누기 쉽다.
"""

READ = "read"
SESSIONS_WRITE = "sessions:write"
MESSAGES_SEND = "messages:send"

CHOICES = [
    (READ, "읽기"),
    (SESSIONS_WRITE, "일정 변경"),
    (MESSAGES_SEND, "메시지 발송"),
]
LABELS = dict(CHOICES)
ALL = frozenset(LABELS)


def label(scope):
    return LABELS.get(scope, scope)
