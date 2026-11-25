"""API 키 인증 (#102). `Authorization: Bearer lemit_…` 헤더만 받는다.

- 헤더가 없으면 None을 돌려주고, 권한 검사에서 401 not_authenticated가 된다.
- 없는 키·폐기된 키·비활성 계정은 구분하지 않고 401 invalid_api_key
  (어느 경우인지 알려 주면 남의 키를 추측하는 데 도움이 됨).
  만료된 키만 401 api_key_expired로 만료 날짜를 알려 준다 (키를 가진 본인이 새로 발급해야 하므로).
- URL 쿼리(?key=)는 받지 않는다. URL은 서버·프록시 로그에 남기 때문이다. 세션 쿠키도 보지 않는다.
- 같은 IP에서 인증 실패가 15분에 20번을 넘으면 그 IP의 키 인증을 잠시 막는다 (기존 로그인 제한과 같은 기준).
  횟수는 worker끼리 공유되도록 DB 캐시(api_throttle)에 둔다.
"""

import math
import time

from django.core.cache import caches
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from accounts.throttle import client_ip
from api import keys
from api.exceptions import WWW_AUTHENTICATE, ApiAuthenticationFailed, ApiError
from api.models import ApiKey

FAILURE_WINDOW = 15 * 60
FAILURES_PER_IP = 20

MALFORMED_HINT = (
    "헤더는 'Authorization: Bearer lemit_…' 형식이어야 합니다. Bearer와 키 사이에 공백 한 칸을 넣어 주세요."
)


class AuthFailureLimiter:
    """IP별 인증 실패 횟수 (최근 FAILURE_WINDOW초 기록 목록). DRF 요청 제한과 같은 방식이라 원자적이지는 않다."""

    cache_alias = "api_throttle"

    def __init__(self, request):
        self.cache = caches[self.cache_alias]
        self.key = f"api:authfail:{client_ip(request) or 'unknown'}"

    def _history(self, now):
        return [t for t in self.cache.get(self.key, []) if t > now - FAILURE_WINDOW]

    def check(self):
        now = time.time()
        history = self._history(now)
        if len(history) >= FAILURES_PER_IP:
            wait = max(1, math.ceil(history[len(history) - FAILURES_PER_IP] + FAILURE_WINDOW - now))
            raise ApiError(
                "rate_limited",
                f"API 키 인증 실패가 너무 많습니다. {wait}초 후 다시 시도해 주세요.",
                hint="키를 정확히 복사했는지 확인한 뒤 다시 시도해 주세요. 잘못된 키로 반복 요청하면 잠시 막힙니다.",
                retry_after=wait,
            )

    def record(self):
        now = time.time()
        history = self._history(now)
        history.append(now)
        self.cache.set(self.key, history, FAILURE_WINDOW)


class ApiKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = get_authorization_header(request)
        if not header:
            return None

        limiter = AuthFailureLimiter(request)
        limiter.check()

        def fail(code="invalid_api_key", message=None, hint=None):
            limiter.record()
            raise ApiAuthenticationFailed(code, message, hint=hint)

        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            fail(message="Authorization 헤더 형식이 올바르지 않습니다.", hint=MALFORMED_HINT)
        try:
            raw = parts[1].decode("ascii")
        except UnicodeDecodeError:
            fail()
        if not keys.looks_like_key(raw):
            fail()

        digest = keys.hash_key(raw)
        api_key = ApiKey.objects.select_related("user").filter(key_hash=digest).first()
        if api_key is None or not keys.same_hash(api_key.key_hash, digest) or api_key.revoked_at is not None:
            fail()
        if not api_key.user.is_active:
            fail()
        if api_key.expires_at <= timezone.now():
            expired_on = timezone.localtime(api_key.expires_at).date()
            fail("api_key_expired", f"API 키가 {expired_on:%Y-%m-%d}에 만료되었습니다.")

        api_key.touch()
        return api_key.user, api_key

    def authenticate_header(self, request):
        # 이 값이 있어야 DRF가 인증 실패를 403이 아닌 401로 답한다
        return WWW_AUTHENTICATE
