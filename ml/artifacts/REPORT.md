# 메모 분류 모델 학습 결과 (v1)

> `python -m ml.train`이 자동으로 만드는 파일입니다. 직접 고치지 마세요.

- 학습 시각(UTC): 2026-11-19T04:20:14Z
- 환경: Python 3.14.10, scikit-learn 1.9.1, numpy 2.5.5
- 데이터: `ml/data/memos.csv` 296건 (SHA-256 앞 12자리 d9b99d104ee4)
- 분리: 학습 236 / 검증 60 (층화, 시드 42)
- 확인용 문장: `ml/data/unseen.csv` 48건 (학습·모델 선택에 쓰지 않음)
- 선택된 설정: `{'C': 10.0, 'analyzer': 'char_wb', 'ngram_range': [1, 3]}`
- 교차검증(학습 데이터 5겹) macro F1: 0.9491

## 클래스별 개수 (중복 제거 후)

| 분류 | 개수 |
|---|---|
| CANCELLATION | 80 |
| MAKEUP | 74 |
| RESCHEDULE | 69 |
| GENERAL | 73 |

## 후보 비교 (교차검증 macro F1)

| 설정 | 평균 | 표준편차 |
|---|---|---|
| `{'C': 10.0, 'analyzer': 'char_wb', 'ngram_range': [1, 3]}` | 0.9491 | 0.0215 |
| `{'C': 1.0, 'analyzer': 'char_wb', 'ngram_range': [1, 3]}` | 0.9324 | 0.0248 |
| `{'C': 10.0, 'analyzer': 'char_wb', 'ngram_range': [2, 4]}` | 0.919 | 0.0199 |
| `{'C': 1.0, 'analyzer': 'char_wb', 'ngram_range': [2, 4]}` | 0.9106 | 0.0155 |
| `{'C': 10.0, 'analyzer': 'word', 'ngram_range': [1, 1]}` | 0.8409 | 0.0412 |
| `{'C': 10.0, 'analyzer': 'word', 'ngram_range': [1, 2]}` | 0.8406 | 0.0462 |
| `{'C': 1.0, 'analyzer': 'word', 'ngram_range': [1, 2]}` | 0.8361 | 0.0587 |
| `{'C': 1.0, 'analyzer': 'word', 'ngram_range': [1, 1]}` | 0.8197 | 0.0539 |

## 성능 기준

- 검증 macro F1 ≥ 0.8, 확인용 macro F1 ≥ 0.7
- 결과: **통과**

## 검증 데이터: 60건

- 정확도 0.95 · macro 정밀도 0.9508 · macro 재현율 0.95 · macro F1 0.9493

| 분류 | 정밀도 | 재현율 | F1 | 개수 |
|---|---|---|---|---|
| CANCELLATION | 0.9412 | 1.0 | 0.9697 | 16 |
| MAKEUP | 0.9286 | 0.8667 | 0.8966 | 15 |
| RESCHEDULE | 0.9333 | 1.0 | 0.9655 | 14 |
| GENERAL | 1.0 | 0.9333 | 0.9655 | 15 |

혼동행렬

| 실제 \ 예측 | CANCEL | MAKEUP | RESCHED | GENERAL |
|---|---|---|---|---|
| CANCEL | 16 | 0 | 0 | 0 |
| MAKEUP | 1 | 13 | 1 | 0 |
| RESCHED | 0 | 0 | 14 | 0 |
| GENERAL | 0 | 1 | 0 | 14 |

오분류 사례

| 문장 | 정답 | 예측 | 확신도 |
|---|---|---|---|
| 레슨 끝나고 상담 잠깐 가능할까요 | GENERAL | MAKEUP | 0.348 |
| 보강은 1시간 반으로 길게 해도 될까요 | MAKEUP | RESCHEDULE | 0.459 |
| 비 때문에 취소된 야외 수업은 다음 주에 보강합니다 | MAKEUP | CANCELLATION | 0.506 |

## 확인용 문장 (학습에 쓰지 않은 새 문장): 48건

- 정확도 0.9167 · macro 정밀도 0.9188 · macro 재현율 0.9167 · macro F1 0.9156

| 분류 | 정밀도 | 재현율 | F1 | 개수 |
|---|---|---|---|---|
| CANCELLATION | 0.9091 | 0.8333 | 0.8696 | 12 |
| MAKEUP | 0.8571 | 1.0 | 0.9231 | 12 |
| RESCHEDULE | 0.9091 | 0.8333 | 0.8696 | 12 |
| GENERAL | 1.0 | 1.0 | 1.0 | 12 |

혼동행렬

| 실제 \ 예측 | CANCEL | MAKEUP | RESCHED | GENERAL |
|---|---|---|---|---|
| CANCEL | 10 | 1 | 1 | 0 |
| MAKEUP | 0 | 12 | 0 | 0 |
| RESCHED | 1 | 1 | 10 | 0 |
| GENERAL | 0 | 0 | 0 | 12 |

오분류 사례

| 문장 | 정답 | 예측 | 확신도 |
|---|---|---|---|
| 조카 돌잔치라 토요일엔 수업 어려워요 | CANCELLATION | RESCHEDULE | 0.309 |
| 한 주 휴강 부탁드려요 | CANCELLATION | MAKEUP | 0.575 |
| 내일은 2시 대신 4시에 만나요 | RESCHEDULE | MAKEUP | 0.375 |
| 다음 수업은 줌으로 할게요 | RESCHEDULE | CANCELLATION | 0.488 |
