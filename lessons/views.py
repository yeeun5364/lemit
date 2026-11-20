"""UC-03 레슨권 등록·종료 (S11), UC-04 회차 등록·수정·삭제 (S06·S07).

모든 동작은 **로그인한 강사의 수강생·레슨권**에서만 객체를 찾는다 (ERD 2-5). 다른 강사의 번호면 404.
"""

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import get_owned_or_404, scope_to_teacher
from lessons import services
from lessons.forms import EnrollmentForm, SessionForm
from lessons.models import Enrollment, LessonSession
from students.models import Student

SESSION_OWNER = "enrollment__student__teacher"


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
            stale = still != set(selected)
            if not stale:
                enrollment = form.save()
                # ACTIVE 조건을 다시 걸어, 그사이 다른 요청이 먼저 종료한 행은 바꾸지도 세지도 않는다 (PR #55 리뷰 2차)
                ended = Enrollment.objects.filter(pk__in=still, status=Enrollment.Status.ACTIVE).update(
                    status=Enrollment.Status.ENDED,
                    updated_at=timezone.now(),  # update()는 auto_now를 갱신하지 않음
                )
        if stale:
            # 잠금을 푼 뒤 최신 후보로 폼을 다시 만들어, 입력값은 유지하고 안내·체크박스를 지금 상태와 맞춘다
            form = EnrollmentForm(request.POST, student=student)
            if form.is_valid():  # 선택이 다시 유효해졌더라도 바뀐 상태를 확인하도록 한 번 멈춘다
                form.add_error("end_exhausted", "선택한 레슨권의 상태가 바뀌었습니다. 화면을 다시 확인하세요.")
            return render(request, "lessons/enrollment_form.html", {"form": form, "student": student})
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
    with transaction.atomic():
        # 레슨권 등록(소진 레슨권 종료)과 같은 수강생 행 잠금 규약을 쓴다 (PR #55 리뷰 2차)
        Student.objects.select_for_update().get(pk=enrollment.student_id)
        changed = Enrollment.objects.filter(pk=enrollment.pk, status=Enrollment.Status.ACTIVE).update(
            status=Enrollment.Status.ENDED, updated_at=timezone.now()
        )
    if changed:
        messages.success(request, f"'{enrollment.title}' 레슨권을 종료했습니다.")
    return redirect("students:detail", pk=enrollment.student_id)


def _owned_session(request, pk):
    qs = LessonSession.objects.select_related("enrollment__student")
    return get_owned_or_404(qs, request.user, SESSION_OWNER, pk=pk)


def _initial_from_query(request):
    """수강생 상세의 '+ 일정'처럼 ?enrollment= 또는 ?student= 로 들어오면 레슨권을 미리 선택한다.

    값은 선택지(내 진행 중 레슨권)에 있을 때만 쓰이므로 다른 강사의 번호를 넣어도 선택되지 않는다.
    """
    initial = {"date": timezone.localdate()}
    mine = scope_to_teacher(Enrollment.objects.active(), request.user, "student__teacher")
    enrollment_id = request.GET.get("enrollment", "")
    student_id = request.GET.get("student", "")
    if enrollment_id.isdigit():
        initial["enrollment"] = mine.filter(pk=enrollment_id).values_list("pk", flat=True).first()
    elif student_id.isdigit():
        initial["enrollment"] = mine.filter(student_id=student_id).values_list("pk", flat=True).first()
    return initial


def _after_save(request, session, verb):
    messages.success(request, f"회차를 {verb}했습니다.")
    for text in services.save_warnings(session):
        messages.warning(request, text)
    return redirect("lessons:session_detail", pk=session.pk)


def session_create(request):
    """S07 회차 등록. 새 회차는 항상 '예정'. 겹침·잔여 초과는 저장 후 경고만 한다 (UC-04)."""
    initial = _initial_from_query(request)
    form = SessionForm(request.POST or None, teacher=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        session = form.save()
        return _after_save(request, session, "등록")
    preselected = Enrollment.objects.filter(pk=initial.get("enrollment")).select_related("student").first()
    if preselected:  # 수강생 상세에서 왔으면 그 수강생으로 돌아간다
        back = {
            "back_url": reverse("students:detail", args=[preselected.student_id]),
            "back_label": preselected.student.name,
        }
    else:
        back = {"back_url": reverse("students:list"), "back_label": "수강생"}
    return render(request, "lessons/session_form.html", {"form": form, **back})


def session_detail(request, pk):
    """S06 회차 상세. 완료 처리 버튼은 UC-06에서 추가한다."""
    session = _owned_session(request, pk)
    enrollment = Enrollment.objects.with_usage().get(pk=session.enrollment_id)
    return render(
        request,
        "lessons/session_detail.html",
        {"session": session, "enrollment": enrollment, "can_delete": services.deletable(session)},
    )


def session_edit(request, pk):
    """S07 회차 수정. '예정' 회차만 고친다 (완료·휴강·결석 회차의 일정 변경은 UC-11 확장)."""
    session = _owned_session(request, pk)
    if session.status != LessonSession.Status.SCHEDULED:
        messages.error(request, "예정 상태인 회차만 수정할 수 있습니다.")
        return redirect("lessons:session_detail", pk=session.pk)
    form = SessionForm(request.POST or None, instance=session, teacher=request.user)
    if request.method == "POST" and form.is_valid():
        session = form.save()
        return _after_save(request, session, "수정")
    back = {"back_url": reverse("lessons:session_detail", args=[session.pk]), "back_label": "회차"}
    return render(request, "lessons/session_form.html", {"form": form, "session": session, **back})


@require_POST
def session_delete(request, pk):
    """회차 삭제. '예정'만 가능하고, 진행 이력(완료·휴강·결석)은 남긴다."""
    session = _owned_session(request, pk)
    if not services.deletable(session):
        messages.error(request, "예정 상태인 회차만 삭제할 수 있습니다.")
        return redirect("lessons:session_detail", pk=session.pk)
    student_id = session.enrollment.student_id
    when = timezone.localtime(session.start_at)
    session.delete()
    messages.success(request, f"{when:%m/%d %H:%M} 회차를 삭제했습니다.")
    return redirect("students:detail", pk=student_id)
