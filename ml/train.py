"""수강생 메시지 분류 모델 학습·평가·저장.

    python -m ml.train --version v1

1. 데이터 확인: 분류 이름, 빈 문장, 중복, 확인용 문장(unseen)과 겹치는지, 클래스별 개수
2. 학습·검증 분리 (층화 80:20, 고정 시드)
3. 학습용 데이터 안에서만 5겹 교차검증으로 후보 비교 (TF-IDF는 Pipeline 안에서 접힘마다 새로 학습)
4. 검증 데이터로 평가하고 성능 기준(GATE, 검증 macro F1)을 넘을 때만 저장 (정확도, 정밀도, 재현율, F1, 혼동행렬, 오분류)
5. 확인용 문장(unseen)은 같은 지표로 계산해 기록만 한다. 저장 기준과 모델 선택에는 쓰지 않는다.
   반복 재학습 때 확인용 점수를 보고 모델을 고르면 확인용 세트가 사실상 검증 세트가 되기 때문이다 (PR #34 리뷰).

저장되는 모델은 "학습용 80%"로만 학습한 것이다. 그래야 기록된 검증 점수가 저장된 모델의 점수와 정확히 같고,
CI에서 같은 점수가 나오는지 다시 확인할 수 있다.
"""

import argparse
import csv
import hashlib
import json
import platform
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split
from sklearn.pipeline import Pipeline

from ml import artifact
from ml.labels import LABELS

ML_DIR = Path(__file__).resolve().parent
DATA_PATH = ML_DIR / "data" / "messages.csv"
UNSEEN_PATH = ML_DIR / "data" / "unseen.csv"
RANDOM_STATE = 42
VAL_SIZE = 0.2

# 성능 기준: 학습 전에 정함. 검증 데이터 점수만 쓴다 (확인용 세트는 참고값, 위 5번).
GATE = {"val_macro_f1": 0.80}

PARAM_GRID = [
    {  # 글자 단위 n-gram: 한국어는 조사·어미가 붙어 단어가 잘게 바뀌므로 형태소 분석기 없이도 강함
        "tfidf__analyzer": ["char_wb"],
        "tfidf__ngram_range": [(1, 3), (2, 4)],
        "clf__C": [1.0, 10.0],
    },
    {  # 비교용: 띄어쓰기 단어 단위
        "tfidf__analyzer": ["word"],
        "tfidf__token_pattern": [r"(?u)\b\w+\b"],  # 한 글자 단어도 포함
        "tfidf__ngram_range": [(1, 1), (1, 2)],
        "clf__C": [1.0, 10.0],
    },
]


def read_csv(path: Path) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = [(r["text"].strip(), r["label"].strip()) for r in csv.DictReader(f)]
    for i, (text, label) in enumerate(rows, start=2):
        if not text:
            sys.exit(f"{path.name}:{i} 빈 문장")
        if label not in LABELS:
            sys.exit(f"{path.name}:{i} 알 수 없는 분류 {label!r}")
    return rows


def data_sha256(path: Path) -> str:
    """데이터 파일 해시. Windows에서 줄바꿈이 CRLF로 바뀌어도 같은 값이 나오게 LF로 맞춘 뒤 계산한다."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def dedupe(rows):
    seen, out, dups = set(), [], []
    for text, label in rows:
        key = " ".join(text.split())
        if key in seen:
            dups.append(text)
            continue
        seen.add(key)
        out.append((text, label))
    return out, dups


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(sublinear_tf=True)),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=RANDOM_STATE)),
        ]
    )


def evaluate(pipeline, texts, labels) -> dict:
    pred = pipeline.predict(texts)
    proba = pipeline.predict_proba(texts).max(axis=1)
    report = classification_report(labels, pred, labels=list(LABELS), output_dict=True, zero_division=0)
    return {
        "n": len(texts),
        "accuracy": round(accuracy_score(labels, pred), 4),
        "macro_precision": round(report["macro avg"]["precision"], 4),
        "macro_recall": round(report["macro avg"]["recall"], 4),
        "macro_f1": round(report["macro avg"]["f1-score"], 4),
        "per_class": {
            lb: {k: round(report[lb][k], 4) for k in ("precision", "recall", "f1-score", "support")} for lb in LABELS
        },
        "confusion_matrix": {
            "labels": list(LABELS),
            "matrix": confusion_matrix(labels, pred, labels=list(LABELS)).tolist(),
        },
        "misclassified": [
            {"text": t, "true": y, "pred": p, "confidence": round(float(c), 3)}
            for t, y, p, c in zip(texts, labels, pred, proba, strict=True)
            if y != p
        ],
    }


def format_matrix(cm: dict) -> str:
    short = {"CANCELLATION": "CANCEL", "MAKEUP": "MAKEUP", "RESCHEDULE": "RESCHED", "GENERAL": "GENERAL"}
    head = "| 실제 \\ 예측 | " + " | ".join(short[lb] for lb in cm["labels"]) + " |"
    sep = "|---" * (len(cm["labels"]) + 1) + "|"
    rows = [
        f"| {short[lb]} | " + " | ".join(str(v) for v in row) + " |"
        for lb, row in zip(cm["labels"], cm["matrix"], strict=True)
    ]
    return "\n".join([head, sep, *rows])


def write_report(meta: dict, path: Path) -> None:
    lines = [
        f"# 메시지 분류 모델 학습 결과 ({meta['model_version']})",
        "",
        "> `python -m ml.train`이 자동으로 만드는 파일입니다. 직접 고치지 마세요.",
        "",
        f"- 학습 시각(UTC): {meta['created_at']}",
        f"- 환경: Python {meta['python_version']}, scikit-learn {meta['sklearn_version']}, "
        f"numpy {meta['numpy_version']}",
        f"- 데이터: `ml/data/messages.csv` {meta['data']['n_total']}건 "
        f"(SHA-256 앞 12자리 {meta['data']['sha256'][:12]})",
        f"- 분리: 학습 {meta['data']['n_train']} / 검증 {meta['data']['n_val']} (층화, 시드 {RANDOM_STATE})",
        f"- 확인용 문장: `ml/data/unseen.csv` {meta['data']['n_unseen']}건 (학습·모델 선택에 쓰지 않음)",
        f"- 선택된 설정: `{meta['best_params']}`",
        f"- 교차검증(학습 데이터 5겹) macro F1: {meta['metrics']['cv_macro_f1']}",
        "",
        "## 클래스별 개수 (중복 제거 후)",
        "",
        "| 분류 | 개수 |",
        "|---|---|",
        *[f"| {lb} | {meta['data']['distribution'][lb]} |" for lb in LABELS],
        "",
        "## 후보 비교 (교차검증 macro F1)",
        "",
        "| 설정 | 평균 | 표준편차 |",
        "|---|---|---|",
        *[f"| `{c['params']}` | {c['mean']} | {c['std']} |" for c in meta["candidates"]],
        "",
        "## 성능 기준",
        "",
        f"- 저장 기준: 검증 macro F1 ≥ {meta['gate']['val_macro_f1']} (확인용 점수는 기준에 쓰지 않는 참고값)",
        f"- 결과: **{'통과' if meta['gate']['passed'] else '미달'}**",
    ]
    for key, title in (
        ("val", "검증 데이터"),
        ("unseen", "확인용 문장 (참고값: 학습·모델 선택·저장 기준에 쓰지 않음)"),
    ):
        m = meta["metrics"][key]
        lines += [
            "",
            f"## {title}: {m['n']}건",
            "",
            f"- 정확도 {m['accuracy']} · macro 정밀도 {m['macro_precision']} · macro 재현율 {m['macro_recall']} "
            f"· macro F1 {m['macro_f1']}",
            "",
            "| 분류 | 정밀도 | 재현율 | F1 | 개수 |",
            "|---|---|---|---|---|",
            *[
                f"| {lb} | {v['precision']} | {v['recall']} | {v['f1-score']} | {int(v['support'])} |"
                for lb, v in m["per_class"].items()
            ],
            "",
            "혼동행렬",
            "",
            format_matrix(m["confusion_matrix"]),
            "",
            "오분류 사례",
            "",
        ]
        if m["misclassified"]:
            lines += ["| 문장 | 정답 | 예측 | 확신도 |", "|---|---|---|---|"]
            lines += [f"| {x['text']} | {x['true']} | {x['pred']} | {x['confidence']} |" for x in m["misclassified"]]
        else:
            lines.append("없음")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="수강생 메시지 분류 모델 학습")
    parser.add_argument("--version", default="v1", help="모델 버전 이름 (예: v1)")
    parser.add_argument("--out-dir", type=Path, default=artifact.DEFAULT_DIR)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔에서 한글 깨짐 방지

    rows, dups = dedupe(read_csv(DATA_PATH))
    unseen = read_csv(UNSEEN_PATH)
    overlap = {t for t, _ in rows} & {t for t, _ in unseen}
    if overlap:
        sys.exit(f"확인용 문장이 학습 데이터와 겹칩니다: {sorted(overlap)}")

    dist = Counter(label for _, label in rows)
    print(f"데이터 {len(rows)}건 (중복 제거 {len(dups)}건), 확인용 {len(unseen)}건")
    for lb in LABELS:
        print(f"  {lb:<13} {dist[lb]}")

    texts = [t for t, _ in rows]
    labels = [lb for _, lb in rows]
    x_train, x_val, y_train, y_val = train_test_split(
        texts, labels, test_size=VAL_SIZE, stratify=labels, random_state=RANDOM_STATE
    )

    search = GridSearchCV(
        build_pipeline(),
        PARAM_GRID,
        scoring="f1_macro",
        cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE),
        refit=True,
    )
    search.fit(x_train, y_train)
    pipeline = search.best_estimator_
    res = search.cv_results_
    candidates = sorted(
        (
            {
                "params": {
                    k.split("__")[1]: (list(v) if isinstance(v, tuple) else v)
                    for k, v in p.items()
                    if k != "tfidf__token_pattern"
                },
                "mean": round(float(m), 4),
                "std": round(float(s), 4),
            }
            for p, m, s in zip(res["params"], res["mean_test_score"], res["std_test_score"], strict=True)
        ),
        key=lambda c: -c["mean"],
    )
    best_params = candidates[0]["params"]
    print(f"선택: {best_params}  교차검증 macro F1 {search.best_score_:.4f}")

    val_m = evaluate(pipeline, x_val, y_val)
    unseen_m = evaluate(pipeline, [t for t, _ in unseen], [lb for _, lb in unseen])
    for name, m in (("검증  ", val_m), ("확인용", unseen_m)):
        print(f"{name} 정확도 {m['accuracy']}  macro F1 {m['macro_f1']}  오분류 {len(m['misclassified'])}건")

    passed = val_m["macro_f1"] >= GATE["val_macro_f1"]
    if not passed:
        print(f"성능 기준 미달 {GATE}. 모델을 저장하지 않습니다.")
        return 1

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / artifact.MODEL_FILENAME
    joblib.dump(pipeline, model_path, compress=3)

    meta = {
        "model_version": args.version,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python_version": platform.python_version(),
        "sklearn_version": sklearn.__version__,
        "numpy_version": numpy.__version__,
        "joblib_version": joblib.__version__,
        "sha256": artifact.sha256_of(model_path),
        "labels": [str(c) for c in pipeline.classes_],
        "data": {
            "sha256": data_sha256(DATA_PATH),
            "unseen_sha256": data_sha256(UNSEEN_PATH),
            "n_total": len(rows),
            "n_duplicates_removed": len(dups),
            "n_train": len(x_train),
            "n_val": len(x_val),
            "n_unseen": len(unseen),
            "distribution": {lb: dist[lb] for lb in LABELS},
        },
        "best_params": best_params,
        "candidates": candidates,
        "gate": {**GATE, "passed": passed},
        "metrics": {"cv_macro_f1": round(float(search.best_score_), 4), "val": val_m, "unseen": unseen_m},
        "val_samples": [[t, y] for t, y in zip(x_val, y_val, strict=True)],
    }
    meta_path = out / artifact.META_FILENAME
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_report(meta, out / "REPORT.md")
    print(f"저장: {model_path.name} ({model_path.stat().st_size:,} bytes), {meta_path.name}, REPORT.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
