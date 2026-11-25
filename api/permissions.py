"""API 권한 (#102).

- IsTeacherApiKey(기본): 유효한 API 키로 인증된 강사 계정만. 수강생 기록에 연결된 계정은 403 account_not_allowed.
- 범위가 필요한 뷰는 `required_scope = scopes.READ`처럼 적는다. 키에 그 범위가 없으면 403 insufficient_scope.
- 데이터 소유(내 수강생인가)는 권한이 아니라 쿼리에서 좁힌다.
  다른 강사의 데이터는 404 (accounts.permissions와 같은 원칙).
"""

from rest_framework.permissions import BasePermission

from api import scopes
from api.exceptions import ApiError
from api.models import ApiKey
from students.models import Student


class IsTeacherApiKey(BasePermission):
    def has_permission(self, request, view):
        if not isinstance(request.auth, ApiKey):
            return False  # DRF가 401 not_authenticated로 바꾼다
        if Student.objects.filter(account=request.user).exists():
            raise ApiError("account_not_allowed")
        required = getattr(view, "required_scope", None)
        if required and not request.auth.has_scope(required):
            have = ", ".join(request.auth.scope_labels) or "없음"
            raise ApiError(
                "insufficient_scope",
                f"이 API 키에는 '{scopes.label(required)}' 권한이 없습니다. (현재 범위: {have})",
            )
        return True
