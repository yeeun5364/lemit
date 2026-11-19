"""모델 파일(joblib)과 메타데이터(JSON)의 저장·불러오기.

불러올 때 확인하는 것:
1. 두 파일이 모두 있는지
2. 모델 파일의 SHA-256이 메타데이터와 같은지 (파일 손상·다른 파일로 바뀜 감지)
3. 학습할 때의 scikit-learn 버전과 지금 설치된 버전이 같은지 (pickle 호환성)
4. 모델의 분류 목록이 코드의 LABELS와 같은지

주의: joblib(pickle) 파일은 불러올 때 코드를 실행할 수 있으므로, 이 저장소에서 만든 파일만 불러온다.
해시 확인은 손상 감지용이며 악의적인 교체를 막는 서명은 아니다.
"""

import hashlib
import json
import warnings
from dataclasses import dataclass
from pathlib import Path

from ml.labels import LABELS

DEFAULT_DIR = Path(__file__).resolve().parent / "artifacts"
MODEL_FILENAME = "memo_classifier.joblib"
META_FILENAME = "memo_classifier.json"


class ArtifactError(Exception):
    """모델을 쓸 수 없는 상태 (파일 없음·손상·버전 불일치 등)."""


@dataclass(frozen=True)
class LoadedModel:
    pipeline: object
    meta: dict

    @property
    def version(self) -> str:
        return self.meta["model_version"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def meta_path_for(model_path: Path) -> Path:
    return Path(model_path).with_name(META_FILENAME)


def load(model_path: Path | str | None = None) -> LoadedModel:
    """모델을 불러와 검증한다. 문제가 있으면 ArtifactError."""
    import joblib
    import sklearn
    from sklearn.exceptions import InconsistentVersionWarning

    model_path = Path(model_path) if model_path else DEFAULT_DIR / MODEL_FILENAME
    meta_path = meta_path_for(model_path)

    if not model_path.is_file():
        raise ArtifactError(f"모델 파일이 없습니다: {model_path.name}")
    if not meta_path.is_file():
        raise ArtifactError(f"모델 메타데이터가 없습니다: {meta_path.name}")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArtifactError(f"메타데이터를 읽을 수 없습니다: {e}") from e

    for key in ("model_version", "sha256", "sklearn_version", "labels"):
        if key not in meta:
            raise ArtifactError(f"메타데이터에 {key}가 없습니다")

    actual = sha256_of(model_path)
    if actual != meta["sha256"]:
        raise ArtifactError("모델 파일의 해시가 메타데이터와 다릅니다 (손상 또는 다른 파일)")

    if meta["sklearn_version"] != sklearn.__version__:
        raise ArtifactError(
            f"scikit-learn 버전 불일치: 학습 {meta['sklearn_version']}, 현재 {sklearn.__version__}. "
            "requirements.txt 버전으로 설치하거나 모델을 다시 학습하세요"
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", InconsistentVersionWarning)
            pipeline = joblib.load(model_path)
    except Exception as e:  # 손상된 pickle은 다양한 예외를 낸다
        raise ArtifactError(f"모델 파일을 불러올 수 없습니다: {type(e).__name__}") from e

    classes = tuple(getattr(pipeline, "classes_", ()))
    if sorted(classes) != sorted(LABELS) or tuple(meta["labels"]) != classes:
        raise ArtifactError(f"모델의 분류 목록이 코드와 다릅니다: {classes}")

    return LoadedModel(pipeline=pipeline, meta=meta)
