"""학습 환경과 서비스 실행 환경의 예측 일치(parity) 확인. 태그 배포 파이프라인(.github/workflows/deploy.yml)에서 쓴다.

    python -m ml.parity dump    --model <joblib> --out expected.json      # 학습 환경(train 작업)에서
    python -m ml.parity compare --model <joblib> --expected expected.json --result result.json
                                                                         # 서비스 환경(parity·image 작업)에서

비교하는 것 (같은 모델 파일 + 같은 검증 입력):
- 모델 파일 SHA-256: 두 환경이 같은 파일을 쓰는지 (다르면 바로 실패)
- 입력: 모델 메타데이터의 검증 문장(val_samples) + ml/data/unseen.csv. 입력 목록의 해시가 같아야 함
- 문장마다 예측 분류: **모두 같아야** 함 (허용 오차 없음)
- 문장마다 분류별 확률: 차이의 최댓값 ≤ PROBA_TOLERANCE (1e-6)
- 검증·확인용 macro F1: 차이 ≤ ml.verify.TOLERANCE (1e-4, 기존 점수 재현 기준과 같음)

허용 오차를 둔 이유: 같은 파일이라도 CPU·BLAS 빌드에 따라 부동소수 끝자리가 다를 수 있다. 분류 결과와 점수는 정확히
같아야 하고, 확률은 사람이 볼 수 있는 자릿수(소수 셋째 자리, inbox.classifier)보다 훨씬 작은 차이만 허용한다.
"""

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

from ml import artifact
from ml.train import UNSEEN_PATH, evaluate, read_csv
from ml.verify import TOLERANCE as SCORE_TOLERANCE

PROBA_TOLERANCE = 1e-6


def environment() -> dict:
    import joblib
    import numpy
    import scipy
    import sklearn

    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "sklearn": sklearn.__version__,
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "joblib": joblib.__version__,
    }


def inputs_for(model) -> dict:
    """비교 입력: 검증 문장(메타데이터에 기록된 그대로) + 확인용 문장(저장소 파일)."""
    unseen = read_csv(UNSEEN_PATH)
    return {
        "val": [(t, y) for t, y in model.meta["val_samples"]],
        "unseen": unseen,
    }


def inputs_sha256(inputs: dict) -> str:
    payload = json.dumps({k: [list(p) for p in v] for k, v in inputs.items()}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def predict(model, inputs: dict) -> dict:
    classes = [str(c) for c in model.pipeline.classes_]
    out = {"classes": classes, "sets": {}}
    for name, rows in inputs.items():
        texts = [t for t, _ in rows]
        labels = [y for _, y in rows]
        proba = model.pipeline.predict_proba(texts)
        pred = [classes[int(i)] for i in proba.argmax(axis=1)]
        out["sets"][name] = {
            "n": len(texts),
            "pred": pred,
            "proba": [[float(v) for v in row] for row in proba],
            "macro_f1": evaluate(model.pipeline, texts, labels)["macro_f1"],
        }
    return out


def snapshot(model_path: Path) -> dict:
    model = artifact.load(model_path)
    inputs = inputs_for(model)
    return {
        "model_version": model.version,
        "model_sha256": model.meta["sha256"],
        "inputs_sha256": inputs_sha256(inputs),
        "environment": environment(),
        "predictions": predict(model, inputs),
    }


def compare(expected: dict, actual: dict) -> dict:
    """두 스냅숏을 비교한 결과. passed가 False면 checks에 이유가 있다."""
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    add("모델 파일 SHA-256", expected["model_sha256"] == actual["model_sha256"], actual["model_sha256"])
    add("모델 버전", expected["model_version"] == actual["model_version"], actual["model_version"])
    add("검증 입력 해시", expected["inputs_sha256"] == actual["inputs_sha256"], actual["inputs_sha256"][:16])
    ep, ap = expected["predictions"], actual["predictions"]
    add("분류 목록·순서", ep["classes"] == ap["classes"], ",".join(ap["classes"]))
    for name in ("val", "unseen"):
        e, a = ep["sets"].get(name), ap["sets"].get(name)
        if not e or not a or e["n"] != a["n"]:
            add(f"{name} 입력 개수", False, f"기대 {e and e['n']}, 실제 {a and a['n']}")
            continue
        diff_labels = sum(1 for x, y in zip(e["pred"], a["pred"], strict=True) if x != y)
        add(f"{name} 예측 분류 일치 ({a['n']}건)", diff_labels == 0, f"다른 예측 {diff_labels}건")
        max_diff = max(
            (abs(x - y) for re, ra in zip(e["proba"], a["proba"], strict=True) for x, y in zip(re, ra, strict=True)),
            default=0.0,
        )
        add(f"{name} 확률 최대 차이 ≤ {PROBA_TOLERANCE}", max_diff <= PROBA_TOLERANCE, f"{max_diff:.3g}")
        f1_diff = abs(e["macro_f1"] - a["macro_f1"])
        add(
            f"{name} macro F1 차이 ≤ {SCORE_TOLERANCE}",
            f1_diff <= SCORE_TOLERANCE,
            f"학습 {e['macro_f1']} / 비교 {a['macro_f1']}",
        )
    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "expected_environment": expected["environment"],
        "actual_environment": actual["environment"],
        "tolerances": {"proba": PROBA_TOLERANCE, "macro_f1": SCORE_TOLERANCE, "labels": 0},
    }


def markdown(result: dict, expected_name: str, actual_name: str) -> str:
    """GitHub 실행 요약용 표."""
    ee, ae = result["expected_environment"], result["actual_environment"]
    lines = [
        f"| 환경 | {expected_name} | {actual_name} |",
        "|---|---|---|",
        *[
            f"| {k} | `{ee.get(k, '-')}` | `{ae.get(k, '-')}` |"
            for k in ("python", "platform", "machine", "sklearn", "numpy", "scipy", "joblib")
        ],
        "",
        "| 비교 항목 | 결과 | 값 |",
        "|---|---|---|",
        *[f"| {c['name']} | {'통과' if c['passed'] else '**실패**'} | {c['detail']} |" for c in result["checks"]],
        "",
        f"**parity: {'통과' if result['passed'] else '실패'}**",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="학습 환경과 서비스 환경의 예측 일치 확인")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="이 환경의 예측을 기록")
    d.add_argument("--model", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("compare", help="기록과 이 환경의 예측을 비교")
    c.add_argument("--model", type=Path, required=True)
    c.add_argument("--expected", type=Path, required=True)
    c.add_argument("--result", type=Path, help="비교 결과 JSON을 쓸 경로")
    c.add_argument("--summary", type=Path, help="요약 표(Markdown)를 덧붙일 경로")
    c.add_argument("--expected-name", default="학습(train)")
    c.add_argument("--actual-name", default="비교 환경")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        snap = snapshot(args.model)
    except artifact.ArtifactError as e:
        print(f"실패: {e}")
        return 1
    if args.cmd == "dump":
        args.out.write_text(json.dumps(snap, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"기록: {args.out} (모델 {snap['model_version']}, {snap['environment']['platform']})")
        return 0

    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    result = compare(expected, snap)
    if args.result:
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    table = markdown(result, args.expected_name, args.actual_name)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(table)
    print(table)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
