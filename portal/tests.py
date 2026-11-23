"""#80 발표용 수강생 화면: 데모 수강생 계정 로그인 → 내 수업, 강사와 메시지(AI 분류는 강사만), 접근 격리."""

import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inbox.models import Conversation, Message
from lessons.models import Enrollment, LessonSession
from students.models import Student

User = get_user_model()
PASSWORD = "Lemit-pass-9876"


def make_user(email, name):
    return User.objects.create_user(email=email, password=PASSWORD, name=name)


def make_session(enrollment, days, hour=16, place="A 레슨실", status=LessonSession.Status.SCHEDULED):
    start = timezone.localtime() + datetime.timedelta(days=days)
    start = start.replace(hour=hour, minute=0, second=0, microsecond=0)
    return LessonSession.objects.create(
        enrollment=enrollment, start_at=start, end_at=start + datetime.timedelta(hours=1), place=place, status=status
    )


class PortalTestCase(TestCase):
    def setUp(self):
        self.teacher = make_user("teacher@example.com", "박강사")
        self.other_teacher = make_user("other@example.com", "최강사")
        self.me_account = make_user("haneul@example.com", "김하늘")
        self.peer_account = make_user("seojun@example.com", "이서준")
        self.me = Student.objects.create(teacher=self.teacher, name="김하늘", account=self.me_account)
        self.peer = Student.objects.create(teacher=self.teacher, name="이서준", account=self.peer_account)
        self.my_pass = Enrollment.objects.create(
            student=self.me, title="피아노 개인 레슨", total_count=8, start_date=timezone.localdate()
        )
        self.peer_pass = Enrollment.objects.create(
            student=self.peer, title="통기타 입문", total_count=4, start_date=timezone.localdate()
        )

    def login(self, account):
        self.client.force_login(account)


class LoginAndAccessTests(PortalTestCase):
    def test_student_logs_in_on_common_login_and_lands_on_my_lessons(self):
        r = self.client.post(reverse("login"), {"username": "haneul@example.com", "password": PASSWORD}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("portal:lessons"))
        self.assertContains(r, "내 수업")
        self.assertContains(r, "담당 강사 박강사")
        self.assertNotContains(r, "대시보드")  # 강사 메뉴가 보이지 않음

    def test_teacher_login_still_goes_to_dashboard(self):
        r = self.client.post(reverse("login"), {"username": "teacher@example.com", "password": PASSWORD}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("home"))
        self.assertContains(r, "대시보드")

    def test_student_cannot_open_teacher_screens(self):
        self.login(self.me_account)
        session = make_session(self.my_pass, 1)
        for url in (
            reverse("home"),
            reverse("students:list"),
            reverse("students:detail", args=[self.me.pk]),
            reverse("students:create"),
            reverse("lessons:schedule"),
            reverse("lessons:session_detail", args=[session.pk]),
            reverse("inbox:list"),
            reverse("lessons:label_list"),
        ):
            r = self.client.get(url)
            self.assertRedirects(r, reverse("portal:lessons"), msg_prefix=url)

    def test_student_cannot_change_teacher_data_by_post(self):
        self.login(self.me_account)
        session = make_session(self.my_pass, 1)
        r = self.client.post(reverse("lessons:session_complete", args=[session.pk]))
        self.assertEqual(r.status_code, 403)
        r = self.client.post(reverse("students:create"), {"name": "새 학생"})
        self.assertEqual(r.status_code, 403)
        session.refresh_from_db()
        self.assertEqual(session.status, LessonSession.Status.SCHEDULED)
        self.assertFalse(Student.objects.filter(name="새 학생").exists())

    def test_student_can_change_password_and_logout(self):
        self.login(self.me_account)
        self.assertEqual(self.client.get(reverse("password_change")).status_code, 200)
        r = self.client.post(reverse("logout"))
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_teacher_gets_404_on_student_screens(self):
        self.login(self.teacher)
        conv = Conversation.objects.create(teacher=self.teacher, student=self.me, subject="질문")
        urls = (reverse("portal:lessons"), reverse("portal:messages"), reverse("portal:conversation", args=[conv.pk]))
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_anonymous_goes_to_login(self):
        r = self.client.get(reverse("portal:lessons"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r["Location"])


class MyLessonsTests(PortalTestCase):
    def test_shows_only_my_sessions_with_date_time_place_status(self):
        make_session(self.my_pass, 2, hour=17, place="B 연습실")
        make_session(self.my_pass, -3, hour=10, place="A 레슨실", status=LessonSession.Status.COMPLETED)
        make_session(self.peer_pass, 2, hour=11, place="다른 학생 장소")
        self.login(self.me_account)
        r = self.client.get(reverse("portal:lessons"))
        self.assertEqual([s.enrollment.student_id for s in r.context["upcoming"]], [self.me.pk])
        self.assertEqual([s.enrollment.student_id for s in r.context["past"]], [self.me.pk])
        html = r.content.decode()
        self.assertIn("17:00–18:00 · 피아노 개인 레슨", html)
        self.assertIn("B 연습실", html)
        self.assertIn("완료", html)
        self.assertIn("예정", html)
        self.assertNotIn("다른 학생 장소", html)
        self.assertNotIn("통기타 입문", html)

    def test_remaining_count_shown(self):
        make_session(self.my_pass, -1, status=LessonSession.Status.COMPLETED)
        self.login(self.me_account)
        self.assertContains(self.client.get(reverse("portal:lessons")), "잔여 7회")


class MessageFlowTests(PortalTestCase):
    def test_full_flow_student_message_teacher_sees_ai_reply_student_sees_reply(self):
        # 1) 수강생이 담당 강사에게 메시지 → 기존 모델로 분류
        self.login(self.me_account)
        r = self.client.post(
            reverse("portal:messages"),
            {"subject": "다음 주 수업", "body": "다음 주 수요일 수업을 금요일로 옮길 수 있을까요?"},
            follow=True,
        )
        conv = Conversation.objects.get(student=self.me)
        self.assertEqual(conv.teacher, self.teacher)  # 받는 강사는 연결된 기록의 강사로 고정
        self.assertRedirects(r, reverse("portal:conversation", args=[conv.pk]))
        msg = conv.messages.get()
        self.assertEqual(msg.sender, Message.Sender.STUDENT)
        self.assertTrue(msg.predicted_label)  # 실제 모델 분류
        self.assertIsNotNone(msg.confidence)
        self.assertNotContains(r, "AI 예측")  # 수강생에게는 분류를 보여 주지 않음

        # 2) 강사가 메시지함에서 분류 배지·확신도 확인 후 답장
        self.login(self.teacher)
        detail = self.client.get(reverse("inbox:detail", args=[conv.pk]))
        self.assertContains(detail, msg.get_predicted_label_display())
        self.assertContains(detail, "AI 예측·참고용")
        self.client.post(reverse("inbox:reply", args=[conv.pk]), {"body": "금요일 같은 시간 가능합니다."})

        # 3) 수강생이 자기 대화에서 답장 확인, 이어 보내면 다시 분류
        self.login(self.me_account)
        self.assertContains(self.client.get(reverse("portal:messages")), "새 답장")
        thread = self.client.get(reverse("portal:conversation", args=[conv.pk]))
        self.assertContains(thread, "금요일 같은 시간 가능합니다.")
        self.assertContains(thread, "박강사 (담당 강사)")
        self.client.post(reverse("portal:conversation", args=[conv.pk]), {"body": "감사합니다!"})
        follow = conv.messages.order_by("-id").first()
        self.assertEqual((follow.sender, follow.body), (Message.Sender.STUDENT, "감사합니다!"))
        self.assertTrue(follow.predicted_label)

    def test_cannot_see_or_post_to_others_conversation(self):
        peer_conv = Conversation.objects.create(teacher=self.teacher, student=self.peer, subject="이서준 질문")
        Message.objects.create(conversation=peer_conv, sender=Message.Sender.STUDENT, body="비밀 내용")
        other_student = Student.objects.create(teacher=self.other_teacher, name="김하늘")
        other_conv = Conversation.objects.create(teacher=self.other_teacher, student=other_student, subject="다른 강사")
        self.login(self.me_account)
        lst = self.client.get(reverse("portal:messages"))
        self.assertNotContains(lst, "이서준 질문")
        self.assertNotContains(lst, "다른 강사")
        for conv in (peer_conv, other_conv):
            url = reverse("portal:conversation", args=[conv.pk])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, {"body": "끼어들기"}).status_code, 404)
        self.assertFalse(Message.objects.filter(body="끼어들기").exists())

    def test_empty_body_is_error(self):
        self.login(self.me_account)
        r = self.client.post(reverse("portal:messages"), {"subject": "제목", "body": "   "})
        self.assertEqual(r.status_code, 200)
        self.assertIn("body", r.context["form"].errors)
        self.assertFalse(Message.objects.exists())

    def test_archived_student_can_view_but_not_send(self):
        Student.objects.filter(pk=self.me.pk).update(is_active=False)
        self.login(self.me_account)
        self.assertEqual(self.client.get(reverse("portal:lessons")).status_code, 200)
        r = self.client.post(reverse("portal:messages"), {"subject": "제목", "body": "내용"}, follow=True)
        self.assertContains(r, "수강이 종료된 상태라 메시지를 보낼 수 없습니다")
        self.assertFalse(Message.objects.exists())


class AccountLinkTests(PortalTestCase):
    def test_teacher_or_admin_account_cannot_be_linked(self):
        """수강생을 가진 강사 계정·관리자·담당 강사 본인은 연결 불가. 역할 필드는 없어서(역할별 가입은 #83)
        수강생이 아직 없는 강사 계정은 이 검사로 구분하지 못한다(연결은 관리자·seed_demo만 한다)."""
        admin = User.objects.create_superuser(email="admin@example.com", password=PASSWORD, name="관리자")
        Student.objects.create(teacher=self.other_teacher, name="최강사의 학생")
        for account in (self.teacher, self.other_teacher, admin):
            student = Student(teacher=self.teacher, name="연결 시험", account=account)
            with self.assertRaises(ValidationError, msg=account.email):
                student.full_clean()

    def test_one_account_links_to_one_student(self):
        student = Student(teacher=self.teacher, name="두 번째", account=self.me_account)
        with self.assertRaises(ValidationError):
            student.full_clean()

    def test_deleting_account_keeps_student_record(self):
        self.me_account.delete()
        self.me.refresh_from_db()
        self.assertIsNone(self.me.account)


class MyCalendarTests(PortalTestCase):
    """#92: 내 수업 월간 달력. 로그인 계정에 연결된 기록의 회차만, 요청 값을 바꿔도 다른 수강생 회차가 섞이지 않는다."""

    def at(self, day, hour):
        tz = timezone.get_current_timezone()
        return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour)), tz)

    def add(self, enrollment, day, hour, place="A 레슨실", status=LessonSession.Status.SCHEDULED):
        start = self.at(day, hour)
        return LessonSession.objects.create(
            enrollment=enrollment,
            start_at=start,
            end_at=start + datetime.timedelta(hours=1),
            place=place,
            status=status,
        )

    def cell(self, response, day):
        return next(c for week in response.context["grid"] for c in week if c["date"] == day)

    def test_month_grid_counts_only_my_sessions_and_selected_day_lists_them_in_time_order(self):
        day = datetime.date(2026, 12, 9)
        self.add(self.my_pass, day, 18, place="B 연습실")
        self.add(self.my_pass, day, 10, status=LessonSession.Status.COMPLETED)
        self.add(self.peer_pass, day, 12, place="다른 학생 장소")  # 같은 날 다른 수강생
        self.login(self.me_account)
        r = self.client.get(reverse("portal:lessons"), {"date": day.isoformat()})
        self.assertEqual(self.cell(r, day)["count"], 2)
        self.assertTrue(self.cell(r, day)["is_selected"])
        self.assertEqual([timezone.localtime(s.start_at).hour for s in r.context["day_sessions"]], [10, 18])
        html = r.content.decode()
        self.assertIn("2026년 12월", html)
        self.assertIn("12월 9일 수요일", html)
        self.assertIn("B 연습실", html)
        self.assertNotIn("다른 학생 장소", html)
        self.assertIn('aria-label="12월 9일, 회차 2개"', html)

    def test_other_request_values_cannot_mix_in_other_students_sessions(self):
        day = datetime.date(2026, 12, 9)
        self.add(self.peer_pass, day, 12, place="다른 학생 장소")
        other_student = Student.objects.create(teacher=self.other_teacher, name="다른 강사 학생")
        other_pass = Enrollment.objects.create(
            student=other_student, title="다른 강사 레슨", total_count=4, start_date=day
        )
        self.add(other_pass, day, 13, place="다른 강사 장소")
        self.login(self.me_account)
        for params in (
            {"date": day.isoformat(), "student": self.peer.pk},
            {"date": day.isoformat(), "student": other_student.pk, "label": "1", "teacher": self.other_teacher.pk},
        ):
            r = self.client.get(reverse("portal:lessons"), params)
            self.assertEqual(self.cell(r, day)["count"], 0, params)
            self.assertEqual(r.context["day_sessions"], [], params)
            self.assertNotContains(r, "다른 학생 장소")
            self.assertNotContains(r, "다른 강사 장소")
            self.assertContains(r, "이날은 수업이 없어요")

    def test_month_navigation_links_and_bad_date_falls_back_to_today(self):
        self.login(self.me_account)
        r = self.client.get(reverse("portal:lessons"), {"date": "2026-01-31"})
        self.assertEqual(r.context["prev_url"], "?date=2025-12-31#calendar")
        self.assertEqual(r.context["next_url"], "?date=2026-02-28#calendar")  # 2월에 없는 날은 말일
        self.assertEqual(r.context["today_url"], f"?date={timezone.localdate().isoformat()}#calendar")
        for bad in ("abc", "2026-13-01", "0001-01-01"):
            r = self.client.get(reverse("portal:lessons"), {"date": bad})
            self.assertEqual(r.context["selected"], timezone.localdate(), bad)

    def test_calendar_is_read_only_and_lists_still_shown(self):
        make_session(self.my_pass, 2)
        self.login(self.me_account)
        html = self.client.get(reverse("portal:lessons")).content.decode()
        self.assertIn('id="calendar"', html)
        self.assertIn("다가오는 수업", html)  # 기존 목록 유지
        self.assertNotIn("일정 추가", html)
        self.assertNotIn(reverse("lessons:session_create"), html)
        self.assertNotIn("<form", html.split('id="calendar"')[1].split("</section>")[0])
