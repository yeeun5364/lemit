"""로그인·비밀번호 재설정 시도 횟수 제한 (#7 '실제 사용자 공개 전 rate limit').

- 최근 WINDOW 동안 같은 이메일의 시도가 PER_EMAIL번 이상이거나, 같은 IP의 시도가 PER_IP번 이상이면 막는다.
  로그인은 실패만, 비밀번호 재설정은 요청마다 센다.
- 기록은 DB(LoginAttempt)에 둔다. gunicorn worker가 여러 개라 프로세스별 메모리 캐시는 서로의 기록을 보지 못한다.
- **검사와 기록은 한 번에(원자적으로) 한다** (PR #70 리뷰 P2): 이메일·IP마다 PostgreSQL advisory lock을 잡은 채
  "한도 확인 → 시도 한 칸 예약(행 추가)"을 하므로, 여러 worker의 동시 요청도 한도를 넘어 통과하지 못한다.
  로그인은 비밀번호를 확인하기 **전에** 칸을 예약하고, 성공하면 그 칸을 반납(release)한다.
- 없는 이메일도 똑같이 세고 똑같이 막으므로, 막혔다는 사실로 가입 여부가 드러나지 않는다.
- 로그인에 성공하면 그 이메일의 **확정된** 실패를 **이메일 집계에서만** 뺀다(email_cleared). 행은 남아 IP 집계에는
  계속 들어간다(PR #70 리뷰 P2: 여러 계정을 번갈아 시도하다 한 번 성공해도 IP 누적은 유지).
- 로그인 예약은 비밀번호 확인이 끝날 때까지 pending=True다. 같은 이메일의 다른 요청이 그사이 성공해도 진행 중인
  예약은 초기화하지 않고, 그 요청이 실패로 끝나면 confirm()으로 확정된 실패가 되어 그대로 남는다(PR #70 재검토 P2).
- IP는 프록시(Caddy) 뒤에서만 X-Forwarded-For의 마지막 값(Caddy가 본 접속 주소)을 쓴다. 프록시 없이 이 헤더를
  믿으면 누구나 위조할 수 있으므로 DJANGO_BEHIND_PROXY가 꺼져 있으면 REMOTE_ADDR만 쓴다.
- 개인정보를 오래 두지 않도록 기록할 때마다 RETENTION보다 오래된 행을 지운다.
"""

import datetime
import hashlib
import ipaddress

from django.conf import settings
from django.db import connection, transaction
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


def _lock_key(*parts):
    """advisory lock 번호(64비트 부호 있는 정수). 같은 종류·같은 이메일(또는 IP)이면 같은 번호."""
    digest = hashlib.blake2b("\x1f".join(parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _lock(kind, email, ip):
    """이메일·IP 잠금을 트랜잭션이 끝날 때까지 잡는다. 교착을 피하려고 번호 순서대로 잡는다."""
    keys = {_lock_key("throttle", kind, "email", email)}
    if ip:
        keys.add(_lock_key("throttle", kind, "ip", ip))
    with connection.cursor() as cursor:
        for key in sorted(keys):
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _counts(kind, email, ip):
    from accounts.models import LoginAttempt

    recent = LoginAttempt.objects.filter(kind=kind, created_at__gte=timezone.now() - WINDOW)
    by_email = recent.filter(email=email, email_cleared=False).count() if email else 0
    by_ip = recent.filter(ip=ip).count() if ip else 0
    return by_email, by_ip


def reserve(kind, request, email, pending=False):
    """한도 안이면 시도 한 칸을 기록하고 그 기록을 돌려준다. 한도에 닿았으면 기록하지 않고 None.

    잠금 안에서 확인과 기록을 함께 하므로, 동시에 들어온 요청도 정확히 한도까지만 통과한다.
    """
    from accounts.models import LoginAttempt

    email, ip = _normalize(email), client_ip(request)
    with transaction.atomic():
        _lock(kind, email, ip)
        by_email, by_ip = _counts(kind, email, ip)
        if by_email >= PER_EMAIL or by_ip >= PER_IP:
            return None
        LoginAttempt.objects.filter(created_at__lt=timezone.now() - RETENTION).delete()
        return LoginAttempt.objects.create(kind=kind, email=email, ip=ip, pending=pending)


def confirm(attempt):
    """진행 중인 로그인 예약을 확정된 실패로 바꾼다 (비밀번호가 틀림)."""
    from accounts.models import LoginAttempt

    if attempt is not None:
        LoginAttempt.objects.filter(pk=attempt.pk).update(pending=False)


def release(attempt):
    """예약한 칸 반납: 로그인에 성공한 시도는 실패가 아니므로 기록에서 뺀다."""
    from accounts.models import LoginAttempt

    if attempt is not None:
        LoginAttempt.objects.filter(pk=attempt.pk).delete()


def clear_email(kind, email):
    """로그인 성공: 그 이메일의 확정된 실패만 이메일 집계에서 뺀다. 행은 남겨 IP 집계에는 계속 들어가게 한다.

    비밀번호를 아직 확인 중인 다른 요청의 예약(pending=True)은 건드리지 않는다 (PR #70 재검토 P2).
    """
    from accounts.models import LoginAttempt

    LoginAttempt.objects.filter(kind=kind, email=_normalize(email), email_cleared=False, pending=False).update(
        email_cleared=True
    )
