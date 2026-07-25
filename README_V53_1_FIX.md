# V53.1 GitHub Actions test fix

원인:
- 기존 저장소에 남아 있던 `tests/test_collector.py`가 `collector.parse_fred_series_csv`를 import함.
- V53 ZIP은 기존 파일을 삭제하지 않으므로 오래된 테스트가 계속 남았고, V53 collector에는 호환 함수가 없어 `Run tests` 단계에서 ImportError가 발생함.

수정:
- `parse_fred_series_csv()` 복원 및 최신 `observation_date`/구형 `DATE` 헤더 모두 지원
- `fred_csv()`가 공통 파서를 사용하도록 변경
- 네트워크 결과에 의존하던 FOMC 테스트를 결정론적 fixture 테스트로 변경
- 실질금리 테스트를 unittest 형식으로 변경하여 GitHub Actions에서 실제 실행
