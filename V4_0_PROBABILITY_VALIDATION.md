# Fed Policy Engine V4.0 — probability validation split

- 정책금리 엔진은 확률예측 모델이므로 Brier score와 log loss 개선을 독립 확률 OOS 게이트로 평가합니다.
- 최다범주 방향분류 성능은 별도 게이트로 유지합니다.
- 현재 저장된 92개 재구성 회의 기준 확률 OOS는 benchmark 대비 개선되지만, 최다범주 방향분류는 majority-hold 대비 우위가 확인되지 않습니다.
- 따라서 화면은 `확률 OOS 통과 · 방향분류 우위 미확인`으로 표시해야 하며, 완전 검증통과로 과장하지 않습니다.
- 검증품질 점수는 적중률이 아니라 proper scoring rule/OOS 품질 요약입니다.
