# LEMIT web 이미지 (#5). 운영은 gunicorn, 개발은 compose.dev.yaml을 덧붙여 runserver로 바꿔 실행한다.
# 학습된 ML 모델(ml/artifacts/)을 이미지에 함께 넣어, 앱 커밋과 모델 버전이 한 묶음으로 배포·롤백되게 한다 (OPERATIONS §5).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 1) 의존성 레이어: requirements.txt가 바뀔 때만 다시 설치 (빌드 캐시)
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) 앱 코드 + 모델 파일 (.dockerignore로 .env·.git·문서 등 제외)
COPY . .

# 3) 정적 파일 모으기. 설정 import에 필요한 값은 이 명령에서만 쓰는 빌드용 가짜 값이다 (이미지에 남지 않음)
RUN DJANGO_SECRET_KEY=build-only-not-secret DATABASE_URL=sqlite:///:memory: \
    python manage.py collectstatic --noinput

# 4) root가 아닌 사용자로 실행
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "scripts/healthcheck.py"]

# worker 수는 GUNICORN_WORKERS로 조정 (기본 2: 작은 Lightsail 인스턴스 기준)
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --access-logfile - --error-logfile -"]
