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
            locked = services.lock_student(student.pk)
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
        services.lock_student(enrollment.student_id)
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

    선택지(SessionForm)와 같은 조건(내 진행 중 레슨권, 수강 중인 수강생)으로만 찾으므로 다른 강사나
    보관된 수강생의 번호를 넣어도 선택되지 않는다.
    """
    initial = {"date": timezone.localdate()}
    mine = scope_to_teacher(
        Enrollment.objects.active().filter(student__is_active=True), request.user, "student__teacher"
    )
    enrollment_id = request.GET.get("enrollment", "")
    student_id = request.GET.get("student", "")
    if enrollment_id.isdigit():
        initial["enrollment"] = mine.filter(pk=enrollment_id).values_list("pk", flat=True).first()
    elif student_id.isdigit():
        initial["enrollment"] = mine.filter(student_id=student_id).values_list("pk", flat=True).first()
    return initial


# 회차 수정 때 폼이 바꾸는 필드만 저장한다. 객체 전체를 저장하면 화면을 연 뒤 다른 곳에서 바뀐
# 상태·사유·보강 연결 등을 예전 값으로 되돌린다 (students 수정과 같은 원칙, ERD 동시 변경 규칙)
SESSION_FORM_FIELDS = ["enrollment", "place", "start_at", "end_at", "updated_at"]


class SaveRejected(Exception):
    """잠금 안에서 다시 확인했더니 저장하면 안 되는 상태. 메시지는 화면에 그대로 보여 준다."""


def _save_session_locked(form, original=None):
    """회차를 수강생 행 잠금 안에서 저장한다 (ERD 동시 변경 규칙).

    폼 검증 뒤 저장 전에 다른 탭·요청이 수강생을 보관하거나 레슨권을 종료하거나 회차를 완료할 수 있다.
    보관(students.archive)·레슨권 등록·종료와 같은 수강생 행을 잠근 뒤 최신 상태로 다시 확인한다.
    """
    enrollment = form.cleaned_data["enrollment"]
    with transaction.atomic():
        student = services.lock_student(enrollment.student_id)
        fresh = Enrollment.objects.get(pk=enrollment.pk)
        if not student.is_active:
            raise SaveRejected("보관된 수강생에게는 회차를 저장할 수 없습니다. 보관을 해제한 뒤 다시 시도하세요.")
        if original is None:
            if not fresh.is_active:
                raise SaveRejected("종료된 레슨권에는 회차를 추가할 수 없습니다. 레슨권을 다시 선택하세요.")
            return form.save()
        current = LessonSession.objects.select_for_update().filter(pk=original.pk).first()
        if current is None or current.status != LessonSession.Status.SCHEDULED:
            raise SaveRejected("그사이 회차 상태가 바뀌어 수정하지 않았습니다. 예정 상태인 회차만 수정할 수 있습니다.")
        if fresh.student_id != current.enrollment.student_id:
            raise SaveRejected("같은 수강생의 레슨권으로만 옮길 수 있습니다.")
        if not fresh.is_active and fresh.pk != current.enrollment_id:
            raise SaveRejected("종료된 레슨권으로는 옮길 수 없습니다. 레슨권을 다시 선택하세요.")
        session = form.save(commit=False)
        session.save(update_fields=SESSION_FORM_FIELDS)
        return session


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
        try:
            session = _save_session_locked(form)
        except SaveRejected as e:
            form.add_error(None, str(e))
        else:
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
    """S06 회차 상세. 예정이면 '완료 처리', 완료면 '완료 취소' (UC-06)."""
    session = _owned_session(request, pk)
    enrollment = Enrollment.objects.with_usage().get(pk=session.enrollment_id)
    return render(
        request,
        "lessons/session_detail.html",
        {
            "session": session,
            "enrollment": enrollment,
            "can_delete": services.deletable(session),
            "not_started": session.start_at > timezone.now(),  # 시작 전 회차는 완료 전에 확인창
        },
    )


def session_edit(request, pk):
    """S07 회차 수정. '예정' 회차만 고친다 (완료·휴강·결석 회차의 일정 변경은 UC-11 확장)."""
    session = _owned_session(request, pk)
    if session.status != LessonSession.Status.SCHEDULED:
        messages.error(request, "예정 상태인 회차만 수정할 수 있습니다.")
        return redirect("lessons:session_detail", pk=session.pk)
    form = SessionForm(request.POST or None, instance=session, teacher=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            session = _save_session_locked(form, original=session)
        except SaveRejected as e:
            messages.error(request, str(e))
            if LessonSession.objects.filter(pk=session.pk).exists():
                return redirect("lessons:session_detail", pk=session.pk)
            return redirect("students:detail", pk=session.enrollment.student_id)  # 그사이 삭제됨
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
    with transaction.atomic():
        # 확인한 뒤 지우기 전에 회차가 완료될 수 있으므로, 잠금 안에서 '예정'일 때만 지운다
        services.lock_student(student_id)
        deleted, _ = LessonSession.objects.filter(pk=session.pk, status=LessonSession.Status.SCHEDULED).delete()
    if not deleted:
        messages.error(request, "그사이 회차 상태가 바뀌어 삭제하지 않았습니다. 예정 상태인 회차만 삭제할 수 있습니다.")
        if LessonSession.objects.filter(pk=session.pk).exists():
            return redirect("lessons:session_detail", pk=session.pk)
        return redirect("students:detail", pk=student_id)
    messages.success(request, f"{when:%m/%d %H:%M} 회차를 삭제했습니다.")
    return redirect("students:detail", pk=student_id)


class StudentArchived(Exception):
    pass


def _change_status_locked(request, pk, *, from_status, to_status, completed_at, require_active_student=False):
    """회차 상태를 수강생 행 잠금 안에서 바꾼다 (UC-06, ERD 동시 변경 규칙).

    확인 뒤 바꾸기 전에 다른 탭·요청이 같은 회차를 완료·삭제할 수 있으므로, 잠금 안에서 최신 상태가
    from_status일 때만 바꾼다. 바뀐 행이 없으면 False (두 번 눌러도 한 번만 차감).
    """
    session = _owned_session(request, pk)
    with transaction.atomic():
        student = services.lock_student(session.enrollment.student_id)
        if require_active_student and not student.is_active:
            raise StudentArchived
        changed = LessonSession.objects.filter(pk=session.pk, status=from_status).update(
            status=to_status, completed_at=completed_at, updated_at=timezone.now()
        )
    return session, bool(changed)


@require_POST
def session_complete(request, pk):
    """완료 처리 (UC-06): 예정 → 완료, 1회 차감. 잔여를 넘으면 '회차 초과' 경고(완료는 허용).

    시작 전 회차의 확인은 화면(확인창)에서 받는다.
    """
    session, changed = _change_status_locked(
        request,
        pk,
        from_status=LessonSession.Status.SCHEDULED,
        to_status=LessonSession.Status.COMPLETED,
        completed_at=timezone.now(),
    )
    if not changed:
        messages.error(request, "예정 상태인 회차만 완료 처리할 수 있습니다. 화면을 다시 확인하세요.")
        return _back_to_session(session)
    e = Enrollment.objects.with_usage().get(pk=session.enrollment_id)
    messages.success(
        request, f"완료 처리했습니다. '{e.title}' 진행 {e.used_count}회 / 잔여 {max(e.remaining_count, 0)}회"
    )
    if e.remaining_count < 0:
        messages.warning(
            request,
            f"회차 초과: 총 {e.total_count}회를 넘어 {-e.remaining_count}회 더 진행했습니다. "
            "새 레슨권 등록을 확인하세요.",
        )
    return redirect("lessons:session_detail", pk=session.pk)


@require_POST
def session_uncomplete(request, pk):
    """완료 취소 (UC-06): 완료 → 예정, 차감 복구. 잘못 누른 완료를 되돌린다.

    보관된 수강생은 되돌리지 않는다. 되돌리면 보관된 수강생에게 '예정' 회차가 다시 생겨, 보관 때 예정 회차를
    지우는 규칙(ERD §3)과 어긋난다. '수업일지가 있으면 불가' 조건은 수업일지(LessonRecord, UC-14 확장)를
    만들 때 이 잠금 안에 추가한다.
    """
    try:
        session, changed = _change_status_locked(
            request,
            pk,
            from_status=LessonSession.Status.COMPLETED,
            to_status=LessonSession.Status.SCHEDULED,
            completed_at=None,
            require_active_student=True,
        )
    except StudentArchived:
        messages.error(request, "보관된 수강생의 회차는 완료 취소할 수 없습니다. 보관을 해제한 뒤 다시 시도하세요.")
        return redirect("lessons:session_detail", pk=pk)
    if not changed:
        messages.error(request, "완료 상태인 회차만 완료 취소할 수 있습니다. 화면을 다시 확인하세요.")
        return _back_to_session(session)
    e = Enrollment.objects.with_usage().get(pk=session.enrollment_id)
    messages.success(
        request, f"완료를 취소했습니다. '{e.title}' 진행 {e.used_count}회 / 잔여 {max(e.remaining_count, 0)}회"
    )
    return redirect("lessons:session_detail", pk=session.pk)


def _back_to_session(session):
    if LessonSession.objects.filter(pk=session.pk).exists():
        return redirect("lessons:session_detail", pk=session.pk)
    return redirect("students:detail", pk=session.enrollment.student_id)  # 그사이 삭제됨
