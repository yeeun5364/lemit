"""분류 이름. 순서와 값은 모델 파일 메타데이터에도 기록되어, 불러올 때 일치하는지 확인한다."""

LABELS = ("CANCELLATION", "MAKEUP", "RESCHEDULE", "GENERAL")

LABEL_DISPLAY = {
    "CANCELLATION": "휴강 요청",
    "MAKEUP": "보강 요청",
    "RESCHEDULE": "일정 변경",
    "GENERAL": "일반 메시지",
}
