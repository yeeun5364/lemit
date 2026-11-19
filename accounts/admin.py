from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import User


class UserCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "name")


class UserEditForm(UserChangeForm):
    class Meta:
        model = User
        fields = ("email", "name")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """이메일 로그인 User용 관리자 화면. 비상시 비밀번호 재설정도 여기서 한다 (UC-15)."""

    form = UserEditForm
    add_form = UserCreationForm

    list_display = ("email", "name", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "name")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("개인 정보", {"fields": ("name",)}),
        ("권한", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("기록", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "name", "password1", "password2")}),
    )
