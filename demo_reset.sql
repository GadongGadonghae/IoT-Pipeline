-- =====================================================================
-- 시연·리허설 리셋 · DB v0.3.13 · 2026-11-20 (오늘 13:00 이후 기록만 지움)
-- 리허설이 끝날 때마다 실행해서 "시연 시작 직전 상태"로 되돌려요.
-- 실행 방법:  \i C:/jyc/sfaas_project/sfaas/demo_reset.sql
--
-- 🗑️ 지움   : "오늘 13:00 이후" 기록만 (리허설·시연은 점심 뒤에 시작) — 측정값(슬리팅·프레스·용접·도장), 코일 입고, 검사, 상태 이력, 비가동, 알람,
--             도장 경로 이력, 재배정 계획서, 보고서 결재·전송 기록(reports), 공정 이동 기록(product_flow)
--             + 설비 신호, 채팅, 테스트 계정(test_manager)
-- ✅ 그대로 : 오늘 13:00 전까지의 기록 (make_history.py로 넣은 과거 며칠 + 오늘 오전 더미), 공정, 라인, 설비, 비가동 코드, 사용자,
--             관리값 기준(SCR-009에서 고친 값 포함), 생산 목표
-- 🔄 되돌림 : 라인의 현재 도장 설비 → 기본값 (A·B·C → PT1, D·E·F → PT2)
-- 11/20 변경: 전부 지우던 TRUNCATE → 오늘 13:00 이후만 DELETE (과거 며칠 + 오늘 오전 더미를 지키려고). 번호(id)는 이어서 매겨요
--   ⚠️ 13:00 전에 리허설을 돌렸다면 그 기록은 남아요 → make_history.py SQL을 다시 실행하면 오전 더미로 바뀌어요
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

-- 1. 오늘 기록 비우기 (외래키 순서대로). 끝나지 않은(열린) 상태·비가동·이동 기록도 같이 지워요
-- 공장 목표 변경 기록 (v0.3.16 target_change): 오늘 13:00 이후 올린 목표의 배정 대기
DELETE FROM target_change WHERE changed_at >= CURRENT_DATE + TIME '13:00';
-- 보고서 결재·전송 기록 (v0.3.15 reports): 리셋 뒤 결재 줄이 "완료"로 남지 않게 (11/20)
DELETE FROM reports WHERE COALESCE(written_at, approved_at, sent_at) >= CURRENT_DATE + TIME '13:00'
   OR replan_report_id IN (SELECT report_id FROM reassignment_report WHERE created_at >= CURRENT_DATE + TIME '13:00');
DELETE FROM reassignment_report r USING downtime_event d
WHERE r.downtime_id = d.downtime_id AND (d.started_at >= CURRENT_DATE + TIME '13:00' OR d.ended_at IS NULL);
DELETE FROM reassignment_report WHERE created_at >= CURRENT_DATE + TIME '13:00';
DELETE FROM equipment_status_history WHERE started_at >= CURRENT_DATE + TIME '13:00' OR ended_at > CURRENT_DATE + TIME '13:00' OR ended_at IS NULL;
DELETE FROM downtime_event WHERE started_at >= CURRENT_DATE + TIME '13:00' OR ended_at IS NULL;
DELETE FROM alarm WHERE raised_at >= CURRENT_DATE + TIME '13:00';
DELETE FROM product_flow WHERE COALESCE(started_at, ended_at) >= CURRENT_DATE + TIME '13:00' OR ended_at IS NULL;
DELETE FROM slitting_data WHERE measured_at >= CURRENT_DATE + TIME '13:00';
DELETE FROM press_data WHERE measured_at >= CURRENT_DATE + TIME '13:00';
DELETE FROM welding_data WHERE measured_at >= CURRENT_DATE + TIME '13:00';
DELETE FROM painting_data WHERE measured_at >= CURRENT_DATE + TIME '13:00';
DELETE FROM inspection_result WHERE inspected_at >= CURRENT_DATE + TIME '13:00';
DELETE FROM coil_receipt WHERE received_at >= CURRENT_DATE + TIME '13:00';
DELETE FROM paint_route_history WHERE changed_at >= CURRENT_DATE + TIME '13:00';

-- 2. 날짜와 상관없는 것: 설비 신호·채팅은 전부 비움
TRUNCATE equipment_signal, chat_message;

-- 3. 도장 경로를 기본값으로
UPDATE line SET current_paint_id = default_paint_id
WHERE current_paint_id IS DISTINCT FROM default_paint_id;

-- 4. test_data.sql의 가짜 계정 지우기 (없으면 그냥 넘어가요)
DELETE FROM users WHERE login_id = 'test_manager';

COMMIT;

-- 확인: *_today(오늘 13:00 이후) 가 모두 0이면 리셋 완료 (past_inspection = 남아 있는 과거·오전 검사 기록 수)
SELECT
    (SELECT count(*) FROM press_data WHERE measured_at >= CURRENT_DATE + TIME '13:00')               AS press_today,
    (SELECT count(*) FROM inspection_result WHERE inspected_at >= CURRENT_DATE + TIME '13:00')       AS inspection_today,
    (SELECT count(*) FROM equipment_status_history WHERE started_at >= CURRENT_DATE + TIME '13:00')  AS status_today,
    (SELECT count(*) FROM alarm WHERE raised_at >= CURRENT_DATE + TIME '13:00')                      AS alarm_today,
    (SELECT count(*) FROM inspection_result WHERE inspected_at < CURRENT_DATE + TIME '13:00') AS past_inspection;
