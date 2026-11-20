-- =====================================================================
-- DB v0.3.15 → v0.3.16 (11/20): 보고서 결재 "작성 중" 상태 추가 · 공장 목표 변경 기록(추가 주문 배정 대기) · 시연용 가상 시계(sim_clock)
--   · reports.status 에 DRAFT(작성 중) 추가
--       AI 보고서를 만들기만 하면 DRAFT → 생산관리팀장이 [작성 완료 · 결재 요청]을 누르면 WRITTEN(공장장에게 알림)
--       → 결재 APPROVED → 전송 SENT. 결재 전에는 [회수]로 WRITTEN → DRAFT
--   · 예전에 만들기만 하고 결재 요청은 안 한 계획수정 보고서(WRITTEN, 결재 요청 기록 없음)는 그대로 둬요
-- 여러 번 실행해도 괜찮아요.
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i C:/jyc/sfaas_project/sfaas/alter_v0.3.16.sql
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

ALTER TABLE reports DROP CONSTRAINT IF EXISTS reports_status_check;
ALTER TABLE reports ADD CONSTRAINT reports_status_check CHECK (status IN ('DRAFT', 'WRITTEN', 'APPROVED', 'SENT'));
COMMENT ON COLUMN reports.status IS 'DRAFT 작성 중(결재 요청 전) · WRITTEN 작성 완료·결재 요청 · APPROVED 결재 완료 · SENT 전송 완료 (v0.3.16)';

-- 2. 공장 목표 변경 기록 (11/20): 공장장이 오늘 목표를 올리면 이전·새 목표를 남겨요.
--    늘어난 수량(after − 라인 목표 합) = 추가 주문 · 배정 대기 → 생산관리팀장 추가 주문 배정 보고서 → 공장장 결재 때 라인 목표에 들어가고 closed_at
CREATE TABLE IF NOT EXISTS target_change (
    change_id    BIGSERIAL PRIMARY KEY,
    target_date  DATE      NOT NULL,
    before_qty   INTEGER   NOT NULL,                   -- 올리기 전 라인 목표 합
    after_qty    INTEGER   NOT NULL,                   -- 공장장이 새로 정한 공장 목표
    changed_by   INTEGER   REFERENCES users(user_id),
    changed_at   TIMESTAMP NOT NULL,
    report_id    BIGINT    REFERENCES reassignment_report(report_id) ON DELETE SET NULL,   -- 이 변경으로 만든 추가 주문 배정 보고서
    closed_at    TIMESTAMP                             -- 결재로 라인에 들어갔거나 다시 바뀌어 대신한 시각 (NULL = 배정 대기)
);
CREATE INDEX IF NOT EXISTS idx_target_change_open ON target_change (target_date) WHERE closed_at IS NULL;
GRANT SELECT, INSERT, UPDATE, DELETE ON target_change TO sfaas_be;
GRANT USAGE, SELECT ON SEQUENCE target_change_change_id_seq TO sfaas_be;

-- 3. 시연용 가상 시계 (11/21): 한 줄(active = TRUE)만 써요. 가상 시각 = virtual_at + (실제 지금 − real_at) × speed
--    백엔드가 1초마다 읽고, 재생기·화면도 같은 시계를 써요 (실제 시각과 상관없이 09:00~18:00 근무를 보여 주려고)
CREATE TABLE IF NOT EXISTS sim_clock (
    id          INTEGER   PRIMARY KEY DEFAULT 1,
    virtual_at  TIMESTAMP NOT NULL,                  -- real_at 때의 가상 시각
    real_at     TIMESTAMP NOT NULL,                  -- 기준 실제 시각 (KST)
    speed       NUMERIC(8,2) NOT NULL DEFAULT 1,     -- 배속 (가상 1초 = 실제 1/speed초)
    active      BOOLEAN   NOT NULL DEFAULT FALSE
);
GRANT SELECT, INSERT, UPDATE, DELETE ON sim_clock TO sfaas_be;

COMMIT;

-- 확인: 아래 결과에 DRAFT 가 들어 있고, target_change 표가 있으면 완료
SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'reports_status_check';
