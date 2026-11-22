"""
LEMIT Django 설정.

비밀값과 환경마다 다른 값은 환경변수(.env)에서 읽는다. 예시는 .env.example 참고.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_CSRF_TRUSTED_ORIGINS=(list, []),
    DJANGO_BEHIND_PROXY=(bool, False),
    DJANGO_SECURE_HTTPS=(bool, False),
    DJANGO_HSTS_SECONDS=(int, 0),
)
# 로컬 개발용 .env 파일이 있으면 읽는다. 배포 환경에서는 실제 환경변수가 우선한다.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "students",
    "lessons",
    "inbox",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # 정적 파일을 web 컨테이너에서 바로 제공 (nginx 없이 시작, #5)
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # 기본 로그인 필수(기본 거부). 공개 화면만 @login_not_required (SCREENS §1 접근 정책, UC-01)
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "inbox.context_processors.unread",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database: PostgreSQL 18 (DATABASE_URL 예: postgres://lemit:lemit@localhost:5432/lemit)

DATABASES = {"default": env.db("DATABASE_URL")}


# 인증: 이메일로 로그인하는 커스텀 User (ERD 2-4)

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# 한국어, 서울 시간대 (ERD 2-6)

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True


# Static files

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # 압축본(gzip·brotli)을 미리 만든다. 해시 파일명(Manifest)은 collectstatic 없이 도는 테스트를 깨뜨려 쓰지 않는다
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}


# 로그인 (UC-01)

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"


# 비밀번호 재설정 메일 (UC-15). 링크는 1회용이고 1시간 뒤 만료된다.
PASSWORD_RESET_TIMEOUT = 3600

# 메일 계정은 .env에만 둔다.
# - EMAIL_HOST_USER가 있으면 Gmail SMTP(앱 비밀번호, STARTTLS 587)로 보낸다.
# - 없고 DEBUG(개발)이면 메일을 콘솔에 출력한다.
# - 없고 운영(DEBUG=False)이면 보내지 않는다. 콘솔로 내보내면 재설정 링크가 운영 로그에 남기 때문 (PR #68 리뷰 P1)
EMAIL_HOST_USER = env.str("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env.str("EMAIL_HOST_PASSWORD", default="")
if EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env.str("EMAIL_HOST", default="smtp.gmail.com")
    EMAIL_PORT = env.int("EMAIL_PORT", default=587)
    EMAIL_USE_TLS = True
elif DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "accounts.mail.DisabledEmailBackend"
EMAIL_TIMEOUT = 10  # SMTP가 멈춰도 요청이 오래 묶이지 않게
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER or "LEMIT <noreply@lemit.local>")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# 운영 보안 (#7, OPERATIONS §3). 개발 기본값은 모두 꺼져 있고 운영 서버의 .env에서 켠다.

# 항상 켜 두는 기본 보안 헤더·쿠키
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True

# TLS를 끝내는 리버스 프록시(Caddy, compose의 proxy 프로필) 뒤에서만 켠다.
# 켜면 프록시가 보낸 X-Forwarded-Proto를 믿고 https 요청으로 판단한다. 프록시 없이 켜면 위조할 수 있으므로 켜지 않는다.
if env("DJANGO_BEHIND_PROXY"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = False

# 도메인 + HTTPS로 서비스할 때 켠다 (IP 주소로 HTTP 시연할 때는 끈다, OPERATIONS §10 HTTPS 예외 범위)
if env("DJANGO_SECURE_HTTPS"):
    SECURE_SSL_REDIRECT = True
    # 컨테이너 내부 헬스체크는 http://127.0.0.1로 부르므로 https 리다이렉트에서 제외한다 (PR #39 리뷰)
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS는 되돌리기 어려우므로 기본 0. 도메인·인증서가 안정된 뒤 짧은 값부터 올린다
    SECURE_HSTS_SECONDS = env("DJANGO_HSTS_SECONDS")
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False


# 로그 (#44, OPERATIONS §7). 모든 로그를 표준 출력으로 보내 `docker compose logs web`에서 본다.
# Django 기본 설정은 DEBUG=False일 때 500 오류 상세를 콘솔에 남기지 않고 관리자 메일로만 보낸다
# (메일 미설정 → 아무 데도 안 남음).
# 요청 본문·헤더·쿠키는 남기지 않는다: django.request는 "Internal Server Error: <경로>"와 traceback만 기록한다.
# 로그 파일 크기는 Docker json-file 로그 설정(10MB × 3)으로 제한한다 (compose.yaml).

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "stdout": {"class": "logging.StreamHandler", "stream": "ext://sys.stdout", "formatter": "plain"},
    },
    "root": {"handlers": ["stdout"], "level": "WARNING"},
    "loggers": {
        # 500 오류(traceback 포함)·404 등. propagate=False로 root와 중복 출력하지 않음
        "django.request": {"handlers": ["stdout"], "level": "ERROR", "propagate": False},
        # 허용되지 않은 Host 등 보안 경고
        "django.security": {"handlers": ["stdout"], "level": "WARNING", "propagate": False},
        # 메시지 분류 모델 로드·불가·예측 오류 (모델 로드 성공도 INFO로 남겨 배포 후 확인)
        "inbox": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}


# 수강생 메시지 분류 모델 (ml/). 학습은 개발 PC에서 하고, 검증된 모델 파일을 저장소·이미지에 포함한다.
# 경로를 바꿀 때만 MESSAGE_CLASSIFIER_PATH를 지정한다.

MESSAGE_CLASSIFIER_PATH = Path(
    env.str("MESSAGE_CLASSIFIER_PATH", default=str(BASE_DIR / "ml" / "artifacts" / "message_classifier.joblib"))
)
