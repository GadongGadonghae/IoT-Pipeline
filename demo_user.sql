-- =====================================================================
-- 데모 로그인 계정 1개 (화면 로그인용) · 아이디 abc / 비밀번호 1234
-- seed.sql 다음에 한 번만 실행해요. 이미 있으면 그대로 둬요 (여러 번 실행해도 안전)
-- password_hash는 BCrypt 결과예요 (원문 1234를 저장하지 않음). 실제 운영에서는 쓰지 마세요
-- =====================================================================
INSERT INTO users (login_id, password_hash, user_name, role)
VALUES ('abc', '$2a$10$6e.aNj6tcfNC5zjXez12euAMpErmqixrYlv7GcF6qgwAkeQfy.zqO', '데모 관리자', 'MANAGER')
ON CONFLICT (login_id) DO NOTHING;

SELECT login_id, user_name, role FROM users;
