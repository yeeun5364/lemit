"""시연용 데모 데이터 (#24, UC-17 #33):  python manage.py seed_demo [--password 비밀번호]

- 데모 강사 계정(DEMO_EMAIL) 하나에 수강생 4명(미성년자 1명), 레슨권, 지난주·이번 주·다음 주 회차,
  분류별 수강생 메시지를 만든다.
- 다시 실행하면 **데모 계정의 데이터만** 지우고 새로 만든다. 다른 강사의 데이터는 건드리지 않는다.
  계정 자체는 지우지 않는다(비밀번호를 주지 않으면 기존 비밀번호 유지).
- **이 명령이 만든 계정인지 표시로 확인한다 (PR #65 리뷰 P1).** 새로 만들 때 Django 그룹 DEMO_GROUP에 넣고,
  다시 실행할 때 그 표시가 있는 계정만 다룬다. 같은 이메일의 계정이 표시 없이 이미 있으면(직접 가입한 일반 계정 등)
  데이터·비밀번호를 바꾸기 전에 멈춘다. 관리자 계정도 거부한다. 회원가입에서도 이 이메일을 받지 않는다.
- 날짜는 실행한 날(서버 시간대) 기준으로 계산한다. 시연 당일에 다시 실행하면 '오늘 수업'과 잔여 회차가 맞춰진다.
- 이름은 가짜, 번호는 010-0000-XXXX 형식만 쓴다 (실제 개인정보 없음).
- 비밀번호: --password > 환경변수 LEMIT_DEMO_PASSWORD > (새 계정이면) 무작위 생성 후 한 번 출력.
  공개 저장소이므로 비밀번호를 코드에 두지 않는다.
- 수강생 메시지는 화면과 같은 경로(inbox.services.add_student_message)로 저장해 **실제 모델(v2)의 분류**를 쓴다.
  모델을 쓸 수 없으면 메시지는 '분류 불가'로 저장된다(UC-17 예외 흐름과 같음).
"""

import datetime
import os
import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import DEMO_EMAIL
from inbox import services
from inbox.models import Conversation, Message
from lessons.models import Enrollment, Label, LessonSession
from students.models import Student

DEMO_NAME = "데모 강사"
DEMO_GROUP = "lemit-demo"  # seed_demo가 만든 계정 표시. 일반 사용자는 화면에서 바꿀 수 없다

# (이름, 연락처, 미성년자, 보호자 이름, 보호자 연락처, 메모)
STUDENTS = [
    ("김하늘", "010-0000-1001", False, "", "", "직장인. 평일 저녁 선호"),
    ("이서준", "010-0000-1002", True, "이은정", "010-0000-2002", "중학생. 연락은 보호자에게 먼저"),
    ("박지민", "010-0000-1003", False, "", "", "취미 보컬. 발성 기초부터"),
    ("최유나", "010-0000-1004", False, "", "", "드럼 4회권 마침. 연장 상담 필요"),
]

# 레슨권: (수강생, 레슨명, 총 회차, 시작일 = 오늘 + 일수)
# 라벨 (UC-16): (이름, 색). 한 레슨권은 라벨 없이 두어 회색 표시도 보이게 한다
LABELS = [
    ("피아노", "blue"),
    ("기타", "green"),
    ("보컬", "orange"),
]

# 레슨권: (수강생, 레슨명, 총 회차, 오늘 + 시작 일수, 라벨 이름 또는 None)
ENROLLMENTS = [
    ("김하늘", "피아노 개인 레슨", 8, -28, "피아노"),
    ("이서준", "통기타 입문", 4, -21, "기타"),
    ("박지민", "보컬 트레이닝", 10, -10, "보컬"),
    ("최유나", "드럼 기초", 4, -30, None),
]

# 회차: (수강생, 오늘 + 일수, 시작 시, 시작 분, 상태, 장소, 사유)
# 잔여 = 총 회차 − 완료 수. 김하늘 3(보통), 이서준 2(강조), 박지민 9, 최유나 0(소진)
S, C, X = LessonSession.Status.SCHEDULED, LessonSession.Status.COMPLETED, LessonSession.Status.CANCELLED
SESSIONS = [
    ("김하늘", -24, 19, 0, C, "강사 작업실", ""),
    ("김하늘", -21, 19, 0, C, "강사 작업실", ""),
    ("김하늘", -17, 19, 0, C, "강사 작업실", ""),
    ("김하늘", -14, 19, 0, C, "강사 작업실", ""),
    ("김하늘", -7, 19, 0, C, "강사 작업실", ""),
    ("김하늘", 0, 19, 0, S, "강사 작업실", ""),  # 오늘 수업
    ("김하늘", 7, 19, 0, S, "강사 작업실", ""),
    ("이서준", -14, 17, 0, C, "수강생 집", ""),
    ("이서준", -7, 17, 0, C, "수강생 집", ""),
    ("이서준", -2, 17, 0, X, "수강생 집", "학교 행사로 휴강. 보강 날짜 조율 중"),  # 보강 필요(보강 미연결)
    ("이서준", 5, 17, 0, S, "수강생 집", ""),
    ("박지민", -8, 14, 0, C, "연습실 A", ""),
    ("박지민", -5, 14, 0, X, "연습실 A", "감기로 휴강"),  # 아래 보강 회차와 연결
    ("박지민", 0, 15, 0, S, "연습실 A", ""),  # 오늘 수업
    ("박지민", 6, 14, 0, S, "연습실 A", ""),
    ("최유나", -27, 20, 0, C, "합주실", ""),
    ("최유나", -20, 20, 0, C, "합주실", ""),
    ("최유나", -13, 20, 0, C, "합주실", ""),
    ("최유나", -6, 20, 0, C, "합주실", ""),
]
# 보강: (수강생, 원래 회차 일수, 보강 일수, 시, 분)
MAKEUPS = [("박지민", -5, 2, 11, 0)]

# 수강생 메시지: (수강생, 제목, 본문, 보낸 시각 = 지금 − 시간, 읽음, 강사 답장 또는 "")
# 본문은 v2 모델이 의도한 분류로 예측하는 것을 확인한 문장이다. 마지막 문장은 확신도가 낮게 나와(약 39%)
# 화면의 '확신 낮음' 표시를 보여 주는 예로 쓴다.
MESSAGES = [
    (
        "박지민",
        "이번 주 수업",
        "선생님, 이번 주 목요일 수업은 감기 때문에 쉬어야 할 것 같아요. 죄송합니다.",
        6 * 24 + 3,
        True,
        "괜찮아요, 푹 쉬세요. 보강은 다음 주에 잡아 볼게요.",
    ),
    (
        "이서준",
        "학교 행사",
        "내일 학교 행사가 있어서 수업 못 갈 것 같습니다. 이번 주는 쉴게요.",
        3 * 24 + 2,
        True,
        "알겠어요. 보강 가능한 날을 알려 주세요.",
    ),
    ("이서준", "보강 문의", "지난주 빠진 수업 보강 가능할까요? 토요일 오전이면 좋겠어요.", 20, False, ""),
    ("김하늘", "수업 요일 변경", "이번 주 수업을 수요일에서 금요일로 옮겨도 될까요?", 3, False, ""),
    ("최유나", "연습 후기", "오늘 배운 곡 집에서 연습해 봤는데 훨씬 좋아졌어요. 감사합니다!", 6 * 24 - 2, True, ""),
    ("박지민", "준비물", "다음 레슨 때 악보 챙겨 가면 될까요?", 1, False, ""),
]


class Command(BaseCommand):
    help = "시연용 데모 강사 계정과 가짜 데이터를 만든다. 다시 실행하면 데모 계정의 데이터만 새로 만든다 (#24)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            help="데모 계정 비밀번호 (없으면 LEMIT_DEMO_PASSWORD, 그것도 없으면 기존 유지/새로 생성)",
        )

    def handle(self, *args, **options):
        password = options.get("password") or os.environ.get("LEMIT_DEMO_PASSWORD") or ""
        User = get_user_model()
        user = User.objects.filter(email__iexact=DEMO_EMAIL).first()
        if user is not None and (user.is_staff or user.is_superuser):
            raise CommandError(f"{DEMO_EMAIL} 은 관리자 계정이라 데모 데이터로 덮어쓰지 않습니다.")
        if user is not None and not user.groups.filter(name=DEMO_GROUP).exists():
            raise CommandError(
                f"{DEMO_EMAIL} 계정이 이미 있지만 seed_demo가 만든 데모 계정이 아닙니다(그룹 {DEMO_GROUP} 없음). "
                "기존 데이터와 비밀번호를 바꾸지 않고 멈춥니다. 그 계정을 확인한 뒤 관리자가 정리하세요."
            )

        generated = ""
        with transaction.atomic():
            if user is None:
                if not password:
                    password = generated = secrets.token_urlsafe(12)
                user = User.objects.create_user(email=DEMO_EMAIL, password=password, name=DEMO_NAME)
                user.groups.add(Group.objects.get_or_create(name=DEMO_GROUP)[0])
            else:
                self._clear(user)
                user.name = DEMO_NAME
                user.is_active = True
                if password:
                    user.set_password(password)
                user.save()
            counts = self._create(user)

        self.stdout.write(self.style.SUCCESS("데모 데이터를 만들었습니다."))
        self.stdout.write(f"  계정: {DEMO_EMAIL}")
        if generated:
            self.stdout.write(f"  비밀번호(새로 생성, 지금만 표시): {generated}")
        elif password:
            self.stdout.write("  비밀번호: 지정한 값으로 설정")
        else:
            self.stdout.write("  비밀번호: 기존 값 유지")
        self.stdout.write("  " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    def _clear(self, teacher):
        """데모 강사의 데이터만 지운다. PROTECT 관계 때문에 메시지·대화 → 회차 → 레슨권 → 수강생 → 라벨 순서."""
        Conversation.objects.filter(teacher=teacher).delete()  # 메시지는 CASCADE
        LessonSession.objects.filter(enrollment__student__teacher=teacher).delete()
        Enrollment.objects.filter(student__teacher=teacher).delete()
        Student.objects.filter(teacher=teacher).delete()
        Label.objects.filter(teacher=teacher).delete()

    def _create(self, teacher):
        today = timezone.localdate()
        now = timezone.now()

        def at(days, hour, minute):
            day = today + datetime.timedelta(days=days)
            return timezone.make_aware(datetime.datetime(day.year, day.month, day.day, hour, minute))

        students = {}
        for name, phone, minor, g_name, g_phone, memo in STUDENTS:
            students[name] = Student.objects.create(
                teacher=teacher,
                name=name,
                phone=phone,
                is_minor=minor,
                guardian_name=g_name,
                guardian_phone=g_phone,
                memo=memo,
            )

        labels = {name: Label.objects.create(teacher=teacher, name=name, color=color) for name, color in LABELS}

        enrollments = {}
        for name, title, total, start, label in ENROLLMENTS:
            enrollments[name] = Enrollment.objects.create(
                student=students[name],
                label=labels.get(label),
                title=title,
                total_count=total,
                start_date=today + datetime.timedelta(days=start),
            )

        sessions = {}
        for name, days, hour, minute, status, place, reason in SESSIONS:
            start = at(days, hour, minute)
            end = start + datetime.timedelta(hours=1)
            sessions[(name, days)] = LessonSession.objects.create(
                enrollment=enrollments[name],
                start_at=start,
                end_at=end,
                status=status,
                place=place,
                reason=reason,
                completed_at=end if status == C else None,
            )
        for name, original_days, days, hour, minute in MAKEUPS:
            start = at(days, hour, minute)
            makeup = LessonSession(
                enrollment=enrollments[name],
                start_at=start,
                end_at=start + datetime.timedelta(hours=1),
                status=S,
                place=sessions[(name, original_days)].place,
                makeup_for=sessions[(name, original_days)],
            )
            makeup.full_clean()  # 보강 규칙(휴강·미차감 결석만, 같은 강사) 검증
            makeup.save()

        for name, subject, body, hours_ago, read, reply in MESSAGES:
            sent = now - datetime.timedelta(hours=hours_ago)
            msg = services.add_student_message(teacher=teacher, student=students[name], subject=subject, body=body)
            Message.objects.filter(pk=msg.pk).update(sent_at=sent, read_at=sent if read else None)
            last = sent
            if reply:
                answer = services.add_teacher_reply(conversation=msg.conversation, body=reply)
                last = sent + datetime.timedelta(minutes=30)
                Message.objects.filter(pk=answer.pk).update(sent_at=last)
            Conversation.objects.filter(pk=msg.conversation_id).update(last_message_at=last)

        return {
            "수강생": Student.objects.filter(teacher=teacher).count(),
            "라벨": Label.objects.filter(teacher=teacher).count(),
            "레슨권": Enrollment.objects.filter(student__teacher=teacher).count(),
            "회차": LessonSession.objects.filter(enrollment__student__teacher=teacher).count(),
            "대화": Conversation.objects.filter(teacher=teacher).count(),
            "메시지": Message.objects.filter(conversation__teacher=teacher).count(),
        }
