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

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
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
