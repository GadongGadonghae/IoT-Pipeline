-- 슬리팅 관리값 기준 (11/21) · 슬리터 A~F-SL 전류·전압·토크
-- 그동안 슬리팅은 기준이 없어서 설정 > 관리값 기준에 아무것도 안 떴어요.
-- 값: 실측(LS SV-iG5A, 11/20 밤 A-SL) 근처 + 재생기 더미 값(전압 약 185 V ± 6 · 전류 약 0.1 A ± 0.05)을 모두 담는 범위
--   ⚠️ PLC 실물로 돌리면 시작 직후(전압 60 V대)·정지(0)는 기준 밖 → 불량으로 판정될 수 있어요
--   토크는 인버터가 안 줘서 0 (기준 0~1)
-- 여러 번 실행해도 괜찮아요 (있으면 값만 바꿈). 설정 화면에서 고친 값을 다시 덮어쓰니 주의
-- 실행 (SQL Shell 또는 Git Bash):  \i C:/jyc/sfaas_project/sfaas/player/threshold_slitting.sql
SET client_encoding = 'UTF8';
INSERT INTO threshold (equipment_id, item_name, item_label, unit, lower_limit, upper_limit)
SELECT l || '-SL', v.item, v.label, v.unit, v.lo, v.hi
FROM unnest(ARRAY['A','B','C','D','E','F']) AS l,
     (VALUES ('current', '전류', 'A', 0.040, 0.160),
             ('voltage', '전압', 'V', 170.000, 200.000),
             ('torque',  '토크', 'N·m', 0.000, 1.000)) AS v(item, label, unit, lo, hi)
ON CONFLICT (equipment_id, item_name)
DO UPDATE SET lower_limit = EXCLUDED.lower_limit, upper_limit = EXCLUDED.upper_limit, updated_at = now();

SELECT equipment_id, item_name, lower_limit, upper_limit FROM threshold WHERE equipment_id LIKE '%-SL' ORDER BY 1, 2;
