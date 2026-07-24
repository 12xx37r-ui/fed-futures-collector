# Fed Policy Engine V3.0

핵심 변경:
- FRED 18개 개별 요청을 **단일 bulk CSV 요청**으로 통합
- FRED 장애 시 마지막 정상값을 stale cache로 유지해 매크로 점수 0 초기화 방지
- FRED 전체 대기시간을 최대 약 36초(재시도 포함)로 제한
- 무효 티커 후보 404를 신뢰도 분모에서 제외하고 논리 소스 그룹 기준으로 재계산
- 로그 버전: `RUNNING FED ENGINE V3.0`, `ENGINE_VERSION=3.0.0-fast-bulk-fred`
