"""외부 API 뷰 (#102). 공통 규칙: 이슈 #102, 오류 코드: api.errors.

모든 API 뷰는 ApiView를 상속한다.
- 웹 화면의 '기본 로그인 필수'(LoginRequiredMiddleware)에서 빼고, 인증은 API 키로만 한다.
- 업무 규칙은 각 앱의 services·selectors를 호출한다 (웹과 같은 규칙).
"""

from django.contrib.auth.decorators import login_not_required
from django.shortcuts import render
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from api.errors import ERRORS
from api.serializers import MeSerializer


class ApiView(APIView):
    @classmethod
    def as_view(cls, **initkwargs):
        return login_not_required(super().as_view(**initkwargs))


class MeView(ApiView):
    """연결 확인: 이 키의 주인과 범위·만료일을 돌려준다. 범위와 관계없이 유효한 키면 호출할 수 있다."""

    @extend_schema(
        summary="연결 확인 (키 정보)",
        description="API 키가 올바르게 설정됐는지 확인할 때 씁니다. 키 주인 이름과 범위·만료일을 돌려줍니다.",
        responses=MeSerializer,
        tags=["연결 확인"],
    )
    def get(self, request):
        data = {"teacher": {"name": request.user.name}, "api_key": request.auth}
        return Response(MeSerializer(data).data)


@login_not_required
def error_docs(request):
    """오류 코드 설명 (공개). 오류 응답의 docs 링크가 이 화면의 #code로 연결된다."""
    return render(request, "api/error_docs.html", {"errors": sorted(ERRORS.items(), key=lambda kv: kv[1].status)})
