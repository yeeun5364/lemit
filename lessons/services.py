"""회차 저장 후 경고 (UC-04). 경고는 저장을 막지 않고, 저장이 끝난 뒤 다음 화면 알림으로 보여 준다."""

from django.utils import timezone

from lessons.models import Enrollment, LessonSession
from students.models import Student


def overlapping_sessions(session):
    """같은 강사의 다른 회차 중 시간이 겹치는 것. 휴강 회차는 실제로 열리지 않으므로 제외한다.

    겹침: 다른 회차 시작 < 이 회차 종료 AND 다른 회차 종료 > 이 회차 시작 (끝과 시작이 맞닿으면 겹침 아님)
    """
    teacher_id = session.enrollment.student.teacher_id
    return (
        LessonSession.objects.filter(enrollment__student__teacher_id=teacher_id)
        .exclude(pk=session.pk)
        .exclude(status=LessonSession.Status.CANCELLED)
        .filter(start_at__lt=session.end_at, end_at__gt=session.start_at)
        .select_related("enrollment__student")
        .order_by("start_at", "id")
    )


def scheduled_over_remaining(enrollment):
    """(예정 회차 수, 잔여 회차)를 돌려준다. 예정 > 잔여이면 '잔여 회차를 넘는 일정' 경고 대상.

    저장 후 기준으로 세므로 지금 저장한 회차가 포함되고, 수정이면 한 번만 센다.
    """
    e = Enrollment.objects.with_usage().get(pk=enrollment.pk)
    scheduled = e.sessions.filter(status=LessonSession.Status.SCHEDULED).count()
    return scheduled, e.remaining_count


def save_warnings(session):
    """저장 직후 보여 줄 경고 문장 목록."""
    warnings = []
    for other in overlapping_sessions(session)[:3]:
        start = timezone.localtime(other.start_at)
        end = timezone.localtime(other.end_at)
        warnings.append(
            f"같은 시간({start:%m/%d %H:%M}~{end:%H:%M})에 {other.enrollment.student.name} 회차가 있습니다."
        )
    scheduled, remaining = scheduled_over_remaining(session.enrollment)
    if scheduled > remaining:
        warnings.append(
            f"잔여 회차를 넘는 일정입니다. '{session.enrollment.title}' 잔여 {max(remaining, 0)}회, 예정 {scheduled}회."
        )
    return warnings


def deletable(session):
    """삭제는 '예정' 회차만 (ERD §3). 진행 이력은 남긴다."""
    return session.status == LessonSession.Status.SCHEDULED


def scheduled_sessions_of(student):
    return LessonSession.objects.filter(enrollment__student=student, status=LessonSession.Status.SCHEDULED)


def lock_student(student_id):
    """수강생 행을 잠그고 최신 값을 돌려준다. 트랜잭션 안에서만 쓴다.

    잔여 회차·레슨권·회차 상태를 바꾸는 쓰기의 공통 직렬화 지점 (ERD 동시 변경 규칙):
    레슨권 등록·종료, 수강생 보관, 회차 등록·수정·삭제. UC-06 완료 처리도 이것을 쓴다.
    """
    return Student.objects.select_for_update().get(pk=student_id)
