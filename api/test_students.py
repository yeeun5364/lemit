"""수강생 조회 API GET /api/v1/students/, /api/v1/students/{id}/ (#103)."""

from accounts.models import User
from api import scopes
from api.test_auth import ApiTestCase, make_key
from students.models import Student

LIST = "/api/v1/students/"
FIELDS = {"id", "name", "is_active", "is_minor", "created_at"}
PRIVATE = {"phone", "guardian_name", "guardian_phone", "memo", "account"}


def detail(pk):
    return f"/api/v1/students/{pk}/"


class StudentApiTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.raw, _ = make_key(self.teacher, [scopes.READ])
        self.other = User.objects.create_user(email="o@example.com", password="pw-2026-lemit!", name="박강사")
        self.minji = Student.objects.create(
            teacher=self.teacher,
            name="김민지",
            phone="010-1111-2222",
            is_minor=True,
            guardian_name="김보호",
            guardian_phone="010-3333-4444",
            memo="개인 메모",
        )
        self.dahye = Student.objects.create(teacher=self.teacher, name="이다혜")
        self.archived = Student.objects.create(teacher=self.teacher, name="가보관", is_active=False)
        self.others = Student.objects.create(teacher=self.other, name="김타인")

    def names(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return [s["name"] for s in response.json()["results"]]


class StudentListTests(StudentApiTestCase):
    def test_lists_only_my_active_students_by_name(self):
        response = self.get(LIST, self.raw)
        body = response.json()
        self.assertEqual(self.names(response), ["김민지", "이다혜"])  # 보관·다른 강사 수강생 없음
        self.assertEqual(set(body), {"count", "next", "previous", "results"})
        self.assertEqual(body["count"], 2)
        self.assertIsNone(body["next"])

    def test_only_minimum_fields_no_personal_info(self):
        student = self.get(LIST, self.raw).json()["results"][0]
        self.assertEqual(set(student), FIELDS)
        self.assertFalse(PRIVATE & set(student))
        self.assertEqual(student["is_minor"], True)
        self.assertTrue(student["created_at"].endswith("+09:00"))
        content = self.get(LIST, self.raw).content.decode()
        for value in ("010-1111-2222", "김보호", "010-3333-4444", "개인 메모"):
            self.assertNotIn(value, content)

    def test_status_filter(self):
        self.assertEqual(self.names(self.get(LIST + "?status=archived", self.raw)), ["가보관"])
        self.assertEqual(self.names(self.get(LIST + "?status=all", self.raw)), ["가보관", "김민지", "이다혜"])
        self.assertEqual(self.names(self.get(LIST + "?status=active", self.raw)), ["김민지", "이다혜"])

    def test_name_search_is_partial_and_stays_in_my_students(self):
        self.assertEqual(self.names(self.get(LIST + "?q=김", self.raw)), ["김민지"])  # 다른 강사의 '김타인' 없음
        self.assertEqual(self.names(self.get(LIST + "?q=%20다혜%20", self.raw)), ["이다혜"])  # 앞뒤 공백 무시
        self.assertEqual(self.names(self.get(LIST + "?q=보관&status=all", self.raw)), ["가보관"])
        self.assertEqual(self.names(self.get(LIST + "?q=없는이름", self.raw)), [])

    def test_pagination(self):
        for i in range(3):
            Student.objects.create(teacher=self.teacher, name=f"하{i}")
        first = self.get(LIST + "?page_size=2", self.raw).json()
        self.assertEqual(first["count"], 5)
        self.assertEqual([s["name"] for s in first["results"]], ["김민지", "이다혜"])
        self.assertIn("page=2", first["next"])
        self.assertIn("page_size=2", first["next"])
        last = self.get(LIST + "?page_size=2&page=3", self.raw).json()
        self.assertEqual([s["name"] for s in last["results"]], ["하2"])
        self.assertIsNone(last["next"])

    def test_default_page_size_is_20(self):
        Student.objects.bulk_create(Student(teacher=self.teacher, name=f"학생{i:02}") for i in range(25))
        body = self.get(LIST, self.raw).json()
        self.assertEqual(len(body["results"]), 20)
        self.assertEqual(body["count"], 27)

    def test_page_past_the_end_is_404_with_last_page(self):
        error = self.assertError(self.get(LIST + "?page=5", self.raw), 404, "not_found")
        self.assertIn("마지막 쪽은 1쪽", error["message"])

    def test_empty_list_first_page_is_ok(self):
        raw, _ = make_key(self.other, [scopes.READ])
        Student.objects.filter(teacher=self.other).delete()
        body = self.get(LIST, raw).json()
        self.assertEqual((body["count"], body["results"]), (0, []))

    def test_bad_query_is_400_not_ignored(self):
        cases = {
            "?status=deleted": "status",
            "?q=" + "가" * 51: "q",
            "?page_size=101": "page_size",
            "?page_size=0": "page_size",
            "?page=0": "page",
            "?page=abc": "page",
        }
        for query, field in cases.items():
            with self.subTest(query=query):
                error = self.assertError(self.get(LIST + query, self.raw), 400, "validation_error")
                self.assertEqual([f["field"] for f in error["fields"]], [field])

    def test_several_bad_params_reported_together(self):
        error = self.assertError(self.get(LIST + "?status=x&page_size=500", self.raw), 400, "validation_error")
        self.assertEqual({f["field"] for f in error["fields"]}, {"status", "page_size"})

    def test_page_numbers_must_be_plain_digits(self):
        # IntegerField는 "2.0"을 2로 받아 주지만, 실제 페이지 나눔과 해석이 갈리지 않게 숫자만 받는다 (PR #116 리뷰 P2)
        for value in ("1.0", "2.0", "+2", "%202", "2%20", "-1", "1e1", "%EF%BC%92", "last", "0x2"):
            for field in ("page", "page_size"):
                with self.subTest(field=field, value=value):
                    error = self.assertError(self.get(f"{LIST}?{field}={value}", self.raw), 400, "validation_error")
                    self.assertEqual([f["field"] for f in error["fields"]], [field])

    def test_non_digit_page_and_size_reported_together(self):
        error = self.assertError(self.get(LIST + "?page=1.0&page_size=2.0&status=x", self.raw), 400, "validation_error")
        self.assertEqual({f["field"] for f in error["fields"]}, {"page", "page_size", "status"})

    def test_validated_page_values_are_what_pagination_uses(self):
        for i in range(3):
            Student.objects.create(teacher=self.teacher, name=f"하{i}")
        body = self.get(LIST + "?page=2&page_size=2", self.raw).json()
        self.assertEqual([s["name"] for s in body["results"]], ["하0", "하1"])  # 기본 20으로 돌아가지 않음
        self.assertEqual(len(self.get(LIST + "?page_size=02", self.raw).json()["results"]), 2)  # 앞의 0은 숫자
        self.assertEqual(len(self.get(LIST + "?page_size=100", self.raw).json()["results"]), 5)  # 최대값 그대로


class PaginationFallbackTests(StudentApiTestCase):
    """뷰가 page_params를 넣지 않아도 ApiPagination이 같은 규칙으로 검증한다 (#104·#105 재사용 대비)."""

    def paginate(self, query):
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from api.pagination import ApiPagination

        request = Request(APIRequestFactory().get("/x/" + query))
        return ApiPagination().paginate_queryset(Student.objects.order_by("name", "id"), request, view=object())

    def test_valid(self):
        self.assertEqual([s.name for s in self.paginate("?page=2&page_size=1")], ["김민지"])

    def test_invalid_is_400(self):
        from rest_framework.exceptions import ValidationError

        for query in ("?page_size=2.0", "?page=1.0", "?page_size=500"):
            with self.subTest(query=query), self.assertRaises(ValidationError):
                self.paginate(query)


class StudentDetailTests(StudentApiTestCase):
    def test_my_student(self):
        response = self.get(detail(self.minji.pk), self.raw)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), FIELDS)
        self.assertEqual((body["id"], body["name"], body["is_minor"]), (self.minji.pk, "김민지", True))

    def test_archived_student_is_visible(self):
        self.assertEqual(self.get(detail(self.archived.pk), self.raw).json()["is_active"], False)

    def test_other_teachers_student_looks_like_missing(self):
        other = self.assertError(self.get(detail(self.others.pk), self.raw), 404, "not_found")
        missing = self.assertError(self.get(detail(999999), self.raw), 404, "not_found")
        self.assertEqual(other["message"], missing["message"])
        self.assertNotIn("김타인", str(other))


class StudentAccessTests(StudentApiTestCase):
    def test_needs_read_scope(self):
        raw, _ = make_key(self.teacher, [scopes.SESSIONS_WRITE])
        self.assertError(self.get(LIST, raw), 403, "insufficient_scope")
        self.assertError(self.get(detail(self.minji.pk), raw), 403, "insufficient_scope")

    def test_needs_api_key(self):
        self.assertError(self.get(LIST), 401, "not_authenticated")
        self.client.force_login(self.teacher)  # 웹 로그인 세션은 API 인증이 아님
        self.assertError(self.get(detail(self.minji.pk)), 401, "not_authenticated")

    def test_read_only(self):
        self.assertError(self.client.post(LIST, {}, HTTP_AUTHORIZATION=f"Bearer {self.raw}"), 405, "method_not_allowed")

    def test_documented_in_openapi(self):
        schema = self.client.get("/api/schema/?format=json").json()
        self.assertIn("/api/v1/students/", schema["paths"])
        self.assertIn("/api/v1/students/{id}/", schema["paths"])
        listing = schema["paths"]["/api/v1/students/"]["get"]
        params = {p["name"] for p in listing["parameters"]}
        self.assertEqual(params, {"q", "status", "page", "page_size"})
        self.assertIn("404", listing["responses"])
        student = schema["components"]["schemas"]["Student"]["properties"]
        self.assertEqual(set(student), FIELDS)
