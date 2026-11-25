from rest_framework import serializers

from inbox.classifier import LOW_CONFIDENCE
from inbox.classifier import MAX_LENGTH as CLASSIFY_LENGTH
from ml.labels import LABELS


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


class ClassifyRequestSerializer(serializers.Serializer):
    """분류 요청 (#101). 길이 제한은 메시지 본문 최대 길이와 같다."""

    MAX_LENGTH = 1000

    text = serializers.JSONField(help_text=f"분류할 메시지 (1~{MAX_LENGTH}자). 앞 {CLASSIFY_LENGTH}자만 분류에 씁니다.")

    def validate_text(self, value):
        # JSONField로 받아 직접 검사한다: CharField는 숫자를 문자열로 바꿔 받아 버리고, 현재 글자 수를 알려 주지 못한다.
        if not isinstance(value, str):
            raise serializers.ValidationError("문자열로 보내 주세요.", code="invalid")
        text = value.strip()
        if not text:
            raise serializers.ValidationError(
                "분류할 메시지를 입력해 주세요. 공백만으로는 분류할 수 없습니다.", code="blank"
            )
        if len(text) > self.MAX_LENGTH:
            raise serializers.ValidationError(
                f"현재 {len(text)}자입니다. {self.MAX_LENGTH}자 이하로 줄여 주세요.", code="max_length"
            )
        return text


class ClassifyResponseSerializer(serializers.Serializer):
    label = serializers.ChoiceField(choices=LABELS, help_text="가장 확률이 높은 분류 코드")
    label_display = serializers.CharField(help_text="분류 이름 (한국어)")
    confidence = serializers.FloatField(help_text="label의 확률 (0~1, 소수 셋째 자리)")
    is_low_confidence = serializers.BooleanField(
        help_text=f"confidence가 {LOW_CONFIDENCE} 미만이면 true. 강사에게 확인을 권합니다."
    )
    scores = serializers.DictField(child=serializers.FloatField(), help_text="4개 분류 각각의 확률 (높은 순)")
    truncated = serializers.BooleanField(help_text=f"{CLASSIFY_LENGTH}자를 넘어 앞부분만 분류했으면 true")
    model_version = serializers.CharField(help_text="분류에 쓴 모델 버전 (/healthz/의 버전과 같음)")
