# Fed Policy Engine V2

무료 데이터 기반 미국 정책금리 확률 엔진의 데이터·계산 백엔드입니다.

## 고정 구조

1. GitHub Actions: 데이터 수집·정제·JSON 생성
2. Google Apps Script: 최종 대시보드
3. 정책금리 엔진:
   - 시장 내재금리
   - 연준 반응함수
   - 시장과 자체모델 차이
   - 신뢰도
   - 백테스트
4. 13개 자산 전망 엔진 연결

## 현재 단계

이번 파일은 V2의 첫 작동판입니다.

- ZQ 연속물
- EFFR·SOFR
- 미국 2년물
- CPI·PCE·고용·NFCI·HY OAS
- 연준 RSS·FOMC 일정
- 초기 앙상블 확률
- 데이터 신뢰도

다음 단계에서 추가:

- ZQ 개별 월물곡선
- SOFR Futures 곡선
- FOMC 회의 전후 일수 분해
- 연준 발언 점수
- 빈티지 백테스트
- GAS 대시보드 연결
