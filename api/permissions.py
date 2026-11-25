"""API 권한 (#102).

- IsTeacherApiKey(기본): 유효한 API 키로 인증된 강사 계정만. 수강생 기록에 연결된 계정은 403 account_not_allowed.
- 모든 API 뷰는 `required_scope`를 **반드시** 적는다 (기본 거부, #102 리뷰 P2).
  - `required_scope = scopes.READ`처럼 적으면 키에 그 범위가 있어야 한다. 없으면 403 insufficient_scope.
  - 범위와 관계없이 유효한 키면 되는 뷰(연결 확인)만 `scopes.ANY_VALID_KEY`를 적는다.
  - 적지 않았거나 모르는 값이면 403 permission_denied로 거부하고 오류 로그를 남긴다 (설정 실수가 공개로 이어지지 않게).
- 데이터 소유(내 수강생인가)는 권한이 아니라 쿼리에서 좁힌다.
  다른 강사의 데이터는 404 (accounts.permissions와 같은 원칙).
"""

import logging

from rest_framework.permissions import BasePermission

from api import scopes
from api.exceptions import ApiError
from api.models import ApiKey
from students.models import Student

logger = logging.getLogger("api")


class IsTeacherApiKey(BasePermission):
    def has_permission(self, request, view):
        if not isinstance(request.auth, ApiKey):
            return False  # DRF가 401 not_authenticated로 바꾼다
        if Student.objects.filter(account=request.user).exists():
            raise ApiError("account_not_allowed")
        required = getattr(view, "required_scope", None)
        if required == scopes.ANY_VALID_KEY:
            return True
        if required not in scopes.ALL:
            logger.error(
                "API 뷰 %s에 올바른 required_scope가 없어 거부 request_id=%s",
                type(view).__name__,
                getattr(request, "request_id", None),
            )
            raise ApiError(
                "permission_denied",
                "이 API는 권한 설정이 끝나지 않아 지금 사용할 수 없습니다.",
                hint="LEMIT 운영자에게 request_id를 알려 주세요.",
            )
        if not request.auth.has_scope(required):
            have = ", ".join(request.auth.scope_labels) or "없음"
            raise ApiError(
                "insufficient_scope",
                f"이 API 키에는 '{scopes.label(required)}' 권한이 없습니다. (현재 범위: {have})",
            )
        return True
