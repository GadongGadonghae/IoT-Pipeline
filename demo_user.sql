-- =====================================================================
-- 데모 로그인 계정 3개 (화면 로그인용, 11/20 계정 권한) · 비밀번호는 모두 1234
--   abc       공장장       (PLANT_MANAGER)      : 생산 목표 · 사용자 관리 · AI 리포트 승인
--   prodlead  생산관리팀장 (PRODUCTION_MANAGER) : 알람 조치 · 관리값 기준 · AI 리포트 작성
--   linelead  라인 담당자  (LINE_MANAGER)       : 알람 조치
-- seed.sql 다음에 한 번만 실행해요. 이미 있으면 권한만 맞춰요 (여러 번 실행해도 안전)
-- password_hash는 BCrypt 결과예요 (원문을 저장하지 않음). 실제 운영에서는 쓰지 마세요
-- =====================================================================
INSERT INTO users (login_id, password_hash, user_name, role) VALUES
    ('abc',      '$2a$10$6e.aNj6tcfNC5zjXez12euAMpErmqixrYlv7GcF6qgwAkeQfy.zqO', '데모 공장장',       'PLANT_MANAGER'),
    ('prodlead', '$2a$10$6e.aNj6tcfNC5zjXez12euAMpErmqixrYlv7GcF6qgwAkeQfy.zqO', '데모 생산관리팀장', 'PRODUCTION_MANAGER'),
    ('linelead', '$2a$10$6e.aNj6tcfNC5zjXez12euAMpErmqixrYlv7GcF6qgwAkeQfy.zqO', '데모 라인담당자',   'LINE_MANAGER')
ON CONFLICT (login_id) DO UPDATE SET role = EXCLUDED.role;

SELECT login_id, user_name, role FROM users;
