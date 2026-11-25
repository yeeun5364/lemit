"""OpenAPI 문서 도우미 (drf-spectacular, #102).

- API 키 인증 방식(Bearer)을 문서에 알린다.
- error_responses(): 엔드포인트가 낼 수 있는 오류 상태를 공통 오류 형식으로 문서에 적는다 (PR #114 리뷰 P2).
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "api.authentication.ApiKeyAuthentication"
    name = "ApiKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "lemit_…",
            "description": "LEMIT 웹 '설정 > API 키'에서 발급한 키. `Authorization: Bearer <키>`",
        }


def error_responses(*extra_codes, write=False):
    """{상태: OpenApiResponse}. 모든 키 인증 API에 공통인 401·403·429·500에 엔드포인트별 오류를 더한다.

    extra_codes는 api.errors.ERRORS의 코드(예: "validation_error", "model_unavailable").
    write=True면 JSON 본문을 받는 API의 400(invalid_json)·415도 넣는다.
    """
    from drf_spectacular.utils import OpenApiResponse

    from api.errors import ERRORS
    from api.serializers import ErrorResponseSerializer

    codes = ["not_authenticated", "invalid_api_key", "api_key_expired", "insufficient_scope", "account_not_allowed"]
    codes += ["rate_limited", "server_error"]
    if write:
        codes += ["invalid_json", "unsupported_media_type"]
    codes += list(extra_codes)
    by_status = {}
    for code in codes:
        by_status.setdefault(ERRORS[code].status, []).append(code)
    return {
        status: OpenApiResponse(
            ErrorResponseSerializer,
            description="공통 오류 형식. 가능한 code: " + ", ".join(f"`{c}`" for c in names),
        )
        for status, names in sorted(by_status.items())
    }
