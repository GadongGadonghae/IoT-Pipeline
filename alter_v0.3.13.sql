-- =====================================================================
-- DB v0.3.12 → v0.3.13 (11/20): 계정 권한 (공장장 · 생산관리팀장 · 라인 담당자)
-- 테이블 구조는 그대로예요. users.role 값만 새 코드로 바꾸고, 시연용 계정 2개를 더해요.
--   PLANT_MANAGER       공장장       : 생산 목표 설정 · 사용자 관리 · AI 리포트 승인(결재)
--   PRODUCTION_MANAGER  생산관리팀장 : 알람 조치 작성 · 관리값 기준 설정 · AI 리포트 작성
--   LINE_MANAGER        라인 담당자  : 알람 조치 작성
--   (공통) 기록 열람
-- 백엔드가 이 코드로 권한을 검사해요 (Back-End auth/Roles.java). 예전 코드(ADMIN·MANAGER·OPERATOR)면 저장 기능이 막혀요.
-- 여러 번 실행해도 괜찮아요. 실행 뒤에는 다시 로그인해야 새 권한이 적용돼요 (로그인 토큰에 권한이 들어 있어서).
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i C:/jyc/sfaas_project/sfaas/alter_v0.3.13.sql
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

-- 1. 예전 역할 → 새 역할
UPDATE users SET role = 'PLANT_MANAGER'      WHERE role = 'ADMIN';
UPDATE users SET role = 'PRODUCTION_MANAGER' WHERE role = 'MANAGER';
UPDATE users SET role = 'LINE_MANAGER'       WHERE role = 'OPERATOR';

-- 1-1. 새 계정의 기본 역할 (schema.sql과 같게)
ALTER TABLE users ALTER COLUMN role SET DEFAULT 'LINE_MANAGER';

-- 2. 데모 계정 abc = 공장장
UPDATE users SET role = 'PLANT_MANAGER', user_name = '데모 공장장' WHERE login_id = 'abc';

-- 3. 시연용 계정 2개 (비밀번호는 abc와 같아요: demo_user.sql 참고). 이미 있으면 권한만 맞춰요
INSERT INTO users (login_id, password_hash, user_name, role) VALUES
    ('prodlead', '$2a$10$6e.aNj6tcfNC5zjXez12euAMpErmqixrYlv7GcF6qgwAkeQfy.zqO', '데모 생산관리팀장', 'PRODUCTION_MANAGER'),
    ('linelead', '$2a$10$6e.aNj6tcfNC5zjXez12euAMpErmqixrYlv7GcF6qgwAkeQfy.zqO', '데모 라인담당자',   'LINE_MANAGER')
ON CONFLICT (login_id) DO UPDATE SET role = EXCLUDED.role;

COMMIT;

-- 확인: 3개 계정이 각각 공장장·생산관리팀장·라인 담당자로 나오면 완료
SELECT login_id, user_name, role FROM users ORDER BY user_id;
