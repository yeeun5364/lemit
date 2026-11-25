"""수강생 계정의 접근 범위 (#80).

로그인한 계정이 수강생 기록(Student.account)에 연결돼 있으면 '수강생 계정'이다. 수강생 계정은 수강생 화면(portal)과
로그아웃·비밀번호 변경·재설정만 쓸 수 있고, 강사 화면(대시보드·수강생·일정·메시지함·라벨·관리자)은 서버에서 막는다.
화면 메뉴를 숨기는 것만으로는 주소를 직접 입력하면 들어갈 수 있기 때문이다.
- GET은 수강생 홈(내 수업)으로 보내고, 그 밖의 요청(POST 등)은 403으로 거부한다(데이터를 바꾸지 않음).
- 강사 계정(연결 없음)은 지금과 같고, 수강생 화면에는 들어갈 수 없다(portal 뷰가 404).
"""

from django.http import HttpResponseForbidden
from django.shortcuts import redirect

from students.models import Student

# 수강생 계정도 쓸 수 있는 공통 화면 (accounts 앱의 URL 이름)
SHARED_URL_NAMES = {
    "logout",
    "password_change",
    "password_change_done",
    "password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
    "healthz",
}


class StudentPortalMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.student_profile = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            request.student_profile = Student.objects.select_related("teacher").filter(account=user).first()
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.student_profile is None:
            return None
        match = request.resolver_match
        # 외부 API(/api/)는 세션이 아니라 API 키로 인증하고, 수강생 계정 거절도 API 권한에서 한다 (#102)
        if match is not None and (match.namespace in ("portal", "api") or match.url_name in SHARED_URL_NAMES):
            return None
        if request.method in ("GET", "HEAD"):
            return redirect("portal:lessons")
        return HttpResponseForbidden("수강생 계정은 이 기능을 쓸 수 없습니다.")
