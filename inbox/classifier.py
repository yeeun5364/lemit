"""수강생 메시지 분류 서비스: 학습된 모델(ml/)로 메시지 유형을 예측한다.

- 예측 결과는 참고용 표시에만 쓴다. 일정을 자동으로 바꾸지 않는다.
- 모델을 쓸 수 없거나 출력이 이상하면 MessageClassifierUnavailable을 낸다. 호출하는 화면은 이 예외를 잡아
  "분류 불가"만 표시하고, 메시지 저장과 일정 기능은 그대로 동작해야 한다.
"""

import logging
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from config.safe_log import describe_exception
from ml import artifact
from ml.labels import LABEL_DISPLAY, LABELS

logger = logging.getLogger(__name__)

MAX_LENGTH = 500  # 이보다 긴 메시지는 앞부분만 분류에 사용
LOW_CONFIDENCE = 0.5  # 가장 높은 확률이 이보다 낮으면 "확신 낮음"으로 표시
SUM_TOLERANCE = 1e-3  # 확률 합이 1에서 이만큼 넘게 벗어나면 이상한 출력으로 본다


class MessageClassifierUnavailable(Exception):
    """모델 파일 없음·손상·버전 불일치·예측 중 오류."""


@dataclass(frozen=True)
class MessagePrediction:
    label: str
    confidence: float
    model_version: str
    scores: dict[str, float] = field(default_factory=dict)  # 분류별 확률 전체 (외부 API #101이 돌려줌)
    truncated: bool = False  # MAX_LENGTH를 넘어 앞부분만 분류했는지

    @property
    def display(self) -> str:
        return LABEL_DISPLAY[self.label]

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < LOW_CONFIDENCE


_lock = threading.Lock()
_cache: dict[Path, artifact.LoadedModel] = {}


def _model_path() -> Path:
    return Path(settings.MESSAGE_CLASSIFIER_PATH)


def get_model() -> artifact.LoadedModel:
    """모델을 프로세스마다 한 번만 불러와 재사용한다.

    캐시 정책 (PR #34 리뷰):
    - 불러오기에 **성공한** 모델은 경로별로 프로세스가 끝날 때까지 캐시한다. 같은 경로의 파일을 바꿔도
      실행 중인 프로세스는 이전 모델을 계속 쓴다. 모델 교체는 새 이미지 배포 = 프로세스 재시작으로 한다.
    - 불러오기에 **실패한** 경우는 캐시하지 않는다. 파일 누락·손상을 고치면 다음 요청부터 동작한다.
    """
    path = _model_path()
    model = _cache.get(path)
    if model is not None:
        return model
    with _lock:
        if path not in _cache:
            try:
                _cache[path] = artifact.load(path)
            except artifact.ArtifactError as e:
                logger.error("메시지 분류 모델을 불러오지 못했습니다: %s", e)
                raise MessageClassifierUnavailable(str(e)) from e
            logger.info("메시지 분류 모델 로드: %s", _cache[path].version)
        return _cache[path]


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def classify(text: str) -> MessagePrediction:
    """메시지 한 건을 분류한다. 빈 메시지는 ValueError, 모델 문제는 MessageClassifierUnavailable."""
    text = (text or "").strip()
    if not text:
        raise ValueError("메시지가 비어 있습니다")
    truncated = len(text) > MAX_LENGTH
    text = text[:MAX_LENGTH]

    model = get_model()
    try:
        # 예측부터 결과 정리까지 한 범위로 묶는다: 클래스·확률 개수가 어긋나도 500이 아니라 "분류 불가"가 되게
        proba = model.pipeline.predict_proba([text])[0]
        classes = [str(c) for c in model.pipeline.classes_]
        scores = _validated_scores(classes, proba)
    except MessageClassifierUnavailable:
        raise
    except Exception as e:  # 예측 중 오류도 서비스 전체 오류로 번지지 않게 한다
        # 예외 메시지에는 입력한 메시지가 섞일 수 있으므로 종류와 코드 위치만 남긴다 (PR #114 리뷰 P1)
        logger.error("메시지 분류 중 오류\n%s", describe_exception(e))
        raise MessageClassifierUnavailable(f"예측 중 오류: {type(e).__name__}") from None

    label = max(scores, key=scores.get)
    return MessagePrediction(
        label=label,
        confidence=round(scores[label], 3),
        model_version=model.version,
        scores={name: round(p, 3) for name, p in scores.items()},
        truncated=truncated,
    )


def _validated_scores(classes, proba) -> dict[str, float]:
    """모델 출력이 '4개 분류의 확률'인지 확인한다. 아니면 MessageClassifierUnavailable (PR #114 리뷰 P2).

    모델 파일은 불러올 때 분류 목록을 확인하지만(ml/artifact), 예측 결과 자체도 한 번 더 본다.
    NaN·무한대·범위 밖 값이 응답으로 나가거나 엉뚱한 분류를 고르지 않게 하기 위해서다.
    """
    values = [float(p) for p in proba]
    problem = None
    if sorted(classes) != sorted(LABELS) or len(set(classes)) != len(classes):
        problem = "분류 목록이 다름"
    elif len(values) != len(classes):
        problem = f"확률 개수({len(values)})와 분류 개수({len(classes)})가 다름"
    elif not all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in values):
        problem = "0~1 범위를 벗어나거나 숫자가 아닌 확률"
    elif abs(sum(values) - 1.0) > SUM_TOLERANCE:
        problem = "확률 합이 1이 아님"
    if problem:
        logger.error("메시지 분류 모델 출력 이상: %s", problem)  # 입력 메시지·확률 값은 남기지 않는다
        raise MessageClassifierUnavailable(f"모델 출력 이상: {problem}")
    return dict(zip(classes, values, strict=True))


def status() -> dict:
    """헬스체크용 상태. 모델을 쓸 수 없어도 예외를 내지 않는다."""
    try:
        model = get_model()
    except MessageClassifierUnavailable as e:
        return {"status": "unavailable", "error": str(e)}
    return {"status": "ok", "version": model.version}
