-- =====================================================================
-- DB v0.3.13 → v0.3.14 (11/20): 고장 건 없는 AI 보고서 · 캠퍼스장 계정
--   · reassignment_report.downtime_id 를 비워 둘 수 있게
--       kind = target  생산 목표 변경 (목표 변동 → 계획정지 라인 몫을 가동 라인에, R-03 basis=target)
--       kind = order   추가 주문 배정 검토 (R-05) · 승인·적용 (R-06)
--   · 캠퍼스장(CAMPUS_HEAD) 보기 전용 계정 campus 추가 (비밀번호는 abc와 같아요: demo_user.sql)
--   · 공장장 승인(R-04)하면 백엔드가 라인 목표·도장 경로를 바꿔요 (구조 변경 없음)
-- 기존 데이터는 그대로 둬요. 여러 번 실행해도 괜찮아요.
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i C:/jyc/sfaas_project/sfaas/alter_v0.3.14.sql      (경로는 내 PC에 맞게)
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

ALTER TABLE reassignment_report ALTER COLUMN downtime_id DROP NOT NULL;

COMMENT ON COLUMN reassignment_report.downtime_id IS '고장 건 (생산 목표 변경 kind = target · 추가 주문 배정 kind = order 이면 NULL)';
COMMENT ON COLUMN reassignment_report.kind IS 'line 라인 전체 정지 / paint 도장 1대 → 다른 1대 / vision 대체 없음 / target 생산 목표 변경 / order 추가 주문 배정';

INSERT INTO users (login_id, password_hash, user_name, role) VALUES
    ('campus', '$2a$10$6e.aNj6tcfNC5zjXez12euAMpErmqixrYlv7GcF6qgwAkeQfy.zqO', '데모 캠퍼스장', 'CAMPUS_HEAD')
ON CONFLICT (login_id) DO UPDATE SET role = EXCLUDED.role;

COMMIT;

-- 확인: campus 계정이 CAMPUS_HEAD로, downtime_id 의 is_nullable 이 YES 이면 완료
SELECT login_id, user_name, role FROM users ORDER BY user_id;
SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'reassignment_report' AND column_name = 'downtime_id';
