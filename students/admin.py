from django.contrib import admin

from students.models import Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("name", "teacher", "is_minor", "is_active", "created_at")
    list_filter = ("is_active", "is_minor")
    search_fields = ("name", "teacher__email")
    raw_id_fields = ("teacher",)
