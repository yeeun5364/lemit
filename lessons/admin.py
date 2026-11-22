"""Django 관리자: 운영자(superuser) 전용. 강사에게 staff 권한을 주지 않는다.

관리자 화면에는 강사별 범위 제한이 없어, 강사에게 열면 다른 강사의 데이터가 보이기 때문이다 (PR #55 리뷰).
"""

from django.contrib import admin

from lessons.models import Enrollment, Label, LessonSession, SessionChangeLog


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "teacher", "created_at")
    search_fields = ("name", "teacher__email")
    raw_id_fields = ("teacher",)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("title", "student", "total_count", "start_date", "status")
    list_filter = ("status",)
    search_fields = ("title", "student__name", "student__teacher__email")
    raw_id_fields = ("student", "label")


@admin.register(LessonSession)
class LessonSessionAdmin(admin.ModelAdmin):
    """관리자 화면도 기록 보존 정책(ERD §3)을 따른다: 회차 삭제는 '예정'만, 일괄 삭제는 제공하지 않는다."""

    list_display = ("enrollment", "start_at", "end_at", "status", "absence_deducted")
    list_filter = ("status",)
    search_fields = ("enrollment__title", "enrollment__student__name")
    raw_id_fields = ("enrollment", "makeup_for")

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status != LessonSession.Status.SCHEDULED:
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)  # 일괄 삭제는 상태를 하나씩 확인하지 않으므로 막는다
        return actions


@admin.register(SessionChangeLog)
class SessionChangeLogAdmin(admin.ModelAdmin):
    """일정 변경 이력은 기록이므로 관리자 화면에서도 읽기만 한다 (UC-11)."""

    list_display = ("session", "old_start_at", "new_start_at", "changed_at")
    raw_id_fields = ("session",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
