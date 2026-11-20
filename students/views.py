"""UC-02 수강생 등록·수정·조회·보관 (S08~S10).

모든 조회·수정·POST 동작은 accounts.permissions 도우미로 **로그인한 강사의 수강생**에서만 객체를 찾는다.
다른 강사의 수강생 번호로 접근하면 404 (존재 여부 비공개).
"""

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.permissions import TeacherScopedQuerysetMixin, get_owned_or_404
from students.forms import StudentForm
from students.models import Student


class StudentListView(TeacherScopedQuerysetMixin, ListView):
    """S08 수강생 목록. 보관한 수강생은 기본으로 숨기고, '보관 포함'을 켜면 함께 보인다."""

    model = Student
    template_name = "students/student_list.html"
    context_object_name = "students"

    def get_queryset(self):
        qs = super().get_queryset()
        self.query = self.request.GET.get("q", "").strip()[:50]
        self.include_archived = self.request.GET.get("archived") == "1"
        if not self.include_archived:
            qs = qs.active()
        if self.query:
            qs = qs.filter(name__icontains=self.query)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(q=self.query, include_archived=self.include_archived, **kwargs)


class StudentDetailView(TeacherScopedQuerysetMixin, DetailView):
    """S10 수강생 상세. 레슨권 카드(UC-03), 회차 이력은 UC-04에서 추가한다."""

    model = Student
    template_name = "students/student_detail.html"
    context_object_name = "student"

    def get_context_data(self, **kwargs):
        from lessons.models import Enrollment

        # 수강생은 이미 로그인한 강사 것으로 좁혀 찾았으므로 그 수강생의 레슨권만 가져온다
        enrollments = list(Enrollment.objects.filter(student=self.object).with_usage())
        return super().get_context_data(
            active_enrollments=[e for e in enrollments if e.is_active],
            ended_enrollments=[e for e in enrollments if not e.is_active],
            **kwargs,
        )


class StudentCreateView(CreateView):
    """S09 수강생 추가. 강사는 폼이 아니라 로그인한 사용자로 정한다."""

    model = Student
    form_class = StudentForm
    template_name = "students/student_form.html"

    def form_valid(self, form):
        form.instance.teacher = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f"{self.object.name} 수강생을 등록했습니다.")
        return response

    def get_success_url(self):
        return reverse("students:detail", args=[self.object.pk])


class StudentUpdateView(TeacherScopedQuerysetMixin, UpdateView):
    """S09 수강생 수정. 다른 강사의 수강생은 쿼리셋에 없으므로 GET·POST 모두 404."""

    model = Student
    form_class = StudentForm
    template_name = "students/student_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "수강생 정보를 수정했습니다.")
        return response

    def get_success_url(self):
        return reverse("students:detail", args=[self.object.pk])


@require_POST
def archive(request, pk):
    """수강 종료(보관). 남은 '예정' 회차 삭제는 회차 기능(UC-04)을 만들 때 이 동작에 추가한다."""
    with transaction.atomic():
        # 레슨권 등록과 같은 수강생 행 잠금을 써서, 보관과 등록이 엇갈려 보관된 수강생에게 레슨권이 생기지 않게 한다
        student = get_owned_or_404(Student.objects.select_for_update(), request.user, pk=pk)
        if student.is_active:
            student.is_active = False
            student.save(update_fields=["is_active", "updated_at"])
            archived = True
        else:
            archived = False
    if archived:
        messages.success(request, f"{student.name} 수강생을 보관했습니다. 목록에서 '보관 포함'으로 볼 수 있습니다.")
    return redirect("students:detail", pk=student.pk)


@require_POST
def restore(request, pk):
    """보관 해제 (잘못 보관한 경우 되돌리기)."""
    student = get_owned_or_404(Student, request.user, pk=pk)
    if not student.is_active:
        student.is_active = True
        student.save(update_fields=["is_active", "updated_at"])
        messages.success(request, f"{student.name} 수강생을 다시 수강 중으로 바꿨습니다.")
    return redirect("students:detail", pk=student.pk)
