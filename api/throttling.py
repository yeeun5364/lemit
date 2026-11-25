"""API 요청 제한 (#102). 키마다 분당 60회(기본, settings.REST_FRAMEWORK). API별 한도는 각 이슈에서 정한다.

횟수는 worker끼리 공유되도록 DB 캐시(api_throttle)에 둔다. 키 없는 요청은 여기서 세지 않는다
(인증 실패는 api.authentication의 IP별 제한이 맡는다).
"""

from django.core.cache import caches
from rest_framework.throttling import SimpleRateThrottle

from api.models import ApiKey


class ApiKeyRateThrottle(SimpleRateThrottle):
    scope = "api_key"
    cache = caches["api_throttle"]

    def get_cache_key(self, request, view):
        if not isinstance(request.auth, ApiKey):
            return None
        return self.cache_format % {"scope": self.scope, "ident": request.auth.pk}
