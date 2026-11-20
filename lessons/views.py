"""UC-03 레슨권 등록·종료 (S11, 레슨권 종료 동작).

모든 동작은 **로그인한 강사의 수강생·레슨권**에서만 객체를 찾는다 (ERD 2-5). 다른 강사의 번호면 404.
"""

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import get_owned_or_404
from lessons.forms import EnrollmentForm
from lessons.models import Enrollment
from students.models import Student


def enrollment_create(request, student_pk):
    """S11 레슨권 등록. 보관된 수강생에게는 추가하지 않는다 (보관 해제 후 추가)."""
    student = get_owned_or_404(Student, request.user, pk=student_pk)
    if not student.is_active:
        messages.error(request, "보관된 수강생에게는 레슨권을 추가할 수 없습니다. 보관을 해제한 뒤 추가하세요.")
        return redirect("students:detail", pk=student.pk)

    form = EnrollmentForm(request.POST or None, student=student)
    if request.method == "POST" and form.is_valid():
        selected = [e.pk for e in form.cleaned_data.get("end_exhausted") or []]
        with transaction.atomic():
            # 검증과 저장 사이에 수강생이 보관되거나 레슨권 상태가 바뀔 수 있으므로(다른 탭·동시 요청),
            # 수강생 행을 잠근 뒤 다시 확인한다. 보관(students.archive)도 같은 행을 잠근다 (PR #55 리뷰)
            locked = Student.objects.select_for_update().get(pk=student.pk)
            if not locked.is_active:
                messages.error(request, "보관된 수강생에게는 레슨권을 추가할 수 없습니다. 보관을 해제한 뒤 추가하세요.")
                return redirect("students:detail", pk=student.pk)
            still = set(
                Enrollment.objects.filter(student=locked, pk__in=selected)
                .active()
                .exhausted()
                .values_list("pk", flat=True)
            )
            if still != set(selected):
                form.add_error("end_exhausted", "선택한 레슨권의 상태가 바뀌었습니다. 화면을 다시 확인하세요.")
                return render(request, "lessons/enrollment_form.html", {"form": form, "student": student})
            enrollment = form.save()
            ended = Enrollment.objects.filter(pk__in=still).update(
                status=Enrollment.Status.ENDED,
                updated_at=timezone.now(),  # update()는 auto_now를 갱신하지 않음
            )
        text = f"'{enrollment.title}' 레슨권({enrollment.total_count}회)을 등록했습니다."
        if ended:
            text += f" 소진된 레슨권 {ended}개를 종료했습니다."
        messages.success(request, text)
        return redirect("students:detail", pk=student.pk)
    return render(request, "lessons/enrollment_form.html", {"form": form, "student": student})


@require_POST
def enrollment_end(request, pk):
    """레슨권 종료. 잔여가 남아 있어도 강사가 직접 종료할 수 있다 (확인은 화면에서)."""
    enrollment = get_owned_or_404(Enrollment.objects.select_related("student"), request.user, "student__teacher", pk=pk)
    if enrollment.is_active:
        enrollment.status = Enrollment.Status.ENDED
        enrollment.save(update_fields=["status", "updated_at"])
        messages.success(request, f"'{enrollment.title}' 레슨권을 종료했습니다.")
    return redirect("students:detail", pk=enrollment.student_id)
