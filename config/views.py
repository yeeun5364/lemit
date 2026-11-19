from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from inbox import classifier


@login_not_required  # 배포 스모크 테스트가 로그인 없이 호출 (UC-01에서 LoginRequiredMiddleware 도입 예정)
@never_cache
@require_GET
def healthz(request):
    """배포 확인용. DB가 안 되면 503. 메시지 분류 모델이 안 되면 200이지만 상태에 표시한다.

    모델은 부가 기능이라 없어도 일정 관리는 동작해야 하므로 서비스 전체를 실패로 보지 않는다.
    배포 스모크 테스트는 message_classifier.status가 ok인지 따로 확인한다.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db = "ok"
    except Exception:
        db = "error"

    clf = classifier.status()
    body = {
        "status": "ok" if db == "ok" else "error",
        "db": db,
        "message_classifier": {k: v for k, v in clf.items() if k != "error"},  # 오류 상세는 로그에만
    }
    return JsonResponse(body, status=200 if db == "ok" else 503)
