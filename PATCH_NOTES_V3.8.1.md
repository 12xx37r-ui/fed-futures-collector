# V3.8.1 output guard

- 예측수학/시장경로는 V3.8과 동일합니다.
- GitHub Actions가 생성한 `public/data/latest.json`의 `engine_version`이 V3.8.1인지 검증합니다.
- `data_quality_score`, `model_validation_score`, `forecast_validation` 필드가 없으면 Action을 실패시킵니다.
- collector 버전 표기도 3.8.1로 통일했습니다.
- 목적: 예전 3.7 `latest.json`이 조용히 남거나 다시 배포되는 상황을 방지합니다.
