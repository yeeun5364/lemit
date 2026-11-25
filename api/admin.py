from django.contrib import admin

from api.models import ApiKey


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    """운영자는 키 상태를 보고 폐기 시각을 넣을 수만 있다. 키는 원문을 보여 줘야 하므로 강사 화면에서만 만든다."""

    list_display = ("name", "user", "prefix", "scopes", "created_at", "expires_at", "last_used_at", "revoked_at")
    list_filter = ("revoked_at",)
    search_fields = ("name", "prefix", "user__email", "user__name")
    readonly_fields = ("user", "prefix", "key_hash", "scopes", "created_at", "expires_at", "last_used_at")
    list_select_related = ("user",)

    def has_add_permission(self, request):
        return False
