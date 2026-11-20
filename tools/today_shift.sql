-- =====================================================================
-- 시연 시작: 미리 넣어 둔 "오늘 중간부터" 기록의 시각을 지금으로 옮기기 (11/21)
-- 순서: (미리) make_history.py --now … 로 만든 SQL을 넣어 둠 → (시작할 때) 이 파일 실행(1초) → 바로 재생기·백엔드 시작
--   · 오늘 기록 중 가장 늦은 공정 끝 시각(product_flow.ended_at)이 "지금 − 2초"가 되게 오늘 기록 전체를 똑같이 뒤로 밀어요
--     → 미리 넣고 몇 분·몇 시간 뒤에 시작해도 "방금까지 돌던 공장"에 이어져요 (같은 날 안에서만)
--   · 오늘 기록 = 어제 20:00 이후 (지난 날 기록은 19시쯤 끝나요. 밤에 찍으면 오늘 가동이 어제 밤에 시작했을 수 있어서)
--   · ⚠️ SQL을 넣은 뒤 재생 전에 한 번만. 넣은 뒤 시작 전까지는 백엔드를 꺼 두세요 (켜 두면 그동안 통신 끊김으로 기록돼요)
-- 실행: psql -U postgres -d sfaas -f today_shift.sql
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

CREATE TEMP TABLE _w ON COMMIT DROP AS
SELECT GREATEST(interval '0', LOCALTIMESTAMP - interval '2 second'
                - (SELECT max(ended_at) FROM product_flow WHERE ended_at >= CURRENT_DATE - interval '4 hour')) AS d,
       CURRENT_DATE - interval '4 hour' AS w;

UPDATE slitting_data     SET measured_at = measured_at + (SELECT d FROM _w) WHERE measured_at >= (SELECT w FROM _w);
UPDATE press_data        SET measured_at = measured_at + (SELECT d FROM _w) WHERE measured_at >= (SELECT w FROM _w);
UPDATE welding_data      SET measured_at = measured_at + (SELECT d FROM _w) WHERE measured_at >= (SELECT w FROM _w);
UPDATE painting_data     SET measured_at = measured_at + (SELECT d FROM _w) WHERE measured_at >= (SELECT w FROM _w);
UPDATE inspection_result SET inspected_at = inspected_at + (SELECT d FROM _w), shipped_at = shipped_at + (SELECT d FROM _w)
 WHERE inspected_at >= (SELECT w FROM _w);
UPDATE product_flow      SET started_at = started_at + (SELECT d FROM _w), ended_at = ended_at + (SELECT d FROM _w)
 WHERE COALESCE(started_at, ended_at) >= (SELECT w FROM _w);
UPDATE coil_receipt      SET received_at = received_at + (SELECT d FROM _w) WHERE received_at >= (SELECT w FROM _w);
UPDATE alarm             SET raised_at = raised_at + (SELECT d FROM _w), cleared_at = cleared_at + (SELECT d FROM _w),
                             acked_at = acked_at + (SELECT d FROM _w) WHERE raised_at >= (SELECT w FROM _w);
UPDATE downtime_event    SET started_at = started_at + (SELECT d FROM _w), ended_at = ended_at + (SELECT d FROM _w),
                             action_at = action_at + (SELECT d FROM _w) WHERE started_at >= (SELECT w FROM _w);
UPDATE equipment_status_history SET started_at = started_at + (SELECT d FROM _w), ended_at = ended_at + (SELECT d FROM _w)
 WHERE started_at >= (SELECT w FROM _w);
TRUNCATE equipment_signal;

SELECT to_char((SELECT d FROM _w), 'HH24:MI:SS') AS shifted_by,
       (SELECT max(ended_at)::time(0) FROM product_flow) AS last_activity_now;
COMMIT;
