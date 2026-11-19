# 메시지 분류 모델 학습 결과 (v2)

> `python -m ml.train`이 자동으로 만드는 파일입니다. 직접 고치지 마세요.

- 학습 시각(UTC): 2026-11-19T04:38:15Z
- 환경: Python 3.14.10, scikit-learn 1.9.1, numpy 2.5.5
- 데이터: `ml/data/messages.csv` 311건 (SHA-256 앞 12자리 2e68bc10d178)
- 분리: 학습 248 / 검증 63 (층화, 시드 42)
- 확인용 문장: `ml/data/unseen.csv` 60건 (학습·모델 선택에 쓰지 않음)
- 선택된 설정: `{'C': 10.0, 'analyzer': 'char_wb', 'ngram_range': [1, 3]}`
- 교차검증(학습 데이터 5겹) macro F1: 0.9471

## 클래스별 개수 (중복 제거 후)

| 분류 | 개수 |
|---|---|
| CANCELLATION | 80 |
| MAKEUP | 77 |
| RESCHEDULE | 79 |
| GENERAL | 75 |

## 후보 비교 (교차검증 macro F1)

| 설정 | 평균 | 표준편차 |
|---|---|---|
| `{'C': 10.0, 'analyzer': 'char_wb', 'ngram_range': [1, 3]}` | 0.9471 | 0.0376 |
| `{'C': 1.0, 'analyzer': 'char_wb', 'ngram_range': [1, 3]}` | 0.9317 | 0.0369 |
| `{'C': 1.0, 'analyzer': 'char_wb', 'ngram_range': [2, 4]}` | 0.9117 | 0.0346 |
| `{'C': 10.0, 'analyzer': 'char_wb', 'ngram_range': [2, 4]}` | 0.9111 | 0.0206 |
| `{'C': 10.0, 'analyzer': 'word', 'ngram_range': [1, 1]}` | 0.8814 | 0.0462 |
| `{'C': 10.0, 'analyzer': 'word', 'ngram_range': [1, 2]}` | 0.8703 | 0.0271 |
| `{'C': 1.0, 'analyzer': 'word', 'ngram_range': [1, 1]}` | 0.8619 | 0.0415 |
| `{'C': 1.0, 'analyzer': 'word', 'ngram_range': [1, 2]}` | 0.8414 | 0.0404 |

## 성능 기준

- 검증 macro F1 ≥ 0.8, 확인용 macro F1 ≥ 0.7
- 결과: **통과**

## 검증 데이터: 63건

- 정확도 0.9365 · macro 정밀도 0.9387 · macro 재현율 0.9354 · macro F1 0.9357

| 분류 | 정밀도 | 재현율 | F1 | 개수 |
|---|---|---|---|---|
| CANCELLATION | 0.8889 | 1.0 | 0.9412 | 16 |
| MAKEUP | 1.0 | 0.9375 | 0.9677 | 16 |
| RESCHEDULE | 0.9375 | 0.9375 | 0.9375 | 16 |
| GENERAL | 0.9286 | 0.8667 | 0.8966 | 15 |

혼동행렬

| 실제 \ 예측 | CANCEL | MAKEUP | RESCHED | GENERAL |
|---|---|---|---|---|
| CANCEL | 16 | 0 | 0 | 0 |
| MAKEUP | 0 | 15 | 0 | 1 |
| RESCHED | 1 | 0 | 15 | 0 |
| GENERAL | 1 | 0 | 1 | 13 |

오분류 사례

| 문장 | 정답 | 예측 | 확신도 |
|---|---|---|---|
| 레슨 끝나고 상담 잠깐 가능할까요 | GENERAL | RESCHEDULE | 0.323 |
| 쌤 이번에 쉰 거 언제 다시 해요? | MAKEUP | GENERAL | 0.645 |
| 이번 주 수업 너무 좋았어요 | GENERAL | CANCELLATION | 0.471 |
| 쌤 오늘 수업은 학교 근처 카페에서 해도 될까요? | RESCHEDULE | CANCELLATION | 0.455 |

## 확인용 문장 (학습에 쓰지 않은 새 문장): 60건

- 정확도 0.9833 · macro 정밀도 0.9844 · macro 재현율 0.9833 · macro F1 0.9833

| 분류 | 정밀도 | 재현율 | F1 | 개수 |
|---|---|---|---|---|
| CANCELLATION | 1.0 | 0.9333 | 0.9655 | 15 |
| MAKEUP | 1.0 | 1.0 | 1.0 | 15 |
| RESCHEDULE | 1.0 | 1.0 | 1.0 | 15 |
| GENERAL | 0.9375 | 1.0 | 0.9677 | 15 |

혼동행렬

| 실제 \ 예측 | CANCEL | MAKEUP | RESCHED | GENERAL |
|---|---|---|---|---|
| CANCEL | 14 | 0 | 0 | 1 |
| MAKEUP | 0 | 15 | 0 | 0 |
| RESCHED | 0 | 0 | 15 | 0 |
| GENERAL | 0 | 0 | 0 | 15 |

오분류 사례

| 문장 | 정답 | 예측 | 확신도 |
|---|---|---|---|
| 쌤 내일 수업 빼주세요! 갑자기 가족 모임이 생겼어요 | CANCELLATION | GENERAL | 0.395 |
