"""목록 API 공통 페이지 나눔 (#102·#103).

- 응답: DRF 기본 형태 `count`·`next`·`previous`·`results`
- `page`(1부터), `page_size`(기본 20, 1~100). 형식이 틀린 값은 뷰가 먼저 400 validation_error로 거절한다
  (DRF 기본은 틀린 page_size를 조용히 무시하거나 100으로 줄여서, 호출한 쪽이 알 수 없다).
- 마지막 쪽을 넘는 page는 404 not_found로 답하고, 안내 문구에 마지막 쪽 번호를 넣는다.
"""

from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination

from api.exceptions import ApiError

PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class PageQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="쪽 번호 (1부터)",
        error_messages={"invalid": "page는 1 이상의 정수로 보내 주세요.", "min_value": "page는 1부터 시작합니다."},
    )
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=MAX_PAGE_SIZE,
        help_text=f"한 쪽에 담을 개수 (기본 {PAGE_SIZE}, 최대 {MAX_PAGE_SIZE})",
        error_messages=dict.fromkeys(
            ("invalid", "min_value", "max_value"), f"page_size는 1~{MAX_PAGE_SIZE} 사이의 정수로 보내 주세요."
        ),
    )


class ApiPagination(PageNumberPagination):
    page_size = PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE

    def paginate_queryset(self, queryset, request, view=None):
        try:
            return super().paginate_queryset(queryset, request, view)
        except NotFound:
            last = self.django_paginator_class(queryset, self.get_page_size(request)).num_pages
            raise ApiError(
                "not_found",
                f"요청한 쪽(page)이 없습니다. 마지막 쪽은 {last}쪽입니다.",
                hint="1쪽부터 조회하고 응답의 next 링크를 따라가세요.",
            ) from None
