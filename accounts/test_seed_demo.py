"""seed_demo 관리 명령 테스트 (#24, UC-17 #33 '데모 데이터에 분류별 가상 수강생 메시지')."""

import datetime
import io
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from accounts.management.commands.seed_demo import DEMO_EMAIL
from inbox import classifier
from inbox.models import Conversation, Message
from lessons.models import Enrollment, LessonSession
from students.models import Student

User = get_user_model()
FAKE_PHONE = re.compile(r"010-0000-\d{4}")


def seed(**kwargs):
    out = io.StringIO()
    call_command("seed_demo", stdout=out, **kwargs)
    return out.getvalue()


class SeedDemoContentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.output = seed(password="demo-pass-1234")
        cls.teacher = User.objects.get(email=DEMO_EMAIL)

    def test_account_and_students(self):
        self.assertTrue(self.teacher.check_password("demo-pass-1234"))
        self.assertFalse(self.teacher.is_staff)
        students = Student.objects.filter(teacher=self.teacher)
        self.assertTrue(3 <= students.count() <= 4)
        minors = students.filter(is_minor=True)
        self.assertEqual(minors.count(), 1)
        self.assertTrue(minors.get().guardian_name and minors.get().guardian_phone)

    def test_only_fake_phone_numbers(self):
        for s in Student.objects.filter(teacher=self.teacher):
            for phone in (s.phone, s.guardian_phone):
                if phone:
                    self.assertRegex(phone, FAKE_PHONE)
                    s.full_clean()  # 연락처 검증 통과

    def test_sessions_mix_last_week_this_week_and_statuses(self):
        sessions = LessonSession.objects.filter(enrollment__student__teacher=self.teacher)
        statuses = set(sessions.values_list("status", flat=True))
        self.assertTrue({"completed", "scheduled", "cancelled"} <= statuses, statuses)
        today = timezone.localdate()
        week_start = today - datetime.timedelta(days=today.weekday())
        days = {timezone.localtime(s.start_at).date() for s in sessions}
        self.assertTrue(any(week_start - datetime.timedelta(days=7) <= d < week_start for d in days))  # 지난주
        self.assertTrue(any(week_start <= d < week_start + datetime.timedelta(days=7) for d in days))  # 이번 주
        self.assertTrue(sessions.filter(start_at__date=today, status="scheduled").exists())  # 대시보드 '오늘 수업'
        # 휴강 중 보강이 아직 없는 회차(보강 필요)와 보강이 연결된 회차가 모두 있다
        cancelled = sessions.filter(status="cancelled")
        self.assertTrue(cancelled.filter(makeup__isnull=True).exists())
        self.assertTrue(cancelled.filter(makeup__isnull=False).exists())
        for s in sessions.filter(status="completed"):
            self.assertIsNotNone(s.completed_at)

    def test_remaining_counts_show_normal_low_and_exhausted(self):
        states = {
            e.student.name: e.remaining_state
            for e in Enrollment.objects.filter(student__teacher=self.teacher).with_usage().select_related("student")
        }
        self.assertIn("low", states.values())
        self.assertIn("exhausted", states.values())
        self.assertIn("", states.values())

    def test_messages_cover_every_label_with_real_model(self):
        msgs = Message.objects.filter(conversation__teacher=self.teacher)
        student_msgs = msgs.filter(sender=Message.Sender.STUDENT)
        self.assertEqual(
            set(student_msgs.values_list("predicted_label", flat=True)),
            {"CANCELLATION", "MAKEUP", "RESCHEDULE", "GENERAL"},
        )
        version = classifier.get_model().version
        self.assertFalse(student_msgs.exclude(model_version=version).exists())
        self.assertTrue(any(m.is_low_confidence for m in student_msgs))  # '확신 낮음' 예시
        self.assertTrue(student_msgs.filter(read_at__isnull=True).exists())  # 안 읽음
        self.assertTrue(msgs.filter(sender=Message.Sender.TEACHER, predicted_label="").exists())  # 답장
        for conv in Conversation.objects.filter(teacher=self.teacher):
            self.assertEqual(conv.last_message_at, conv.messages.order_by("-sent_at").first().sent_at)

    def test_demo_pages_render(self):
        self.client.force_login(self.teacher)
        for url in ("/", "/students/", "/schedule/", "/inbox/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class SeedDemoRerunTests(TestCase):
    def setUp(self):
        self.other = User.objects.create_user(email="other@example.com", password="x", name="다른 강사")
        self.other_student = Student.objects.create(teacher=self.other, name="다른 학생")
        e = Enrollment.objects.create(
            student=self.other_student, title="다른 레슨", total_count=4, start_date=datetime.date(2026, 11, 1)
        )
        start = timezone.now()
        LessonSession.objects.create(enrollment=e, start_at=start, end_at=start + datetime.timedelta(hours=1))
        Conversation.objects.create(teacher=self.other, student=self.other_student, subject="안녕")

    def counts(self, teacher):
        return (
            Student.objects.filter(teacher=teacher).count(),
            Enrollment.objects.filter(student__teacher=teacher).count(),
            LessonSession.objects.filter(enrollment__student__teacher=teacher).count(),
            Conversation.objects.filter(teacher=teacher).count(),
            Message.objects.filter(conversation__teacher=teacher).count(),
        )

    def test_rerun_replaces_only_demo_data_and_keeps_password(self):
        seed(password="first-pass-1234")
        teacher = User.objects.get(email=DEMO_EMAIL)
        first = self.counts(teacher)
        other_before = self.counts(self.other)
        first_ids = set(Student.objects.filter(teacher=teacher).values_list("pk", flat=True))

        out = seed()  # 비밀번호 없이 다시 실행
        self.assertIn("기존 값 유지", out)
        teacher.refresh_from_db()
        self.assertEqual(User.objects.filter(email=DEMO_EMAIL).count(), 1)
        self.assertTrue(teacher.check_password("first-pass-1234"))
        self.assertEqual(self.counts(teacher), first)  # 중복 없이 같은 구성
        self.assertFalse(Student.objects.filter(pk__in=first_ids).exists())  # 새로 만들어짐
        self.assertEqual(self.counts(self.other), other_before)  # 다른 강사 데이터 그대로

    def test_new_account_without_password_gets_random_password_printed_once(self):
        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("LEMIT_DEMO_PASSWORD", None)
            out = seed()
        m = re.search(r"비밀번호\(새로 생성, 지금만 표시\): (\S+)", out)
        self.assertIsNotNone(m, out)
        self.assertTrue(User.objects.get(email=DEMO_EMAIL).check_password(m.group(1)))

    def test_password_from_environment(self):
        with mock.patch.dict("os.environ", {"LEMIT_DEMO_PASSWORD": "env-pass-1234"}):
            seed()
        self.assertTrue(User.objects.get(email=DEMO_EMAIL).check_password("env-pass-1234"))

    def test_refuses_to_overwrite_admin_account(self):
        User.objects.create_superuser(email=DEMO_EMAIL, password="admin", name="관리자")
        with self.assertRaises(CommandError):
            seed()
        self.assertEqual(Student.objects.filter(teacher__email=DEMO_EMAIL).count(), 0)

    def test_works_when_model_unavailable(self):
        with mock.patch.object(classifier, "classify", side_effect=classifier.MessageClassifierUnavailable("없음")):
            seed(password="p-1234-5678")
        msgs = Message.objects.filter(conversation__teacher__email=DEMO_EMAIL, sender=Message.Sender.STUDENT)
        self.assertTrue(msgs.exists())
        self.assertFalse(msgs.exclude(predicted_label="").exists())  # '분류 불가'로 저장
