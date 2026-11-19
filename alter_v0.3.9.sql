-- =====================================================================
-- DB v0.3.8 → v0.3.9 (11/18 백엔드 요청 확정판): 공정 이동 기록 product_flow 추가
-- 공정 테이블 구조는 v0.3.8 그대로예요. 기존 데이터는 그대로 둬요.
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i C:/jyc/sfaas_project/sfaas/alter_v0.3.9.sql
-- 새로 설치할 땐 이 파일 대신 schema.sql → grants.sql → seed.sql
-- 두 번 실행해도 괜찮아요. 11/18 오전 초안(시각 칸 추가판)을 이미 실행했어도 이 파일이 되돌려요
-- =====================================================================
BEGIN;

-- 0. 초안에서 넣었던 시각 칸이 있으면 빼요 (없으면 아무 일 없음)
ALTER TABLE slitting_data DROP COLUMN IF EXISTS started_at, DROP COLUMN IF EXISTS ended_at;
ALTER TABLE welding_data  DROP COLUMN IF EXISTS started_at, DROP COLUMN IF EXISTS ended_at;
ALTER TABLE painting_data DROP COLUMN IF EXISTS started_at, DROP COLUMN IF EXISTS ended_at;
ALTER TABLE press_data
    DROP COLUMN IF EXISTS pr1_started_at, DROP COLUMN IF EXISTS pr1_ended_at,
    DROP COLUMN IF EXISTS pr2_started_at, DROP COLUMN IF EXISTS pr2_ended_at,
    DROP COLUMN IF EXISTS pr3_started_at, DROP COLUMN IF EXISTS pr3_ended_at,
    DROP COLUMN IF EXISTS pr4_started_at, DROP COLUMN IF EXISTS pr4_ended_at;
ALTER TABLE inspection_result DROP COLUMN IF EXISTS started_at;
DELETE FROM inspection_result WHERE result IS NULL OR inspected_at IS NULL;   -- 초안으로 생긴 검사 중 행 (없으면 0건)
ALTER TABLE inspection_result ALTER COLUMN result SET NOT NULL;
ALTER TABLE inspection_result ALTER COLUMN inspected_at SET NOT NULL;
DROP TABLE IF EXISTS product_flow;   -- 초안의 product_flow(line_id NULL 허용)는 지우고 새로 만들어요

-- 1. 공정 이동 기록: 제품이 설비 1대를 거칠 때마다 1줄
CREATE TABLE product_flow (
    flow_id       BIGSERIAL   PRIMARY KEY,
    product_id    VARCHAR(30) NOT NULL,
    equipment_id  VARCHAR(20) NOT NULL REFERENCES equipment(equipment_id),
    line_id       VARCHAR(5)  NOT NULL REFERENCES line(line_id),
    started_at    TIMESTAMP,
    ended_at      TIMESTAMP,
    UNIQUE (product_id, equipment_id),
    CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX idx_flow_open    ON product_flow (equipment_id) WHERE ended_at IS NULL;
CREATE INDEX idx_flow_started ON product_flow (equipment_id, started_at);
CREATE INDEX idx_flow_line    ON product_flow (line_id, equipment_id);

-- 2. 권한
GRANT SELECT, INSERT, UPDATE, DELETE ON product_flow TO sfaas_be;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE product_flow_flow_id_seq TO sfaas_be;
GRANT SELECT, INSERT ON product_flow TO sfaas_nr;
GRANT UPDATE (started_at, ended_at) ON product_flow TO sfaas_nr;
GRANT USAGE ON SEQUENCE product_flow_flow_id_seq TO sfaas_nr;

COMMIT;

-- 확인 1: product_flow 칸 6개 (flow_id, product_id, equipment_id, line_id, started_at, ended_at)
SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'product_flow' ORDER BY ordinal_position;
-- 확인 2: 0이면 공정 테이블이 v0.3.8 구조 그대로
SELECT COUNT(*) AS draft_columns_left FROM information_schema.columns
WHERE table_name IN ('slitting_data', 'press_data', 'welding_data', 'painting_data', 'inspection_result')
  AND column_name ~ '^(pr[1-4]_)?(started|ended)_at$';
