from django.contrib import admin

from inbox.models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("sender", "body", "sent_at", "read_at", "predicted_label", "confidence", "model_version")
    readonly_fields = ("predicted_label", "confidence", "model_version")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("subject", "student", "teacher", "last_message_at")
    search_fields = ("subject", "student__name", "teacher__email")
    raw_id_fields = ("teacher", "student")
    inlines = [MessageInline]
