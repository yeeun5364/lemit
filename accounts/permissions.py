"""강사별 데이터 접근 제한 (ERD 2-5, SCREENS §7 데이터 소유권).

인증(로그인했는가)은 LoginRequiredMiddleware가 모든 화면에 기본 적용한다.
권한(이 데이터가 로그인한 강사의 것인가)은 로그인만으로는 막을 수 없으므로, UC-02 이후 모든 앱의 뷰·폼이
아래 도우미로 **로그인한 강사의 데이터로 좁힌 쿼리셋**에서만 객체를 찾고 선택지를 만든다.

`teacher_lookup`은 모델에서 강사(User)까지 가는 경로다.
    Student       → "teacher"
    Enrollment    → "student__teacher"
    LessonSession → "enrollment__student__teacher"
    Conversation  → "teacher"
    Message       → "conversation__teacher"
"""

from django.shortcuts import get_object_or_404


def scope_to_teacher(queryset, teacher, teacher_lookup="teacher"):
    """쿼리셋을 이 강사의 데이터로 좁힌다."""
    return queryset.filter(**{teacher_lookup: teacher})


def get_owned_or_404(queryset_or_model, teacher, teacher_lookup="teacher", **lookup):
    """이 강사의 객체만 찾는다. 다른 강사의 객체는 존재 여부를 드러내지 않도록 404 (403 아님)."""
    return get_object_or_404(queryset_or_model, **{teacher_lookup: teacher}, **lookup)


class TeacherScopedQuerysetMixin:
    """클래스 기반 뷰(ListView·DetailView·UpdateView·DeleteView 등)의 get_queryset을 로그인한 강사 기준으로 좁힌다.

    상세·수정·삭제 뷰는 이 쿼리셋에서 객체를 찾으므로 다른 강사의 pk를 넣으면 자동으로 404가 된다.
    """

    teacher_lookup = "teacher"

    def get_queryset(self):
        return scope_to_teacher(super().get_queryset(), self.request.user, self.teacher_lookup)


class TeacherScopedFormMixin:
    """폼의 외래키 선택지를 로그인한 강사의 데이터로 제한한다.

    조작된 POST로 다른 강사의 id를 보내면 "올바른 선택이 아닙니다" 폼 오류가 된다.
    사용: teacher_scoped_fields = {"enrollment": "student__teacher"}, 폼 생성 시 teacher=request.user
    """

    teacher_scoped_fields: dict[str, str] = {}

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher = teacher
        for field_name, lookup in self.teacher_scoped_fields.items():
            field = self.fields[field_name]
            field.queryset = scope_to_teacher(field.queryset, teacher, lookup)
