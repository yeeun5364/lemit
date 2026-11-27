"""일정(회차) 조회 API GET /api/v1/sessions/, /api/v1/sessions/{id}/ (#104)."""

import datetime
from unittest import mock

from django.utils import timezone

from accounts.models import User
from api import scopes
from api.test_auth import ApiTestCase, make_key
from lessons.models import Enrollment, LessonSession
from students.models import Student

LIST = "/api/v1/sessions/"
FIELDS = {
    "id",
    "student",
    "enrollment",
    "start_at",
    "end_at",
    "status",
    "status_display",
    "place",
    "is_makeup",
    "makeup_for",
    "absence_deducted",
}
TZ = timezone.get_fixed_timezone(9 * 60)
TODAY = datetime.date(2026, 12, 1)  # 화요일, 테스트 기준 '오늘' (서울)


def at(day, hour, minute=0):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def detail(pk):
    return f"/api/v1/sessions/{pk}/"


class SessionApiTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.raw, _ = make_key(self.teacher, [scopes.READ])
        self.minji = Student.objects.create(teacher=self.teacher, name="김민지")
        self.dahye = Student.objects.create(teacher=self.teacher, name="이다혜", is_active=False)
        self.piano = Enrollment.objects.create(student=self.minji, title="피아노 8회", total_count=8, start_date=TODAY)
        self.violin = Enrollment.objects.create(
            student=self.dahye, title="바이올린 4회", total_count=4, start_date=TODAY
        )
        self.today = self.session(self.piano, TODAY, 15, place="연습실 A")
        self.cancelled = self.session(
            self.piano, TODAY + datetime.timedelta(days=2), 15, status="cancelled", reason="병원 진료"
        )
        self.makeup = self.session(self.piano, TODAY + datetime.timedelta(days=5), 18, makeup_for=self.cancelled)
        self.violin_next = self.session(self.violin, TODAY + datetime.timedelta(days=1), 10)
        self.past = self.session(self.piano, TODAY - datetime.timedelta(days=1), 15, status="completed")
        self.edge_last = self.session(self.piano, TODAY + datetime.timedelta(days=13), 23, 30)  # 14일째 밤
        self.edge_out = self.session(self.piano, TODAY + datetime.timedelta(days=14), 0, 0)  # 15일째 0시
        other_teacher = User.objects.create_user(email="o@example.com", password="pw-2026-lemit!", name="박강사")
        self.other_student = Student.objects.create(teacher=other_teacher, name="김타인")
        other_pass = Enrollment.objects.create(
            student=self.other_student, title="첼로", total_count=4, start_date=TODAY
        )
        self.others = self.session(other_pass, TODAY, 16)

    def enrollment(self, student, title):
        return Enrollment.objects.create(student=student, title=title, total_count=8, start_date=TODAY)

    def session(self, enrollment, day, hour, minute=0, **extra):
        start = at(day, hour, minute)
        return LessonSession.objects.create(
            enrollment=enrollment, start_at=start, end_at=start + datetime.timedelta(minutes=50), **extra
        )

    def ids(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return [s["id"] for s in response.json()["results"]]


class SessionListTests(SessionApiTestCase):
    def test_default_is_today_plus_14_days_my_sessions_in_time_order(self):
        # 어제(past)·15일째 0시(edge_out)·다른 강사 회차는 빠지고, 14일째 23:30은 들어감
        expected = [self.today, self.violin_next, self.cancelled, self.makeup, self.edge_last]
        self.assertEqual(self.ids(self.get(LIST, self.raw)), [s.pk for s in expected])

    def test_fields_and_values(self):
        results = self.get(LIST, self.raw).json()["results"]
        first = results[0]
        self.assertEqual(set(first), FIELDS)
        self.assertEqual(first["student"], {"id": self.minji.pk, "name": "김민지"})
        self.assertEqual(first["enrollment"], {"id": self.piano.pk, "title": "피아노 8회"})
        self.assertEqual(first["start_at"], "2026-12-01T15:00:00+09:00")
        self.assertEqual(first["end_at"], "2026-12-01T15:50:00+09:00")
        self.assertEqual((first["status"], first["status_display"], first["place"]), ("scheduled", "예정", "연습실 A"))
        makeup = next(s for s in results if s["id"] == self.makeup.pk)
        self.assertEqual((makeup["is_makeup"], makeup["makeup_for"]), (True, self.cancelled.pk))
        self.assertEqual((first["is_makeup"], first["makeup_for"]), (False, None))

    def test_reason_is_not_exposed(self):
        response = self.get(LIST, self.raw)
        self.assertNotIn("reason", response.json()["results"][0])
        self.assertNotIn("병원 진료", response.content.decode())
        self.assertNotIn("병원 진료", self.get(detail(self.cancelled.pk), self.raw).content.decode())

    def test_date_range_inclusive_in_seoul_time(self):
        day = TODAY + datetime.timedelta(days=14)
        self.assertEqual(self.ids(self.get(f"{LIST}?from={day}&to={day}", self.raw)), [self.edge_out.pk])
        self.assertEqual(self.ids(self.get(f"{LIST}?from=2026-11-30&to=2026-11-30", self.raw)), [self.past.pk])

    def test_only_from_gives_14_days_from_there(self):
        start = TODAY - datetime.timedelta(days=1)
        ids = self.ids(self.get(f"{LIST}?from={start}", self.raw))
        self.assertEqual(ids[0], self.past.pk)
        self.assertNotIn(self.edge_last.pk, ids)  # from+13일 = 12일째까지

    def test_only_to(self):
        self.assertEqual(self.ids(self.get(f"{LIST}?to={TODAY}", self.raw)), [self.today.pk])

    def test_student_filter_including_archived_student(self):
        self.assertEqual(self.ids(self.get(f"{LIST}?student={self.dahye.pk}", self.raw)), [self.violin_next.pk])

    def test_status_filter(self):
        self.assertEqual(self.ids(self.get(f"{LIST}?status=cancelled", self.raw)), [self.cancelled.pk])

    def test_next_lesson_recipe_skips_todays_unprocessed_past_session(self):
        # 문서의 '다음 수업 찾기' 절차 (PR #117 리뷰 P2): 지금이 오늘 12:00일 때,
        # 오늘 9:00 예정 회차(아직 완료 처리 안 함)는 결과 맨 앞에 나오지만 건너뛰고,
        # next를 따라가 start_at이 지금 이후인 첫 회차(오늘 15:00)를 찾는다
        stale = self.session(self.piano, TODAY, 9)
        now = at(TODAY, 12)
        url, seen, found = f"{LIST}?student={self.minji.pk}&status=scheduled&page_size=1", [], None
        while url and found is None:
            body = self.get(url, self.raw).json()
            for item in body["results"]:
                seen.append(item["id"])
                if datetime.datetime.fromisoformat(item["start_at"]) > now:
                    found = item["id"]
                    break
            url = body["next"]
        self.assertEqual(seen, [stale.pk, self.today.pk])  # 지난 예정 회차가 먼저, 그다음 미래 회차
        self.assertEqual(found, self.today.pk)

    def test_next_lesson_recipe_when_none_in_default_period(self):
        # 기본 14일 안에 예정 회차가 없으면 빈 결과 → from을 to 다음 날로 옮겨 다시 조회
        LessonSession.objects.filter(enrollment=self.violin).delete()
        late = self.session(self.violin, TODAY + datetime.timedelta(days=30), 10)
        body = self.get(f"{LIST}?student={self.dahye.pk}&status=scheduled", self.raw).json()
        self.assertEqual((body["count"], body["next"]), (0, None))
        later = TODAY + datetime.timedelta(days=14)
        self.assertEqual(
            self.ids(self.get(f"{LIST}?student={self.dahye.pk}&status=scheduled&from={later}", self.raw)), []
        )
        to = later + datetime.timedelta(days=91)
        ids = self.ids(self.get(f"{LIST}?student={self.dahye.pk}&status=scheduled&from={later}&to={to}", self.raw))
        self.assertEqual(ids, [late.pk])

    def test_dates_must_be_zero_padded_iso(self):
        # strptime은 2026-1-1을 받아 주지만 명세는 YYYY-MM-DD만 (PR #117 리뷰 P2)
        bad = [
            "2026-1-1",
            "2026-12-1",
            "2026-1-01",
            "26-12-01",
            "2026/12/01",
            "2026-12-01 ",
            "%202026-12-01",
            "2026-02-30",
            "２０２６-12-01",
            "+2026-12-01",
            "2026-W49-2",
        ]
        for field in ("from", "to"):
            for value in bad:
                with self.subTest(field=field, value=value):
                    error = self.assertError(self.get(f"{LIST}?{field}={value}", self.raw), 400, "validation_error")
                    self.assertEqual([f["field"] for f in error["fields"]], [field])
                    self.assertIn("YYYY-MM-DD", error["fields"][0]["message"])

    def test_extreme_dates_are_400_not_500(self):
        # 9999-12-31에 하루·13일을 더하면 OverflowError → 받는 날짜를 2000-01-01~2100-12-31로 제한 (PR #117 리뷰 P2)
        for query, field in {
            "?from=9999-12-31": "from",
            "?from=9999-12-31&to=9999-12-31": "from",
            "?to=9999-12-31": "to",
            "?from=0001-01-01": "from",
            "?from=1999-12-31&to=2000-01-01": "from",
            "?from=2100-12-31&to=2101-01-01": "to",
        }.items():
            with self.subTest(query=query):
                error = self.assertError(self.get(LIST + query, self.raw), 400, "validation_error")
                self.assertIn(field, [f["field"] for f in error["fields"]])
                self.assertIn("2100-12-31", next(f["message"] for f in error["fields"] if f["field"] == field))

    def test_date_range_edges_are_accepted(self):
        for query in (
            "?from=2000-01-01",
            "?from=2000-01-01&to=2000-01-01",
            "?from=2100-12-31",
            "?to=2100-12-31&from=2100-10-01",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.get(LIST + query, self.raw).status_code, 200)

    def test_other_teachers_or_missing_student_is_404(self):
        other = self.assertError(self.get(f"{LIST}?student={self.other_student.pk}", self.raw), 404, "not_found")
        missing = self.assertError(self.get(f"{LIST}?student=999999", self.raw), 404, "not_found")
        self.assertEqual(other["message"], missing["message"])

    def test_max_range_92_days(self):
        end = TODAY + datetime.timedelta(days=91)
        self.assertEqual(self.get(f"{LIST}?from={TODAY}&to={end}", self.raw).status_code, 200)
        error = self.assertError(
            self.get(f"{LIST}?from={TODAY}&to={end + datetime.timedelta(days=1)}", self.raw), 400, "validation_error"
        )
        self.assertEqual(error["fields"][0]["field"], "to")
        self.assertIn("93일", error["fields"][0]["message"])

    def test_bad_query_is_400(self):
        cases = {
            "?from=2026-13-01": "from",
            "?from=20261201": "from",
            "?from=2026-12-01T00:00": "from",
            "?to=tomorrow": "to",
            f"?from={TODAY}&to=2026-11-30": "to",
            "?student=abc": "student",
            "?student=1.0": "student",
            "?status=done": "status",
            "?page_size=2.0": "page_size",
        }
        for query, field in cases.items():
            with self.subTest(query=query):
                error = self.assertError(self.get(LIST + query, self.raw), 400, "validation_error")
                self.assertEqual([f["field"] for f in error["fields"]], [field])

    def test_several_bad_params_together(self):
        error = self.assertError(self.get(LIST + "?status=x&student=y&page=0", self.raw), 400, "validation_error")
        self.assertEqual({f["field"] for f in error["fields"]}, {"status", "student", "page"})

    def test_pagination(self):
        body = self.get(LIST + "?page_size=2&page=2", self.raw).json()
        self.assertEqual(body["count"], 5)
        self.assertEqual([s["id"] for s in body["results"]], [self.cancelled.pk, self.makeup.pk])
        self.assertIn("page=3", body["next"])

    def test_query_count_does_not_grow_with_rows(self):
        # 회차가 늘어도 수강생·레슨권을 회차마다 따로 읽지 않는다 (select_related)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        path = LIST + "?page_size=100"
        self.get(path, self.raw)  # 첫 호출은 키 마지막 사용 시각 기록이 더해지므로 제외
        with CaptureQueriesContext(connection) as before:
            self.assertEqual(len(self.get(path, self.raw).json()["results"]), 5)
        for i in range(5):
            self.session(self.piano, TODAY + datetime.timedelta(days=3), 8 + i)
        with CaptureQueriesContext(connection) as after:
            self.assertEqual(len(self.get(path, self.raw).json()["results"]), 10)
        self.assertEqual(len(after), len(before))


class SessionDetailTests(SessionApiTestCase):
    def test_my_session(self):
        body = self.get(detail(self.makeup.pk), self.raw).json()
        self.assertEqual(set(body), FIELDS)
        self.assertEqual((body["id"], body["makeup_for"]), (self.makeup.pk, self.cancelled.pk))

    def test_past_session_has_no_date_limit(self):
        self.assertEqual(self.get(detail(self.past.pk), self.raw).json()["status"], "completed")

    def test_other_teachers_session_looks_like_missing(self):
        other = self.assertError(self.get(detail(self.others.pk), self.raw), 404, "not_found")
        missing = self.assertError(self.get(detail(999999), self.raw), 404, "not_found")
        self.assertEqual(other["message"], missing["message"])


class SessionAccessTests(SessionApiTestCase):
    def test_needs_read_scope(self):
        raw, _ = make_key(self.teacher, [scopes.MESSAGES_SEND])
        self.assertError(self.get(LIST, raw), 403, "insufficient_scope")
        self.assertError(self.get(detail(self.today.pk), raw), 403, "insufficient_scope")

    def test_needs_api_key(self):
        self.assertError(self.get(LIST), 401, "not_authenticated")

    def test_documented_in_openapi(self):
        schema = self.client.get("/api/schema/?format=json").json()
        listing = schema["paths"]["/api/v1/sessions/"]["get"]
        self.assertEqual(
            {p["name"] for p in listing["parameters"]}, {"student", "from", "to", "status", "page", "page_size"}
        )
        self.assertIn("/api/v1/sessions/{id}/", schema["paths"])
        self.assertEqual(set(schema["components"]["schemas"]["Session"]["properties"]), FIELDS)
