"""수강생 메시지 분류 서비스: 학습된 모델(ml/)로 메시지 유형을 예측한다.

- 예측 결과는 참고용 표시에만 쓴다. 일정을 자동으로 바꾸지 않는다.
- 모델을 쓸 수 없으면 MessageClassifierUnavailable을 낸다. 호출하는 화면은 이 예외를 잡아
  "분류 불가"만 표시하고, 메시지 저장과 일정 기능은 그대로 동작해야 한다.
"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from ml import artifact
from ml.labels import LABEL_DISPLAY

logger = logging.getLogger(__name__)

MAX_LENGTH = 500  # 이보다 긴 메시지는 앞부분만 분류에 사용
LOW_CONFIDENCE = 0.5  # 가장 높은 확률이 이보다 낮으면 "확신 낮음"으로 표시


class MessageClassifierUnavailable(Exception):
    """모델 파일 없음·손상·버전 불일치·예측 중 오류."""


@dataclass(frozen=True)
class MessagePrediction:
    label: str
    confidence: float
    model_version: str

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
    """모델을 한 번만 불러와 프로세스 안에서 재사용한다. 실패는 캐시하지 않는다(파일을 고치면 다음 요청부터 동작)."""
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
    text = text[:MAX_LENGTH]

    model = get_model()
    try:
        proba = model.pipeline.predict_proba([text])[0]
    except Exception as e:  # 예측 중 오류도 서비스 전체 오류로 번지지 않게 한다
        logger.exception("메시지 분류 중 오류")
        raise MessageClassifierUnavailable(f"예측 중 오류: {type(e).__name__}") from e

    best = int(proba.argmax())
    return MessagePrediction(
        label=str(model.pipeline.classes_[best]),
        confidence=round(float(proba[best]), 3),
        model_version=model.version,
    )


def status() -> dict:
    """헬스체크용 상태. 모델을 쓸 수 없어도 예외를 내지 않는다."""
    try:
        model = get_model()
    except MessageClassifierUnavailable as e:
        return {"status": "unavailable", "error": str(e)}
    return {"status": "ok", "version": model.version}
