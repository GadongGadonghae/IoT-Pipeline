-- =====================================================================
-- DB v0.3.13 → v0.3.14 (11/20): 추가 주문 배정 검토 (R-05) · 승인 적용 (R-06)
-- 고장 건 없이 만드는 보고서(kind = order)가 생겨서 reassignment_report.downtime_id를 비워 둘 수 있게 해요.
-- 기존 데이터는 그대로 둬요. 여러 번 실행해도 괜찮아요.
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i 'C:/sfaas/IoT-Pipeline/alter_v0.3.14.sql'      (경로는 내 PC에 맞게)
-- =====================================================================

SET client_encoding = 'UTF8';

ALTER TABLE reassignment_report ALTER COLUMN downtime_id DROP NOT NULL;

COMMENT ON COLUMN reassignment_report.downtime_id IS '고장 건 (추가 주문 배정 kind = order 이면 NULL)';
COMMENT ON COLUMN reassignment_report.kind IS 'line 라인 전체 정지 / paint 도장 1대 → 다른 1대 / vision 대체 없음 / order 추가 주문 배정';
