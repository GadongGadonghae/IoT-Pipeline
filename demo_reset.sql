-- =====================================================================
-- 시연·리허설 리셋 · DB v0.3.11 · 2026-11-20 (오늘 기록만 지움)
-- 리허설이 끝날 때마다 실행해서 "시연 시작 직전 상태"로 되돌려요.
-- 실행 방법:  \i C:/jyc/sfaas_project/sfaas/demo_reset.sql
--
-- 🗑️ 지움   : "오늘(CURRENT_DATE) 이후" 기록만 — 측정값(슬리팅·프레스·용접·도장), 코일 입고, 검사, 상태 이력, 비가동, 알람,
--             도장 경로 이력, 재배정 계획서, 공정 이동 기록(product_flow)
--             + 설비 신호, 채팅, 테스트 계정(test_manager)
-- ✅ 그대로 : 어제까지의 기록 (make_history.py로 넣은 과거 데이터), 공정, 라인, 설비, 비가동 코드, 사용자,
--             관리값 기준(SCR-009에서 고친 값 포함), 생산 목표
-- 🔄 되돌림 : 라인의 현재 도장 설비 → 기본값 (A·B·C → PT1, D·E·F → PT2)
-- 11/20 변경: 전부 지우던 TRUNCATE → 오늘 것만 DELETE (과거 3일치를 지키려고). 번호(id)는 이어서 매겨요
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

-- 1. 오늘 기록 비우기 (외래키 순서대로). 끝나지 않은(열린) 상태·비가동·이동 기록도 같이 지워요
DELETE FROM reassignment_report r USING downtime_event d
WHERE r.downtime_id = d.downtime_id AND (d.started_at >= CURRENT_DATE OR d.ended_at IS NULL);
DELETE FROM reassignment_report WHERE created_at >= CURRENT_DATE;
DELETE FROM equipment_status_history WHERE started_at >= CURRENT_DATE OR ended_at >= CURRENT_DATE OR ended_at IS NULL;
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

-- 확인: *_today 가 모두 0이면 리셋 완료 (past_inspection = 남아 있는 과거 검사 기록 수)
SELECT
    (SELECT count(*) FROM press_data WHERE measured_at >= CURRENT_DATE)               AS press_today,
    (SELECT count(*) FROM inspection_result WHERE inspected_at >= CURRENT_DATE)       AS inspection_today,
    (SELECT count(*) FROM equipment_status_history WHERE started_at >= CURRENT_DATE)  AS status_today,
    (SELECT count(*) FROM alarm WHERE raised_at >= CURRENT_DATE)                      AS alarm_today,
    (SELECT count(*) FROM inspection_result WHERE inspected_at < CURRENT_DATE)        AS past_inspection;
