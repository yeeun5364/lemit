"""#45 UI 리디자인 테스트.

대시보드 실제 수치·강사별 격리, 공통 레이아웃(메뉴·접근성), 메시지함 2단, 보관 확인창 이스케이프.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inbox.models import Conversation, Message
from students.models import Student

User = get_user_model()


def make_teacher(email, name):
    return User.objects.create_user(email=email, password="lesson-time-2026!", name=name)


def add_student_message(teacher, student, subject, body, label="", confidence=None, read=False):
    """분류 결과를 직접 넣어 대화·수강생 메시지를 만든다 (모델 파일과 무관하게 화면만 검증)."""
    conv, _ = Conversation.objects.get_or_create(teacher=teacher, student=student, subject=subject)
    return Message.objects.create(
        conversation=conv,
        sender=Message.Sender.STUDENT,
        body=body,
        predicted_label=label,
        confidence=confidence,
        model_version="test" if label else "",
        classified_at=timezone.now() if label else None,
        read_at=timezone.now() if read else None,
    )


class UiTestBase(TestCase):
    def setUp(self):
        self.a = make_teacher("a@example.com", "에이강사")
        self.b = make_teacher("b@example.com", "비강사")
        self.sky = Student.objects.create(teacher=self.a, name="김하늘")
        self.doyun = Student.objects.create(teacher=self.a, name="이도윤")
        Student.objects.create(teacher=self.a, name="박보관", is_active=False)
        self.other = Student.objects.create(teacher=self.b, name="비학생")
        Student.objects.create(teacher=self.b, name="비학생둘")
        add_student_message(self.a, self.sky, "수요일 수업", "금요일에 보강 가능할까요?", "MAKEUP", 0.97)
        add_student_message(self.a, self.sky, "수요일 수업", "시간은 아무 때나 괜찮아요", "MAKEUP", 0.91)
        add_student_message(self.a, self.doyun, "목요일 레슨", "금요일로 옮길 수 있을까요?", "RESCHEDULE", 0.42)
        add_student_message(self.a, self.doyun, "교재", "다음 책은 뭐예요?", "GENERAL", 0.8, read=True)
        add_student_message(self.b, self.other, "비의대화", "비의 메시지", "CANCELLATION", 0.9)
        self.client.force_login(self.a)


class DashboardTests(UiTestBase):
    def test_summary_uses_real_counts_for_logged_in_teacher_only(self):
        res = self.client.get(reverse("home"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["active_students"], 2)  # 보관 제외, 다른 강사 제외
        self.assertEqual(res.context["unread_messages"], 3)  # 안 읽은 내 수강생 메시지 (읽은 것·다른 강사 제외)
        self.assertEqual(res.context["attention_total"], 2)  # 안 읽은 메시지가 있는 내 대화
        self.assertNotContains(res, "비학생")
        self.assertNotContains(res, "비의대화")

    def test_attention_list_shows_unread_conversations_with_ai_label_as_reference(self):
        res = self.client.get(reverse("home"))
        self.assertContains(res, "김하늘 · 수요일 수업")
        self.assertContains(res, "이도윤 · 목요일 레슨")
        self.assertNotContains(res, "이도윤 · 교재")  # 모두 읽은 대화는 '확인할 메시지'가 아니다
        self.assertContains(res, "보강 요청")
        self.assertContains(res, "확신 낮음")  # 0.42 < 0.5
        self.assertContains(res, "참고용")
        self.assertContains(res, "안 읽음 2")
        self.assertEqual(res.context["label_summary"], [("MAKEUP", "보강 요청", 1), ("RESCHEDULE", "일정 변경", 1)])

    def test_schedule_is_shown_as_pending_not_fake_data(self):
        """일정 기능(UC-04·05)이 없으므로 오늘 수업 수·일정 목록을 지어내지 않는다."""
        res = self.client.get(reverse("home"))
        self.assertContains(res, "수업 일정 기능을 준비하고 있어요")
        self.assertContains(res, "준비 중")
        self.assertNotIn("today_lessons", res.context)

    def test_empty_state_when_everything_is_read(self):
        Message.objects.filter(conversation__teacher=self.a).update(read_at=timezone.now())
        res = self.client.get(reverse("home"))
        self.assertEqual(res.context["unread_messages"], 0)
        self.assertContains(res, "확인할 메시지가 없어요")


class LayoutTests(UiTestBase):
    def test_sidebar_marks_current_section_and_schedule_is_not_a_link(self):
        cases = [
            (reverse("home"), reverse("home")),
            (reverse("students:list"), reverse("students:list")),
            (reverse("students:detail", args=[self.sky.pk]), reverse("students:list")),
            (reverse("inbox:list"), reverse("inbox:list")),
        ]
        for url, active_href in cases:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn(f'href="{active_href}" class="nav-item is-active" aria-current="page"', html)
                self.assertEqual(html.count('class="nav-item is-active"'), 1)
                self.assertIn('<span class="nav-item is-disabled" aria-disabled="true">', html)

    def test_accessibility_hooks_and_logout(self):
        res = self.client.get(reverse("home"))
        self.assertContains(res, 'href="#main"')  # 본문 바로가기
        self.assertContains(res, 'id="main"')
        self.assertContains(res, 'aria-controls="sidebar" aria-expanded="false"')  # 접이식 메뉴 버튼
        self.assertContains(res, "js/app.js")
        self.assertContains(res, f'action="{reverse("logout")}"')
        self.assertContains(res, 'aria-label="안 읽은 메시지 3개"')

    def test_login_page_has_no_app_navigation(self):
        self.client.logout()
        res = self.client.get(reverse("login"))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'aria-label="주 메뉴"')
        self.assertContains(res, "로그인")

    def test_required_fields_are_marked(self):
        res = self.client.get(reverse("students:create"))
        self.assertContains(res, '<span class="sr-only">(필수)</span>', count=1)  # 이름만 필수
        self.assertContains(res, "미성년자 필수")


class InboxTwoPaneTests(UiTestBase):
    def conv(self, subject):
        return Conversation.objects.get(teacher=self.a, subject=subject)

    def test_detail_shows_own_list_with_selected_conversation(self):
        res = self.client.get(reverse("inbox:detail", args=[self.conv("수요일 수업").pk]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.context["conversations"]), 3)
        self.assertContains(res, 'aria-current="true"', count=1)
        self.assertContains(res, "이도윤 · 목요일 레슨")  # 왼쪽 목록
        self.assertNotContains(res, "비의대화")
        self.assertNotContains(res, "비학생")

    def test_opening_marks_read_before_list_is_built(self):
        conv = self.conv("수요일 수업")
        res = self.client.get(reverse("inbox:detail", args=[conv.pk]))
        selected = next(c for c in res.context["conversations"] if c.pk == conv.pk)
        self.assertEqual(selected.unread, 0)

    def test_label_filter_is_kept_in_list_links(self):
        conv = self.conv("수요일 수업")
        res = self.client.get(reverse("inbox:detail", args=[conv.pk]), {"label": "MAKEUP"})
        self.assertEqual([c.pk for c in res.context["conversations"]], [conv.pk])
        self.assertContains(res, f'href="{reverse("inbox:detail", args=[conv.pk])}?label=MAKEUP"')

    def test_invalid_label_is_ignored(self):
        res = self.client.get(reverse("inbox:list"), {"label": "<script>"})
        self.assertEqual(res.context["current_label"], "")
        self.assertEqual(len(res.context["conversations"]), 3)

    def test_invalid_reply_keeps_two_pane_layout(self):
        conv = self.conv("수요일 수업")
        res = self.client.post(reverse("inbox:reply", args=[conv.pk]), {"body": "   "})
        self.assertEqual(res.status_code, 400)
        self.assertContains(res, 'class="errorlist"', status_code=400)  # 오류가 답장 칸에 표시됨
        self.assertEqual(len(res.context["conversations"]), 3)

    def test_reply_keeps_label_filter(self):
        """Codex 리뷰: 분류 탭에서 답장해도 필터가 풀리지 않는다 (성공·검증 오류 모두)."""
        conv = self.conv("수요일 수업")
        detail = self.client.get(reverse("inbox:detail", args=[conv.pk]), {"label": "MAKEUP"})
        self.assertContains(detail, f'action="{reverse("inbox:reply", args=[conv.pk])}?label=MAKEUP"')
        reply_url = reverse("inbox:reply", args=[conv.pk]) + "?label=MAKEUP"
        res = self.client.post(reply_url, {"body": "토요일에 봬요"})
        self.assertRedirects(res, reverse("inbox:detail", args=[conv.pk]) + "?label=MAKEUP")
        bad = self.client.post(reply_url, {"body": ""})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.context["current_label"], "MAKEUP")
        self.assertEqual([c.pk for c in bad.context["conversations"]], [conv.pk])
        hacked = self.client.post(reverse("inbox:reply", args=[conv.pk]) + "?label=evil", {"body": "확인"})
        self.assertRedirects(hacked, reverse("inbox:detail", args=[conv.pk]))  # 허용되지 않은 값은 버림

    def test_other_teachers_conversation_is_404(self):
        other_conv = Conversation.objects.get(teacher=self.b)
        self.assertEqual(self.client.get(reverse("inbox:detail", args=[other_conv.pk])).status_code, 404)


class StudentDetailTests(UiTestBase):
    def test_archive_confirm_message_is_html_escaped(self):
        """보관 확인 문구는 data 속성에 들어가므로 자동 이스케이프되어야 한다 (이전: escapejs 인라인 스크립트)."""
        student = Student.objects.create(teacher=self.a, name='<b>"x"</b>')
        res = self.client.get(reverse("students:detail", args=[student.pk]))
        self.assertContains(res, 'data-confirm="&lt;b&gt;&quot;x&quot;&lt;/b&gt; 수강생을 보관할까요?')
        self.assertNotContains(res, '<b>"x"</b>')
