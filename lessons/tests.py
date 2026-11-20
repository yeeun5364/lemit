"""UC-03 레슨권 등록·종료, 잔여 회차 계산(ERD 2-2), 강사 A/B 격리 테스트."""

import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from lessons.models import Enrollment, Label, LessonSession
from students.models import Student

User = get_user_model()
PASSWORD = "lesson-time-2026!"


def make_teacher(email, name):
    return User.objects.create_user(email=email, password=PASSWORD, name=name)


def make_pass(student, title="피아노 개인 레슨", total=8, **kwargs):
    return Enrollment.objects.create(
        student=student, title=title, total_count=total, start_date=datetime.date(2026, 11, 1), **kwargs
    )


def add_session(enrollment, status=LessonSession.Status.SCHEDULED, deducted=False, day=1):
    start = timezone.make_aware(datetime.datetime(2026, 11, day, 17, 0))
    return LessonSession.objects.create(
        enrollment=enrollment,
        start_at=start,
        end_at=start + datetime.timedelta(hours=1),
        status=status,
        absence_deducted=deducted,
    )


def usage(enrollment):
    return Enrollment.objects.with_usage().get(pk=enrollment.pk)


class UsageCalculationTests(TestCase):
    """잔여 = 총 − (완료 + 차감한 결석). 예정·휴강·미차감 결석은 차감하지 않는다."""

    def setUp(self):
        teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=teacher, name="박서연")
        self.enrollment = make_pass(self.student, total=8)

    def test_new_pass_has_full_remaining(self):
        e = usage(self.enrollment)
        self.assertEqual((e.used_count, e.remaining_count), (0, 8))

    def test_only_completed_and_deducted_absence_count(self):
        S = LessonSession.Status
        add_session(self.enrollment, S.COMPLETED, day=1)
        add_session(self.enrollment, S.COMPLETED, day=2)
        add_session(self.enrollment, S.ABSENT, deducted=True, day=3)
        add_session(self.enrollment, S.ABSENT, deducted=False, day=4)
        add_session(self.enrollment, S.CANCELLED, day=5)
        add_session(self.enrollment, S.SCHEDULED, day=6)
        e = usage(self.enrollment)
        self.assertEqual((e.used_count, e.remaining_count), (3, 5))

    def test_remaining_can_go_negative_and_pass_is_not_auto_ended(self):
        e = make_pass(self.student, title="2회권", total=2)
        for day in (1, 2, 3):
            add_session(e, LessonSession.Status.COMPLETED, day=day)
        e = usage(e)
        self.assertEqual(e.remaining_count, -1)
        self.assertEqual(e.status, Enrollment.Status.ACTIVE)  # 자동 종료하지 않음 (#2)
        self.assertIn(e, Enrollment.objects.exhausted())

    def test_counts_are_per_pass(self):
        other = make_pass(self.student, title="바이올린", total=4)
        add_session(self.enrollment, LessonSession.Status.COMPLETED)
        self.assertEqual(usage(other).used_count, 0)
        self.assertEqual(usage(self.enrollment).used_count, 1)


class ModelConstraintTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")

    def test_total_count_must_be_at_least_one_in_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_pass(self.student, total=0)

    def test_session_end_must_be_after_start(self):
        e = make_pass(self.student)
        start = timezone.now()
        with self.assertRaises(IntegrityError), transaction.atomic():
            LessonSession.objects.create(enrollment=e, start_at=start, end_at=start)

    def test_deducted_flag_only_on_absent(self):
        e = make_pass(self.student)
        with self.assertRaises(IntegrityError), transaction.atomic():
            add_session(e, LessonSession.Status.COMPLETED, deducted=True)

    def test_label_must_belong_to_same_teacher(self):
        other = make_teacher("b@example.com", "B강사")
        foreign = Label.objects.create(teacher=other, name="피아노", color=Label.Color.BLUE)
        e = Enrollment(student=self.student, title="x", total_count=1, start_date=datetime.date.today(), label=foreign)
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_makeup_only_for_cancelled_or_undeducted_absence(self):
        e = make_pass(self.student)
        S = LessonSession.Status
        for original, ok in (
            (add_session(e, S.CANCELLED, day=1), True),
            (add_session(e, S.ABSENT, deducted=False, day=2), True),
            (add_session(e, S.ABSENT, deducted=True, day=3), False),  # 차감한 결석은 보강 불가
            (add_session(e, S.COMPLETED, day=4), False),
        ):
            start = timezone.make_aware(datetime.datetime(2026, 12, 1, 17, 0))
            makeup = LessonSession(
                enrollment=e, start_at=start, end_at=start + datetime.timedelta(hours=1), makeup_for=original
            )
            with self.subTest(status=original.status, deducted=original.absence_deducted):
                if ok:
                    makeup.full_clean()
                else:
                    with self.assertRaises(ValidationError):
                        makeup.full_clean()


class EnrollmentCreateViewTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_create", args=[self.student.pk])

    def post(self, **overrides):
        data = {"title": "피아노 개인 레슨", "total_count": "8", "start_date": "2026-11-20", **overrides}
        return self.client.post(self.url, data)

    def test_url_matches_screen_spec(self):
        self.assertEqual(self.url, f"/students/{self.student.pk}/enrollments/new/")

    def test_get_shows_form_with_today_as_start(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "레슨권 추가")
        self.assertEqual(response.context["form"]["start_date"].value(), timezone.localdate())
        self.assertFalse(response.context["form"].has_exhausted)
        self.assertNotContains(response, "소진된 레슨권 종료")

    def test_create_pass_and_show_remaining_as_total(self):
        response = self.post()
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        e = Enrollment.objects.get()
        self.assertEqual((e.student, e.title, e.total_count, e.status), (self.student, "피아노 개인 레슨", 8, "active"))
        detail = self.client.get(response.url)
        self.assertContains(detail, "피아노 개인 레슨")
        self.assertEqual(detail.context["active_enrollments"][0].remaining_count, 8)

    def test_total_count_below_one_is_rejected(self):
        for value in ("0", "-1", ""):
            with self.subTest(value=value):
                response = self.post(total_count=value)
                self.assertEqual(response.status_code, 200)
                self.assertIn("total_count", response.context["form"].errors)
        self.assertFalse(Enrollment.objects.exists())

    def test_blank_title_is_rejected(self):
        response = self.post(title="   ")
        self.assertIn("title", response.context["form"].errors)

    def test_student_cannot_be_changed_by_post(self):
        other_student = Student.objects.create(teacher=self.teacher, name="다른 학생")
        self.post(student=str(other_student.pk))
        self.assertEqual(Enrollment.objects.get().student, self.student)

    def test_multiple_active_passes_allowed(self):
        self.post(title="피아노")
        self.post(title="작곡")
        self.assertEqual(Enrollment.objects.filter(student=self.student).active().count(), 2)

    def test_archived_student_cannot_get_new_pass(self):
        self.student.is_active = False
        self.student.save()
        response = self.post()
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.assertFalse(Enrollment.objects.exists())


class ExhaustedPassProposalTests(TestCase):
    """소진된 진행 중 레슨권이 있으면 새 레슨권 등록 때 종료를 제안한다 (기본 체크, 강사가 해제 가능)."""

    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_create", args=[self.student.pk])
        self.used_up = make_pass(self.student, title="이전 4회권", total=4)
        for day in range(1, 5):
            add_session(self.used_up, LessonSession.Status.COMPLETED, day=day)
        self.still_left = make_pass(self.student, title="남은 레슨권", total=8)

    def data(self, **extra):
        return {"title": "새 8회권", "total_count": "8", "start_date": "2026-11-20", **extra}

    def test_form_offers_only_exhausted_active_pass_checked_by_default(self):
        form = self.client.get(self.url).context["form"]
        field = form.fields["end_exhausted"]
        self.assertEqual(list(field.queryset), [self.used_up])
        self.assertEqual(field.initial, [self.used_up])
        self.assertContains(self.client.get(self.url), "이전 4회권")

    def test_checked_pass_is_ended(self):
        self.client.post(self.url, self.data(end_exhausted=[self.used_up.pk]))
        self.used_up.refresh_from_db()
        self.still_left.refresh_from_db()
        self.assertEqual(self.used_up.status, Enrollment.Status.ENDED)
        self.assertEqual(self.still_left.status, Enrollment.Status.ACTIVE)
        self.assertTrue(Enrollment.objects.filter(title="새 8회권").exists())

    def test_unchecked_pass_stays_active(self):
        self.client.post(self.url, self.data())
        self.used_up.refresh_from_db()
        self.assertEqual(self.used_up.status, Enrollment.Status.ACTIVE)

    def test_cannot_end_non_exhausted_pass_by_tampered_post(self):
        response = self.client.post(self.url, self.data(end_exhausted=[self.still_left.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("end_exhausted", response.context["form"].errors)
        self.still_left.refresh_from_db()
        self.assertEqual(self.still_left.status, Enrollment.Status.ACTIVE)
        self.assertFalse(Enrollment.objects.filter(title="새 8회권").exists())


class EnrollmentEndTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.enrollment = make_pass(self.student)
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_end", args=[self.enrollment.pk])

    def test_end_pass_even_with_remaining(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, Enrollment.Status.ENDED)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_detail_separates_active_and_ended(self):
        make_pass(self.student, title="작곡 레슨")
        self.client.post(self.url)
        response = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertEqual([e.title for e in response.context["active_enrollments"]], ["작곡 레슨"])
        self.assertEqual([e.title for e in response.context["ended_enrollments"]], ["피아노 개인 레슨"])
        self.assertContains(response, "종료된 레슨권 1개")


class TeacherIsolationTests(TestCase):
    """강사 B는 강사 A의 수강생에게 레슨권을 추가하거나 A의 레슨권을 종료·조회할 수 없다 (404)."""

    def setUp(self):
        a = make_teacher("a@example.com", "A강사")
        self.b = make_teacher("b@example.com", "B강사")
        self.a_student = Student.objects.create(teacher=a, name="A의 학생")
        self.a_pass = make_pass(self.a_student)
        self.client.force_login(self.b)

    def test_cannot_add_pass_to_other_teachers_student(self):
        url = reverse("lessons:enrollment_create", args=[self.a_student.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        data = {"title": "x", "total_count": "8", "start_date": "2026-11-20"}
        self.assertEqual(self.client.post(url, data).status_code, 404)
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_cannot_end_other_teachers_pass(self):
        response = self.client.post(reverse("lessons:enrollment_end", args=[self.a_pass.pk]))
        self.assertEqual(response.status_code, 404)
        self.a_pass.refresh_from_db()
        self.assertEqual(self.a_pass.status, Enrollment.Status.ACTIVE)

    def test_cannot_end_other_teachers_exhausted_pass_via_proposal(self):
        """B가 자기 수강생 레슨권을 만들면서 A의 레슨권 번호를 '종료' 값으로 보내도 A의 레슨권은 바뀌지 않는다."""
        b_student = Student.objects.create(teacher=self.b, name="B의 학생")
        for day in range(1, 9):
            add_session(self.a_pass, LessonSession.Status.COMPLETED, day=day)
        url = reverse("lessons:enrollment_create", args=[b_student.pk])
        data = {"title": "x", "total_count": "8", "start_date": "2026-11-20", "end_exhausted": [self.a_pass.pk]}
        self.client.post(url, data)
        self.a_pass.refresh_from_db()
        self.assertEqual(self.a_pass.status, Enrollment.Status.ACTIVE)

    def test_login_required(self):
        self.client.logout()
        url = reverse("lessons:enrollment_create", args=[self.a_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse(settings.LOGIN_URL)))


# ---------------------------------------------------------------------------
# PR #55 Codex 리뷰 반영
# ---------------------------------------------------------------------------


class UsageJoinInflationTests(TestCase):
    """회차 조건으로 다시 조인해도 차감 회차가 부풀지 않는다 (Count distinct)."""

    def test_extra_session_join_does_not_inflate_used_count(self):
        teacher = make_teacher("a@example.com", "A강사")
        e = make_pass(Student.objects.create(teacher=teacher, name="박서연"), total=3)
        add_session(e, LessonSession.Status.COMPLETED, day=1)
        for day in (2, 3, 4):
            add_session(e, LessonSession.Status.SCHEDULED, day=day)
        joined = Enrollment.objects.with_usage().filter(sessions__status="scheduled").distinct().get()
        self.assertEqual((joined.used_count, joined.remaining_count), (1, 2))
        self.assertFalse(Enrollment.objects.filter(sessions__status="scheduled").exhausted().exists())


class EnrollmentCreateRaceTests(TestCase):
    """검증과 저장 사이에 상태가 바뀌거나, 조작·지난 선택이 오면 새 레슨권을 만들지 않는다."""

    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_create", args=[self.student.pk])

    def data(self, **extra):
        return {"title": "새 8회권", "total_count": "8", "start_date": "2026-11-20", **extra}

    def used_up_pass(self):
        e = make_pass(self.student, title="이전 2회권", total=2)
        sessions = [add_session(e, LessonSession.Status.COMPLETED, day=d) for d in (1, 2)]
        return e, sessions

    def after_validation(self, change):
        """폼 검증 직후(저장 트랜잭션 전)에 change()를 실행해 다른 탭·관리자의 변경을 흉내 낸다."""
        from unittest import mock

        from lessons.forms import EnrollmentForm

        original = EnrollmentForm.is_valid

        def is_valid(form):
            ok = original(form)
            change()
            return ok

        return mock.patch.object(EnrollmentForm, "is_valid", is_valid)

    def test_tampered_choice_without_candidates_is_error_and_no_new_pass(self):
        other = make_teacher("b@example.com", "B강사")
        foreign = make_pass(Student.objects.create(teacher=other, name="B의 학생"))
        response = self.client.post(self.url, self.data(end_exhausted=[foreign.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("end_exhausted", response.context["form"].errors)
        self.assertFalse(Enrollment.objects.filter(title="새 8회권").exists())

    def test_already_ended_choice_from_other_tab_is_error(self):
        e, _ = self.used_up_pass()
        Enrollment.objects.filter(pk=e.pk).update(status=Enrollment.Status.ENDED)  # 다른 탭에서 먼저 종료
        response = self.client.post(self.url, self.data(end_exhausted=[e.pk]))
        self.assertIn("end_exhausted", response.context["form"].errors)
        self.assertFalse(Enrollment.objects.filter(title="새 8회권").exists())
        # 후보가 사라졌으므로 '소진 레슨권이 있습니다' 안내는 빼고 오류만 연결한다
        self.assertNotContains(response, "잔여 회차를 다 쓴 진행 중 레슨권이 있습니다")
        self.assertContains(response, 'aria-describedby="id_end_exhausted_error"')

    def test_pass_no_longer_exhausted_after_validation_is_not_ended(self):
        e, sessions = self.used_up_pass()

        def reopen():  # 관리자가 완료 1회를 예정으로 되돌림 → 잔여 1
            LessonSession.objects.filter(pk=sessions[0].pk).update(status=LessonSession.Status.SCHEDULED)

        with self.after_validation(reopen):
            response = self.client.post(self.url, self.data(end_exhausted=[e.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("상태가 바뀌었습니다", str(response.context["form"].errors))
        e.refresh_from_db()
        self.assertEqual(e.status, Enrollment.Status.ACTIVE)
        self.assertFalse(Enrollment.objects.filter(title="새 8회권").exists())

    def test_student_archived_after_validation_gets_no_pass(self):
        def archive():
            Student.objects.filter(pk=self.student.pk).update(is_active=False)

        with self.after_validation(archive):
            response = self.client.post(self.url, self.data())
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.assertFalse(Enrollment.objects.exists())

    def test_ended_count_and_updated_at(self):
        e, _ = self.used_up_pass()
        before = e.updated_at
        response = self.client.post(self.url, self.data(end_exhausted=[e.pk]), follow=True)
        self.assertContains(response, "소진된 레슨권 1개를 종료했습니다.")
        e.refresh_from_db()
        self.assertEqual(e.status, Enrollment.Status.ENDED)
        self.assertGreater(e.updated_at, before)

    def test_error_is_linked_to_fieldset_for_screen_readers(self):
        e, _ = self.used_up_pass()
        Enrollment.objects.filter(pk=e.pk).update(status=Enrollment.Status.ENDED)
        make_pass(self.student, title="다른 소진", total=1)
        add_session(Enrollment.objects.get(title="다른 소진"), LessonSession.Status.COMPLETED, day=5)
        response = self.client.post(self.url, self.data(end_exhausted=[e.pk]))
        self.assertContains(response, 'aria-describedby="id_end_exhausted_helptext id_end_exhausted_error"')
        self.assertContains(response, 'id="id_end_exhausted_error"')


class MakeupOriginalGuardTests(TestCase):
    """보강이 연결된 원래 회차를 보강할 수 없는 상태로 바꾸면 거절한다 (중복 차감 방지)."""

    def test_cannot_deduct_absence_that_has_makeup(self):
        teacher = make_teacher("a@example.com", "A강사")
        e = make_pass(Student.objects.create(teacher=teacher, name="박서연"))
        original = add_session(e, LessonSession.Status.ABSENT, deducted=False, day=1)
        makeup = add_session(e, day=2)
        makeup.makeup_for = original
        makeup.full_clean()
        makeup.save()
        original.absence_deducted = True
        with self.assertRaises(ValidationError):
            original.full_clean()
        original.refresh_from_db()
        original.status = LessonSession.Status.COMPLETED
        with self.assertRaises(ValidationError):
            original.full_clean()

    def test_session_without_makeup_can_change_freely(self):
        teacher = make_teacher("a@example.com", "A강사")
        s = add_session(make_pass(Student.objects.create(teacher=teacher, name="박서연")), day=1)
        s.status = LessonSession.Status.ABSENT
        s.absence_deducted = True
        s.full_clean()


class AdminSessionDeleteTests(TestCase):
    """관리자 화면도 '예정' 회차만 삭제할 수 있고 일괄 삭제는 없다 (ERD §3 기록 보존)."""

    def setUp(self):
        admin_user = User.objects.create_superuser(email="admin@example.com", password=PASSWORD, name="운영자")
        teacher = make_teacher("a@example.com", "A강사")
        e = make_pass(Student.objects.create(teacher=teacher, name="박서연"))
        self.done = add_session(e, LessonSession.Status.COMPLETED, day=1)
        self.planned = add_session(e, day=2)
        self.client.force_login(admin_user)

    def test_completed_session_cannot_be_deleted(self):
        url = reverse("admin:lessons_lessonsession_delete", args=[self.done.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(LessonSession.objects.filter(pk=self.done.pk).exists())

    def test_scheduled_session_can_be_deleted(self):
        url = reverse("admin:lessons_lessonsession_delete", args=[self.planned.pk])
        self.client.post(url, {"post": "yes"})
        self.assertFalse(LessonSession.objects.filter(pk=self.planned.pk).exists())

    def test_bulk_delete_action_is_removed(self):
        response = self.client.get(reverse("admin:lessons_lessonsession_changelist"))
        self.assertNotContains(response, 'value="delete_selected"')
