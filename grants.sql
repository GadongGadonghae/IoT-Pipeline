-- =====================================================================
-- 계정별 권한 · DB v0.3.8 · 2026-11-17
-- schema.sql을 실행하면 테이블이 새로 만들어지면서 권한이 사라져요.
-- 그래서 schema.sql 바로 다음에 이 파일을 실행해요. (여러 번 실행해도 안전)
--
-- 실행 순서:  schema.sql → grants.sql → seed.sql
--   \i C:/jyc/sfaas_project/sfaas/grants.sql
--
-- 계정 3개
--   postgres  : DB 관리자 (테이블 생성·삭제, 권한 관리). 정연창만 사용
--   sfaas_be  : 백엔드 (Spring Boot). 모든 테이블 조회·추가·수정·삭제. 구조 변경은 불가
--   sfaas_nr  : Node-RED. 수집 테이블에 저장만
--
-- ※ 계정 자체는 이미 만들어 두었어요 (CREATE ROLE은 여기 넣지 않음, 비밀번호 보호)
-- ※ 다른 PC 접속 허용은 pg_hba.conf · Windows 방화벽에서 (192.168.200.0/22, 192.168.0.0/24)
-- =====================================================================

-- ① 백엔드 (sfaas_be) -------------------------------------------------
GRANT CONNECT ON DATABASE sfaas TO sfaas_be;
GRANT USAGE ON SCHEMA public TO sfaas_be;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sfaas_be;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO sfaas_be;   -- 새 줄 번호 + stop_group_seq
-- 앞으로 새로 만드는 테이블에도 자동으로
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sfaas_be;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO sfaas_be;

-- ② Node-RED (sfaas_nr): 입고 담당. 판정 테이블은 못 건드려요 -------------
GRANT CONNECT ON DATABASE sfaas TO sfaas_nr;
GRANT USAGE ON SCHEMA public TO sfaas_nr;
-- 수집 값 추가 (지우기 불가. 고치기는 프레스만, 아래 참고)
GRANT INSERT ON coil_receipt, slitting_data, press_data, welding_data, painting_data, inspection_result TO sfaas_nr;
-- 같은 제품이 두 번 들어오면 무시하려고 (ON CONFLICT) product_id 칸만 읽기
GRANT SELECT (product_id) ON slitting_data, welding_data, painting_data, inspection_result TO sfaas_nr;
-- 프레스: 제품 1줄에 PR2~PR4 값을 채워요 (v0.3.8). 측정값·위치·판정 비우기만 고칠 수 있어요
GRANT SELECT ON press_data TO sfaas_nr;   -- 고쳐 쓸 때 기존 값·새 값(EXCLUDED)을 읽어야 해서 조회도 필요
GRANT UPDATE (equipment_id, measured_at, pressure_left, pressure_right, pressure_diff, press_time, pressure_total, result)
    ON press_data TO sfaas_nr;
-- 설비 신호: 설비마다 1줄을 덮어씀
GRANT SELECT, INSERT, UPDATE ON equipment_signal TO sfaas_nr;
-- v0.3.9 공정 이동 기록: 들어갈 때 줄 생성(started_at), 나갈 때 ended_at
GRANT SELECT, INSERT ON product_flow TO sfaas_nr;
GRANT UPDATE (started_at, ended_at) ON product_flow TO sfaas_nr;
-- 새 줄 번호(id) 매기기
GRANT USAGE ON SEQUENCE coil_receipt_receipt_id_seq, slitting_data_id_seq, press_data_id_seq,
                        welding_data_id_seq, painting_data_id_seq, inspection_result_inspection_id_seq,
                        product_flow_flow_id_seq TO sfaas_nr;

-- 확인: 아래 결과에 두 계정의 권한 수가 나오면 완료 (sfaas_be 22, sfaas_nr 8)
SELECT grantee, count(DISTINCT table_name) AS tables
FROM information_schema.role_table_grants
WHERE grantee IN ('sfaas_be', 'sfaas_nr')
GROUP BY grantee ORDER BY grantee;
