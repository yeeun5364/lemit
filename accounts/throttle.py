"""로그인·비밀번호 재설정 시도 횟수 제한 (#7 '실제 사용자 공개 전 rate limit').

- 최근 WINDOW 동안 같은 이메일의 실패가 PER_EMAIL번 이상이거나, 같은 IP의 실패가 PER_IP번 이상이면 막는다.
- 기록은 DB(LoginAttempt)에 둔다. gunicorn worker가 여러 개라 프로세스별 메모리 캐시는 서로의 기록을 보지 못한다.
- 없는 이메일도 똑같이 세고 똑같이 막으므로, 막혔다는 사실로 가입 여부가 드러나지 않는다.
- 로그인에 성공하면 그 이메일의 로그인 실패 기록을 지운다(IP 기록은 남김: 여러 계정을 번갈아 시도하는 경우 대비).
- IP는 프록시(Caddy) 뒤에서만 X-Forwarded-For의 마지막 값(Caddy가 본 접속 주소)을 쓴다. 프록시 없이 이 헤더를
  믿으면 누구나 위조할 수 있으므로 DJANGO_BEHIND_PROXY가 꺼져 있으면 REMOTE_ADDR만 쓴다.
- 개인정보를 오래 두지 않도록 기록할 때마다 RETENTION보다 오래된 행을 지운다.
"""

import datetime
import ipaddress

from django.conf import settings
from django.utils import timezone

WINDOW = datetime.timedelta(minutes=15)
RETENTION = datetime.timedelta(days=1)
PER_EMAIL = 5
PER_IP = 20
BLOCKED_MESSAGE = "로그인 시도가 너무 많습니다. 15분 뒤에 다시 시도하세요."


def client_ip(request):
    if request is None:
        return None
    candidates = []
    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):  # DJANGO_BEHIND_PROXY=True일 때만 설정됨
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            candidates.append(forwarded.split(",")[-1].strip())
    candidates.append(request.META.get("REMOTE_ADDR", ""))
    for value in candidates:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    return None


def _normalize(email):
    return (email or "").strip().lower()[:254]


def _recent(kind, request, email):
    from accounts.models import LoginAttempt

    since = timezone.now() - WINDOW
    recent = LoginAttempt.objects.filter(kind=kind, created_at__gte=since)
    email, ip = _normalize(email), client_ip(request)
    by_email = recent.filter(email=email).count() if email else 0
    by_ip = recent.filter(ip=ip).count() if ip else 0
    return by_email, by_ip


def is_blocked(kind, request, email):
    by_email, by_ip = _recent(kind, request, email)
    return by_email >= PER_EMAIL or by_ip >= PER_IP


def record(kind, request, email):
    from accounts.models import LoginAttempt

    now = timezone.now()
    LoginAttempt.objects.filter(created_at__lt=now - RETENTION).delete()
    LoginAttempt.objects.create(kind=kind, email=_normalize(email), ip=client_ip(request))


def clear_email(kind, email):
    from accounts.models import LoginAttempt

    LoginAttempt.objects.filter(kind=kind, email=_normalize(email)).delete()
