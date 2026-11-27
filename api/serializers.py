import datetime
import re

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.errors import ERRORS
from api.pagination import StrictQueryIntegerField
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


class StudentRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class EnrollmentRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(help_text="레슨명")


SESSION_STATUS_CHOICES = [
    ("scheduled", "예정"),
    ("completed", "완료"),
    ("cancelled", "휴강"),
    ("absent", "결석"),
]


class SessionSerializer(serializers.Serializer):
    """회차 (#104).

    사유(reason)는 건강·가정사 같은 개인정보가 들어갈 수 있는 자유 입력이라 넣지 않는다 (2026-11-26 결정).
    """

    id = serializers.IntegerField(help_text="회차 번호")
    student = StudentRefSerializer(source="enrollment.student", help_text="수강생")
    enrollment = EnrollmentRefSerializer(help_text="레슨권")
    start_at = serializers.DateTimeField(help_text="시작 (서울 시간)")
    end_at = serializers.DateTimeField(help_text="종료 (서울 시간)")
    status = serializers.ChoiceField(
        choices=SESSION_STATUS_CHOICES, help_text="scheduled(예정)·completed(완료)·cancelled(휴강)·absent(결석)"
    )
    status_display = serializers.CharField(source="get_status_display", help_text="상태 이름 (한국어)")
    place = serializers.CharField(help_text="장소 (없으면 빈 문자열)")
    is_makeup = serializers.SerializerMethodField(help_text="다른 회차의 보강 회차면 true")
    makeup_for = serializers.IntegerField(source="makeup_for_id", allow_null=True, help_text="보강 대상 회차 번호")
    absence_deducted = serializers.BooleanField(help_text="결석이면서 회차를 차감했으면 true")

    def get_is_makeup(self, obj) -> bool:
        return obj.makeup_for_id is not None


SESSION_DEFAULT_DAYS = 14  # 기간을 안 주면 오늘부터 14일 (오늘 포함)
SESSION_MAX_DAYS = 92  # 한 번에 조회할 수 있는 최대 기간 (약 3개월)
# 받을 수 있는 날짜 범위. 기본 기간(+13일)·조회 상한(+1일)·시간대 변환을 해도 넘치지 않게 좁혀 둔다
# (9999-12-31에 하루를 더하면 OverflowError로 500이 나므로, PR #117 리뷰 P2)
QUERY_DATE_MIN = datetime.date(2000, 1, 1)
QUERY_DATE_MAX = datetime.date(2100, 12, 31)
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@extend_schema_field(
    {
        "type": "string",
        "format": "date",
        "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
        "description": f"YYYY-MM-DD, {QUERY_DATE_MIN}~{QUERY_DATE_MAX}",
    }
)
class StrictQueryDateField(serializers.Field):
    """쿼리 문자열의 날짜. YYYY-MM-DD(숫자 자릿수 고정)만 받는다.

    DateField(input_formats=["%Y-%m-%d"])는 strptime이라 2026-1-1도 받아 주므로 쓰지 않는다 (PR #117 리뷰 P2).
    """

    default_error_messages = {
        "invalid": "날짜는 YYYY-MM-DD 형식으로 보내 주세요. 예: 2026-12-01",
        "out_of_range": f"날짜는 {QUERY_DATE_MIN}부터 {QUERY_DATE_MAX}까지만 조회할 수 있습니다.",
    }

    def to_internal_value(self, data):
        if not (isinstance(data, str) and ISO_DATE.fullmatch(data)):
            self.fail("invalid")
        try:
            value = datetime.date.fromisoformat(data)
        except ValueError:  # 2026-13-01, 2026-02-30 같은 없는 날짜
            self.fail("invalid")
        if not QUERY_DATE_MIN <= value <= QUERY_DATE_MAX:
            self.fail("out_of_range")
        return value

    def to_representation(self, value):
        return value.isoformat()


class SessionListQuerySerializer(serializers.Serializer):
    """일정 목록 조회 조건 (#104). 날짜는 서울 기준, from·to 둘 다 포함."""

    student = StrictQueryIntegerField(
        required=False,
        min_value=1,
        help_text="수강생 번호. 빼면 내 수강생 전체",
        error_messages=dict.fromkeys(("invalid", "min_value"), "student는 수강생 번호(숫자만)로 보내 주세요."),
    )
    status = serializers.ChoiceField(
        choices=SESSION_STATUS_CHOICES,
        required=False,
        help_text="scheduled·completed·cancelled·absent 중 하나. 빼면 전체",
        error_messages={"invalid_choice": "scheduled, completed, cancelled, absent 중 하나로 보내 주세요."},
    )

    def get_fields(self):
        # from은 파이썬 예약어라 클래스 속성으로 쓸 수 없어서 여기서 넣는다
        fields = super().get_fields()
        fields["from"] = StrictQueryDateField(
            required=False,
            help_text=f"시작 날짜 YYYY-MM-DD (서울 기준, 포함, {QUERY_DATE_MIN}~{QUERY_DATE_MAX}). 빼면 오늘",
        )
        fields["to"] = StrictQueryDateField(
            required=False,
            help_text=(
                f"끝 날짜 YYYY-MM-DD (포함). 빼면 from부터 {SESSION_DEFAULT_DAYS}일. 기간은 최대 {SESSION_MAX_DAYS}일"
            ),
        )
        return fields

    def validate(self, attrs):
        start = attrs.get("from") or timezone.localdate()
        end = attrs.get("to") or start + datetime.timedelta(days=SESSION_DEFAULT_DAYS - 1)
        if end < start:
            raise serializers.ValidationError({"to": f"to({end})는 from({start})과 같거나 뒤여야 합니다."})
        days = (end - start).days + 1
        if days > SESSION_MAX_DAYS:
            raise serializers.ValidationError(
                {"to": f"기간이 {days}일입니다. 한 번에 {SESSION_MAX_DAYS}일까지 조회할 수 있으니 나눠서 조회하세요."}
            )
        attrs["from"], attrs["to"] = start, end
        return attrs
