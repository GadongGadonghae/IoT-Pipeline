-- =====================================================================
-- DB v0.3.14 → v0.3.15 (11/20): 보고서 결재·전송 + 임시 MES · 권한 이름 · 라인 담당자 담당 라인
--   · users.role 이름을 MES 명세에 맞춰요
--       PLANT_MANAGER → PLANT_HEAD (공장장) · PRODUCTION_MANAGER → PROD_LEAD (생산관리팀장)
--       LINE_MANAGER → LINE_WORKER (라인 담당자) · CAMPUS_HEAD → CAMPUS (캠퍼스장)
--   · users.lines: 라인 담당자의 담당 라인 (쉼표, 예: 'A,B'). 조치 쓰기는 이 라인 설비만 (백엔드 검사)
--   · reports: 보고서 결재·전송 (작성 WRITTEN → 결재 APPROVED → 전송 SENT). 전송된 것은 임시 MES 수신함에
-- 여러 번 실행해도 괜찮아요. 실행 뒤에는 다시 로그인해야 새 권한이 적용돼요.
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i C:/jyc/sfaas_project/sfaas/alter_v0.3.15.sql
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

-- 1. 권한 이름
UPDATE users SET role = 'PLANT_HEAD'  WHERE role = 'PLANT_MANAGER';
UPDATE users SET role = 'PROD_LEAD'   WHERE role = 'PRODUCTION_MANAGER';
UPDATE users SET role = 'LINE_WORKER' WHERE role = 'LINE_MANAGER';
UPDATE users SET role = 'CAMPUS'      WHERE role = 'CAMPUS_HEAD';
ALTER TABLE users ALTER COLUMN role SET DEFAULT 'LINE_WORKER';

-- 2. 라인 담당자의 담당 라인 (시연 계정 linelead = A·B라인)
ALTER TABLE users ADD COLUMN IF NOT EXISTS lines VARCHAR(30);
UPDATE users SET lines = 'A,B' WHERE login_id = 'linelead' AND lines IS NULL;

-- 3. 보고서 결재·전송 (REPLAN 내용은 reassignment_report, 여기엔 번호와 그때 JSON)
CREATE TABLE IF NOT EXISTS reports (
    report_id         BIGSERIAL    PRIMARY KEY,
    kind              VARCHAR(10)  NOT NULL,              -- DAILY 일일 생산 · REPLAN 계획수정
    target_key        VARCHAR(40)  NOT NULL,              -- DAILY = 날짜, REPLAN = 비가동 번호 · TARGET-날짜 · ORDER-보고서번호
    title             VARCHAR(100) NOT NULL,
    status            VARCHAR(10)  NOT NULL CHECK (status IN ('WRITTEN', 'APPROVED', 'SENT')),
    campus_id         VARCHAR(10)  NOT NULL,              -- BODY 차체 · ELEC 전장 (설정 plant.campus-id)
    factory_id        INTEGER      NOT NULL,              -- 캠퍼스 안 공장 번호 (설정 plant.factory-id)
    content           TEXT,                               -- 보고서 내용 JSON (만든 순간 그대로 보존)
    replan_report_id  BIGINT       REFERENCES reassignment_report(report_id) ON DELETE SET NULL,
    written_by        INTEGER      REFERENCES users(user_id),
    written_at        TIMESTAMP,
    approved_by       INTEGER      REFERENCES users(user_id),
    approved_at       TIMESTAMP,
    sent_by           INTEGER      REFERENCES users(user_id),
    sent_at           TIMESTAMP,
    UNIQUE (kind, target_key)
);
CREATE INDEX IF NOT EXISTS idx_reports_sent ON reports (sent_at) WHERE status = 'SENT';
GRANT SELECT, INSERT, UPDATE, DELETE ON reports TO sfaas_be;
GRANT USAGE, SELECT ON SEQUENCE reports_report_id_seq TO sfaas_be;

COMMIT;

-- 확인: 계정 4개가 PLANT_HEAD · PROD_LEAD · LINE_WORKER(lines A,B) · CAMPUS 로, reports 표가 있으면 완료
SELECT login_id, user_name, role, lines FROM users ORDER BY user_id;
SELECT count(*) AS reports FROM reports;
