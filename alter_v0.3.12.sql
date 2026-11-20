-- =====================================================================
-- DB v0.3.11 → v0.3.12 (11/20): 시각 색인 추가 (테이블 구조·데이터는 그대로)
-- 과거 기록(make_history.py 10일치 ≈ 공정 이동 기록 100만 줄)을 넣으니 "오늘만", "이번 달만" 찾는 조회가
-- 매번 전체를 처음부터 넘겨 봐서 느려졌어요 (재공 0.4초, 가동률 월간 1초 이상).
-- 시각 칸에 색인(책의 찾아보기)을 걸어 필요한 날짜만 바로 찾게 해요.
-- 여러 번 실행해도 괜찮아요 (IF NOT EXISTS). 몇 초면 끝나요. 백엔드를 다시 켤 필요 없어요.
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i C:/jyc/sfaas_project/sfaas/alter_v0.3.12.sql      (경로는 내 PC에 맞게)
-- =====================================================================
SET client_encoding = 'UTF8';

-- 공정 이동 기록: 오늘 재공·위치·막힘 판정 (백엔드가 1초마다 조회)
CREATE INDEX IF NOT EXISTS idx_flow_started_at ON product_flow (started_at);
CREATE INDEX IF NOT EXISTS idx_flow_ended_at   ON product_flow (ended_at);

-- 공정 측정값·검사: 날짜 범위 집계 (가동률·생산량·양품률 일/주/월)
CREATE INDEX IF NOT EXISTS idx_slitting_time   ON slitting_data     (measured_at);
CREATE INDEX IF NOT EXISTS idx_press_time      ON press_data        (measured_at);
CREATE INDEX IF NOT EXISTS idx_welding_time    ON welding_data      (measured_at);
CREATE INDEX IF NOT EXISTS idx_painting_time   ON painting_data     (measured_at);
CREATE INDEX IF NOT EXISTS idx_inspection_time ON inspection_result (inspected_at);

-- 새 색인을 DB가 바로 쓰도록 통계 갱신
ANALYZE product_flow, slitting_data, press_data, welding_data, painting_data, inspection_result;

-- 확인: 7줄이 나오면 완료
SELECT indexname FROM pg_indexes
WHERE indexname IN ('idx_flow_started_at', 'idx_flow_ended_at', 'idx_slitting_time', 'idx_press_time',
                    'idx_welding_time', 'idx_painting_time', 'idx_inspection_time')
ORDER BY 1;
