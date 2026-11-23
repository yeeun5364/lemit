"""수업 일정 화면(S05·S05-W, UC-05)의 날짜 계산과 회차 조회.

- 한 주는 일요일부터 토요일까지 (월간 달력과 같은 기준, SCREENS S05-W).
- 날짜 경계는 Asia/Seoul. 회차는 시작 시각이 그날(현지 0시~다음 날 0시)에 있으면 그날 회차다.
- 보관된 수강생의 회차는 일정에 보이지 않는다 (기록은 수강생 상세에서 확인, SCREENS S05-W).
"""

import calendar
import datetime
from collections import defaultdict

from django.utils import timezone

from lessons.models import LessonSession

WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"]


def parse_date(value, default):
    """'YYYY-MM-DD'를 date로. 잘못된 값이나 너무 먼 날짜는 default (주소를 직접 고쳐도 오류 없이)."""
    try:
        d = datetime.date.fromisoformat(value or "")
    except ValueError:
        return default
    return d if 1900 <= d.year <= 2999 else default


def week_start(d):
    """d가 속한 주의 일요일."""
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def month_weeks(d):
    """d가 속한 달의 달력: 일요일부터 시작하는 주들의 날짜 목록 (앞뒤 달의 날짜도 채움)."""
    first = d.replace(day=1)
    last = d.replace(day=calendar.monthrange(d.year, d.month)[1])
    start, end = week_start(first), week_start(last) + datetime.timedelta(days=6)
    days = [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]
    return [days[i : i + 7] for i in range(0, len(days), 7)]


def add_months(d, n):
    """n개월 앞뒤의 같은 날 (그 달에 없는 날이면 말일로, 예: 1/31 + 1개월 = 2/28)."""
    m = d.month - 1 + n
    year, month = d.year + m // 12, m % 12 + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def _local_midnight(d):
    return timezone.make_aware(datetime.datetime.combine(d, datetime.time.min), timezone.get_current_timezone())


MAX_DOTS = 3


def day_dots(sessions):
    """달력 점 색: 그날 회차의 라벨 색을 나온 순서대로 최대 3개, 같은 라벨은 하나로 (SCREENS S05).

    라벨 없는 회차는 ""(회색)이고, 라벨 없음끼리도 점 하나로 합친다.
    """
    dots, seen = [], set()
    for s in sessions:
        label = s.enrollment.label
        key = label.pk if label else None
        if key not in seen:
            seen.add(key)
            dots.append(label.color if label else "")
    return dots[:MAX_DOTS]


def _group_by_day(qs, first_day, last_day):
    """회차 조회 결과를 first_day~last_day(포함) 안에서 {날짜: [회차, ...]}로. 각 날 안은 시작 시각 순."""
    qs = (
        qs.filter(
            start_at__gte=_local_midnight(first_day),
            start_at__lt=_local_midnight(last_day + datetime.timedelta(days=1)),
        )
        .select_related("enrollment__student", "enrollment__label", "makeup")
        .order_by("start_at", "id")
    )
    days = defaultdict(list)
    for s in qs:
        days[timezone.localtime(s.start_at).date()].append(s)
    return days


def sessions_by_day(teacher, first_day, last_day, student_id=None, label_id=None):
    """강사 일정: first_day~last_day(포함) 사이의 내 회차를 {날짜: [회차, ...]}로 (보관 수강생 제외)."""
    qs = LessonSession.objects.filter(enrollment__student__teacher=teacher, enrollment__student__is_active=True)
    if student_id:
        qs = qs.filter(enrollment__student_id=student_id)
    if label_id:
        qs = qs.filter(enrollment__label_id=label_id)
    return _group_by_day(qs, first_day, last_day)


def student_sessions_by_day(student, first_day, last_day):
    """수강생 화면(#92): 이 수강생 기록의 회차만.
    수강생 기록은 서버가 로그인 계정에서 정하고 요청 값으로 바꿀 수 없다."""
    return _group_by_day(LessonSession.objects.filter(enrollment__student=student), first_day, last_day)


def month_grid(selected, today, days, url_for):
    """월간 달력 칸 목록 (강사 S05·수강생 S16 공용). url_for(date) → 그 날짜를 고르는 주소."""
    return [
        [
            {
                "date": d,
                "in_month": d.month == selected.month,
                "is_today": d == today,
                "is_selected": d == selected,
                "count": len(days.get(d, [])),
                "dots": day_dots(days.get(d, [])),
                "url": url_for(d),
            }
            for d in week
        ]
        for week in month_weeks(selected)
    ]
