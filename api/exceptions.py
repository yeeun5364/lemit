"""모든 API 오류를 하나의 JSON 형식으로 바꾼다 (#102).

    {"error": {"code", "message", "hint", "fields", "retryable", "docs", "request_id"}}  (+ 429는 retry_after)

- DRF 예외(입력 오류·인증·권한·요청 제한 등)와 Django의 Http404를 공통 형식으로 바꾼다.
- 예상하지 못한 예외도 이 처리기에서 500 JSON으로 답한다. 원인은 request_id와 함께 서버 로그에만 남기고,
  응답에는 내부 정보를 넣지 않는다. 로그에도 예외 메시지는 빼고 예외 종류·코드 위치만 남긴다 (config.safe_log).
- 뷰 밖(URL 없음·미들웨어 오류)의 /api/ 404·500은 config.views의 handler가 error_response()로 같은 형식을 만든다.
"""

import logging
import math

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, JsonResponse
from django.urls import reverse
from rest_framework import exceptions as drf
from rest_framework.response import Response

from api.errors import ERRORS
from config.safe_log import describe_exception

logger = logging.getLogger("api")

WWW_AUTHENTICATE = 'Bearer realm="api"'


class ApiError(drf.APIException):
    """공통 형식으로 응답할 API 오류. code는 api.errors.ERRORS의 키."""

    def __init__(self, code, message=None, *, hint=None, fields=None, retry_after=None, extra=None):
        spec = ERRORS[code]
        self.code = code
        self.status_code = spec.status
        self.message = message or spec.message
        self.hint = hint or spec.hint
        self.fields = fields or []
        self.retryable = spec.retryable
        self.retry_after = retry_after
        self.extra = extra or {}
        super().__init__(detail=self.message, code=code)


class ApiAuthenticationFailed(ApiError, drf.AuthenticationFailed):
    """인증 실패(401). DRF가 인증 실패로 알아보고 WWW-Authenticate 헤더를 붙이게 AuthenticationFailed도 상속한다."""


def _request_id(request):
    return getattr(request, "request_id", None)


def build_body(request, code, message, hint, *, fields=(), retryable=False, retry_after=None, extra=None):
    body = {
        "code": code,
        "message": message,
        "hint": hint,
        "fields": list(fields),
        "retryable": retryable,
    }
    if retry_after is not None:
        body["retry_after"] = retry_after
    if extra:
        body.update(extra)
    body["docs"] = request.build_absolute_uri(reverse("api:error_docs")) + f"#{code}"
    body["request_id"] = _request_id(request)
    return {"error": body}


def error_response(request, code, message=None):
    """DRF 밖(handler404·handler500)에서 공통 형식 응답을 만든다."""
    spec = ERRORS[code]
    body = build_body(request, code, message or spec.message, spec.hint, retryable=spec.retryable)
    return JsonResponse(body, status=spec.status, json_dumps_params={"ensure_ascii": False})


def _flatten(detail, path=None):
    """DRF ValidationError.detail(dict·list 중첩)을 [{field, code, message}] 목록으로 편다."""
    if isinstance(detail, dict):
        items = []
        for key, value in detail.items():
            name = None if key == "non_field_errors" else (f"{path}.{key}" if path else str(key))
            items += _flatten(value, name)
        return items
    if isinstance(detail, list):
        items = []
        for index, value in enumerate(detail):
            sub = f"{path}[{index}]" if isinstance(value, (dict, list)) else path
            items += _flatten(value, sub)
        return items
    return [{"field": path, "code": getattr(detail, "code", "invalid"), "message": str(detail)}]


def _convert(exc):
    """여러 종류의 예외를 ApiError 하나로 모은다. 모르는 예외는 None."""
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, Http404 | drf.NotFound):
        return ApiError("not_found")
    if isinstance(exc, drf.ValidationError):
        fields = _flatten(exc.detail)
        message = f"입력값 {len(fields)}개를 확인해 주세요." if fields else ERRORS["validation_error"].message
        return ApiError("validation_error", message, fields=fields)
    if isinstance(exc, drf.ParseError):
        return ApiError("invalid_json")
    if isinstance(exc, drf.UnsupportedMediaType):
        return ApiError("unsupported_media_type")
    if isinstance(exc, drf.NotAcceptable):
        return ApiError("not_acceptable")
    if isinstance(exc, drf.NotAuthenticated):
        return ApiError("not_authenticated")
    if isinstance(exc, drf.AuthenticationFailed):
        return ApiError("invalid_api_key")
    if isinstance(exc, DjangoPermissionDenied | drf.PermissionDenied):
        return ApiError("permission_denied")
    if isinstance(exc, drf.MethodNotAllowed):
        return ApiError("method_not_allowed")
    if isinstance(exc, drf.Throttled):
        wait = max(1, math.ceil(exc.wait or 1))
        return ApiError("rate_limited", f"요청이 너무 많습니다. {wait}초 후 다시 시도해 주세요.", retry_after=wait)
    return None


def exception_handler(exc, context):
    request = context["request"]
    error = _convert(exc)
    if error is None:
        # 예상하지 못한 오류: 원인은 로그에만. 예외 메시지에는 수강생 이름·메시지 같은 값이 섞일 수 있으므로
        # (예: DB 제약 위반 DETAIL) 예외 종류와 코드 위치만 남긴다 (config.safe_log, #102 리뷰 P2)
        logger.error(
            "API 처리 중 오류 request_id=%s path=%s\n%s",
            _request_id(request),
            request.path,
            describe_exception(exc),
        )
        error = ApiError("server_error")

    # DRF 기본 처리기와 같이, ATOMIC_REQUESTS를 켰을 때 오류 응답의 변경을 되돌린다 (#102 리뷰 P1).
    # 쓰기 API의 처리 함수는 ApiView가 트랜잭션으로 감싸므로 예외가 그 밖으로 나오면 이미 되돌아가 있다.
    from rest_framework.views import set_rollback  # 모듈 수준 import는 순환 참조(views → 설정 → 인증 → 여기)

    set_rollback()

    body = build_body(
        request,
        error.code,
        error.message,
        error.hint,
        fields=error.fields,
        retryable=error.retryable,
        retry_after=error.retry_after,
        extra=error.extra,
    )
    response = Response(body, status=error.status_code)
    if error.status_code == 401:
        response["WWW-Authenticate"] = WWW_AUTHENTICATE
    if error.retry_after is not None:
        response["Retry-After"] = str(error.retry_after)
    if isinstance(exc, drf.MethodNotAllowed):
        view = context.get("view")
        if view is not None:
            response["Allow"] = ", ".join(view.allowed_methods)
    return response
