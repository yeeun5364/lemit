import datetime

from django import forms
from django.utils import timezone

from accounts.permissions import scope_to_teacher
from lessons.models import Enrollment, Label, LessonSession


class LabelChoiceField(forms.ModelChoiceField):
    """레슨권의 라벨 선택. 선택지는 강사 본인의 라벨뿐이라 조작된 POST로 남의 라벨을 보내면 폼 오류 (UC-16)."""

    def __init__(self, **kwargs):
        super().__init__(
            queryset=Label.objects.none(),
            required=False,
            empty_label="라벨 없음 (회색)",
            label="라벨",
            error_messages={"invalid_choice": "내 라벨만 선택할 수 있습니다."},
            **kwargs,
        )

    def label_from_instance(self, label):
        return f"{label.name} ({label.get_color_display()})"


def scope_label_field(field, teacher):
    field.queryset = Label.objects.filter(teacher=teacher).order_by("name", "id")


class LabelForm(forms.ModelForm):
    """S12 라벨 추가·수정 (UC-16). 강사는 폼이 아니라 로그인한 사용자로 정한다.

    색은 모델의 8색 선택지에서만 받는다 (목록 밖 값은 폼 오류). 같은 강사 안에서 이름 중복은 폼 오류이고,
    동시에 저장해 검증을 지나쳐도 DB 제약(label_unique_name_per_teacher)이 막는다 (뷰가 폼 오류로 바꿈).
    """

    class Meta:
        model = Label
        fields = ("name", "color")
        widgets = {
            "name": forms.TextInput(attrs={"autofocus": True, "placeholder": "예: 피아노, 출강, 체험"}),
            "color": forms.RadioSelect(attrs={"class": "color-input"}),
        }
        error_messages = {"color": {"invalid_choice": "정해진 8색 중에서 고르세요.", "required": "색을 고르세요."}}

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.teacher = teacher
        self.fields["color"].choices = Label.Color.choices  # 빈 선택지(---------) 없이 8색만

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("이름을 입력하세요.")
        same = Label.objects.filter(teacher=self.instance.teacher, name=name).exclude(pk=self.instance.pk)
        if same.exists():
            raise forms.ValidationError("같은 이름의 라벨이 이미 있습니다.")
        return name

    def validate_unique(self):
        pass  # 이름 중복은 clean_name에서 같은 문구로 검사한다 (모델 제약 문구가 영어로 섞이지 않게)


class EnrollmentLabelForm(forms.ModelForm):
    """등록된 레슨권의 라벨만 바꾸거나 비운다 (UC-16 '레슨권에 라벨을 하나 붙이거나 비울 수 있다')."""

    label = LabelChoiceField()

    class Meta:
        model = Enrollment
        fields = ("label",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_label_field(self.fields["label"], self.instance.student.teacher)


class EnrollmentForm(forms.ModelForm):
    """S11 레슨권 등록 (UC-03).

    - 수강생은 폼에서 받지 않고 URL의 '내 수강생'으로 정한다 (조작 방지).
    - 라벨 선택지는 이 수강생 강사의 라벨뿐이다 (UC-16). 저장 직전 모델 clean()이 한 번 더 확인한다.
    - 소진된 진행 중 레슨권이 있으면 '함께 종료' 체크박스를 보여 주고 기본으로 체크한다.
      선택지는 이 수강생의 소진된 진행 중 레슨권뿐이라, 조작된 POST로 다른 레슨권을 보내면 폼 오류가 된다.
      후보가 없어도 필드는 남겨 둔다. 지우면 조작된 값이나 다른 탭에서 이미 종료한 선택이 조용히 무시되고
      새 레슨권만 저장되기 때문이다 (PR #55 리뷰). 화면에는 후보가 있을 때만 보인다(has_exhausted).
    """

    end_exhausted = forms.ModelMultipleChoiceField(
        label="소진된 레슨권 종료",
        queryset=Enrollment.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="체크한 레슨권은 '종료'로 보관됩니다. 종료하지 않아도 새 레슨권은 저장됩니다.",
        error_messages={
            "invalid_choice": "이미 종료되었거나 잔여 회차가 남은 레슨권입니다. 화면을 다시 확인하세요.",
            "invalid_pk_value": "올바르지 않은 레슨권입니다.",
        },
    )

    label = LabelChoiceField(help_text="일정에서 이 레슨권의 회차를 라벨 색으로 구분합니다.")

    # 회차 자동 생성 (#76, 최소 범위): 체크하면 첫 수업 날짜부터 고른 요일마다 총 회차만큼 '예정' 회차를 함께 만든다.
    # 반복 규칙은 저장하지 않는다(만든 뒤에는 보통 회차처럼 하나씩 수정·삭제). 규칙 저장·일괄 수정은 #19.
    WEEKDAY_CHOICES = [("0", "월"), ("1", "화"), ("2", "수"), ("3", "목"), ("4", "금"), ("5", "토"), ("6", "일")]
    MAX_AUTO_SESSIONS = 100
    make_sessions = forms.BooleanField(
        label="회차도 함께 만들기",
        required=False,
        help_text="첫 수업 날짜부터 고른 요일마다 총 회차만큼 '예정' 회차를 만들어 달력에 표시합니다.",
    )
    first_date = forms.DateField(
        label="첫 수업 날짜", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    first_start = forms.TimeField(
        label="시작", required=False, widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
    )
    first_end = forms.TimeField(
        label="종료", required=False, widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
    )
    weekdays = forms.MultipleChoiceField(
        label="반복 요일",
        required=False,
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text="비우면 첫 수업 날짜의 요일로 매주 만듭니다.",
    )
    session_place = forms.CharField(
        label="장소 메모", required=False, max_length=100, widget=forms.TextInput(attrs={"placeholder": "예: A 레슨실"})
    )

    # 이전 레슨권의 레슨명 고르기 (#75). 이전 레슨권이 없는 수강생에게는 이 칸을 지우고 레슨명을 바로 입력받는다.
    title_choice = forms.ChoiceField(label="레슨명", required=False, widget=forms.RadioSelect)
    MAX_PREVIOUS_TITLES = 5

    class Meta:
        model = Enrollment
        fields = ("title", "total_count", "start_date", "label")
        widgets = {
            "title": forms.TextInput(attrs={"autofocus": True, "placeholder": "예: 피아노 개인 레슨"}),
            "total_count": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        error_messages = {
            "total_count": {"min_value": "총 회차는 1 이상이어야 합니다."},
        }

    def __init__(self, *args, student, **kwargs):
        super().__init__(*args, **kwargs)
        self.student = student
        self.instance.student = student
        scope_label_field(self.fields["label"], student.teacher)
        if not self.is_bound:
            self.fields["start_date"].initial = timezone.localdate()
        exhausted = Enrollment.objects.filter(student=student).active().exhausted().order_by("start_date", "id")
        field = self.fields["end_exhausted"]
        field.queryset = exhausted
        field.label_from_instance = lambda e: f"'{e.title}' ({e.used_count}/{e.total_count}회 사용)을 종료합니다"
        candidates = list(exhausted)
        self.has_exhausted = bool(candidates)
        field.initial = candidates
        self._setup_title_choice(student)

    def _setup_title_choice(self, student):
        """이전 레슨권의 레슨명(최근 것부터, 중복 없이 최대 5개)을 고르게 하고 가장 최근 것을 기본으로 둔다 (#75).
        '직접 입력'을 고르면 새 레슨명 칸에 입력한 값을 쓴다. 이전 레슨명이 없으면 지금처럼 바로 입력한다.
        title_choice를 보내지 않은 요청(예전 화면·직접 POST)은 '직접 입력'과 같게 처리해 입력한 레슨명을 쓴다."""
        # 선택지 값은 레슨명이 아니라 그 레슨명을 쓴 가장 최근 레슨권의 PK다 (PR #86 리뷰 P3).
        # 레슨명 자체를 값으로 쓰면 실제 레슨명이 '직접 입력' 값(__custom__)과 같을 때 구분할 수 없다.
        self.previous_titles = {}  # {레슨권 PK 문자열: 레슨명}, 최근 것부터
        rows = Enrollment.objects.filter(student=student).order_by("-start_date", "-id").values_list("pk", "title")
        for pk, title in rows:
            if title not in self.previous_titles.values():
                self.previous_titles[str(pk)] = title
            if len(self.previous_titles) == self.MAX_PREVIOUS_TITLES:
                break
        if not self.previous_titles:
            del self.fields["title_choice"]
            return
        choice = self.fields["title_choice"]
        choice.choices = [*self.previous_titles.items(), (self.CUSTOM_TITLE, "직접 입력")]
        choice.initial = next(iter(self.previous_titles))
        choice.help_text = "이전 레슨권의 레슨명입니다. 새 이름이면 '직접 입력'을 고르세요."
        title = self.fields["title"]
        title.required = False  # 고른 레슨명을 쓸 때는 비어 있어도 된다. 직접 입력이면 clean_title이 확인
        title.label = "새 레슨명"
        title.widget.attrs.pop("autofocus", None)
        self.order_fields(["title_choice", "title"])  # title_choice를 먼저 검증해 clean_title에서 쓴다

    CUSTOM_TITLE = "__custom__"

    def clean(self):
        cleaned = super().clean()
        self.session_slots = []
        if cleaned.get("make_sessions"):
            self._plan_sessions(cleaned)
        return cleaned

    def _plan_sessions(self, cleaned):
        """만들 회차의 (시작, 종료) 목록을 정한다 (#76). 첫 수업 날짜를 포함해 그 날부터 고른 요일마다, 총 회차만큼."""
        first, start, end = cleaned.get("first_date"), cleaned.get("first_start"), cleaned.get("first_end")
        for name, value in (("first_date", first), ("first_start", start), ("first_end", end)):
            if not value and name not in self.errors:
                self.add_error(name, "회차를 함께 만들려면 입력하세요.")
        total = cleaned.get("total_count")
        if total and total > self.MAX_AUTO_SESSIONS:
            self.add_error("total_count", f"회차를 함께 만들 때 총 회차는 {self.MAX_AUTO_SESSIONS}회까지입니다.")
        if not (first and start and end and total) or total > self.MAX_AUTO_SESSIONS:
            return
        if end <= start:
            self.add_error("first_end", "종료 시간은 시작 시간보다 늦어야 합니다.")
            return
        days = {int(d) for d in cleaned.get("weekdays") or []} or {first.weekday()}
        if first.weekday() not in days:
            self.add_error("weekdays", "첫 수업 날짜의 요일을 반복 요일에 포함하세요.")
            return
        tz = timezone.get_current_timezone()
        day = first
        while len(self.session_slots) < total:
            if day.weekday() in days:
                self.session_slots.append(
                    (
                        timezone.make_aware(datetime.datetime.combine(day, start), tz),
                        timezone.make_aware(datetime.datetime.combine(day, end), tz),
                    )
                )
            day += datetime.timedelta(days=1)

    def clean_title(self):
        choice = self.cleaned_data.get("title_choice")
        if choice and choice != self.CUSTOM_TITLE:  # 선택지 밖 값은 title_choice 필드 오류로 이미 걸러짐
            return self.previous_titles[choice]
        title = (self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError("레슨명을 입력하세요.")
        return title


class EnrollmentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, e):
        return f"{e.student.name} · {e.title}"


class SessionForm(forms.ModelForm):
    """S07 회차 등록·수정 (UC-04). 날짜·시작·종료 시간을 따로 받아 Asia/Seoul 기준 datetime으로 합친다.

    - 레슨권 선택지: 로그인한 강사의 '진행 중' 레슨권(보관 수강생 제외). 조작된 POST로 다른 강사의
      레슨권을 보내면 폼 오류 (ERD 2-5).
    - 수정이면 **같은 수강생**의 레슨권으로만 바꿀 수 있다(지금 레슨권은 종료됐어도 포함). 회차를 다른
      수강생에게 옮기면 진행 기록이 엉뚱한 수강생에게 붙기 때문이다.
    - 상태는 받지 않는다. 새 회차는 항상 '예정'.
    - 저장 직전 재확인(수강생 보관·레슨권 종료·회차 상태)은 뷰가 수강생 행 잠금 안에서 한다.
    """

    enrollment = EnrollmentChoiceField(label="수강생·레슨권", queryset=Enrollment.objects.none())
    date = forms.DateField(label="날짜", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    start_time = forms.TimeField(label="시작", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    end_time = forms.TimeField(
        label="종료", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"), help_text="기본: 시작 + 1시간"
    )

    class Meta:
        model = LessonSession
        fields = ("enrollment", "place")
        widgets = {"place": forms.TextInput(attrs={"placeholder": "예: A 레슨실"})}
        labels = {"place": "장소 메모"}

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        choices = Enrollment.objects.active().filter(student__is_active=True)
        if self.instance.pk:
            choices = choices.filter(student_id=self.instance.enrollment.student_id) | Enrollment.objects.filter(
                pk=self.instance.enrollment_id
            )
        self.fields["enrollment"].queryset = (
            scope_to_teacher(choices, teacher, "student__teacher")
            .select_related("student")
            .order_by("student__name", "-start_date", "id")
        )
        if self.instance.pk:
            start = timezone.localtime(self.instance.start_at)
            end = timezone.localtime(self.instance.end_at)
            self.initial.update(date=start.date(), start_time=start.time(), end_time=end.time())

    def clean(self):
        cleaned = super().clean()
        date, start, end = cleaned.get("date"), cleaned.get("start_time"), cleaned.get("end_time")
        if date and start and end:
            if end <= start:
                self.add_error("end_time", "종료 시간은 시작 시간보다 늦어야 합니다.")
            else:
                tz = timezone.get_current_timezone()
                self.instance.start_at = timezone.make_aware(datetime.datetime.combine(date, start), tz)
                self.instance.end_at = timezone.make_aware(datetime.datetime.combine(date, end), tz)
        return cleaned


class CancelForm(forms.Form):
    """휴강 처리 (UC-08). 사유는 선택."""

    reason = forms.CharField(
        label="휴강 사유",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "예: 강사 개인 사정 (선택)"}),
    )


class MakeupForm(forms.Form):
    """보강 등록 (UC-09). 레슨권은 원래 회차의 것으로 고정하고 날짜·시간·장소만 받는다."""

    date = forms.DateField(label="날짜", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    start_time = forms.TimeField(label="시작", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    end_time = forms.TimeField(label="종료", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    place = forms.CharField(
        label="장소 메모",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "예: A 레슨실"}),
    )

    def __init__(self, *args, original, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            start = timezone.localtime(original.start_at)
            end = timezone.localtime(original.end_at)
            self.initial.update(
                date=timezone.localdate() + datetime.timedelta(days=1),
                start_time=start.time(),
                end_time=end.time(),
                place=original.place,
            )

    def clean(self):
        cleaned = super().clean()
        date, start, end = cleaned.get("date"), cleaned.get("start_time"), cleaned.get("end_time")
        if date and start and end:
            if end <= start:
                self.add_error("end_time", "종료 시간은 시작 시간보다 늦어야 합니다.")
            else:
                tz = timezone.get_current_timezone()
                cleaned["start_at"] = timezone.make_aware(datetime.datetime.combine(date, start), tz)
                cleaned["end_at"] = timezone.make_aware(datetime.datetime.combine(date, end), tz)
        return cleaned


class AbsentForm(forms.Form):
    """결석 처리 (UC-10). 차감 여부는 기본값 없이 반드시 고른다 (README 회차 규칙). 사유는 선택."""

    DEDUCT_CHOICES = [
        ("yes", "차감 (당일 취소 등, 레슨권에서 1회 뺌, 보강 불가)"),
        ("no", "미차감 (레슨권 유지, 이후 보강 등록 가능)"),
    ]

    deducted = forms.ChoiceField(
        label="레슨권 차감",
        choices=DEDUCT_CHOICES,
        widget=forms.RadioSelect,
        error_messages={"required": "차감 여부를 고르세요.", "invalid_choice": "차감 여부를 다시 고르세요."},
    )
    reason = forms.CharField(
        label="결석 사유",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "예: 수강생 감기 (선택)"}),
    )

    def __init__(self, *args, **kwargs):
        # 회차 상세에서 휴강 폼(CancelForm)과 같은 페이지에 렌더링된다. 기본 ID(id_reason)가 겹치면
        # '결석 사유' 라벨이 휴강 사유 칸을 가리키므로 결석 폼 필드는 absent_ 접두사를 쓴다 (PR #72 리뷰)
        kwargs.setdefault("auto_id", "absent_%s")
        super().__init__(*args, **kwargs)

    def clean_deducted(self):
        return self.cleaned_data["deducted"] == "yes"
