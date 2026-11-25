from rest_framework import serializers


class ApiKeyInfoSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="키 이름")
    prefix = serializers.CharField(help_text="키 앞부분 (원문 아님)")
    scopes = serializers.ListField(
        child=serializers.CharField(), help_text="범위 코드: read, sessions:write, messages:send"
    )
    scopes_display = serializers.ListField(child=serializers.CharField(), source="scope_labels")
    created_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField()
    expires_soon = serializers.BooleanField(help_text="만료까지 14일 이하")


class TeacherSerializer(serializers.Serializer):
    name = serializers.CharField()


class MeSerializer(serializers.Serializer):
    teacher = TeacherSerializer()
    api_key = ApiKeyInfoSerializer()
