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
            # 미성년자가 아니면 보호자 정보를 저장하지 않는다 (개인정보 최소 수집)
            cleaned["guardian_phone"] = ""
            cleaned["guardian_name"] = ""
        return cleaned
