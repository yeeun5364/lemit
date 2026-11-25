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


# 범위와 관계없이 유효한 키면 되는 뷰(연결 확인 등)가 명시적으로 적는 값. 키에 저장하는 범위가 아니다.
# 뷰가 required_scope를 적지 않으면 거부한다 (기본 거부, #102 리뷰 P2).
ANY_VALID_KEY = "*"


def label(scope):
    return LABELS.get(scope, scope)
