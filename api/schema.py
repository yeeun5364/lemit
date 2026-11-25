"""OpenAPI 문서에 API 키 인증 방식을 알린다 (drf-spectacular 확장, #102)."""

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
