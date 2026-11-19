import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from memos import classifier
from ml import artifact, verify
from ml.labels import LABELS
from ml.train import UNSEEN_PATH, read_csv


class BrokenArtifactMixin:
    """임시 폴더에 모델 파일을 복사해 손상·누락 상황을 만든다."""

    def setUp(self):
        super().setUp()
        classifier.clear_cache()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(classifier.clear_cache)
        src = Path(settings.MEMO_CLASSIFIER_PATH)
        self.model_path = self.tmp / src.name
        self.meta_path = artifact.meta_path_for(self.model_path)
        shutil.copy(src, self.model_path)
        shutil.copy(artifact.meta_path_for(src), self.meta_path)

    def edit_meta(self, **changes):
        meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        meta.update(changes)
        self.meta_path.write_text(json.dumps(meta), encoding="utf-8")

    def assert_unavailable(self, message_part):
        with override_settings(MEMO_CLASSIFIER_PATH=self.model_path), self.assertLogs("memos.classifier", "ERROR"):
            with self.assertRaisesMessage(classifier.MemoClassifierUnavailable, message_part):
                classifier.classify("내일 수업 취소해 주세요")


class ClassifyTests(SimpleTestCase):
    def setUp(self):
        classifier.clear_cache()

    def test_returns_label_confidence_and_version(self):
        p = classifier.classify("다음 주 수요일 수업을 못 갈 것 같아서 금요일에 보강하고 싶어요")
        self.assertIn(p.label, LABELS)
        self.assertEqual(p.label, "MAKEUP")
        self.assertEqual(p.display, "보강 요청")
        self.assertTrue(0 < p.confidence <= 1)
        self.assertEqual(p.model_version, classifier.get_model().version)

    def test_unseen_sentences_meet_gate(self):
        """학습에 쓰지 않은 문장으로 실제 추론. 문장별 정답이 아니라 전체 정확도로 본다 (오분류는 REPORT.md에 기록)."""
        rows = read_csv(UNSEEN_PATH)
        correct = sum(classifier.classify(t).label == y for t, y in rows)
        self.assertGreaterEqual(correct / len(rows), classifier.get_model().meta["gate"]["unseen_macro_f1"])

    def test_empty_memo_is_rejected(self):
        for text in ("", "   ", "\n\t", None):
            with self.subTest(text=text), self.assertRaises(ValueError):
                classifier.classify(text)

    def test_long_memo_is_truncated(self):
        p = classifier.classify("수업 시간 변경 부탁드려요 " * 200)
        self.assertIn(p.label, LABELS)

    def test_model_is_loaded_once(self):
        with mock.patch.object(artifact, "load", wraps=artifact.load) as load:
            classifier.classify("오늘 수업 정말 잘했어요")
            classifier.classify("내일 수업 취소해 주세요")
        self.assertEqual(load.call_count, 1)

    def test_prediction_error_is_wrapped(self):
        model = classifier.get_model()
        with mock.patch.object(model.pipeline, "predict_proba", side_effect=RuntimeError("boom")):
            with self.assertRaises(classifier.MemoClassifierUnavailable), self.assertLogs("memos.classifier", "ERROR"):
                classifier.classify("내일 수업 취소해 주세요")


class ArtifactFailureTests(BrokenArtifactMixin, SimpleTestCase):
    def test_missing_model_file(self):
        self.model_path.unlink()
        self.assert_unavailable("모델 파일이 없습니다")

    def test_missing_metadata(self):
        self.meta_path.unlink()
        self.assert_unavailable("메타데이터가 없습니다")

    def test_corrupted_model_file_detected_by_hash(self):
        self.model_path.write_bytes(b"not a model")
        self.assert_unavailable("해시")

    def test_corrupted_pickle_with_matching_hash(self):
        """해시까지 맞춘 깨진 파일도 불러오기 단계에서 걸러진다."""
        self.model_path.write_bytes(b"\x80\x04garbage")
        self.edit_meta(sha256=artifact.sha256_of(self.model_path))
        self.assert_unavailable("불러올 수 없습니다")

    def test_sklearn_version_mismatch(self):
        self.edit_meta(sklearn_version="0.0.1")
        self.assert_unavailable("버전 불일치")

    def test_label_mismatch(self):
        self.edit_meta(labels=["A", "B", "C", "D"])
        self.assert_unavailable("분류 목록")

    def test_failure_is_not_cached(self):
        """파일을 고치면 재시작 없이 다음 요청부터 동작한다."""
        backup = self.model_path.read_bytes()
        self.model_path.unlink()
        self.assert_unavailable("모델 파일이 없습니다")
        self.model_path.write_bytes(backup)
        with override_settings(MEMO_CLASSIFIER_PATH=self.model_path):
            self.assertEqual(classifier.classify("내일 수업 취소해 주세요").label, "CANCELLATION")


class ModelCompatibilityTests(SimpleTestCase):
    def test_committed_model_passes_verify(self):
        """저장소의 모델이 현재 코드·라이브러리·데이터와 맞고, 기록된 점수가 재현되며 기준을 넘는다."""
        with mock.patch("builtins.print"):
            self.assertEqual(verify.main(settings.MEMO_CLASSIFIER_PATH), 0)


class HealthzTests(BrokenArtifactMixin, TestCase):
    def test_ok(self):
        res = self.client.get(reverse("healthz"))
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["db"], "ok")
        self.assertEqual(body["memo_classifier"]["status"], "ok")
        self.assertEqual(body["memo_classifier"]["version"], classifier.get_model().version)
        self.assertIn("no-cache", res["Cache-Control"])

    def test_missing_model_keeps_service_up(self):
        self.model_path.unlink()
        with override_settings(MEMO_CLASSIFIER_PATH=self.model_path), self.assertLogs("memos.classifier", "ERROR"):
            res = self.client.get(reverse("healthz"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["memo_classifier"], {"status": "unavailable"})

    def test_db_error_returns_503(self):
        with mock.patch.object(connection, "cursor", side_effect=Exception("db down")):
            res = self.client.get(reverse("healthz"))
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["db"], "error")

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post(reverse("healthz")).status_code, 405)
