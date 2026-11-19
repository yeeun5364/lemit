"""저장된 모델 검증 (CI·배포 전 실행). 다시 학습하지 않는다.

    python -m ml.verify

확인 항목
1. 불러오기: 파일 존재, 해시, scikit-learn 버전, 분류 목록 (ml.artifact.load)
2. 데이터 일치: 지금 저장소의 학습·확인용 CSV가 모델을 학습할 때와 같은지 (데이터만 바꾸고 재학습을 잊은 경우 실패)
3. 재현: 기록된 검증 문장·확인용 문장을 다시 예측해 macro F1이 기록값과 같은지
4. 성능 기준: 기록된 GATE를 넘는지
"""

import sys

from ml import artifact
from ml.train import DATA_PATH, UNSEEN_PATH, data_sha256, evaluate, read_csv

TOLERANCE = 1e-4


def main(model_path=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        model = artifact.load(model_path)
    except artifact.ArtifactError as e:
        print(f"실패: {e}")
        return 1
    meta = model.meta
    print(f"모델 {model.version} (scikit-learn {meta['sklearn_version']}, 학습 {meta['created_at']})")

    errors = []
    if data_sha256(DATA_PATH) != meta["data"]["sha256"]:
        errors.append("ml/data/memos.csv가 학습 때와 다릅니다. python -m ml.train으로 다시 학습하세요")
    if data_sha256(UNSEEN_PATH) != meta["data"]["unseen_sha256"]:
        errors.append("ml/data/unseen.csv가 학습 때와 다릅니다. python -m ml.train으로 다시 학습하세요")

    val = meta["val_samples"]
    unseen = read_csv(UNSEEN_PATH)
    results = {
        "val": evaluate(model.pipeline, [t for t, _ in val], [y for _, y in val]),
        "unseen": evaluate(model.pipeline, [t for t, _ in unseen], [y for _, y in unseen]),
    }
    for key, m in results.items():
        recorded = meta["metrics"][key]["macro_f1"]
        print(f"{key:<7} macro F1 {m['macro_f1']} (기록 {recorded}, 기준 {meta['gate'][f'{key}_macro_f1']})")
        if abs(m["macro_f1"] - recorded) > TOLERANCE:
            errors.append(f"{key} 점수가 기록과 다릅니다 (재현 실패)")
        if m["macro_f1"] < meta["gate"][f"{key}_macro_f1"]:
            errors.append(f"{key} 점수가 성능 기준 미달")

    for e in errors:
        print(f"실패: {e}")
    if not errors:
        print("통과")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
