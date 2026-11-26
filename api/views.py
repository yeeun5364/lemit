"""외부 API 뷰 (#102). 공통 규칙: 이슈 #102, 오류 코드: api.errors.

모든 API 뷰는 ApiView를 상속한다.
- 웹 화면의 '기본 로그인 필수'(LoginRequiredMiddleware)에서 빼고, 인증은 API 키로만 한다.
- 업무 규칙은 각 앱의 services·selectors를 호출한다 (웹과 같은 규칙).
- `required_scope`를 반드시 적는다 (api.permissions, 적지 않으면 거부).
- 데이터를 바꾸는 메서드(POST·PUT·PATCH·DELETE)의 처리 함수는 트랜잭션 하나로 감싼다 (#102 리뷰 P1).
  처리 중 예외가 나면(ApiError 포함) 그 요청의 변경은 모두 되돌아간다. 오류는 응답을 직접 만들지 말고 예외로 낸다.
  인증·권한·요청 제한(initial)은 이 트랜잭션 밖이라, 인증 실패·요청 횟수 기록은 오류 응답이어도 남는다.
"""

from django.contrib.auth.decorators import login_not_required
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from api import scopes
from api.errors import ERRORS
from api.exceptions import ApiError
from api.pagination import ApiPagination, PageQuerySerializer
from api.schema import error_responses
from api.serializers import (
    ClassifyRequestSerializer,
    ClassifyResponseSerializer,
    MeSerializer,
    StudentListQuerySerializer,
    StudentSerializer,
)
from inbox import classifier
from students.models import Student


class ApiView(APIView):
    @classmethod
    def as_view(cls, **initkwargs):
        return login_not_required(super().as_view(**initkwargs))

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # 인증·권한·요청 제한
        name = request.method.lower()
        if request.method not in SAFE_METHODS and name in self.http_method_names and hasattr(self, name):
            # 이 요청의 view 객체에만 감싼 함수를 둔다 (클래스는 그대로)
            setattr(self, name, transaction.atomic(getattr(self, name)))


class MeView(ApiView):
    """연결 확인: 이 키의 주인과 범위·만료일을 돌려준다. 범위와 관계없이 유효한 키면 호출할 수 있다."""

    required_scope = scopes.ANY_VALID_KEY

    @extend_schema(
        summary="연결 확인 (키 정보)",
        description="API 키가 올바르게 설정됐는지 확인할 때 씁니다. 키 주인 이름과 범위·만료일을 돌려줍니다.",
        responses={200: MeSerializer, **error_responses()},
        tags=["연결 확인"],
    )
    def get(self, request):
        data = {"teacher": {"name": request.user.name}, "api_key": request.auth}
        return Response(MeSerializer(data).data)


@login_not_required
def error_docs(request):
    """오류 코드 설명 (공개). 오류 응답의 docs 링크가 이 화면의 #code로 연결된다."""
    return render(request, "api/error_docs.html", {"errors": sorted(ERRORS.items(), key=lambda kv: kv[1].status)})


class ClassifyView(ApiView):
    """메시지 분류 (#101). 웹 메시지함과 같은 모델·기준(inbox.classifier)을 쓰고, 아무것도 저장하지 않는다."""

    required_scope = scopes.READ

    @extend_schema(
        summary="메시지 분류",
        description=(
            "수강생 메시지 한 건을 휴강 요청·보강 요청·일정 변경·일반 메시지 중 하나로 분류합니다. "
            "결과는 참고용이며 메시지·회차·레슨권을 저장하거나 바꾸지 않습니다.\n\n"
            "- 필요 범위: `read`\n"
            "- 앞 500자만 분류합니다 (넘으면 `truncated: true`).\n"
            "- `is_low_confidence`가 true이거나 `scores`의 1·2위가 비슷하면 강사에게 확인하세요.\n"
            "- 모델을 쓸 수 없으면 `503 model_unavailable` (`retryable: true`)."
        ),
        request=ClassifyRequestSerializer,
        responses={
            200: ClassifyResponseSerializer,
            **error_responses("validation_error", "model_unavailable", write=True),
        },
        examples=[
            OpenApiExample(
                "일정 변경 요청",
                request_only=True,
                value={"text": "다음 주 화요일 수업 목요일로 옮길 수 있을까요?"},
            ),
            OpenApiExample(
                "분류 결과",
                response_only=True,
                value={
                    "label": "RESCHEDULE",
                    "label_display": "일정 변경",
                    "confidence": 0.812,
                    "is_low_confidence": False,
                    "scores": {"RESCHEDULE": 0.812, "MAKEUP": 0.104, "CANCELLATION": 0.061, "GENERAL": 0.023},
                    "truncated": False,
                    "model_version": "v2",
                },
            ),
        ],
        tags=["메시지"],
    )
    def post(self, request):
        serializer = ClassifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            prediction = classifier.classify(serializer.validated_data["text"])
        except classifier.MessageClassifierUnavailable:
            # 원인은 inbox.classifier가 로그에 남긴다. 응답에는 내부 경로·파일 이름을 넣지 않는다.
            raise ApiError("model_unavailable") from None
        data = {
            "label": prediction.label,
            "label_display": prediction.display,
            "confidence": prediction.confidence,
            "is_low_confidence": prediction.is_low_confidence,
            "scores": dict(sorted(prediction.scores.items(), key=lambda kv: kv[1], reverse=True)),
            "truncated": prediction.truncated,
            "model_version": prediction.model_version,
        }
        return Response(ClassifyResponseSerializer(data).data)


STUDENT_PRIVACY_NOTE = "연락처·보호자 정보·메모는 개인정보 보호를 위해 API로 제공하지 않습니다."


class StudentListView(ApiView, generics.ListAPIView):
    """수강생 목록 (#103). 웹 수강생 목록(S08)과 같은 기준: 내 수강생만, 이름순, 보관은 기본으로 숨김."""

    required_scope = scopes.READ
    serializer_class = StudentSerializer
    pagination_class = ApiPagination

    def get_queryset(self):
        params = self.filters
        qs = Student.objects.for_teacher(self.request.user)
        if params["status"] == "active":
            qs = qs.filter(is_active=True)
        elif params["status"] == "archived":
            qs = qs.filter(is_active=False)
        if params.get("q"):
            qs = qs.filter(name__icontains=params["q"])
        return qs.order_by("name", "id")

    @extend_schema(
        summary="수강생 목록",
        description=(
            "내 수강생을 이름순으로 돌려줍니다. 다른 강사의 수강생은 보이지 않습니다.\n\n"
            "- 필요 범위: `read`\n"
            "- 기본은 수강 중인 수강생만. 보관(수강 종료)한 수강생은 `status=archived` 또는 `status=all`\n"
            f"- {STUDENT_PRIVACY_NOTE}\n"
            "- 레슨권·잔여 회차는 잔여 회차 조회 API(#105)에서 다룹니다."
        ),
        parameters=[StudentListQuerySerializer, PageQuerySerializer],
        responses={200: StudentSerializer(many=True), **error_responses("validation_error", "not_found")},
        tags=["수강생"],
    )
    def get(self, request, *args, **kwargs):
        # 형식이 틀린 조건은 조용히 무시하지 않고 400으로 알려 준다 (에이전트가 잘못 보낸 줄 알 수 있게)
        query = StudentListQuerySerializer(data=request.query_params)
        page = PageQuerySerializer(data=request.query_params)
        errors = {}
        for serializer in (query, page):
            if not serializer.is_valid():
                errors.update(serializer.errors)
        if errors:
            raise ValidationError(errors)
        self.filters = query.validated_data
        self.page_params = page.validated_data  # ApiPagination이 원본 쿼리 대신 이 값을 쓴다 (PR #116 리뷰 P2)
        return super().get(request, *args, **kwargs)


class StudentDetailView(ApiView, generics.RetrieveAPIView):
    """수강생 상세 (#103). 내 수강생이 아니면(다른 강사·없는 번호) 똑같이 404."""

    required_scope = scopes.READ
    serializer_class = StudentSerializer

    def get_object(self):
        return get_object_or_404(Student.objects.for_teacher(self.request.user), pk=self.kwargs["pk"])

    @extend_schema(
        summary="수강생 상세",
        description=(
            "수강생 한 명의 정보를 돌려줍니다. 보관한 수강생도 조회할 수 있습니다(`is_active: false`).\n\n"
            "- 필요 범위: `read`\n"
            "- 내 수강생이 아니면 없는 번호와 똑같이 `404 not_found`\n"
            f"- {STUDENT_PRIVACY_NOTE}"
        ),
        responses={200: StudentSerializer, **error_responses("not_found")},
        tags=["수강생"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
