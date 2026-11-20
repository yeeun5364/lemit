from django.contrib import admin

from lessons.models import Enrollment, Label, LessonSession


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
    list_display = ("enrollment", "start_at", "end_at", "status", "absence_deducted")
    list_filter = ("status",)
    search_fields = ("enrollment__title", "enrollment__student__name")
    raw_id_fields = ("enrollment", "makeup_for")
