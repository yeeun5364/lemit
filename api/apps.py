from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "외부 API"

    def ready(self):
        # OpenAPI 문서에 'Bearer API 키' 인증 방식을 알린다 (drf-spectacular 확장 등록)
        from api import schema  # noqa: F401
