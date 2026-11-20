from django import forms
from django.utils import timezone

from lessons.models import Enrollment


class EnrollmentForm(forms.ModelForm):
    """S11 레슨권 등록 (UC-03).

    - 수강생은 폼에서 받지 않고 URL의 '내 수강생'으로 정한다 (조작 방지).
    - 라벨 선택 칸은 UC-16(후속)에서 추가한다.
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

    class Meta:
        model = Enrollment
        fields = ("title", "total_count", "start_date")
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
