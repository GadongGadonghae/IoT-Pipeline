"""ERD 이미지 생성기 (DB v0.3.x)
사용: python make_erd.py <출력.dot>  →  dot -Tpng -Gdpi=110 <출력.dot> -o ERD.png
- 실행 전: PostgreSQL에 sfaas DB를 만들고 schema.sql을 실행해 둬야 해요 (구조만 있으면 됨)
- 아래 q() 함수의 psql 접속 정보(-h, -p, -U)를 내 환경에 맞게 바꿔요
- 필요: Graphviz(dot), 한글 글꼴 Noto Sans CJK KR
- users로 가는 선은 생략하고 칸에 FK→users로 표시, equipment에서 나가는 선은 묶어서 그려요
"""
import subprocess, json, sys, html
def q(sql):
    r = subprocess.run(["psql","-h","/tmp","-p","5499","-U","postgres","-d","sfaas","-At","-F","\t","-c",sql],capture_output=True,text=True,check=True)
    return [l.split("\t") for l in r.stdout.strip().splitlines()]
cols = q("""SELECT c.table_name, c.column_name,
  CASE WHEN c.data_type='character varying' THEN 'varchar('||c.character_maximum_length||')'
       WHEN c.data_type='character' THEN 'char('||c.character_maximum_length||')'
       WHEN c.data_type='numeric' THEN 'numeric('||c.numeric_precision||','||c.numeric_scale||')'
       WHEN c.data_type LIKE 'timestamp%' THEN 'timestamp'
       WHEN c.column_default LIKE 'nextval%' AND c.data_type='bigint' THEN 'bigserial'
       WHEN c.column_default LIKE 'nextval%' THEN 'serial'
       ELSE c.data_type END, c.is_nullable
  FROM information_schema.columns c WHERE c.table_schema='public' ORDER BY c.table_name, c.ordinal_position""")
pks = {(t,c) for t,c in q("""SELECT tc.table_name, kcu.column_name FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name
  WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public'""")}
fks = q("""SELECT cl.relname, a.attname, fcl.relname FROM pg_constraint con
  JOIN pg_class cl ON cl.oid=con.conrelid JOIN pg_class fcl ON fcl.oid=con.confrelid
  JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=ANY(con.conkey)
  WHERE con.contype='f' ORDER BY 1,2""")
fkset = {(t,c) for t,c,_ in fks}
fkto = {(t,c):ft for t,c,ft in fks}
tables = {}
for t,c,ty,nl in cols: tables.setdefault(t,[]).append((c,ty,nl))

groups = [
 ("① 기준정보", "#E8F1FB", "#2F6DB5", {"process":"공정","line":"라인","equipment":"설비 39대","downtime_code":"비가동 코드","users":"사용자","threshold":"관리값 기준 (설비별)"}),
 ("② 계획", "#FFF4E0", "#C77C00", {"production_target":"생산 목표"}),
 ("③ 수집 데이터 (한 줄 = 제품 1개)", "#E9F7EE", "#2E8B57", {"coil_receipt":"코일 입고","slitting_data":"슬리팅","press_data":"프레스","welding_data":"용접","painting_data":"도장","inspection_result":"출고 검사"}),
 ("④ 이벤트·이력", "#F4ECFA", "#7B4BA8", {"equipment_status_history":"상태 이력","downtime_event":"비가동","alarm":"알람 (NEW)","paint_route_history":"도장 경로 이력 (NEW)","equipment_signal":"설비 신호 (NEW)","reassignment_report":"재배정 계획서","reassignment_detail":"재배정 상세"}),
 ("⑤ 기타", "#F2F2F2", "#666666", {"chat_message":"채팅 (NEW, 후순위)"}),
]
FONT = "Noto Sans CJK KR"
out = [f'digraph ERD {{ graph [rankdir=LR, splines=spline, concentrate=true, nodesep=0.35, ranksep=1.1, fontname="{FONT}", fontsize=22, label="라인 가동 현황 모니터링 시스템 · ERD v0.3.5 (2026-11-17, 테이블 21개)", labelloc=t, pad=0.4, bgcolor=white];',
       f'node [shape=plain, fontname="{FONT}"]; edge [fontname="{FONT}", color="#666666", arrowhead=crow, arrowtail=none, penwidth=1.2];']
for gi,(gname,bg,hd,tbls) in enumerate(groups):
    out.append(f'subgraph cluster_{gi} {{ label="{gname}"; fontsize=18; fontcolor="{hd}"; style="rounded,filled"; fillcolor="{bg}"; color="{hd}"; margin=14;')
    for t,label in tbls.items():
        rows = []
        for c,ty,nl in tables[t]:
            key = "PK" if (t,c) in pks else ""
            if (t,c) in fkset:
                fk = "FK→" + fkto[(t,c)] if fkto[(t,c)] == "users" else "FK"
                key = (key+" · "+fk) if key else fk
            weight = "<B>%s</B>" % c if (t,c) in pks else c
            color = "#B03A2E" if (t,c) in fkset and (t,c) not in pks else "#222222"
            rows.append(f'<TR><TD ALIGN="LEFT" PORT="{c}"><FONT COLOR="{color}">{weight}</FONT></TD><TD ALIGN="LEFT"><FONT COLOR="#777777" POINT-SIZE="10">{ty}</FONT></TD><TD ALIGN="LEFT" PORT="{c}_out">{("<FONT COLOR=\"%s\" POINT-SIZE=\"10\"><B>%s</B></FONT>" % (hd,key)) if key else " "}</TD></TR>')
        out.append(f'"{t}" [label=<<TABLE BORDER="1" COLOR="{hd}" CELLBORDER="0" CELLSPACING="0" CELLPADDING="4" BGCOLOR="white"><TR><TD COLSPAN="3" BGCOLOR="{hd}"><FONT COLOR="white" POINT-SIZE="13"><B>{t}</B> · {html.escape(label)}</FONT></TD></TR>{"".join(rows)}</TABLE>>];')
    out.append("}")
seen=set()
pk_of = {}
for (t,c) in pks: pk_of.setdefault(t,c)
for t,c,ft in fks:
    if (t,c,ft) in seen or ft == 'users': continue
    seen.add((t,c,ft))
    fcol = 'line_id' if ft=='line' else pk_of[ft]
    out.append(f'"{ft}":"{fcol}_out":e -> "{t}":"{c}":w [dir=forward];')
out.append('legend [label=<<TABLE BORDER="0" CELLPADDING="3"><TR><TD ALIGN="LEFT"><B>읽는 법</B></TD></TR><TR><TD ALIGN="LEFT">PK: 행마다 고유한 값 · <FONT COLOR="#B03A2E">빨간 글씨</FONT> = FK (다른 테이블을 가리킴)</TD></TR><TR><TD ALIGN="LEFT">선의 까마귀발(갈래) 쪽이 "여러 개" 쪽 (1:N)</TD></TR><TR><TD ALIGN="LEFT">FK→users (입력·확인한 사람) 칸은 선을 생략했어요</TD></TR><TR><TD ALIGN="LEFT">(NEW) v0.3에서 새로 생긴 테이블</TD></TR></TABLE>>];')
out.append("}")
open(sys.argv[1],"w").write("\n".join(out))
print(len(fks), "FK", len(seen), "edges")
