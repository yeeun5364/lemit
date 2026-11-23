def student_profile(request):
    """템플릿에서 수강생 계정이면 수강생 메뉴를 보여 주려고 쓴다 (#80). 미들웨어가 정한 값을 그대로 넘긴다."""
    return {"portal_student": getattr(request, "student_profile", None)}
