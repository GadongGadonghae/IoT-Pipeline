-- =====================================================================
-- DB v0.3.10 → v0.3.11 (11/20): 비가동 코드 UP_BLOCKED(후공정 정지 대기) 추가
-- 프레스 1~4단은 단 사이에 쌓을 자리가 없어서, 뒤 단이 멈추면 앞 단은 끝낸 제품을 들고 기다려요.
-- 이 대기를 "재공부족"과 구분하려고 코드를 하나 더 둬요. 백엔드 판정이 이 코드로 비가동을 남겨요.
-- 테이블 구조는 그대로예요. 여러 번 실행해도 괜찮아요. 안 하면 판정이 이 코드를 저장하다 실패해요.
-- 실행 (SQL Shell, postgres):
--   \c sfaas
--   \i 'C:/sfaas/IoT-Pipeline/alter_v0.3.11.sql'      (경로는 내 PC에 맞게)
-- =====================================================================
SET client_encoding = 'UTF8';

INSERT INTO downtime_code (code, code_name, is_planned, description)
VALUES ('UP_BLOCKED', '후공정 정지 대기', FALSE, '프레스 1~3단이 끝낸 제품을 들고 뒤 단을 기다림 (v0.3.11)')
ON CONFLICT (code) DO NOTHING;

-- 확인: 아래 한 줄이 나오면 완료
SELECT code, code_name FROM downtime_code WHERE code = 'UP_BLOCKED';
