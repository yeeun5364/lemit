from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.errors import ERRORS
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


TEXT_MAX_LENGTH = 1000  # 메시지 본문 최대 길이(inbox.Message.body)와 같다


@extend_schema_field(
    {
        "type": "string",
        "minLength": 1,
        "description": (
            f"분류할 메시지. **앞뒤 공백을 뺀 길이**가 1~{TEXT_MAX_LENGTH}자여야 합니다 "
            f"(공백만 있으면 거절, 보낸 문자열 자체의 길이가 아님). 앞 {CLASSIFY_LENGTH}자만 분류에 씁니다."
        ),
        "example": "다음 주 화요일 수업 목요일로 옮길 수 있을까요?",
    }
)
class StrictTextField(serializers.Field):
    """JSON 문자열만 받는 필드. CharField는 숫자 123을 "123"으로 바꿔 받아 버리므로 쓰지 않는다."""

    def to_internal_value(self, data):
        if not isinstance(data, str):
            raise serializers.ValidationError("문자열로 보내 주세요.", code="invalid")
        return data

    def to_representation(self, value):
        return value


class ClassifyRequestSerializer(serializers.Serializer):
    """분류 요청 (#101). 길이 제한은 메시지 본문 최대 길이와 같다."""

    MAX_LENGTH = TEXT_MAX_LENGTH

    text = StrictTextField()

    def validate_text(self, value):
        # 앞뒤 공백을 뺀 길이로 검사하고, 넘으면 현재 글자 수를 알려 준다
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


class ErrorFieldSerializer(serializers.Serializer):
    field = serializers.CharField(allow_null=True, help_text="문제가 있는 입력 항목. 본문 전체 문제면 null")
    code = serializers.CharField(help_text="항목 오류 코드 (예: required, blank, max_length, invalid)")
    message = serializers.CharField(help_text="고치는 방법 (한국어)")


class ErrorBodySerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=sorted(ERRORS), help_text="오류 코드. 분기는 이 값으로 하세요")
    message = serializers.CharField(help_text="무엇이 잘못됐는지 (한국어)")
    hint = serializers.CharField(help_text="어떻게 고치는지 (한국어)")
    fields = ErrorFieldSerializer(many=True, help_text="입력 항목별 문제 (입력 오류가 아니면 빈 목록)")
    retryable = serializers.BooleanField(help_text="같은 요청을 나중에 다시 보내면 성공할 수 있는지")
    retry_after = serializers.IntegerField(required=False, help_text="429일 때만: 몇 초 뒤 다시 시도할지")
    docs = serializers.URLField(help_text="오류 코드 설명 주소 (/api/docs/errors/#code)")
    request_id = serializers.CharField(help_text="X-Request-ID 헤더와 같은 값. 문의할 때 알려 주세요")


class ErrorResponseSerializer(serializers.Serializer):
    """모든 API 오류 응답의 공통 형식 (#102)."""

    error = ErrorBodySerializer()


class StudentSerializer(serializers.Serializer):
    """수강생 (#103). 개인정보 최소 공개: 연락처·보호자 정보·메모는 넣지 않는다 (2026-11-26 결정).

    API 결과는 MCP를 거쳐 외부 AI 에이전트에 전달될 수 있으므로, 수강생을 알아보고 고르는 데 필요한 값만 준다.
    """

    id = serializers.IntegerField(help_text="수강생 번호. 상세·레슨권·일정 조회에 씁니다")
    name = serializers.CharField(help_text="이름")
    is_active = serializers.BooleanField(help_text="수강 중이면 true, 보관(수강 종료)이면 false")
    is_minor = serializers.BooleanField(help_text="미성년자 여부")
    created_at = serializers.DateTimeField(help_text="등록 시각 (서울 시간)")


class StudentListQuerySerializer(serializers.Serializer):
    """수강생 목록 조회 조건 (#103). 페이지 조건(page·page_size)은 api.pagination.PageQuerySerializer."""

    STATUS_CHOICES = [("active", "수강 중만"), ("archived", "보관만"), ("all", "전체")]
    Q_MAX_LENGTH = 50

    q = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=Q_MAX_LENGTH,
        help_text=f"이름 검색 (일부만 같아도 찾음, 최대 {Q_MAX_LENGTH}자)",
        error_messages={"max_length": f"검색어는 {Q_MAX_LENGTH}자 이하로 보내 주세요."},
    )
    status = serializers.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        default="active",
        help_text="active(기본, 수강 중만) · archived(보관만) · all(전체)",
        error_messages={"invalid_choice": "active, archived, all 중 하나로 보내 주세요."},
    )
