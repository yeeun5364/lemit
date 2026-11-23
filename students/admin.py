from django.contrib import admin

from students.models import Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("name", "teacher", "account", "is_minor", "is_active", "created_at")
    list_filter = ("is_active", "is_minor")
    search_fields = ("name", "teacher__email", "account__email")
    # account: 수강생 화면 계정 연결 (#80). 저장 때 Student.clean이 강사·관리자 계정 연결을 막는다
    raw_id_fields = ("teacher", "account")
