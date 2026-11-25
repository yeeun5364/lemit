"""메시지 분류 API POST /api/v1/messages/classify/ (#101)."""

import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from django.test import override_settings

from api import scopes
from api.test_auth import ApiTestCase, make_key
from inbox import classifier
from inbox.models import Conversation, Message
from lessons.models import Enrollment, LessonSession, SessionChangeLog
from ml.labels import LABELS
from students.models import Student

URL = "/api/v1/messages/classify/"


class ClassifyTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        classifier.clear_cache()
        self.addCleanup(classifier.clear_cache)
        self.raw, _ = make_key(self.teacher, [scopes.READ])

    def classify(self, body, raw=None, content_type="application/json"):
        raw = self.raw if raw is None else raw
        headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"} if raw else {}
        return self.client.post(URL, body, content_type=content_type, **headers)


class ClassifySuccessTests(ClassifyTestCase):
    def test_returns_label_scores_and_model_version(self):
        response = self.classify({"text": "다음 주 수요일 수업을 못 갈 것 같아서 금요일에 보강하고 싶어요"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(
            set(body),
            {"label", "label_display", "confidence", "is_low_confidence", "scores", "truncated", "model_version"},
        )
        self.assertEqual(body["label"], "MAKEUP")  # inbox 분류 테스트와 같은 문장·같은 결과
        self.assertEqual(body["label_display"], "보강 요청")
        self.assertEqual(set(body["scores"]), set(LABELS))
        self.assertAlmostEqual(sum(body["scores"].values()), 1, delta=0.01)
        self.assertEqual(body["label"], max(body["scores"], key=body["scores"].get))
        self.assertEqual(body["confidence"], body["scores"][body["label"]])
        self.assertEqual(list(body["scores"].values()), sorted(body["scores"].values(), reverse=True))  # 높은 순
        self.assertFalse(body["truncated"])
        self.assertEqual(body["model_version"], classifier.get_model().version)

    def test_long_text_is_truncated_at_500(self):
        self.assertFalse(self.classify({"text": "가" * 500}).json()["truncated"])
        self.assertTrue(self.classify({"text": "수업 시간 변경 부탁드려요 " * 60}).json()["truncated"])

    def test_surrounding_spaces_do_not_count(self):
        response = self.classify({"text": "  " + "가" * 1000 + "  \n"})
        self.assertEqual(response.status_code, 200)

    def test_low_confidence_flag(self):
        fake = SimpleNamespace(
            version="test",
            pipeline=SimpleNamespace(
                classes_=np.array(LABELS), predict_proba=lambda texts: np.array([[0.1, 0.25, 0.45, 0.2]])
            ),
        )
        with mock.patch.object(classifier, "get_model", return_value=fake):
            body = self.classify({"text": "음 그게요"}).json()
        self.assertEqual(body["label"], "RESCHEDULE")
        self.assertEqual(body["confidence"], 0.45)
        self.assertTrue(body["is_low_confidence"])
        self.assertEqual(list(body["scores"]), ["RESCHEDULE", "MAKEUP", "GENERAL", "CANCELLATION"])

    def test_nothing_is_saved(self):
        student = Student.objects.create(teacher=self.teacher, name="김하늘")
        Conversation.objects.create(teacher=self.teacher, student=student)
        models = (Message, Conversation, LessonSession, Enrollment, SessionChangeLog, Student)
        before = {m: m.objects.count() for m in models}
        self.assertEqual(self.classify({"text": "내일 수업 취소해 주세요"}).status_code, 200)
        self.assertEqual({m: m.objects.count() for m in models}, before)

    def test_message_text_is_not_logged(self):
        secret = "비밀문장-김하늘-010-1234-5678"
        records = []
        handler = logging.Handler(level=logging.DEBUG)
        handler.emit = records.append
        # "api" 로거는 상위로 전달하지 않으므로 따로 붙인다
        loggers = [logging.getLogger(name) for name in ("", "api", "inbox", "django")]
        for lg in loggers:
            lg.addHandler(handler)
        try:
            self.classify({"text": f"{secret} 내일 수업 취소해 주세요"})
            self.classify({"text": secret * 100})  # 오류 응답 경로도
        finally:
            for lg in loggers:
                lg.removeHandler(handler)
        output = "\n".join(r.getMessage() for r in records)
        self.assertIn("/api/v1/messages/classify/", output)  # 접근 로그는 남는다
        self.assertNotIn(secret, output)


class ClassifyRejectTests(ClassifyTestCase):
    def assertFieldError(self, response, code, message_part=None):
        error = self.assertError(response, 400, "validation_error")
        self.assertEqual(error["fields"][0]["field"], "text")
        self.assertEqual(error["fields"][0]["code"], code)
        if message_part:
            self.assertIn(message_part, error["fields"][0]["message"])
        return error

    def test_invalid_text(self):
        self.assertFieldError(self.classify({}), "required")
        self.assertFieldError(self.classify({"text": ""}), "blank")
        self.assertFieldError(self.classify({"text": "  \n\t "}), "blank")
        self.assertFieldError(self.classify({"text": None}), "null")
        self.assertFieldError(self.classify({"text": 123}), "invalid", "문자열")
        self.assertFieldError(self.classify({"text": ["a"]}), "invalid", "문자열")
        self.assertFieldError(self.classify({"text": "가" * 1001}), "max_length", "현재 1001자")

    def test_body_must_be_json_object(self):
        self.assertError(self.classify('{"text": "수업', content_type="application/json"), 400, "invalid_json")
        self.assertError(
            self.classify({"text": "a"}, content_type="application/x-www-form-urlencoded"),
            415,
            "unsupported_media_type",
        )
        error = self.assertError(self.classify(["내일 취소"]), 400, "validation_error")
        self.assertIsNone(error["fields"][0]["field"])

    def test_auth_and_scope(self):
        self.assertError(self.classify({"text": "a"}, raw=""), 401, "not_authenticated")
        expired_raw, expired = make_key(self.teacher)
        expired.__class__.objects.filter(pk=expired.pk).update(
            created_at=expired.created_at.replace(year=2020), expires_at=expired.created_at.replace(year=2021)
        )
        self.assertError(self.classify({"text": "a"}, raw=expired_raw), 401, "api_key_expired")
        send_only, _ = make_key(self.teacher, [scopes.MESSAGES_SEND])
        error = self.assertError(self.classify({"text": "a"}, raw=send_only), 403, "insufficient_scope")
        self.assertIn("읽기", error["message"])

    def test_get_not_allowed(self):
        response = self.client.get(URL, HTTP_AUTHORIZATION=f"Bearer {self.raw}")
        self.assertError(response, 405, "method_not_allowed")
        self.assertEqual(response["Allow"].replace(" ", ""), "POST,OPTIONS")


class ClassifyModelUnavailableTests(ClassifyTestCase):
    def test_missing_model_is_retryable_503_without_internals(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "secret-dir" / "model.joblib"
            with override_settings(MESSAGE_CLASSIFIER_PATH=missing), self.assertLogs("inbox.classifier", "ERROR"):
                response = self.classify({"text": "내일 수업 취소해 주세요"})
        error = self.assertError(response, 503, "model_unavailable")
        self.assertTrue(error["retryable"])
        content = response.content.decode()
        self.assertNotIn("secret-dir", content)
        self.assertNotIn("joblib", content)

    def test_prediction_error_is_503(self):
        broken = SimpleNamespace(version="x", pipeline=SimpleNamespace(classes_=np.array(LABELS)))
        broken.pipeline.predict_proba = mock.Mock(side_effect=RuntimeError("/srv/model 손상"))
        with mock.patch.object(classifier, "get_model", return_value=broken), self.assertLogs("inbox.classifier"):
            response = self.classify({"text": "내일 수업 취소해 주세요"})
        self.assertError(response, 503, "model_unavailable")
        self.assertNotIn("/srv/model", response.content.decode())


class ClassifyDocsTests(ClassifyTestCase):
    def test_schema_and_error_docs(self):
        logging.disable(logging.CRITICAL)
        try:
            schema = self.client.get("/api/schema/").content.decode()
            errors = self.client.get("/api/docs/errors/")
        finally:
            logging.disable(logging.NOTSET)
        self.assertIn("/api/v1/messages/classify/", schema)
        self.assertIn("RESCHEDULE", schema)  # 예시
        self.assertContains(errors, 'id="model_unavailable"')


class ClassifyAbnormalOutputTests(ClassifyTestCase):
    def test_abnormal_model_output_is_503(self):
        fake = SimpleNamespace(
            version="x",
            pipeline=SimpleNamespace(
                classes_=np.array(LABELS), predict_proba=lambda t: np.array([[np.nan, 0.5, 0.5, 0.0]])
            ),
        )
        with mock.patch.object(classifier, "get_model", return_value=fake), self.assertLogs("inbox", "ERROR"):
            response = self.classify({"text": "내일 수업 취소해 주세요"})
        error = self.assertError(response, 503, "model_unavailable")
        self.assertTrue(error["retryable"])
        self.assertNotIn("NaN", response.content.decode())


class ClassifySchemaTests(ClassifyTestCase):
    """OpenAPI 문서: 요청 타입과 오류 응답 (PR #114 리뷰 P2)."""

    def schema(self):
        logging.disable(logging.CRITICAL)
        try:
            response = self.client.get("/api/schema/", {"format": "json"})
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def resolve(self, schema, node):
        while "$ref" in node:
            node = schema["components"]["schemas"][node["$ref"].rsplit("/", 1)[-1]]
        return node

    def test_request_text_is_documented_as_string_after_trim(self):
        schema = self.schema()
        body = schema["paths"][URL]["post"]["requestBody"]["content"]["application/json"]["schema"]
        request = self.resolve(schema, body)
        text = request["properties"]["text"]
        self.assertEqual(text["type"], "string")
        self.assertIn("앞뒤 공백을 뺀 길이", text["description"])
        self.assertNotIn("maxLength", text)  # 보낸 문자열 길이가 아니라 공백 제거 후 길이로 검사하므로
        self.assertIn("text", request["required"])

    def test_error_responses_documented(self):
        schema = self.schema()
        responses = schema["paths"][URL]["post"]["responses"]
        self.assertEqual(set(responses), {"200", "400", "401", "403", "415", "429", "500", "503"})
        expected = {"code", "message", "hint", "fields", "retryable", "docs", "request_id"}
        for status in ("400", "401", "403", "415", "429", "500", "503"):
            with self.subTest(status=status):
                error = self.resolve(schema, responses[status]["content"]["application/json"]["schema"])
                body = self.resolve(schema, error["properties"]["error"])
                self.assertTrue(expected <= set(body["properties"]))
        self.assertIn("model_unavailable", responses["503"]["description"])
        self.assertIn("validation_error", responses["400"]["description"])
        self.assertIn("invalid_json", responses["400"]["description"])
        self.assertIn("insufficient_scope", responses["403"]["description"])
        me = schema["paths"]["/api/v1/me/"]["get"]["responses"]
        self.assertTrue({"200", "401", "403", "429", "500"} <= set(me))
