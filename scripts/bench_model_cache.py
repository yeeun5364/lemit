"""메시지 분류 모델 캐시 효과 측정 (#51). 결과는 docs/ML.md §6에 기록한다.

    PC:   python scripts/bench_model_cache.py
    서버: docker compose exec -T web python scripts/bench_model_cache.py

- 캐시 없음: 요청마다 모델 파일을 불러온 뒤(artifact.load) 예측한다.
- 캐시 있음: 서비스 경로 그대로 classifier.classify()를 부른다(프로세스당 한 번만 로드).
- DB에 읽기·쓰기를 하지 않는다. 운영 컨테이너에서 실행해도 데이터는 바뀌지 않는다.
- 반복 횟수: 환경변수 N_NOCACHE(기본 50), N_CACHED(기본 500)
"""

import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

t0 = time.perf_counter()
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402

from inbox import classifier  # noqa: E402
from ml import artifact  # noqa: E402

t_import = time.perf_counter() - t0

MESSAGES = [
    "선생님 이번 주 목요일 수업 취소할 수 있을까요?",
    "지난번 빠진 수업 보강 언제 가능할까요?",
    "다음 주 화요일 수업을 수요일로 옮길 수 있나요?",
    "오늘 숙제 자료 다시 보내주실 수 있나요?",
]


def summary(seconds: list[float]) -> str:
    ms = sorted(s * 1000 for s in seconds)
    p95 = ms[max(0, int(len(ms) * 0.95) - 1)]
    return f"중앙값 {statistics.median(ms):.2f}ms, 평균 {statistics.mean(ms):.2f}ms, p95 {p95:.2f}ms (n={len(ms)})"


def timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def main() -> None:
    path = Path(settings.MESSAGE_CLASSIFIER_PATH)
    n_nocache = int(os.environ.get("N_NOCACHE", 50))
    n_cached = int(os.environ.get("N_CACHED", 500))

    classifier.clear_cache()
    first = timed(lambda: classifier.classify(MESSAGES[0]))

    def load_and_predict(text: str) -> None:
        artifact.load(path).pipeline.predict_proba([text])

    no_cache = [timed(lambda i=i: load_and_predict(MESSAGES[i % 4])) for i in range(n_nocache)]
    load_only = [timed(lambda: artifact.load(path)) for _ in range(20)]
    cached = [timed(lambda i=i: classifier.classify(MESSAGES[i % 4])) for i in range(n_cached)]

    print(f"모델 파일 {path.stat().st_size / 1024:.0f}KB, 버전 {classifier.get_model().version}")
    print(f"Django·라이브러리 준비: {t_import * 1000:.0f}ms (프로세스 시작 시 1회)")
    print(f"첫 분류 (모델 로드 포함, 프로세스당 1회): {first * 1000:.1f}ms")
    print(f"모델 로드만: {summary(load_only)}")
    print(f"[캐시 없음] 로드+예측: {summary(no_cache)}")
    print(f"[캐시 있음] classify(): {summary(cached)}")
    ratio = statistics.median(no_cache) / statistics.median(cached)
    print(f"→ 요청당 약 {ratio:.0f}배 차이 (중앙값 기준)")


if __name__ == "__main__":
    main()
