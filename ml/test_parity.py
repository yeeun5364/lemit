"""ml.parity 테스트 (태그 파이프라인 3. parity).

같은 모델·같은 입력이면 통과, 예측·확률·점수·모델·입력이 다르면 실패.
"""

import copy
import json
import tempfile
from pathlib import Path
from unittest import TestCase

from ml import artifact, parity


class ParityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model_path = artifact.DEFAULT_DIR / artifact.MODEL_FILENAME
        cls.snap = parity.snapshot(cls.model_path)

    def mutated(self, change):
        exp = copy.deepcopy(self.snap)
        change(exp)
        return parity.compare(exp, self.snap)

    def failed(self, result):
        return [c["name"] for c in result["checks"] if not c["passed"]]

    def test_same_model_and_inputs_pass(self):
        result = parity.compare(copy.deepcopy(self.snap), self.snap)
        self.assertTrue(result["passed"], result["checks"])
        names = [c["name"] for c in result["checks"]]
        for expected in ("모델 파일 SHA-256", "검증 입력 해시", "val 예측 분류 일치", "unseen macro F1 차이"):
            self.assertTrue(any(n.startswith(expected) for n in names), names)
        self.assertEqual(result["tolerances"], {"proba": 1e-6, "macro_f1": 1e-4, "labels": 0})

    def test_inputs_are_validation_samples_and_unseen(self):
        sets = self.snap["predictions"]["sets"]
        meta = json.loads(artifact.meta_path_for(self.model_path).read_text(encoding="utf-8"))
        self.assertEqual(sets["val"]["n"], len(meta["val_samples"]))
        self.assertEqual(sets["unseen"]["n"], meta["data"]["n_unseen"])
        self.assertEqual(sets["val"]["macro_f1"], meta["metrics"]["val"]["macro_f1"])  # 기록된 검증 점수 재현

    def test_differences_fail(self):
        def flip_label(e):
            pred = e["predictions"]["sets"]["val"]["pred"]
            pred[0] = "GENERAL" if pred[0] != "GENERAL" else "MAKEUP"

        def nudge_proba(e):
            e["predictions"]["sets"]["unseen"]["proba"][0][0] += 2 * parity.PROBA_TOLERANCE

        def shift_f1(e):
            e["predictions"]["sets"]["val"]["macro_f1"] += 0.001

        cases = {
            "예측 분류": (flip_label, "val 예측 분류 일치"),
            "확률": (nudge_proba, "unseen 확률 최대 차이"),
            "점수": (shift_f1, "val macro F1 차이"),
            "모델 파일": (lambda e: e.update(model_sha256="0" * 64), "모델 파일 SHA-256"),
            "입력": (lambda e: e.update(inputs_sha256="0" * 64), "검증 입력 해시"),
        }
        for label, (change, check) in cases.items():
            with self.subTest(label):
                result = self.mutated(change)
                self.assertFalse(result["passed"])
                self.assertTrue(any(n.startswith(check) for n in self.failed(result)), self.failed(result))

    def test_tiny_float_difference_within_tolerance_passes(self):
        result = self.mutated(
            lambda e: e["predictions"]["sets"]["val"]["proba"][0].__setitem__(
                0, e["predictions"]["sets"]["val"]["proba"][0][0] + 1e-9
            )
        )
        self.assertTrue(result["passed"], self.failed(result))

    def test_cli_dump_and_compare(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "expected.json"
            summary = Path(d) / "summary.md"
            self.assertEqual(parity.main(["dump", "--model", str(self.model_path), "--out", str(out)]), 0)
            rc = parity.main(
                ["compare", "--model", str(self.model_path), "--expected", str(out), "--summary", str(summary)]
            )
            self.assertEqual(rc, 0)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("| sklearn |", text)
            self.assertIn("**parity: 통과**", text)
            bad = json.loads(out.read_text(encoding="utf-8"))
            bad["predictions"]["sets"]["val"]["macro_f1"] = 0.0
            out.write_text(json.dumps(bad), encoding="utf-8")
            rc = parity.main(["compare", "--model", str(self.model_path), "--expected", str(out)])
            self.assertEqual(rc, 1)
