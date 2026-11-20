-- =====================================================================
-- 밤·오전 테스트용 리셋 · 2026-11-21 · 오늘(0시 이후) 기록을 전부 지움
-- demo_reset.sql은 "오늘 13:00 이후"만 지워요 (오전 더미 보호). 13시 전에 리허설을 돌리면 그 기록이 남아서
-- 새벽·오전 테스트 때는 이 파일을 써요. 촬영 당일 13시 이후에는 demo_reset.sql을 쓰세요 (이 파일은 오늘 오전 더미도 지워요)
-- 실행 방법:  \i C:/jyc/sfaas_project/sfaas/demo_reset_all.sql
-- 지우는 것·남기는 것은 demo_reset.sql과 같고 기준 시각만 오늘 0시예요
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

-- 1. 오늘 기록 비우기 (외래키 순서대로). 끝나지 않은(열린) 상태·비가동·이동 기록도 같이 지워요
-- 공장 목표 변경 기록 (v0.3.16 target_change): 오늘 13:00 이후 올린 목표의 배정 대기
DELETE FROM target_change WHERE changed_at >= CURRENT_DATE;
-- 보고서 결재·전송 기록 (v0.3.15 reports): 리셋 뒤 결재 줄이 "완료"로 남지 않게 (11/20)
DELETE FROM reports WHERE COALESCE(written_at, approved_at, sent_at) >= CURRENT_DATE
   OR replan_report_id IN (SELECT report_id FROM reassignment_report WHERE created_at >= CURRENT_DATE);
DELETE FROM reassignment_report r USING downtime_event d
WHERE r.downtime_id = d.downtime_id AND (d.started_at >= CURRENT_DATE OR d.ended_at IS NULL);
DELETE FROM reassignment_report WHERE created_at >= CURRENT_DATE;
DELETE FROM equipment_status_history WHERE started_at >= CURRENT_DATE OR ended_at > CURRENT_DATE OR ended_at IS NULL;
DELETE FROM downtime_event WHERE started_at >= CURRENT_DATE OR ended_at IS NULL;
DELETE FROM alarm WHERE raised_at >= CURRENT_DATE;
DELETE FROM product_flow WHERE COALESCE(started_at, ended_at) >= CURRENT_DATE OR ended_at IS NULL;
DELETE FROM slitting_data WHERE measured_at >= CURRENT_DATE;
DELETE FROM press_data WHERE measured_at >= CURRENT_DATE;
DELETE FROM welding_data WHERE measured_at >= CURRENT_DATE;
DELETE FROM painting_data WHERE measured_at >= CURRENT_DATE;
DELETE FROM inspection_result WHERE inspected_at >= CURRENT_DATE;
DELETE FROM coil_receipt WHERE received_at >= CURRENT_DATE;
DELETE FROM paint_route_history WHERE changed_at >= CURRENT_DATE;

-- 2. 날짜와 상관없는 것: 설비 신호·채팅은 전부 비움
TRUNCATE equipment_signal, chat_message;

-- 3. 도장 경로를 기본값으로
UPDATE line SET current_paint_id = default_paint_id
WHERE current_paint_id IS DISTINCT FROM default_paint_id;

-- 4. test_data.sql의 가짜 계정 지우기 (없으면 그냥 넘어가요)
DELETE FROM users WHERE login_id = 'test_manager';

COMMIT;

-- 확인: *_today(오늘 0시 이후) 가 모두 0이면 리셋 완료 (past_inspection = 남아 있는 과거 검사 기록 수)
SELECT
    (SELECT count(*) FROM press_data WHERE measured_at >= CURRENT_DATE)               AS press_today,
    (SELECT count(*) FROM inspection_result WHERE inspected_at >= CURRENT_DATE)       AS inspection_today,
    (SELECT count(*) FROM equipment_status_history WHERE started_at >= CURRENT_DATE)  AS status_today,
    (SELECT count(*) FROM alarm WHERE raised_at >= CURRENT_DATE)                      AS alarm_today,
    (SELECT count(*) FROM inspection_result WHERE inspected_at < CURRENT_DATE) AS past_inspection;
