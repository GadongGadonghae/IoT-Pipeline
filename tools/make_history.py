"""과거 기록(시연용 더미 데이터) 생성기 · DB v0.3.13 · 2026-11-20
사용: python make_history.py [기준 날짜] [--days 3]
  예) python make_history.py 2026-11-24          → 11/21·11/22·11/23 3일치
      python make_history.py 2026-11-20 --days 1 → 11/19 하루치
  - 기준 날짜를 안 쓰면 오늘. 기준 날짜 "전" N일을 달력 그대로(주말 포함) 만들어요.
  - 출력: sfaas/history/history_<기준날짜>.sql (커서 Git에 안 올림)
  - 그 SQL은 맨 앞에서 "기준 날짜 이전 기록"을 전부 지우고 새로 넣어요 → 몇 번 다시 넣어도 결과가 같아요.
    오늘(기준 날짜) 기록은 건드리지 않아요.
  - 필요: pandas, numpy, openpyxl

하루 흐름: 08:00 투입 시작 → 12:00~13:00 점심(계획정지 PL_SHIFT) → 19:00 투입 끝 (이미 들어간 제품은 끝까지 처리)
  처리 시간은 리허설과 같고(make_rehearsal.py) ±3% 흔들림. 라인 박자 17초(프레스와 같게 → 재공이 계속 쌓이지 않음)
  측정값은 KAMP 원본의 정상 범위 행, 이상치는 KAMP 원본에 실제로 있는 기준 밖 값
이상치·고장 (보통): 공정 불량 약 0.5% (프레스 0.3·용접 0.1·도장 0.1%) → 그 자리 폐기 + 알람 / 비전 불량 약 1% /
  장비고장 하루 2~3건 (10~40분, 라인 설비 무작위) → 앞 프레스는 후공정 정지 대기(UP_BLOCKED), 뒤 공정은 재공부족(UP_SHORTAGE)
대기(I)는 10초 이상만 기록 (짧은 건 가동으로 봄). 판정 결과(result)·상태 이력·비가동·알람을 모두 채워 넣어요 (백엔드가 다시 판정하지 않게).
코일 입고는 매일 07:30·12:30, 그날 쓴 만큼 딱 맞게 (다음 날로 넘어가는 재공 없음).
"""
import argparse, json, math, os, random
from datetime import date, datetime, timedelta
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(description="과거 기록(더미) SQL 만들기")
ap.add_argument("base", nargs="?", default=date.today().isoformat(), help="기준 날짜 (이 날짜 전 N일을 만듦). 기본 오늘")
ap.add_argument("--days", type=int, default=3, help="만들 날 수 (기본 3)")
ap.add_argument("--no-morning", action="store_true", help="기준 날짜(오늘) 오전 더미를 넣지 않음")
ap.add_argument("--kamp", default=os.path.join(HERE, "..", "..", "kamp_data"), help="KAMP 원본 폴더")
ap.add_argument("--out", default=os.path.join(HERE, "..", "history"), help="출력 폴더")
args = ap.parse_args()
BASE = date.fromisoformat(args.base)
DAYS = [BASE - timedelta(days=n) for n in range(args.days, 0, -1)]
random.seed(int(BASE.strftime("%Y%m%d")))   # 같은 기준 날짜면 항상 같은 데이터
np.random.seed(int(BASE.strftime("%Y%m%d")) % (2**32))

# ---------------- 설정 ----------------
LINES = list("ABCDEF")
PAINT_OF = {l: ("PT1" if l in "ABC" else "PT2") for l in LINES}
CT = dict(SL=16, PR=17, WD=17, PT=4, VI=2)
TAKT = 17
JITTER = 0.03
OPEN, CLOSE = 8 * 3600, 19 * 3600
LUNCH = (12 * 3600, 13 * 3600)
TARGET = 2000                         # 라인당 하루 목표
CAP_SL, CAP_WD = 10, 5                # 설비 사이에 쌓을 수 있는 자리: 슬리팅→프레스 10개, 프레스4→용접 5개 (꽉 차면 앞 설비가 멈춤)
IDLE_MIN = 10                         # 이 초 이상인 대기만 I로 기록
P_PRESS_STAGE = 0.00075               # 프레스 단마다 (4단 합계 약 0.3%)
P_WELD, P_PAINT, P_VISION = 0.001, 0.001, 0.01
BREAKS_PER_DAY = (2, 3)
BREAK_MIN = (30, 50)                  # 고장 길이(분). 11/20: 가동률 화면에서 잘 보이게 30분 이상
# 오늘(기준 날짜) 오전 더미 (11/20): 리허설은 점심 뒤에 시작한다고 보고 08:00~12:00 가동 + 12:00~13:00 점심을 넣어요
MORNING_INPUT_END = 11 * 3600 + 52 * 60   # 투입 끝 → 12:00 전에 모두 처리되게
MORNING_BREAKS = (1, 2)                   # 오전 고장 건수
MORNING_PID_OFFSET = 5000                 # 오전 제품 번호 5001부터 (리허설 0001~과 안 겹치게)
DEMO_START = 13 * 3600                    # 이 시각 전 오늘 기록은 더미로 바꾸고, demo_reset은 이 뒤만 지워요
BREAK_MEMO = {   # 고장 조치 메모 (설비 종류별)
    "SL": ["슬리팅 나이프 이빨 빠짐 → 나이프 교체", "인버터 과전류 트립 → 리셋 후 점검", "코일 언코일러 걸림 → 재정렬"],
    "PR": ["유압 호스 누유 → 호스 교체", "금형 센서 오작동 → 센서 재설치", "모터 과열 → 냉각 후 재가동",
           "근접 센서 접점 불량 → 커넥터 교체", "이송 피더 위치 틀어짐 → 재조정"],
    "WD": ["용접 팁 마모 → 팁 교체", "냉각수 유량 부족 → 필터 청소", "에어 압력 저하 → 레귤레이터 조정"]}

TH = {"press": dict(pressure_left=(270, 285.3), pressure_right=(267, 276.3), pressure_diff=(-1.8, 12.5), press_time=(540, 570.6)),
      "weld": dict(press_force=(2.16, 9.27), current=(14.52, 15.07), voltage=(2.467, 2.847), weld_time=(70, 73)),
      "paint": dict(ph=(1.51, 5.18), temperature=(18.54, 27.99), current=(1.63, 4.49), voltage=(1.39, 4.99))}
LABEL = {"pressure_left": "좌 압력", "pressure_right": "우 압력", "pressure_diff": "압력 차", "press_time": "가압 시간",
         "press_force": "가압력", "current": "전류", "voltage": "전압", "weld_time": "통전시간", "ph": "pH", "temperature": "온도"}
PRESS_ITEM = {"PR1": "pressure_left", "PR2": "pressure_right", "PR3": "pressure_diff", "PR4": "press_time"}
PRESS_COLS = ["pressure_left", "pressure_right", "pressure_diff", "press_time"]
EQS = [f"{l}-SL" for l in LINES] + [f"{l}-PR{n}" for l in LINES for n in range(1, 5)] + \
      [f"{l}-WD" for l in LINES] + ["PT1", "PT2", "VI"]

# ---------------- KAMP 원본: 정상 행 / 기준 밖 행 ----------------
def split(df, th):
    ok = pd.Series(True, index=df.index)
    for col, (lo, hi) in th.items():
        ok &= df[col].between(lo, hi)
    return df[ok].reset_index(drop=True), df[~ok & df[list(th)].notna().all(axis=1)].reset_index(drop=True)

kamp = args.kamp
p = pd.read_excel(f"{kamp}/07. Dataset_Press/dataset/Press_RawDataSet.xlsx").rename(columns={
    "Press time(ms)": "press_time", "Pressure 1": "pressure_left", "Pressure 2": "pressure_right"})
p["pressure_diff"] = p.pressure_left - p.pressure_right
p_ok, _ = split(p, TH["press"])
p_bad = {it: p.loc[~p[it].between(*TH["press"][it]) & p[it].notna(), it].to_numpy() for it in PRESS_COLS}
w = pd.read_excel(f"{kamp}/05. Dataset_Welding/dataset/Welding Data Set_01.xlsx").rename(columns={
    "weld force(bar)": "press_force", "weld current(kA)": "current", "weld Voltage(v)": "voltage", "weld time(ms)": "weld_time"})
w_ok, w_bad = split(w, TH["weld"])
c = pd.read_csv(f"{kamp}/Dataset_품질 이상탐지 진단(도금욕) AI 데이터셋/data/kemp-abh-sensor-2021.09.06.csv").rename(columns={
    "pH": "ph", "Temp": "temperature", "Current": "current", "Voltage": "voltage"})
c_ok, c_bad = split(c, TH["paint"])

def pick(df, cols):
    r = df.iloc[random.randrange(len(df))]
    return {k: round(float(r[k]), 3) for k in cols}

def pick_bad(df, th):
    """기준 밖 행 하나 (소수 셋째 자리로 반올림해도 기준 밖인 행)"""
    while True:
        vals = pick(df, list(th))
        out = first_out(vals, th)
        if out:
            return vals, out

def bad_value(item):
    lo, hi = TH["press"][item]
    pool = [v for v in p_bad[item] if not lo <= round(float(v), 3) <= hi]
    if pool:
        return round(float(pool[random.randrange(len(pool))]), 3)
                                                        # 원본에 기준 밖 값이 없으면 기준 바로 바깥 값
    return round(hi + (hi - lo) * random.uniform(0.01, 0.05), 3)

def first_out(vals, th):
    for k, (lo, hi) in th.items():
        if not lo <= vals[k] <= hi:
            return k, lo, hi
    return None

# ---------------- 시뮬레이션 ----------------
def run(eq, free, arrive, base, pauses):
    """도착·설비 빈 시각 중 늦은 때 시작. 점심·고장 구간에 걸리면 그만큼 밀림"""
    start = max(arrive, free)
    for a, b in pauses.get(eq, ()):
        if a <= start < b:
            start = b
    end = start + base * (1 + random.uniform(-JITTER, JITTER))
    for a, b in pauses.get(eq, ()):
        if start < a < end:
            end += b - a
    return start, end

def simulate(day, close=CLOSE, hard_end=None, pid_offset=0, n_breaks=BREAKS_PER_DAY):
    """하루(또는 오전) 흐름. close = 투입 끝, hard_end = 이 시각을 넘겨 끝나는 제품은 통째로 뺌(오전 더미: 12:00)"""
    dstr = day.strftime("%Y%m%d")
    pauses = {eq: [LUNCH] for eq in EQS}
    breaks = []
    cand = [e for e in EQS if e[0] in LINES and "-" in e]
    last_ok = (hard_end or CLOSE) - 1800   # 고장이 끝난 뒤 30분은 다시 흐르게
    for _ in range(random.randint(*n_breaks)):
        for _try in range(50):
            eq = random.choice(cand)
            dur = random.randint(BREAK_MIN[0] * 60, BREAK_MIN[1] * 60)
            a = random.randint(OPEN + 1800, last_ok - dur)
            b = a + dur
            if b <= LUNCH[0] or a >= LUNCH[1]:
                if all(x[0] != eq or b <= x[1] or a >= x[2] for x in breaks):
                    breaks.append((eq, a, b))
                    break
    for eq, a, b in breaks:
        pauses[eq] = sorted(pauses[eq] + [(a, b)])

    rows = []                      # 설비 1대를 거친 기록 (product_flow 1줄)
    to_paint = []
    for line in LINES:
        stages = [("SL", f"{line}-SL", CT["SL"])] + [("PR", f"{line}-PR{n}", CT["PR"]) for n in range(1, 5)] + \
                 [("WD", f"{line}-WD", CT["WD"])]
        free = {eq: float(OPEN) for _, eq, _ in stages}
        slot, k = float(OPEN), 0
        pr1_starts, wd_starts = [], []           # 자리 수 계산용: 프레스1·용접에 들어간 시각 (들어간 순서)
        while True:
            pid = f"{line}-{dstr}-{k + 1 + pid_offset:04d}"
            t, prev, alive = slot, None, True
            for kind, eq, base in stages:
                blk = None
                if kind == "SL" and k >= CAP_SL and pr1_starts[k - CAP_SL] > max(t, free[eq]):
                    blk = (max(t, free[eq]), pr1_starts[k - CAP_SL])   # 프레스 앞 자리가 꽉 차서 슬리팅이 기다림
                    t = blk[1]
                start, end = run(eq, free[eq], t, base, pauses)
                if kind == "SL":
                    if start >= close:
                        alive = None
                        break
                    slot = start + TAKT
                if prev is not None and prev["kind"] == "PR" and prev["eq"][-1] in "123":
                    prev["rel"] = start                 # 프레스 1~3단은 다음 단이 받아 갈 때까지 들고 있음
                    free[prev["eq"]] = start
                free[eq] = end
                if eq.endswith("PR1"):
                    pr1_starts.append(start)
                elif kind == "WD":
                    wd_starts.append(start)
                r = dict(kind=kind, eq=eq, line=line, pid=pid, s=start, t=end, rel=end, vals={}, result="OK", alarm=None, blk=blk)
                if kind == "PR":
                    item = PRESS_ITEM[eq.split("-")[1]]
                    if random.random() < P_PRESS_STAGE:
                        v = bad_value(item)
                        lo, hi = TH["press"][item]
                        r.update(result="NG", alarm=(item, v, lo, hi))
                    else:
                        v = round(float(pick(p_ok, [item])[item]), 3)
                    r["vals"] = {item: v}
                elif kind == "WD":
                    if random.random() < P_WELD and len(w_bad):
                        r["vals"], (it, lo, hi) = pick_bad(w_bad, TH["weld"])
                        r.update(result="NG", alarm=(it, r["vals"][it], lo, hi))
                    else:
                        r["vals"] = pick(w_ok, list(TH["weld"]))
                rows.append(r)
                prev = r
                t = end
                if eq.endswith("PR4") and r["result"] == "OK" and len(wd_starts) >= CAP_WD and wd_starts[-CAP_WD] > end:
                    r["rel"] = t = free[eq] = wd_starts[-CAP_WD]    # 용접 앞 자리가 꽉 차서 프레스4가 들고 기다림
                if r["result"] == "NG":
                    alive = False
                    break
            if alive is None:
                break
            if alive:
                to_paint.append((t, line, pid))
            k += 1

    # 도장 (공용 2대, 먼저 온 순서)
    pfree = {"PT1": float(OPEN), "PT2": float(OPEN)}
    to_vi = []
    for arr, line, pid in sorted(to_paint):
        pt = PAINT_OF[line]
        start, end = run(pt, pfree[pt], arr, CT["PT"], pauses)
        pfree[pt] = end
        r = dict(kind="PT", eq=pt, line=line, pid=pid, s=start, t=end, rel=end, result="OK", alarm=None, blk=None)
        if random.random() < P_PAINT and len(c_bad):
            r["vals"], (it, lo, hi) = pick_bad(c_bad, TH["paint"])
            r.update(result="NG", alarm=(it, r["vals"][it], lo, hi))
        else:
            r["vals"] = pick(c_ok, list(TH["paint"]))
            to_vi.append((end, line, pid, pt))
        rows.append(r)
    # 검사 (공용 1대)
    vfree = float(OPEN)
    for arr, line, pid, pt in sorted(to_vi):
        start, end = run("VI", vfree, arr, CT["VI"], pauses)
        vfree = end
        rows.append(dict(kind="VI", eq="VI", line=line, pid=pid, s=start, t=end, rel=end, paint=pt, blk=None,
                         result="NG" if random.random() < P_VISION else "OK", vals={}, alarm=None))

    # 오전 더미: hard_end(12:00)를 넘겨 끝나는 제품은 통째로 빼요 (점심 뒤로 넘어가는 재공이 남지 않게)
    if hard_end is not None:
        late = {r["pid"] for r in rows if r["rel"] > hard_end}
        rows = [r for r in rows if r["pid"] not in late]
    # 코일이 딱 맞게: 그날 투입 수를 20의 배수로 (가장 늦게 들어간 제품부터 통째로 뺌)
    sl = sorted((r for r in rows if r["kind"] == "SL"), key=lambda r: r["s"])
    drop = {r["pid"] for r in sl[len(sl) - len(sl) % 20:]} if len(sl) % 20 else set()
    rows = [r for r in rows if r["pid"] not in drop]
    return rows, breaks, sl

# ---------------- 상태 이력 (1초 단위로 칠해서 구간으로 묶음) ----------------
R, SHORT, BLOCK, D, S, OFF = range(6)
def timeline(rows, breaks, lunch_from_last=False):
    """lunch_from_last: 오전 더미 — 설비마다 마지막 처리가 끝난 때부터 13:00까지 점심(S)으로"""
    day_end = int(math.ceil(max(r["rel"] for r in rows))) + 1
    L = day_end - OPEN
    out = {}
    by_eq = {}
    for r in rows:
        by_eq.setdefault(r["eq"], []).append(r)
    for eq in EQS:
        arr = np.full(L, SHORT, dtype=np.int8)
        rs = by_eq.get(eq, [])
        last = OPEN
        for r in rs:
            a, b = int(r["s"]) - OPEN, int(math.ceil(r["t"])) - OPEN
            arr[a:b] = R
            if r["rel"] > r["t"]:
                arr[b:int(r["rel"]) - OPEN] = BLOCK
            if r.get("blk"):
                arr[int(math.ceil(r["blk"][0])) - OPEN:int(r["blk"][1]) - OPEN] = BLOCK
            last = max(last, r["rel"])
        # 짧은 대기(10초 미만)는 가동으로
        i = 0
        while i < L:
            if arr[i] in (SHORT, BLOCK):
                j = i
                while j < L and arr[j] == arr[i]:
                    j += 1
                if j - i < IDLE_MIN:
                    arr[i:j] = R
                i = j
            else:
                i += 1
        for e, a, b in breaks:
            if e == eq:
                arr[a - OPEN:b - OPEN] = D
        arr[LUNCH[0] - OPEN:LUNCH[1] - OPEN] = S
        arr[int(math.ceil(last)) - OPEN:] = OFF
        segs, i = [], 0
        while i < L:
            j = i
            while j < L and arr[j] == arr[i]:
                j += 1
            if arr[i] != OFF:
                segs.append((int(arr[i]), OPEN + i, OPEN + j))
            i = j
        if lunch_from_last:
            segs.append((S, int(math.ceil(last)), LUNCH[1]))
        out[eq] = segs
    return out

# ---------------- SQL ----------------
def ts(day, sec):
    t = datetime.combine(day, datetime.min.time()) + timedelta(seconds=round(sec, 1))
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-5]

def cell(v):
    if v is None:
        return r"\N"
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, float):
        return repr(round(v, 3))
    return str(v).replace("\\", "\\\\").replace("\t", " ").replace("\n", " ")

out_lines = []
def copy(table, cols, recs):
    if not recs:
        return
    out_lines.append(f"COPY {table} ({', '.join(cols)}) FROM stdin;")
    out_lines.extend("\t".join(cell(v) for v in rec) for rec in recs)
    out_lines.append("\\.")

first, base_s = DAYS[0].isoformat(), BASE.isoformat()
MORNING = not args.no_morning
# 지우는 경계: 오전 더미를 넣으면 오늘 13:00 전까지 (그 뒤 = 실제 리허설·시연 기록은 그대로)
cut = f"{base_s} {DEMO_START // 3600:02d}:00:00" if MORNING else base_s
MORNING_NOTE = (f"+ 오늘({base_s}) 오전 08:00~12:00 가동 더미 + 점심~13:00 (리허설은 13:00 뒤 시작, 제품 번호 5001~)"
                if MORNING else "오늘 오전 더미 없음 (--no-morning)")
out_lines.append(f"""-- =====================================================================
-- 과거 기록 (시연용 더미) · {first} ~ {DAYS[-1].isoformat()} ({len(DAYS)}일) · DB v0.3.13
-- make_history.py {base_s} --days {len(DAYS)} 로 만듦. 08:00~19:00 (점심 12~13시 계획정지), 고장 30~50분
-- {MORNING_NOTE}
-- ⚠️ 맨 앞에서 {cut} 이전 기록을 전부 지우고 새로 넣어요. 그 뒤 기록(리허설·시연)은 그대로 둬요.
--    오전 더미를 쓰면 발표 당일 점심 뒤(13:00 이후), 리허설 직전에 실행해요 (오전에 백엔드가 남긴 상태 기록을 더미로 바꿈)
-- 실행: SQL Shell에서  \\i C:/jyc/sfaas_project/sfaas/history/history_{BASE.strftime('%Y%m%d')}.sql
-- =====================================================================
SET client_encoding = 'UTF8';
BEGIN;

-- 0. {cut} 이전 기록 지우기 (외래키 순서대로)
DELETE FROM reassignment_report r USING downtime_event d WHERE r.downtime_id = d.downtime_id AND d.started_at < '{cut}';
DELETE FROM equipment_status_history WHERE started_at < '{cut}';
DELETE FROM downtime_event WHERE started_at < '{cut}';
DELETE FROM alarm WHERE raised_at < '{cut}';
DELETE FROM product_flow WHERE COALESCE(started_at, ended_at) < '{cut}';
DELETE FROM slitting_data WHERE measured_at < '{cut}';
DELETE FROM press_data WHERE measured_at < '{cut}';
DELETE FROM welding_data WHERE measured_at < '{cut}';
DELETE FROM painting_data WHERE measured_at < '{cut}';
DELETE FROM inspection_result WHERE inspected_at < '{cut}';
DELETE FROM coil_receipt WHERE received_at < '{cut}';
DELETE FROM paint_route_history WHERE changed_at < '{cut}';
DELETE FROM production_target WHERE target_date < '{base_s}';
""")

summary = []
group_no = 0
for day, morning_only in [(x, False) for x in DAYS] + ([(BASE, True)] if MORNING else []):
    if morning_only:
        rows, breaks, sl = simulate(day, close=MORNING_INPUT_END, hard_end=LUNCH[0], pid_offset=MORNING_PID_OFFSET, n_breaks=MORNING_BREAKS)
    else:
        rows, breaks, sl = simulate(day)
    tl = timeline(rows, breaks, lunch_from_last=morning_only)
    d = lambda sec: ts(day, sec)
    out_lines.append(f"\n-- ===== {day.isoformat()}{' 오전 (08:00~12:00 + 점심)' if morning_only else ''} =====")
    if morning_only:
        # 오늘 목표는 공장장이 이미 정했으면 그대로 둬요
        out_lines.append("INSERT INTO production_target (target_date, line_id, target_qty, created_at) VALUES\n"
                         + ",\n".join(f"    ('{day.isoformat()}', '{l}', {TARGET}, '{(day - timedelta(days=1)).isoformat()} 17:00:00')" for l in LINES)
                         + "\nON CONFLICT (target_date, line_id) DO NOTHING;")
    else:
        copy("production_target", ["target_date", "line_id", "target_qty", "created_at"],
             [(day.isoformat(), l, TARGET, f"{(day - timedelta(days=1)).isoformat()} 17:00:00") for l in LINES])
    kept = {x["pid"] for x in rows if x["kind"] == "SL"}
    morning = sum(1 for r in sl if r["s"] < LUNCH[0] and r["pid"] in kept)
    n_sl = len(kept)
    rolls = n_sl // 20
    r1 = min(rolls, -(-morning // 20))
    copy("coil_receipt", ["material_id", "received_at", "product_qty"],
         [(f"COIL-{day.strftime('%Y%m%d')}-{i + (500 if morning_only else 0):03d}",   # 오전 더미 코일은 501번부터 (리허설 입고 001~과 안 겹치게)
           f"{day.isoformat()} {'07:30:00' if i <= r1 else '12:30:00'}", 20)
          for i in range(1, rolls + 1)])
    srt = lambda kind: sorted((r for r in rows if r["kind"] == kind), key=lambda r: (r["t"], r["eq"]))
    copy("slitting_data", ["product_id", "equipment_id", "measured_at", "result"],
         [(r["pid"], r["eq"], d(r["t"]), "OK") for r in srt("SL")])
    press = {}
    for r in srt("PR"):
        x = press.setdefault(r["pid"], dict(t0=r["t"], vals={}, result="OK"))
        x["vals"].update(r["vals"]); x["eq"] = r["eq"]
        if r["result"] == "NG":
            x["result"] = "NG"
    copy("press_data", ["product_id", "equipment_id", "measured_at", *PRESS_COLS, "result"],
         [(pid, x["eq"], d(x["t0"]), *(x["vals"].get(c) for c in PRESS_COLS), x["result"])
          for pid, x in sorted(press.items(), key=lambda kv: kv[1]["t0"])])
    copy("welding_data", ["product_id", "equipment_id", "measured_at", *TH["weld"], "result"],
         [(r["pid"], r["eq"], d(r["t"]), *(r["vals"][c] for c in TH["weld"]), r["result"]) for r in srt("WD")])
    copy("painting_data", ["product_id", "equipment_id", "measured_at", "line_id", *TH["paint"], "result"],
         [(r["pid"], r["eq"], d(r["t"]), r["line"], *(r["vals"][c] for c in TH["paint"]), r["result"]) for r in srt("PT")])
    copy("inspection_result", ["equipment_id", "product_id", "line_id", "paint_equipment_id", "result", "inspected_at", "shipped_at"],
         [("VI", r["pid"], r["line"], r["paint"], r["result"], d(r["t"]), d(r["t"] + 1) if r["result"] == "OK" else None) for r in srt("VI")])
    copy("product_flow", ["product_id", "equipment_id", "line_id", "started_at", "ended_at"],
         [(r["pid"], r["eq"], r["line"], d(r["s"]), d(r["t"])) for r in sorted(rows, key=lambda r: (r["s"], r["eq"]))])
    # 알람: 기준 밖 값 → 발생, 같은 설비의 다음 정상 값에서 해제
    alarms = []
    by_eq = {}
    for r in rows:
        by_eq.setdefault(r["eq"], []).append(r)
    for r in rows:
        if not r["alarm"]:
            continue
        it, v, lo, hi = r["alarm"]
        nxt = [x["t"] for x in by_eq[r["eq"]] if x["t"] > r["t"] and x["result"] == "OK"]
        side = "상한 초과" if v > hi else "하한 미달"
        alarms.append((r["eq"], it, v, lo, hi, "CRITICAL", f"{LABEL[it]} {side}", d(r["t"]),
                       d(min(nxt)) if nxt else None, json.dumps(dict(product_id=r["pid"], **r["vals"]), ensure_ascii=False)))
    copy("alarm", ["equipment_id", "item_name", "measured_value", "lower_limit", "upper_limit", "level", "message",
                   "raised_at", "cleared_at", "raw_values"], sorted(alarms, key=lambda a: a[7]))
    # 상태 이력 · 비가동
    st, dt = [], []
    memo = {eq: random.choice(BREAK_MEMO[eq.split("-")[1][:2]]) for eq, _, _ in breaks}
    line_group = {}
    for eq, segs in tl.items():
        for code, a, b in segs:
            status = {R: "R", SHORT: "I", BLOCK: "I", D: "D", S: "S"}[code]
            st.append((eq, status, d(a), d(b)))
            if code == R:
                continue
            grp = None
            if code == S and eq[0] in LINES and "-" in eq:
                if (day, eq[0]) not in line_group:
                    group_no += 1
                    line_group[(day, eq[0])] = -group_no      # 음수 임시 번호 → 아래에서 stop_group_seq 번호로 바꿈
                grp = line_group[(day, eq[0])]
            cd, detail, mm, at = {
                SHORT: ("UP_SHORTAGE", "앞 공정에서 넘어온 제품 없음", None, None),
                BLOCK: ("UP_BLOCKED", "뒤 설비가 제품을 받지 못해 대기", None, None),
                D: ("UP_BREAKDOWN", "설비 고장 신호", memo.get(eq), d(b)),
                S: ("PL_SHIFT", "점심시간", None, None)}[code]
            dt.append((eq, status, cd, detail, d(a), d(b), mm, at, grp))
    copy("downtime_event", ["equipment_id", "status", "code", "detail", "started_at", "ended_at", "action_memo", "action_at", "stop_group_id"],
         sorted(dt, key=lambda x: (x[4], x[0])))
    copy("equipment_status_history", ["equipment_id", "status", "started_at", "ended_at"], sorted(st, key=lambda x: (x[2], x[0])))

    vi = [r for r in rows if r["kind"] == "VI"]
    summary.append(dict(day=day.isoformat() + (" 오전" if morning_only else ""), input=n_sl, coils=rolls,
                        shipped={l: sum(1 for r in vi if r["line"] == l and r["result"] == "OK") for l in LINES},
                        process_ng=sum(1 for r in rows if r["kind"] in ("PR", "WD", "PT") and r["result"] == "NG"),
                        vision_ng=sum(1 for r in vi if r["result"] == "NG"),
                        breakdowns=[(e, d(a)[11:19], round((b - a) / 60)) for e, a, b in breaks],
                        downtime_rows=len(dt), status_rows=len(st), flow_rows=len(rows),
                        last_end=d(max(r["t"] for r in rows))[11:19]))

out_lines.append(f"""
-- 계획정지 묶음 번호: 임시 음수 → stop_group_seq 번호
UPDATE downtime_event d SET stop_group_id = m.new_id
FROM (SELECT g, nextval('stop_group_seq') AS new_id
      FROM (SELECT DISTINCT stop_group_id AS g FROM downtime_event WHERE stop_group_id < 0 ORDER BY 1 DESC) s) m
WHERE d.stop_group_id = m.g;

-- 상태 이력 ↔ 비가동 연결
UPDATE equipment_status_history h SET downtime_id = d.downtime_id
FROM downtime_event d
WHERE h.equipment_id = d.equipment_id AND h.started_at = d.started_at AND h.status <> 'R'
  AND h.started_at < '{cut}' AND h.downtime_id IS NULL;

COMMIT;
ANALYZE;

-- 확인: 날짜별 출고 양품 수
SELECT inspected_at::date AS day, count(*) FILTER (WHERE result = 'OK') AS shipped, count(*) FILTER (WHERE result = 'NG') AS vision_ng
FROM inspection_result WHERE inspected_at < '{cut}' GROUP BY 1 ORDER BY 1;
""")

os.makedirs(args.out, exist_ok=True)
path = os.path.abspath(os.path.join(args.out, f"history_{BASE.strftime('%Y%m%d')}.sql"))
with open(path, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(out_lines))
print(json.dumps(summary, ensure_ascii=False, indent=1))
print(f"\n만든 파일: {path} ({os.path.getsize(path) / 1e6:.1f} MB)")
