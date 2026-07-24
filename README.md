# Fed Policy Engine V2 Full

## 포함된 7개 핵심 기능

1. ZQ 개별 월물곡선
2. SOFR Futures Curve
3. FOMC 회의 전후 날짜 분해
4. 연준 발언 NLP(감사 가능한 사전 기반 초기판)
5. SEP 점도표 자동분석 + 공식 수치 수동 보정 파일
6. 스냅샷·Brier Score·Log Loss 백테스트
7. 충분한 라벨 데이터 축적 후 자동가중치 최적화

## 중요한 정확성 원칙

- 무료 시장 소스에서 월물 심볼이 확보되지 않으면 임의 값을 만들지 않습니다.
- 실패한 소스는 source_status.json에 기록합니다.
- 점도표 웹 자동추출은 보조값입니다. 정확한 점도표 수치는 data/manual/dotplot.json으로 검증합니다.
- 자동가중치 최적화는 최소 40개 라벨 스냅샷 전에는 작동하지 않습니다.
- 과거 빈티지 데이터가 없는 상태에서 가짜 백테스트를 만들지 않습니다.

## 실행 결과

- public/data/raw.json
- public/data/source_status.json
- public/data/latest.json
- public/data/history.json
- public/data/backtest.json
