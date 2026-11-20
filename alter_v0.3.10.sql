-- =====================================================================
-- DB v0.3.9 → v0.3.10 (11/19): 계획수정 보고서 저장 (R-03)
-- 고장(D) 건으로 만든 계획수정 보고서의 계산 기준과 AI 문장을 reassignment_report에 같이 저장해요.
-- 도장 보고서는 다른 도장 설비(PT1·PT2)에 나눠서 reassignment_detail.line_id에 설비 ID도 들어가요.
-- 기존 데이터는 그대로 둬요. 여러 번 실행해도 괜찮아요.
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i 'C:/sfaas/IoT-Pipeline/alter_v0.3.10.sql'      (경로는 내 PC에 맞게)
-- =====================================================================

-- 이 파일은 UTF-8이라 psql(윈도우 기본 UHC)에서 한글 주석이 깨지지 않게 먼저 맞춰요
SET client_encoding = 'UTF8';

ALTER TABLE reassignment_report
    ADD COLUMN IF NOT EXISTS created_by   INTEGER REFERENCES users(user_id),  -- 만든 사람
    ADD COLUMN IF NOT EXISTS kind         VARCHAR(10),    -- line 라인 전체 정지 / paint 도장 1대 → 다른 1대 / vision 대체 없음
    ADD COLUMN IF NOT EXISTS base_at      TIMESTAMP,      -- 계산 기준 시각 (오늘 건 = 만든 시각, 지난 날 건 = 고장 시작)
    ADD COLUMN IF NOT EXISTS ct_sec       NUMERIC(6,2),   -- 계산에 쓴 CT (라인 = 슬리팅·프레스·용접 중 가장 긴 값, 도장 = 도장 CT)
    ADD COLUMN IF NOT EXISTS stopped_plan INTEGER,        -- 멈춘 쪽 그날 계획
    ADD COLUMN IF NOT EXISTS stopped_done INTEGER,        -- 멈춘 쪽 기준 시각까지 실적 (출고 양품)
    ADD COLUMN IF NOT EXISTS target_qty   INTEGER,        -- 그날 공장 목표 (라인 목표 합, 없으면 NULL)
    ADD COLUMN IF NOT EXISTS unmet_qty    INTEGER,        -- 나눠도 남는 목표 미달 예상
    ADD COLUMN IF NOT EXISTS ai_content   TEXT;           -- AI(템플릿) 문장 JSON: title·summary·actions·risks·markdown·model·mock

ALTER TABLE reassignment_detail DROP CONSTRAINT IF EXISTS reassignment_detail_line_id_fkey;
ALTER TABLE reassignment_detail ALTER COLUMN line_id TYPE VARCHAR(20);           -- 라인 ID 또는 도장 설비 ID
ALTER TABLE reassignment_detail ADD COLUMN IF NOT EXISTS remaining_plan INTEGER; -- 그 라인(설비)의 남은 계획

-- 확인: 아래 두 줄이 나오면 완료 (kind, remaining_plan)
SELECT table_name, column_name FROM information_schema.columns
WHERE (table_name, column_name) IN (('reassignment_report', 'kind'), ('reassignment_detail', 'remaining_plan'));
