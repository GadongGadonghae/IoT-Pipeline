"""리허설 데이터 생성기 (DB v0.3.9 · 11/17 제품 추적 + 11/18 공정 시작 시각)
사용: python make_rehearsal.py <kamp_data 폴더> <출력 폴더> [길이(분), 기본 3]
- 필요: pandas, openpyxl
- 출력: press·welding·painting·inspection·slitting_<N>min.csv, rehearsal_<N>min_data.sql, 요약 JSON(화면 출력)
- 방식: 공정마다 대기열이 있는 시뮬레이션. 앞 공정이 끝난 제품이 줄을 서고, 설비가 비면 처리해요.
  처리 시간은 흔들림 없이 고정(JITTER = 0). 재공은 공정 처리 시간 차이로만 생겨요 (프레스 17초 > 박자 16초). 11/18: 검사 7→2초로 바꿔 검사 병목을 없앰.
- 공정 시간(CT), 라인 박자(TAKT), 사건(불량·고장 시각)은 아래 상수에서 바꿔요.
- 제품 번호(product_id): 슬리팅 투입 때 생겨요 ({라인}-{날짜}-{라인 안 순번 4자리}). 모든 공정 메시지에 붙어요.
- 프레스: 제품 1개 = DB 1줄. PR1 통과 때 줄이 생기고(INSERT) PR2~PR4는 같은 줄에 값을 채워요(UPDATE).
  PR1 = pressure_left, PR2 = pressure_right, PR3 = pressure_diff, PR4 = press_time (메시지에는 자기 항목 1개만)
  CSV·메시지는 프레스마다 1줄(1건)이에요. 값이 붙을 때마다 백엔드가 판정 → 벗어나면 그 자리에서 NG·폐기
- 알람은 관리값 이탈일 때만 (CRITICAL). 고장은 알람 없이 상태 D + 비가동으로만 남겨요.
"""
import sys, json, random
from datetime import datetime, timedelta
import pandas as pd

kamp, out = sys.argv[1], sys.argv[2]
random.seed(1118)

# ---------------- 설정 ----------------
MIN = int(sys.argv[3]) if len(sys.argv) > 3 else 3   # 리허설 길이(분). 명령 3번째 칸 (기본 3)
END = MIN * 60
T0 = datetime(2026, 11, 18, 9, 0, 0)
LINES = list("ABCDEF")
PAINT_OF = {l: ("PT1" if l in "ABC" else "PT2") for l in LINES}
TAKT = 16                            # 라인마다 16초에 1개 투입 (슬리팅 처리 시간과 같음) → 6개 라인 합계 1분에 22.5개 (검사 2초 = 1분에 30개 → 검사는 병목 아님, 11/18)
CT = dict(SL=16, PR=17, WD=17, PT=4, VI=2)   # 제품 1개 처리 시간(초). 프레스는 1대당, 라인에 4대
JITTER = 0                           # 처리 시간 흔들림 없음 (11/18 결정: 재공은 공정 처리 시간 차이로만). 예전 값 0.15
COILS = -(-len(LINES) * -(-END // TAKT) // 20)   # 시작 전 입고 코일 롤 수 (1롤 = 20개). 길이에 맞춰 자동: 3분 4 · 5분 6 · 10분 12
FAULT_EQ, FAULT_ON, FAULT_OFF = "C-PR3", 90, 155   # 65초 고장 → 1분 이상이라 비가동 1건으로 셈
NG_PRESS = ("B", 2, "B-PR2")         # (라인, 라인 내 제품 번호 k(0부터), 불량이 나는 프레스) → 우 압력 상한 초과
VISION_NG = ("E", 2)                 # E라인 2번째로 검사받는 제품

TH = {
    "press": dict(press_time=(540, 570.6), pressure_left=(270, 285.3),
                  pressure_right=(267, 276.3), pressure_total=(541, 560)),   # 정상 행 고르기용 (판정 기준은 아래 PRESS_JUDGE)
    "weld": dict(press_force=(2.16, 9.27), current=(14.52, 15.07),
                 voltage=(2.467, 2.847), weld_time=(70, 73)),
    "paint": dict(ph=(1.51, 5.18), temperature=(18.54, 27.99),
                  current=(1.63, 4.49), voltage=(1.39, 4.99)),
}

# 백엔드 판정 순서와 같은 설비별 기준 (seed.sql threshold와 같은 값)
PRESS_JUDGE = {"PR1": ("pressure_left", "좌 압력", 270, 285.3),
               "PR2": ("pressure_right", "우 압력", 267, 276.3),
               "PR3": ("pressure_diff", "압력 차", -1.8, 12.5),     # 좌 − 우 (PR3이 잰 값)
               "PR4": ("press_time", "가압 시간", 540, 570.6)}
PRESS_COLS = ["pressure_left", "pressure_right", "pressure_diff", "press_time"]
DATE = T0.strftime("%Y%m%d")
def pid_of(line, k):
    return f"{line}-{DATE}-{k + 1:04d}"

def in_spec(df, th):
    m = pd.Series(True, index=df.index)
    for c, (lo, hi) in th.items():
        m &= df[c].between(lo, hi)
    return df[m].reset_index(drop=True)

# ---------------- KAMP 원본 (정상 범위 행만) ----------------
p = pd.read_excel(f"{kamp}/07. Dataset_Press/dataset/Press_RawDataSet.xlsx")
p = p.rename(columns={"Press time(ms)": "press_time", "Pressure 1": "pressure_left",
                      "Pressure 2": "pressure_right", "Pressure 5": "pressure_total"})
p = in_spec(p, TH["press"])
p = p[(p.pressure_left - p.pressure_right).between(-1.8, 12.5)].reset_index(drop=True)   # 압력 차도 정상인 행만
w = pd.read_excel(f"{kamp}/05. Dataset_Welding/dataset/Welding Data Set_01.xlsx")
w = w.rename(columns={"weld force(bar)": "press_force", "weld current(kA)": "current",
                      "weld Voltage(v)": "voltage", "weld time(ms)": "weld_time"})
w = in_spec(w, TH["weld"])
c = pd.read_csv(f"{kamp}/Dataset_품질 이상탐지 진단(도금욕) AI 데이터셋/data/kemp-abh-sensor-2021.09.06.csv")
c = c.rename(columns={"pH": "ph", "Temp": "temperature", "Current": "current", "Voltage": "voltage"})
c = in_spec(c, TH["paint"])

def sampler(df, cols, start):
    i = [start]
    def nxt():
        r = df.iloc[i[0] % len(df)]
        i[0] += 1
        return {k: float(r[k]) for k in cols}
    return nxt

press_next = {f"{l}-PR{n}": sampler(p, list(TH["press"]), 600 * (li * 4 + n))
              for li, l in enumerate(LINES) for n in range(1, 5)}
weld_next = {f"{l}-WD": sampler(w, list(TH["weld"]), 900 * li) for li, l in enumerate(LINES)}
paint_next = {"PT1": sampler(c, list(TH["paint"]), 0), "PT2": sampler(c, list(TH["paint"]), 600)}

def jit(t):
    return t * (1 + random.uniform(-JITTER, JITTER))

def run(eq, free, arrive, base, fixed=False):
    """설비 eq에서 처리: 도착·설비 빈 시각 중 늦은 때 시작. 고장 구간에 걸리면 그만큼 밀림"""
    start = max(arrive, free)
    dur = base if fixed else jit(base)
    if eq == FAULT_EQ:
        if FAULT_ON <= start < FAULT_OFF:
            start = FAULT_OFF
        elif start < FAULT_ON < start + dur:
            dur += FAULT_OFF - FAULT_ON
    return round(start, 1), round(start + dur, 1)

# ---------------- 라인 시뮬레이션 ----------------
rows = {"SL": [], "PR": [], "WD": [], "PT": [], "VI": []}
alarms = []
to_paint = []        # (도장 앞 도착 시각, 라인, k)
for line in LINES:
    stages = [("SL", f"{line}-SL", CT["SL"])] + \
             [("PR", f"{line}-PR{n}", CT["PR"]) for n in range(1, 5)] + \
             [("WD", f"{line}-WD", CT["WD"])]
    free = {eq: 0.0 for _, eq, _ in stages}
    k = 0
    while TAKT * k < END:
        t = float(TAKT * k)                     # 슬리팅 투입 시각
        for kind, eq, base in stages:
            start, done = run(eq, free[eq], t, base)
            if done > END:
                t = None
                break
            free[eq] = done
            t = done
            note, res = "", "OK"
            if kind == "SL":
                vals = {}
            elif kind == "PR":
                no = eq.split("-")[1]
                item, label, lo, hi = PRESS_JUDGE[no]
                smp = press_next[eq]()
                smp["pressure_diff"] = round(smp["pressure_left"] - smp["pressure_right"], 3)
                vals = {item: smp[item]}                    # 이 프레스가 맡은 항목 1개만
                if (line, k) == NG_PRESS[:2] and eq == NG_PRESS[2]:
                    vals = {item: 278.4}
                    res, note = "NG", f"★ 공정 불량: {label} 상한 초과 → 이 프레스에서 폐기"
                    alarms.append(dict(eq=eq, item=item, val=278.4, lo=lo, hi=hi, level="CRITICAL",
                                       msg=f"프레스 {label} 상한 초과", t=done, clear=None,
                                       raw=dict(product_id=pid_of(line, k), **vals), line=line))
            else:
                vals = weld_next[eq]()
            rows[kind].append(dict(t=done, s=start, eq=eq, line=line, k=k, pid=pid_of(line, k), vals=vals, result=res, note=note))
            if res == "NG":
                t = None
                break
        if t is not None:
            to_paint.append((t, line, k))
        k += 1

# 도장 (공용 2대, 먼저 온 순서)
pfree = {"PT1": 0.0, "PT2": 0.0}
to_vi = []
for arr, line, k in sorted(to_paint, key=lambda x: (x[0], x[1])):
    pt = PAINT_OF[line]
    start, done = run(pt, pfree[pt], arr, CT["PT"], fixed=True)
    if done > END:
        continue
    pfree[pt] = done
    rows["PT"].append(dict(t=done, s=start, eq=pt, line=line, k=k, pid=pid_of(line, k), vals=paint_next[pt](), result="OK", note=""))
    to_vi.append((done, line, k, pt))

# 검사 (공용 1대)
vfree, seq = 0.0, {l: 0 for l in LINES}
for arr, line, k, pt in sorted(to_vi, key=lambda x: (x[0], x[1])):
    start, done = run("VI", vfree, arr, CT["VI"], fixed=True)
    if done > END:
        continue
    vfree = done
    seq[line] += 1
    n = seq[line]
    res, note = ("NG", "★ 비전 불량 → 출고하지 않음") if (line, n) == VISION_NG else ("OK", "")
    rows["VI"].append(dict(t=done, s=start, eq="VI", line=line, k=k, paint=pt,
                           pid=pid_of(line, k), result=res, note=note))

# 알람 해제: 같은 설비·항목의 다음 줄이 범위 안이면 그 시각에 해제 (백엔드 판정 로직 v1)
for a in alarms:
    nxt = [r for r in rows["PR"] if r["eq"] == a["eq"] and r["t"] > a["t"]]
    a["clear"] = min(r["t"] for r in nxt) if nxt else None
    sofar = {}                                   # raw_values: 그 순간 제품 줄에 붙어 있던 값 전부
    for r in sorted(rows["PR"], key=lambda r: r["t"]):
        if r["pid"] == a["raw"]["product_id"] and r["t"] <= a["t"]:
            sofar.update(r["vals"])
    a["raw"] = dict(product_id=a["raw"]["product_id"], **sofar)

# ---------------- CSV ----------------
def ts(sec):
    t = T0 + timedelta(seconds=sec)
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-5] if t.microsecond else t.strftime("%Y-%m-%d %H:%M:%S")

def write_csv(name, kind, cols):
    recs, per = [], {}
    for r in sorted(rows[kind], key=lambda r: (r["t"], r["eq"])):
        per[r["eq"]] = per.get(r["eq"], 0) + 1
        rec = {"planned_offset_sec": r["t"], "planned_start_sec": r["s"], "equipment_id": r["eq"], "row_no": per[r["eq"]], "product_id": r["pid"]}
        if kind == "PT":
            rec["line_id"] = r["line"]
        rec.update({c: r["vals"].get(c) for c in cols})   # 프레스: 자기 항목만 있고 나머지 칸은 비움
        rec["scenario_note"] = r["note"]
        recs.append(rec)
    pd.DataFrame(recs).to_csv(f"{out}/{name}", index=False, encoding="utf-8-sig")

write_csv(f"press_{MIN}min.csv", "PR", PRESS_COLS)
write_csv(f"welding_{MIN}min.csv", "WD", list(TH["weld"]))
write_csv(f"painting_{MIN}min.csv", "PT", list(TH["paint"]))
write_csv(f"slitting_{MIN}min.csv", "SL", [])
pd.DataFrame([{"planned_offset_sec": r["t"], "planned_start_sec": r["s"], "equipment_id": "VI", "product_id": r["pid"], "line_id": r["line"],
               "paint_equipment_id": r["paint"], "result": r["result"], "scenario_note": r["note"]}
              for r in sorted(rows["VI"], key=lambda r: r["t"])]
             ).to_csv(f"{out}/inspection_{MIN}min.csv", index=False, encoding="utf-8-sig")

# ---------------- SQL ----------------
def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return repr(round(v, 3)) if isinstance(v, float) else str(v)

L = [f"""-- =====================================================================
-- {MIN}분 리허설 데이터 (2026-11-18 09:00:00 ~ {ts(END)[11:]}) · DB v0.3.9
-- 처리 시간(초): 슬리팅 {CT['SL']} · 프레스 {CT['PR']} ×4 · 용접 {CT['WD']} (±{int(JITTER*100)}%) · 도장 {CT['PT']} · 검사 {CT['VI']}
-- 라인 박자 {TAKT}초 → 6개 라인 합계 1분에 {60 * len(LINES) / TAKT:g}개 투입, 검사는 1분에 {60 / CT['VI']:g}개
-- 수집 → DB 연결 없이 화면·집계를 확인하려고, 리허설이 끝난 순간의 DB 상태를 그대로 넣어요.
-- 실행 순서: demo_reset.sql → rehearsal_{MIN}min_data.sql
-- ⚠️ 가짜 시연 데이터. 측정값은 KAMP 원본(정상 범위 행), 불량값만 일부러 넣었어요.
-- 11/18: 공정 테이블 result는 비워 둬요(NULL) → 백엔드가 판정·알람. 비전 결과·상태 이력(고장)·비가동은 넣어요
-- 시나리오: 시나리오_{MIN}분.md
-- =====================================================================

BEGIN;

-- 0. 생산 목표 (이미 있으면 그대로 둠)
INSERT INTO production_target (target_date, line_id, target_qty) VALUES"""]
L.append(",\n".join(f"    ('2026-11-18', '{l}', 250)" for l in LINES) + "\nON CONFLICT (target_date, line_id) DO NOTHING;\n")
L.append(f"-- 1. 코일 입고: {COILS}롤 × 20개 = 제품 {COILS*20}개분\nINSERT INTO coil_receipt (material_id, received_at, product_qty) VALUES\n"
         + ",\n".join(f"    ('COIL-20261118-{i:03d}', '2026-11-18 08:55:00', 20)" for i in range(1, COILS + 1)) + ";\n")

def insert(table, cols, recs, comment):
    if not recs:
        return
    L.append(f"-- {comment}\nINSERT INTO {table} ({', '.join(cols)}) VALUES")
    L.append(",\n".join("    (" + ", ".join(q(v) for v in r) + ")" for r in recs) + ";\n")

srt = lambda k: sorted(rows[k], key=lambda r: (r["t"], r["eq"]))
# v0.3.9: 공정 테이블 = 처리 끝난 값만 (measured_at = 끝난 시각 고정), 들어간·나간 시각은 product_flow
insert("slitting_data", ["product_id", "equipment_id", "measured_at", "torque", "current", "voltage", "result"],
       [(r["pid"], r["eq"], ts(r["t"]), None, None, None, None) for r in srt("SL")], "2. 슬리팅 (가상 슬리팅은 측정값 비움, result는 백엔드가 판정)")
press_prod = {}   # 제품 1개 = 1줄: PR1 끝날 때 생기고 PR2~PR4가 값을 채운 "마지막 상태"
for r in srt("PR"):
    d = press_prod.setdefault(r["pid"], dict(pid=r["pid"], vals={}, result="OK", t0=r["t"]))   # t0 = PR1 끝난 시각
    d["vals"].update(r["vals"]); d["eq"], d["t"] = r["eq"], r["t"]
    if r["result"] == "NG":
        d["result"] = "NG"
insert("press_data", ["product_id", "equipment_id", "measured_at", *PRESS_COLS, "result"],
       [(d["pid"], d["eq"], ts(d["t0"]), *(d["vals"].get(c) for c in PRESS_COLS), None)
        for d in sorted(press_prod.values(), key=lambda d: (d["t0"], d["eq"]))],
       "3. 프레스 (KAMP 7번) · 제품 1개 = 1줄. equipment_id = 마지막으로 끝낸 단 (PR4면 프레스 완료), measured_at = PR1 끝난 시각(고정)")
insert("welding_data", ["product_id", "equipment_id", "measured_at", *TH["weld"].keys(), "result"],
       [(r["pid"], r["eq"], ts(r["t"]), *r["vals"].values(), None) for r in srt("WD")], "4. 용접 (KAMP 5번)")
insert("painting_data", ["product_id", "equipment_id", "measured_at", "line_id", *TH["paint"].keys(), "result"],
       [(r["pid"], r["eq"], ts(r["t"]), r["line"], *r["vals"].values(), None) for r in srt("PT")], "5. 도장 (KAMP 도금욕, 도장 끝날 때의 용액 상태)")
insert("inspection_result", ["equipment_id", "product_id", "line_id", "paint_equipment_id", "result", "inspected_at", "shipped_at"],
       [("VI", r["pid"], r["line"], r["paint"], r["result"], ts(r["t"]), None if r["result"] == "NG" else ts(r["t"] + 1))
        for r in sorted(rows["VI"], key=lambda r: r["t"])], "6. 출고 검사 (비전 판정, 양품은 1초 뒤 출고)")
insert("product_flow", ["product_id", "equipment_id", "line_id", "started_at", "ended_at"],
       [(r["pid"], r["eq"], r["line"], ts(r["s"]), ts(r["t"]))
        for r in sorted((r for k in ("SL", "PR", "WD", "PT", "VI") for r in rows[k]), key=lambda r: (r["s"], r["eq"]))],
       "6-1. 공정 이동 기록 (v0.3.9): 제품이 설비 1대를 거칠 때마다 1줄 (들어간·나간 시각)")
eqs =[f"{l}-SL" for l in LINES] + [f"{l}-PR{n}" for l in LINES for n in range(1, 5)] + \
      [f"{l}-WD" for l in LINES] + ["PT1", "PT2", "VI"]
st = []
for e in eqs:
    if e == FAULT_EQ:
        st += [(e, "R", ts(0), ts(FAULT_ON)), (e, "D", ts(FAULT_ON), ts(FAULT_OFF)), (e, "R", ts(FAULT_OFF), None)]
    else:
        st.append((e, "R", ts(0), None))
insert("equipment_status_history", ["equipment_id", "status", "started_at", "ended_at"], st,
       f"7. 상태 이력 ({FAULT_EQ}만 {FAULT_ON}~{FAULT_OFF}초 고장)\n--    ※ 단순화: 앞 공정 대기(I) 구간은 넣지 않음 (백엔드 판정 결과로 생길 부분)")
insert("downtime_event", ["equipment_id", "status", "code", "detail", "started_at", "ended_at", "action_memo"],
       [(FAULT_EQ, "D", "UP_BREAKDOWN", "고장 신호 수신 (리허설 버튼)", ts(FAULT_ON), ts(FAULT_OFF), "리허설: 복구 버튼으로 재가동")],
       f"8. 비가동 ({FAULT_OFF - FAULT_ON}초 → 1분 이상이라 비가동 1건)")
L.append("-- 9. 알람: 넣지 않아요 (11/18: 백엔드 판정 로직이 공정 result와 알람을 만들어요)\n")
insert("equipment_signal", ["equipment_id", "last_seen_at", "fault", "updated_at"],
       [(e, ts(END), 0, ts(END)) for e in eqs], "10. 설비 신호: 리허설 끝 시점에 모두 살아 있음, 고장 없음")
L.append("""-- 11. 상태 이력의 고장 구간 ↔ 비가동 번호 연결
UPDATE equipment_status_history h SET downtime_id = d.downtime_id
FROM downtime_event d
WHERE h.equipment_id = d.equipment_id AND h.started_at = d.started_at AND h.status <> 'R';

COMMIT;
""")
open(f"{out}/rehearsal_{MIN}min_data.sql", "w", encoding="utf-8").write("\n".join(L))

# ---------------- 요약 ----------------
def cnt(kind, cond=lambda r: True):
    return sum(1 for r in rows[kind] if cond(r))
wip = {}   # 백엔드 방식: 큰 공정 사이만, 누적
for l in LINES:
    sl = cnt("SL", lambda r: r["line"] == l)
    pr = sum(1 for d in press_prod.values() if d["pid"][0] == l)                       # press_data 줄 수
    pr_ok = sum(1 for d in press_prod.values() if d["pid"][0] == l and d["eq"].endswith("PR4") and d["result"] == "OK")
    wd = cnt("WD", lambda r: r["line"] == l)
    pt = cnt("PT", lambda r: r["line"] == l)
    vi = cnt("VI", lambda r: r["line"] == l)
    wip[l] = {"SL>PR": sl - pr, "PR>WD": pr_ok - wd, "WD>PT": wd - pt, "PT>VI": pt - vi}
summary = {
    "sl": cnt("SL"), "press_msgs": cnt("PR"), "press_rows": len(press_prod),
    "press_done": sum(1 for d in press_prod.values() if d["eq"].endswith("PR4")),
    "press_ng": cnt("PR", lambda r: r["result"] == "NG"),
    "weld": cnt("WD"), "paint": cnt("PT"), "vi": cnt("VI"),
    "vi_ok": cnt("VI", lambda r: r["result"] == "OK"),
    "vi_by_line": {l: cnt("VI", lambda r: r["line"] == l) for l in LINES},
    "first_vi": min(r["t"] for r in rows["VI"]) if rows["VI"] else None,
    "press_ng_t": [r["t"] for r in rows["PR"] if r["result"] == "NG"],
    "alarms": [(a["eq"], a["item"], a["val"], a["t"], a["clear"]) for a in alarms],
    "press_msgs_by": {n: cnt("PR", lambda r, n=n: r["eq"].endswith(n)) for n in ("PR1", "PR2", "PR3", "PR4")},
    "vi_ng": [(r["pid"], r["t"]) for r in rows["VI"] if r["result"] == "NG"],
    "paint_by": {pt: cnt("PT", lambda r, pt=pt: r["eq"] == pt) for pt in ("PT1", "PT2")},
    "coil_wip": COILS * 20 - cnt("SL"),
    "wip": wip,
    "wip_total": {k: sum(wip[l][k] for l in LINES) for k in wip["A"]},
}
print(json.dumps(summary, ensure_ascii=False))
