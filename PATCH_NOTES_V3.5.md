# 미국 정책금리 엔진 V3.5 실전 안전패치

기준본: V3.4 정상복구본. 예측 철학과 기본 가중치는 유지하고 확인된 오류만 최소 수정했다.

## 수정
1. 월말 FOMC 역산 폭주 방지: 회의 후 일수가 7일 미만이거나 원시 역산이 50bp 초과·월물평균과 35bp 초과 이탈하면 직접 월물곡선으로 자동 후퇴한다.
2. 무관측 선물계약 차단: observations가 비어 있는 Yahoo 메타가격은 사용하지 않는다.
3. SOFR 곡선 이상치 차단: 동일 월 SR1 우선·SR3 보완, 인접월 대비 고립 이상치를 제거한다.
4. 404 후보 남발 제거: 거래소 suffix(.CBT/.CME)만 요청하고 미상장 월물 404는 예상 결측으로 집계한다.
5. 점도표 오인식 방지: 공식 SEP 표 행에서 일관된 값이 확인되거나 수동 공식 URL·점 값이 있을 때만 사용한다.
6. 백테스트 자동 라벨링: 회의 후 FRED DFF 전후 창으로 실제 cut/hold/hike를 자동 확정한다.
7. 신뢰도 분리: 수집 완전성(data quality)과 예측 검증(forecast validation)을 분리한다. 라벨 40건·정확도 50%·Brier skill 양수 전에는 준기관급으로 표시하지 않는다.

## 출력 추가
- latest.json.confidence.data_quality_score
- latest.json.confidence.forecast_validation
- latest.json.validation
- meeting_path[].estimate_method / raw_calendar_inversion_rate / stability_flags

## 주의
외부 API와 거래소 심볼 변경 가능성 때문에 영구 무수정은 보장할 수 없다. 대신 동일 유형의 오류는 자동 차단·후퇴·경고되도록 구성했다.
