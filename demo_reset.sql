-- =====================================================================
-- 시연·리허설 리셋 · DB v0.3.9 · 2026-11-18 (product_flow 추가. alter_v0.3.9.sql을 먼저 실행해야 해요)
-- 리허설이 끝날 때마다 실행해서 "시연 시작 직전 상태"로 되돌려요.
-- 실행 방법:  \i C:/jyc/sfaas_project/sfaas/demo_reset.sql
--
-- 🗑️ 지움   : 측정값(슬리팅·프레스·용접·도장), 코일 입고, 검사, 상태 이력, 비가동, 알람,
--             도장 경로 이력, 재배정 계획서, 채팅, 설비 신호, 공정 이동 기록(product_flow), 테스트 계정(test_manager)
-- 🔄 되돌림 : 라인의 현재 도장 설비 → 기본값 (A·B·C → PT1, D·E·F → PT2)
-- ✅ 그대로 : 공정, 라인, 설비, 비가동 코드, 사용자, 관리값 기준(SCR-009에서 고친 값 포함), 생산 목표
-- =====================================================================

BEGIN;

-- 1. 리허설 기록 비우기 (번호도 1부터 다시)
TRUNCATE product_flow, equipment_signal, chat_message, alarm, paint_route_history, reassignment_detail, reassignment_report,
         downtime_event, equipment_status_history, inspection_result,
         painting_data, welding_data, press_data, slitting_data, coil_receipt
         RESTART IDENTITY;
ALTER SEQUENCE stop_group_seq RESTART;   -- 계획정지 묶음 번호도 1부터

-- 2. 도장 경로를 기본값으로
UPDATE line SET current_paint_id = default_paint_id
WHERE current_paint_id IS DISTINCT FROM default_paint_id;

-- 3. test_data.sql의 가짜 계정 지우기 (없으면 그냥 넘어가요)
DELETE FROM users WHERE login_id = 'test_manager';

COMMIT;

-- 확인: 아래 값이 모두 0이면 리셋 완료
SELECT
    (SELECT count(*) FROM press_data)  AS press_rows,
    (SELECT count(*) FROM inspection_result)  AS inspection_rows,
    (SELECT count(*) FROM equipment_status_history)  AS status_rows,
    (SELECT count(*) FROM alarm)       AS alarm_rows;
