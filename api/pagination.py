"""목록 API 공통 페이지 나눔 (#102·#103).

- 응답: DRF 기본 형태 `count`·`next`·`previous`·`results`
- `page`(1부터), `page_size`(기본 20, 1~100). 값은 **숫자만으로 된 정수**(예: `2`)만 받는다.
  `2.0`·`+2`·` 2`·`１２`(전각)·`last`는 400 validation_error로 거절한다.
- 검증한 값을 그대로 페이지 나눔에 쓴다 (PR #116 리뷰 P2). DRF 기본은 원본 쿼리를 다시 읽고, 틀린 page_size는
  조용히 기본값으로 돌리거나 100으로 줄여서 호출한 쪽이 알 수 없다.
- 뷰가 다른 조회 조건과 함께 검증해 `view.page_params`에 넣어 두면 그 값을 쓰고(오류를 한 번에 알려 주려고),
  없으면 여기서 검증한다.
- 마지막 쪽을 넘는 page는 404 not_found로 답하고, 안내 문구에 마지막 쪽 번호를 넣는다.
"""

import re

from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination

from api.exceptions import ApiError

PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DIGITS = re.compile(r"[0-9]+")


class StrictQueryIntegerField(serializers.IntegerField):
    """쿼리 문자열의 정수. IntegerField는 "2.0"을 2로 바꿔 받으므로, 숫자만으로 된 문자열만 받는다."""

    def to_internal_value(self, data):
        if not (isinstance(data, str) and DIGITS.fullmatch(data)):
            self.fail("invalid")
        return super().to_internal_value(data)


class PageQuerySerializer(serializers.Serializer):
    page = StrictQueryIntegerField(
        required=False,
        min_value=1,
        help_text="쪽 번호 (1부터, 숫자만)",
        error_messages=dict.fromkeys(
            ("invalid", "min_value", "max_string_length"), "page는 1 이상의 정수(숫자만)로 보내 주세요."
        ),
    )
    page_size = StrictQueryIntegerField(
        required=False,
        min_value=1,
        max_value=MAX_PAGE_SIZE,
        help_text=f"한 쪽에 담을 개수 (기본 {PAGE_SIZE}, 1~{MAX_PAGE_SIZE}, 숫자만)",
        error_messages=dict.fromkeys(
            ("invalid", "min_value", "max_value", "max_string_length"),
            f"page_size는 1~{MAX_PAGE_SIZE} 사이의 정수(숫자만)로 보내 주세요.",
        ),
    )


def validate_page_params(query_params):
    serializer = PageQuerySerializer(data=query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class ApiPagination(PageNumberPagination):
    page_size = PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE

    def paginate_queryset(self, queryset, request, view=None):
        params = getattr(view, "page_params", None)
        self.params = validate_page_params(request.query_params) if params is None else params
        try:
            return super().paginate_queryset(queryset, request, view)
        except NotFound:
            last = self.django_paginator_class(queryset, self.get_page_size(request)).num_pages
            raise ApiError(
                "not_found",
                f"요청한 쪽(page)이 없습니다. 마지막 쪽은 {last}쪽입니다.",
                hint="1쪽부터 조회하고 응답의 next 링크를 따라가세요.",
            ) from None

    # 원본 쿼리 대신 검증한 값을 쓴다
    def get_page_size(self, request):
        return self.params.get("page_size", self.page_size)

    def get_page_number(self, request, paginator):
        return self.params.get("page", 1)
