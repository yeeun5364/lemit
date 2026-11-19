from django import forms

from students.models import Student


class StudentForm(forms.ModelForm):
    """S09 수강생 등록·수정. teacher는 폼에서 받지 않고 뷰에서 로그인한 강사로 정한다 (조작 방지)."""

    class Meta:
        model = Student
        fields = ("name", "phone", "is_minor", "guardian_phone", "guardian_name", "memo")
        widgets = {
            "name": forms.TextInput(attrs={"autofocus": True}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "off"}),
            "guardian_phone": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "off"}),
            "memo": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and not self._submitted_is_minor():
            # 미성년자가 아니면 보호자 입력은 어차피 버리는 값이므로 **검사 전에** 비운다.
            # 그래야 미성년자→성인으로 바꿀 때 남아 있던 잘못된·너무 긴 보호자 값 때문에
            # 저장이 막히지 않는다 (PR #37 리뷰).
            data = self.data.copy()
            for name in ("guardian_phone", "guardian_name"):
                data[self.add_prefix(name)] = ""
            self.data = data

    def _submitted_is_minor(self):
        widget = self.fields["is_minor"].widget
        return widget.value_from_datadict(self.data, self.files, self.add_prefix("is_minor"))

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("이름을 입력하세요.")
        return name

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_minor"):
            if not cleaned.get("guardian_phone"):
                self.add_error("guardian_phone", "미성년자는 보호자 연락처가 필요합니다.")
        else:
            # 미성년자가 아니면 보호자 정보를 저장하지 않는다 (개인정보 최소 수집).
            # __init__에서 이미 비웠고 여기는 안전장치
            cleaned["guardian_phone"] = ""
            cleaned["guardian_name"] = ""
        return cleaned
