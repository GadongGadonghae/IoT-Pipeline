"""과거 기록(시연용 더미 데이터) 생성기 · DB v0.3.13 · 2026-11-20
사용: python make_history.py [기준 날짜] [--days 3]
  예) python make_history.py 2026-11-24          → 11/21·11/22·11/23 3일치
      python make_history.py 2026-11-20 --days 1 → 11/19 하루치
  - 기준 날짜를 안 쓰면 오늘. 기준 날짜 "전" N일을 달력 그대로(주말 포함) 만들어요.
  - 출력: sfaas/history/history_<기준날짜>.sql (커서 Git에 안 올림)
  - 그 SQL은 맨 앞에서 "기준 날짜 이전 기록"을 전부 지우고 새로 넣어요 → 몇 번 다시 넣어도 결과가 같아요.
    오늘(기준 날짜) 기록은 건드리지 않아요.
  - 필요: pandas, numpy, openpyxl
  🆕 11/21 시연 영상용 "오늘 중간부터" (--now):
      python make_history.py --now 14:30 --today-lines ABCD --per-line 100
      → 지난 3일 + 오늘은 (지금 − 약 30분) ~ 지금(14:30)까지 A~D가 라인당 약 100개(총 약 400개)를 만든 기록,
        E·F는 오늘 시작부터 계획정지(진행 중). 오늘 목표 2000 = A~D 500씩. 오늘 기록은 통째로 새로 넣어요.
        이 SQL을 넣자마자 재생기를 시작하면 "이미 돌던 공장"에 이어서 흘러요 (제품 번호 5001~ → 재생은 0001~).
  🆕 11/21 하루 근무 시연 (--shift): 근무 09:00~18:00, 목표 1000개를 4라인(A~D)이 만드는 "느린 공장"
      python make_history.py --shift 09:00-18:00 --prep-until 11:00 --target 1000
      → 지난 3일(같은 근무·속도) + 오늘 09:00~11:00 기록(SQL) + 오늘 11:00~18:00 재생 CSV(scenario_shift/*_shift.csv, E·F 포함)
        같은 시뮬레이션에서 나눠서 제품 번호·흐름이 이어져요. 공정 표준 CT(process.cycle_time_sec)도 느린 속도로 바꿔요
  🆕 KAMP 원본 엑셀이 없으면 시나리오 CSV(scenario_1·2, rehearsal_*)의 KAMP 측정값을 재료로 써요 (--pool로 폴더 지정 가능)

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
ap.add_argument("--pool", nargs="*", default=None, help="KAMP 원본 대신 측정값을 가져올 시나리오 CSV 폴더 (원본이 없으면 자동)")
ap.add_argument("--now", default=None, help="오늘 중간부터 (HH:MM[:SS]): 이 시각까지 오늘 기록을 채움 (오전 더미 대신)")
ap.add_argument("--today-lines", default="ABCD", help="--now: 오늘 가동한 라인 (나머지는 계획정지 진행 중)")
ap.add_argument("--per-line", type=int, default=None, help="--now: 라인당 만든 수 (대략). --today-total이 우선")
ap.add_argument("--today-total", type=int, default=1000, help="--now: 오늘 지금까지 만든 수 (가동 라인 합, 대략)")
ap.add_argument("--today-hours", type=float, default=2.0, help="--now: 지금보다 몇 시간 전부터 가동했는지")
ap.add_argument("--today-breaks", type=int, default=2, help="--now: 오늘 고장 건수 (30~50분, 가동률 80%% 안팎)")
ap.add_argument("--today-ng", type=float, default=3.0, help="--now: 오늘 불량(공정·비전) 비율 배수 (불량률이 쌓여 보이게)")
ap.add_argument("--shift", default=None, help="하루 근무 시연 (예: 09:00-18:00). --prep-until까지 DB, 그 뒤는 재생 CSV")
ap.add_argument("--prep-until", default="11:00", help="--shift: 준비 때 DB에 넣을 끝 시각 (재생은 여기서부터)")
ap.add_argument("--shift-takt", type=float, default=145.0, help="--shift: 라인 박자(초). 145초면 4라인이 9시간에 약 880개")
ap.add_argument("--csv-out", default=os.path.join(HERE, "..", "scenario_shift"), help="--shift: 재생 CSV 폴더")
ap.add_argument("--target", type=int, default=2000, help="--now: 오늘 공장 목표 (가동 라인에 똑같이)")
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
KAMP_PRESS = f"{kamp}/07. Dataset_Press/dataset/Press_RawDataSet.xlsx"
if args.pool is None and os.path.exists(KAMP_PRESS):
    p = pd.read_excel(KAMP_PRESS).rename(columns={
        "Press time(ms)": "press_time", "Pressure 1": "pressure_left", "Pressure 2": "pressure_right"})
    p["pressure_diff"] = p.pressure_left - p.pressure_right
    w = pd.read_excel(f"{kamp}/05. Dataset_Welding/dataset/Welding Data Set_01.xlsx").rename(columns={
        "weld force(bar)": "press_force", "weld current(kA)": "current", "weld Voltage(v)": "voltage", "weld time(ms)": "weld_time"})
    c = pd.read_csv(f"{kamp}/Dataset_품질 이상탐지 진단(도금욕) AI 데이터셋/data/kemp-abh-sensor-2021.09.06.csv").rename(columns={
        "pH": "ph", "Temp": "temperature", "Current": "current", "Voltage": "voltage"})
else:
    # 🆕 11/21 원본 엑셀이 없을 때: 시나리오·리허설 CSV에 들어 있는 KAMP 측정값(원본에서 뽑은 정상 범위 값)을 재료로
    import glob
    dirs = args.pool or [os.path.join(HERE, "..", d) for d in ("scenario_1", "scenario_2", "rehearsal_10min", "rehearsal_5min", "rehearsal_3min")]
    read = lambda kind: pd.concat([pd.read_csv(f, encoding="utf-8-sig") for d in dirs for f in glob.glob(os.path.join(d, f"{kind}_*.csv"))],
                                  ignore_index=True)
    pr, wd, pt = read("press"), read("welding"), read("painting")
    if pr.empty or wd.empty or pt.empty:
        raise SystemExit("[오류] KAMP 원본도, 측정값을 가져올 시나리오 CSV도 없어요 (--kamp 또는 --pool 확인)")
    n = 4000
    # 프레스 CSV는 줄마다 1개 항목만 → 항목마다 따로 뽑아 한 줄로
    p = pd.DataFrame({col: pr[col].dropna().sample(n, replace=True, random_state=k).to_numpy() for k, col in enumerate(PRESS_COLS)})
    w = wd[list(TH["weld"])].dropna().reset_index(drop=True)
    c = pt[list(TH["paint"])].dropna().reset_index(drop=True)

    def with_bad(df, th, k=60):
        """기준 밖 행이 없으면 정상 행 하나의 한 항목을 기준 바로 바깥으로 (불량·알람 연출용)"""
        rows = df.sample(k, replace=True, random_state=7).reset_index(drop=True)
        for i in range(k):
            col = list(th)[i % len(th)]
            lo, hi = th[col]
            rows.loc[i, col] = round(hi + (hi - lo) * random.uniform(0.02, 0.08), 3)
        return pd.concat([df, rows], ignore_index=True)
    w, c = with_bad(w, TH["weld"]), with_bad(c, TH["paint"])
    print(f"KAMP 원본 없음 → 시나리오 CSV 값을 재료로 써요 (프레스 {len(pr)} · 용접 {len(wd)} · 도장 {len(pt)}줄)")
p_ok, _ = split(p, TH["press"])
p_bad = {it: p.loc[~p[it].between(*TH["press"][it]) & p[it].notna(), it].to_numpy() for it in PRESS_COLS}
w_ok, w_bad = split(w, TH["weld"])
c_ok, c_bad = split(c, TH["paint"])

# 🆕 11/21 슬리팅 측정값 더미 (재생기 player.py SLITTING_DUMMY와 같은 범위 = 교육장 인버터 실측 근처). ⚠️ 가짜 시연 값
#   토크는 인버터(LS SV-iG5A)가 주지 않아 빈 값(None) → 화면 "측정 안 함". 관리 기준: 전류 0.04~0.16 A · 전압 170~200 V
import zlib
SL_DUMMY = {"current": (0.1, 0.02), "voltage": (185.0, 3.0)}
def sl_vals(eq, pid):
    """[토크, 전류, 전압] (토크는 None)"""
    rnd = random.Random(zlib.crc32(f"{eq}|{pid}".encode()))
    shift = ("ABCDEF".index(eq[0]) - 2.5) / 2.5
    return [None] + [round(max(c0 - s0 * 2, min(c0 + s0 * 2, c0 + shift * s0 * 0.5 + rnd.gauss(0, s0 / 2))), 2) for c0, s0 in SL_DUMMY.values()]

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
        if LUNCH[1] > OPEN:
            arr[max(0, LUNCH[0] - OPEN):LUNCH[1] - OPEN] = S
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
SHIFT_MODE = args.shift is not None
if SHIFT_MODE:
    args.now = args.prep_until
    if args.target == 2000:
        args.target = 1000
NOW_MODE = args.now is not None
MORNING = not args.no_morning and not NOW_MODE
# 지우는 경계: 오전 더미를 넣으면 오늘 13:00 전까지 (그 뒤 = 실제 리허설·시연 기록은 그대로)
cut = f"{base_s} {DEMO_START // 3600:02d}:00:00" if MORNING else base_s
if NOW_MODE:   # 오늘 기록도 통째로 새로 (시연 시작 직전)
    cut = (BASE + timedelta(days=1)).isoformat()
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
-- 지난 날 보고서 결재·전송 기록과 고장 건 없는 계획수정 보고서 (11/21 더미 보고서를 다시 넣으려고)
DELETE FROM reports WHERE COALESCE(written_at, approved_at, sent_at) < '{cut}';
DELETE FROM reassignment_report WHERE downtime_id IS NULL AND created_at < '{cut}';
""")
if NOW_MODE:
    out_lines.append(f"""-- 오늘 중간부터 (--now): 오늘 만든 보고서·결재·목표 변경도 비워요 (시연을 처음부터)
DELETE FROM reports WHERE COALESCE(written_at, approved_at, sent_at) >= '{base_s}'
   OR replan_report_id IN (SELECT report_id FROM reassignment_report WHERE created_at >= '{base_s}');
DELETE FROM target_change WHERE target_date >= '{base_s}';
DELETE FROM reassignment_report WHERE created_at >= '{base_s}';
UPDATE line SET current_paint_id = default_paint_id WHERE current_paint_id IS DISTINCT FROM default_paint_id;
TRUNCATE equipment_signal;
-- 11/21 도장 운영 판단: 운휴 계획정지 코드 (없는 DB에도 넣어 둠) · 도장 운휴는 처음부터 다시
INSERT INTO downtime_code (code, code_name, is_planned, description) VALUES ('PL_STANDBY', '물량 적음 운휴', TRUE, '가동 라인이 적어 공용 설비(도장)를 쉬게 함') ON CONFLICT (code) DO NOTHING;
DELETE FROM equipment_status_history h USING downtime_event d WHERE h.downtime_id = d.downtime_id AND d.code = 'PL_STANDBY';
DELETE FROM downtime_event WHERE code = 'PL_STANDBY';
""")

summary = []
group_no = 0

# ---------------- 🆕 11/21 지난 날 보고서 더미: 일일 생산 보고서(전송 완료) + 고장마다 AI 계획수정 보고서(결재·전송 완료) ----------------
EQ_NAME = {"SL": "슬리팅 1호기", "WD": "용접 1호기"}
def eq_name(eq):
    st = eq.split("-")[1]
    return f"{eq[0]}라인 " + (EQ_NAME.get(st) or f"프레스 {st[2]}호기")
def q(v):
    return "'" + str(v).replace("'", "''") + "'"
def jtxt(o):
    return q(json.dumps(o, ensure_ascii=False))
pct = lambda v: f"{v:.1f}%"
fmt = lambda n: f"{int(n):,}"
WRITER = "(SELECT user_id FROM users WHERE role = 'PROD_LEAD' ORDER BY user_id LIMIT 1)"
BOSS = "(SELECT user_id FROM users WHERE role = 'PLANT_HEAD' ORDER BY user_id LIMIT 1)"

def past_reports(day, rows, breaks, tl, vi, alarms):
    d = lambda sec: ts(day, sec)
    ymd = day.strftime("%Y%m%d")
    ok = {l: sum(1 for r in vi if r["line"] == l and r["result"] == "OK") for l in LINES}
    ng_line = {l: sum(1 for r in rows if r["line"] == l and r["result"] == "NG") for l in LINES}
    total_ok = sum(ok.values())
    total_ng = sum(ng_line.values())
    plan = TARGET * len(LINES)
    # 가동률: 라인 설비의 가동(R) 시간 ÷ (가동 + 대기 + 고장) 시간 (점심 계획정지 제외)
    run = tot = 0
    for eq, segs in tl.items():
        if "-" not in eq:
            continue
        for code, a, b in segs:
            if code == S or b is None:
                continue
            tot += b - a
            run += (b - a) if code == R else 0
    avail = run / tot * 100 if tot else 0
    quality = total_ok / (total_ok + total_ng) * 100 if total_ok + total_ng else 0
    perf = min(99.0, total_ok / max(1, plan) * 100 / max(0.01, avail / 100))
    oee = avail * perf * quality / 1e4
    reps = []
    # 고장마다 계획수정 보고서 (고장 5분 뒤 작성 → 15분 뒤 결재 → 20분 뒤 전송)
    for n, (eq, a, b) in enumerate(sorted(breaks, key=lambda x: x[1]), 1):
        line, base = eq[0], a + 300
        done = sum(1 for r in vi if r["line"] == line and r["result"] == "OK" and r["t"] < base)
        loss = max(1, int((b - a) / TAKT))
        others = [l for l in LINES if l != line]
        rem_sec = CLOSE - base
        rows_d = []
        each, rest = divmod(loss, len(others))
        for i, l in enumerate(others):
            done_l = sum(1 for r in vi if r["line"] == l and r["result"] == "OK" and r["t"] < base)
            rp = max(0, TARGET - done_l)
            cap = max(0, int(rem_sec / TAKT) - rp)
            rows_d.append((l, cap, each + (1 if i < rest else 0), rp))
        cap_sum = sum(x[1] for x in rows_d) or 1
        name = eq_name(eq)
        title = f"{name} 고장 생산계획 수정 보고서"
        summary = (f"{name} 고장({round((b - a) / 60)}분)으로 {line}라인 생산이 멈춰 약 {loss}개 부족이 예상됩니다. "
                   f"정상 가동 중인 {', '.join(others)}라인에 남은 생산능력 비율대로 {loss}개를 재배정하는 안을 제안합니다.")
        actions = [f"{l}라인에 {aq}개 추가 배정 (남은 능력 비율 {c / cap_sum * 100:.1f}%)" for l, c, aq, _ in rows_d]
        actions.append(f"{name} 복구 후 첫 제품 품질을 확인합니다.")
        risks = [f"{name} 복구가 늦어지면 부족 수량이 늘어 다시 계산해야 합니다.", "본 보고서는 생산계획을 자동 변경하지 않습니다."]
        content = dict(title=title, summary=summary, actions=actions, risks=risks, markdown=None, model="gemini-3.5-flash-lite",
                       mock=False, fallbackReason=None, additionalRequest=None,
                       requestInterpretation=dict(excludedLines=[], stoppedLines=[], priority="BALANCED", appliedRules=["잔여 생산능력 기준 적용"]),
                       causeTrace=None, order=None)
        doc = dict(documentNo=f"AI-RPT-{ymd}-__RID__", generatedAt=d(base + 60)[:16], targetLine=f"{line}라인", plan=TARGET, actual=done,
                   shortage=loss, stopSec=int(b - a), factLine=f"{line}라인", factEquipment=name, factStart=d(a)[11:16],
                   factCause="장비고장 · 설비 고장 신호", remainingSec=int(rem_sec), modelName="AI 분석 보고서", docStatus="전송 완료", author="생산관리팀장",
                   allocationText=f"{line}라인 부족 {loss}개를 {', '.join(others)}라인에 남은 능력 비율대로 나눴습니다.",
                   rows=[[f"{l}", fmt(rp), fmt(c), pct(c / cap_sum * 100), fmt(aq)] for l, c, aq, rp in rows_d],
                   totalPlan=fmt(sum(x[3] for x in rows_d)), totalCapacity=fmt(cap_sum), totalAllocation=fmt(loss),
                   resultGrade="목표 달성 가능", resultText=f"재배정 후 {line}라인 부족분을 오늘 안에 만들 수 있습니다. {summary}",
                   actions=actions, risks=risks)
        vals = ", ".join(f"('{l}', {c}, {aq}, {rp})" for l, c, aq, rp in rows_d)
        out_lines.append(f"""
WITH dd AS (SELECT downtime_id FROM downtime_event WHERE equipment_id = '{eq}' AND status = 'D' AND started_at = '{d(a)}' LIMIT 1),
rr AS (INSERT INTO reassignment_report (downtime_id, loss_qty, created_at, approved, approved_by, approved_at, created_by, kind, base_at,
                                        ct_sec, stopped_plan, stopped_done, target_qty, unmet_qty, ai_content)
       SELECT dd.downtime_id, {loss}, '{d(base + 60)}', TRUE, {BOSS}, '{d(base + 900)}', {WRITER}, 'line', '{d(base)}',
              {TAKT}, {TARGET}, {done}, {plan}, 0, {jtxt(content)} FROM dd RETURNING report_id, downtime_id),
xx AS (INSERT INTO reassignment_detail (report_id, line_id, spare_capacity, added_qty, remaining_plan)
       SELECT rr.report_id, v.l, v.c, v.a, v.p FROM rr, (VALUES {vals}) v(l, c, a, p))
INSERT INTO reports (kind, target_key, title, status, campus_id, factory_id, content, replan_report_id,
                     written_by, written_at, approved_by, approved_at, sent_by, sent_at)
SELECT 'REPLAN', rr.downtime_id || '#' || rr.report_id, {q('계획수정 보고서 · ' + title)}, 'SENT', 'BODY', 1,
       replace({jtxt(dict(doc=doc))}, '__RID__', lpad(rr.report_id::text, 3, '0')), rr.report_id,
       {WRITER}, '{d(base + 600)}', {BOSS}, '{d(base + 900)}', {BOSS}, '{d(base + 1200)}' FROM rr;""")
        reps.append((eq, a, b, n))

    # 일일 생산 보고서 (19:30 작성 → 19:50 결재 → 20:00 전송)
    procs = [("슬리팅", "SL"), ("프레스", "PR"), ("용접", "WD"), ("도장", "PT"), ("비전 검사", "VI")]
    proc_rows = []
    for nm, k in procs:
        out = sum(1 for r in rows if r["kind"] == k and (k != "PR" or r["eq"].endswith("PR4") or r["result"] == "NG"))
        ng = sum(1 for r in rows if r["kind"] == k and r["result"] == "NG")
        proc_rows.append([nm, fmt(out), fmt(ng), pct(ng / out * 100 if out else 0)])
    down_rows = [[eq_name(eq), "장비고장 · 설비 고장 신호", f"{d(a)[11:16]}~{d(b)[11:16]} ({round((b - a) / 60)}분)", f"AI-RPT-{ymd}-…"]
                 for eq, a, b, _ in reps] or [["-", "비가동 없음", "-", "-"]]
    rate = total_ok / plan * 100
    worst = max(LINES, key=lambda l: ng_line[l])
    doc = dict(
        values=dict(DOC_NO=f"DAILY-{ymd}-D", DOC_AT=f"{day.isoformat()} 19:30", PROD_DATE=day.isoformat(), SHIFT="주간 08:00~19:00", STATUS="전송 완료",
                    SUM_PLAN=f"{fmt(plan)}개", SUM_ACTUAL=f"{fmt(total_ok)}개", SUM_RATE=pct(rate), SUM_DEFECT=f"{fmt(total_ng)}개",
                    SUMMARY_TEXT=f"{day.isoformat()} 생산계획 {fmt(plan)}개 대비 양품 {fmt(total_ok)}개를 생산하여 달성률 {pct(rate)}로 마감했습니다. 불량은 {fmt(total_ng)}개입니다.",
                    TOTAL_0="합계", TOTAL_1=fmt(plan), TOTAL_2=fmt(total_ok), TOTAL_3=pct(rate), TOTAL_5=fmt(total_ng),
                    M0=pct(oee), M1=pct(avail), M2=pct(perf), M3=pct(quality),
                    AI_REVIEW=f"OEE {pct(oee)} (시간가동률 {pct(avail)} · 성능가동률 {pct(perf)} · 양품률 {pct(quality)}). "
                              + (f"장비고장 {len(reps)}건으로 해당 라인 생산이 멈췄으나 재배정으로 목표 대비 {pct(rate)}를 달성했습니다." if reps else "비가동 없이 마감했습니다."),
                    QUALITY=f"공정 불량 {fmt(sum(1 for r in rows if r['kind'] in ('PR', 'WD', 'PT') and r['result'] == 'NG'))}개 · 비전 불량 {fmt(sum(1 for r in vi if r['result'] == 'NG'))}개. 불량이 가장 많은 라인은 {worst}라인({ng_line[worst]}개)입니다."),
        tables=dict(
            LINES=[[l, fmt(TARGET), fmt(ok[l]), pct(ok[l] / TARGET * 100), pct(oee), fmt(ng_line[l])] for l in LINES],
            PROC=proc_rows, DOWN=down_rows, WIP=[["슬리팅", "프레스", "0개"]],
            HANDOVER=[[f"1. {eq_name(reps[-1][0])} 복구 후 첫 제품 품질 확인 결과 공유" if reps else "1. 특이사항 없음"],
                      ["2. 다음 날 코일 입고 07:30 예정 (자재 확인)"]]),
        fileName=f"일일생산보고서_{ymd}.doc")
    out_lines.append(f"""
INSERT INTO reports (kind, target_key, title, status, campus_id, factory_id, content, written_by, written_at, approved_by, approved_at, sent_by, sent_at)
VALUES ('DAILY', '{day.isoformat()}', {q('일일 생산 보고서 · ' + day.isoformat())}, 'SENT', 'BODY', 1, {jtxt(doc)},
        {WRITER}, '{day.isoformat()} 19:30:00', {BOSS}, '{day.isoformat()} 19:50:00', {BOSS}, '{day.isoformat()} 20:00:00')
ON CONFLICT (kind, target_key) DO NOTHING;""")
if NOW_MODE:
    hh = [int(x) for x in args.now.split(":")] + [0, 0]
    T0 = hh[0] * 3600 + hh[1] * 60 + hh[2]
    PIPE = 200                                     # 슬리팅 투입부터 검사 끝까지 대략 (초)
    # 오늘 가동 시작: 지금보다 --today-hours 전 (자정을 넘기면 어제 밤부터 — 오늘 화면에는 00:00 뒤만 오늘로 보여요)
    TODAY_OPEN = int(T0 - args.today_hours * 3600)
    TODAY_LINES = [l for l in args.today_lines.upper() if l in LINES]
    STOPPED_LINES = [l for l in LINES if l not in TODAY_LINES]
if SHIFT_MODE:
    # 느린 공장: 공정 처리 시간을 박자에 맞게 늘려요 (설비가 놀지 않게). 점심 없음, 하루 고장 1~2건
    s0, s1 = [sum(int(x) * m for x, m in zip(t.split(":"), (3600, 60))) for t in args.shift.split("-")]
    k = args.shift_takt / 17.0
    CT = dict(SL=round(args.shift_takt * 0.96), PR=round(args.shift_takt * 0.97), WD=round(args.shift_takt * 0.97), PT=round(4 * k), VI=round(2 * k))
    TAKT = args.shift_takt
    PIPE = CT["SL"] + 4 * CT["PR"] + CT["WD"] + CT["PT"] + CT["VI"] + 60
    OPEN, CLOSE, LUNCH = s0, s1 - PIPE, (-10**7, -10**7)
    TARGET = args.target // len(TODAY_LINES)
    BREAKS_PER_DAY = (1, 2)
    out_lines.append(f"""-- 하루 근무 시연 (--shift {args.shift}): 공정 표준 CT를 느린 공장에 맞춰요 (성능가동률·재배정 계산이 같은 기준)
UPDATE process SET cycle_time_sec = {CT['SL']} WHERE process_id = 'SLITTING';
UPDATE process SET cycle_time_sec = {CT['PR']} WHERE process_id = 'PRESS';
UPDATE process SET cycle_time_sec = {CT['WD']} WHERE process_id = 'WELDING';
UPDATE process SET cycle_time_sec = {CT['PT']} WHERE process_id = 'PAINTING';
UPDATE process SET cycle_time_sec = {CT['VI']} WHERE process_id = 'INSPECTION';""")

def write_shift_csv(full, outdir, base, dstr):
    """오늘 하루 시뮬레이션 → 재생기 CSV (*_shift.csv). 시각은 근무 시작(base) 뒤 초"""
    import csv as _csv
    os.makedirs(outdir, exist_ok=True)
    off = lambda sec: round(sec - base, 1)
    rowno = {}
    def nxt(eq):
        rowno[eq] = rowno.get(eq, 0) + 1
        return rowno[eq]
    files = {
        "slitting": ["planned_offset_sec", "planned_start_sec", "equipment_id", "row_no", "product_id", "scenario_note"],
        "press": ["planned_offset_sec", "planned_start_sec", "equipment_id", "row_no", "product_id", *PRESS_COLS, "scenario_note"],
        "welding": ["planned_offset_sec", "planned_start_sec", "equipment_id", "row_no", "product_id", *TH["weld"], "scenario_note"],
        "painting": ["planned_offset_sec", "planned_start_sec", "equipment_id", "row_no", "product_id", "line_id", *TH["paint"], "scenario_note"],
        "inspection": ["planned_offset_sec", "planned_start_sec", "equipment_id", "product_id", "line_id", "paint_equipment_id", "result", "scenario_note"],
    }
    out = {k: [] for k in files}
    kind_of = {"SL": "slitting", "PR": "press", "WD": "welding", "PT": "painting", "VI": "inspection"}
    for r in sorted(full, key=lambda r: (r["t"], r["eq"])):
        kind = kind_of[r["kind"]]
        note = "★ 불량" if r["result"] == "NG" else ""
        row = {"planned_offset_sec": off(r["t"]), "planned_start_sec": off(r["s"]), "equipment_id": r["eq"], "product_id": r["pid"], "scenario_note": note}
        if kind == "inspection":
            row.update(line_id=r["line"], paint_equipment_id=r["paint"], result=r["result"])
        else:
            row["row_no"] = nxt(r["eq"])
            row.update({c: v for c, v in r["vals"].items()})
            if kind == "painting":
                row["line_id"] = r["line"]
        out[kind].append(row)
    for kind, cols in files.items():
        with open(os.path.join(outdir, f"{kind}_shift.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(out[kind])
    return {k: len(v) for k, v in out.items()}

ALL_LINES, DAY_OPEN, DAY_LUNCH = LINES, OPEN, LUNCH
for day, morning_only in [(x, False) for x in DAYS] + ([(BASE, True)] if MORNING or NOW_MODE else []):
    if morning_only and SHIFT_MODE:
        random.seed(int(BASE.strftime("%Y%m%d")) + 11)
        LINES = ALL_LINES
        # 오늘은 불량을 평소의 --today-ng 배로 (출고 화면 라인별 불량·불량률이 쌓여 보이게, 11/21)
        saved_p = (P_PRESS_STAGE, P_WELD, P_PAINT, P_VISION)
        P_PRESS_STAGE, P_WELD, P_PAINT, P_VISION = (x * args.today_ng for x in saved_p)
        full, breaks, sl = simulate(day, close=CLOSE, n_breaks=(0, 0))
        P_PRESS_STAGE, P_WELD, P_PAINT, P_VISION = saved_p
        csv_counts = write_shift_csv(full, args.csv_out, OPEN, day.strftime("%Y%m%d"))
        run_rows = [r for r in full if r["line"] in TODAY_LINES]
        rows = [r for r in run_rows if r["t"] <= T0]                       # 준비 때 DB에 넣을 끝난 기록
        open_rows = [r for r in run_rows if r["s"] < T0 < r["t"]]           # 준비 시각에 처리 중 (끝은 재생이 보냄)
        clipped = [dict(r, t=T0, rel=T0, blk=None) for r in open_rows]
        tl = timeline(rows + clipped, breaks)
        for eq in [e for e in EQS if e[0] in STOPPED_LINES and "-" in e]:
            tl[eq] = [(S, OPEN, None)]
        n_all = len({r["pid"] for r in full if r["kind"] == "SL"})
    elif morning_only and NOW_MODE:
        # 오늘 중간부터: TODAY_OPEN ~ T0, 점심·고장 없음, 가동 라인만. T0를 넘겨 끝나는 제품은 뺌 (재생이 이어서 흘림)
        OPEN, LUNCH, LINES = TODAY_OPEN, (-10**7, -10**7), TODAY_LINES
        want = args.today_total if args.per_line is None else args.per_line * len(TODAY_LINES)
        # 불량을 평소의 --today-ng 배로 (불량률 화면에 쌓여 보이게)
        saved_p = (P_PRESS_STAGE, P_WELD, P_PAINT, P_VISION)
        P_PRESS_STAGE, P_WELD, P_PAINT, P_VISION = (x * args.today_ng for x in saved_p)
        # 라인 박자를 맞춰 "지금까지 만든 수"가 want에 가깝게 (고장으로 멈춘 만큼 덜 만듦). 같은 씨앗으로 몇 번 다시 계산
        takt0, TAKT_TODAY = TAKT, max(17.0, (T0 - PIPE - TODAY_OPEN) * len(TODAY_LINES) * 0.8 / max(1, want))
        seed = int(BASE.strftime("%Y%m%d")) + 7
        for _ in range(6):
            TAKT = TAKT_TODAY
            random.seed(seed)
            rows, breaks, sl = simulate(day, close=T0 - PIPE, hard_end=T0, pid_offset=MORNING_PID_OFFSET,
                                        n_breaks=(args.today_breaks, args.today_breaks))
            made = len({x["pid"] for x in rows if x["kind"] == "SL"})
            if made == 0 or abs(made - want) <= want * 0.03:
                break
            TAKT_TODAY = max(17.0, TAKT_TODAY * made / want)
        TAKT = takt0
        P_PRESS_STAGE, P_WELD, P_PAINT, P_VISION = saved_p
        LINES = ALL_LINES
        tl = timeline(rows, breaks)
        # 쉬는 라인: 오늘 시작부터 계획정지(S) 진행 중 (재생 뒤 추가 주문 결재로 가동)
        for eq in [e for e in EQS if e[0] in STOPPED_LINES and "-" in e]:
            tl[eq] = [(S, TODAY_OPEN, None)]
    elif morning_only:
        rows, breaks, sl = simulate(day, close=MORNING_INPUT_END, hard_end=LUNCH[0], pid_offset=MORNING_PID_OFFSET, n_breaks=MORNING_BREAKS)
        tl = timeline(rows, breaks, lunch_from_last=True)
    else:
        OPEN, LUNCH = DAY_OPEN, DAY_LUNCH
        if SHIFT_MODE:   # 지난 날도 같은 근무·속도로, 가동 라인만 (쉬는 라인은 하루 종일 계획정지). 18:00 넘겨 끝나는 제품은 뺌
            LINES = TODAY_LINES
            rows, breaks, sl = simulate(day, hard_end=s1, n_breaks=(1, 2))
        else:
            rows, breaks, sl = simulate(day)
        LINES = ALL_LINES
        tl = timeline(rows, breaks)
        if SHIFT_MODE:
            last = int(math.ceil(max(r["rel"] for r in rows))) + 1
            for eq in [e for e in EQS if e[0] in STOPPED_LINES and "-" in e]:
                tl[eq] = [(S, OPEN, last)]
    d = lambda sec: ts(day, sec)
    out_lines.append(f"\n-- ===== {day.isoformat()}{' 오전 (08:00~12:00 + 점심)' if morning_only else ''} =====")
    if morning_only and NOW_MODE:
        each, rest = divmod(args.target, len(TODAY_LINES))
        qty = {l: (each + (1 if i < rest else 0)) for i, l in enumerate(TODAY_LINES)}
        out_lines.append("INSERT INTO production_target (target_date, line_id, target_qty, created_at, created_by) VALUES\n"
                         + ",\n".join(f"    ('{day.isoformat()}', '{l}', {qty.get(l, 0)}, '{day.isoformat()} 07:00:00', "
                                       f"(SELECT user_id FROM users WHERE role = 'PLANT_HEAD' ORDER BY user_id LIMIT 1))" for l in LINES)
                         + "\nON CONFLICT (target_date, line_id) DO UPDATE SET target_qty = EXCLUDED.target_qty, created_at = EXCLUDED.created_at, created_by = EXCLUDED.created_by;")
    elif morning_only:
        # 오늘 목표는 공장장이 이미 정했으면 그대로 둬요
        out_lines.append("INSERT INTO production_target (target_date, line_id, target_qty, created_at) VALUES\n"
                         + ",\n".join(f"    ('{day.isoformat()}', '{l}', {TARGET}, '{(day - timedelta(days=1)).isoformat()} 17:00:00')" for l in LINES)
                         + "\nON CONFLICT (target_date, line_id) DO NOTHING;")
    else:
        copy("production_target", ["target_date", "line_id", "target_qty", "created_at"],
             [(day.isoformat(), l, TARGET if not SHIFT_MODE or l in TODAY_LINES else 0,
               f"{(day - timedelta(days=1)).isoformat()} 17:00:00") for l in LINES])
    kept = {x["pid"] for x in rows if x["kind"] == "SL"}
    morning = sum(1 for r in sl if r["s"] < LUNCH[0] and r["pid"] in kept)
    n_sl = len(kept)
    rolls = n_sl // 20
    r1 = min(rolls, -(-morning // 20))
    if SHIFT_MODE and morning_only:
        rolls, r1 = -(-n_all // 20) + 2, 0
    copy("coil_receipt", ["material_id", "received_at", "product_qty"],
         [(f"COIL-{day.strftime('%Y%m%d')}-{i + (500 if morning_only else 0):03d}",   # 오전 더미 코일은 501번부터 (리허설 입고 001~과 안 겹치게)
           (ts(day, OPEN - 1800) if SHIFT_MODE and morning_only else ts(day, TODAY_OPEN - 600) if NOW_MODE and morning_only else f"{day.isoformat()} {'07:30:00' if i <= r1 else '12:30:00'}"), 20)
          for i in range(1, rolls + 1)])
    srt = lambda kind: sorted((r for r in rows if r["kind"] == kind), key=lambda r: (r["t"], r["eq"]))
    copy("slitting_data", ["product_id", "equipment_id", "measured_at", "torque", "current", "voltage", "result"],
         [(r["pid"], r["eq"], d(r["t"]), *sl_vals(r["eq"], r["pid"]), "OK") for r in srt("SL")])
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
    if SHIFT_MODE and morning_only and open_rows:
        copy("product_flow", ["product_id", "equipment_id", "line_id", "started_at", "ended_at"],
             [(r["pid"], r["eq"], r["line"], d(r["s"]), None) for r in sorted(open_rows, key=lambda r: (r["s"], r["eq"]))])
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
            st.append((eq, status, d(a), d(b) if b is not None else None))
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
                D: ("UP_BREAKDOWN", "설비 고장 신호", memo.get(eq), d(b) if b is not None else None),
                S: ("PL_SHIFT", "점심시간", None, None)}[code]
            if b is None:   # 오늘 중간부터: 쉬는 라인 계획정지 진행 중
                detail = "계획정지 (오늘 가동하지 않는 라인, 추가 주문 시 가동)"
            elif code == S and SHIFT_MODE:   # 하루 근무 시연: 점심 없음 → 하루 종일 쉬는 라인
                detail = "계획정지 (가동하지 않는 라인)"
            dt.append((eq, status, cd, detail, d(a), d(b) if b is not None else None, mm, at, grp))
    copy("downtime_event", ["equipment_id", "status", "code", "detail", "started_at", "ended_at", "action_memo", "action_at", "stop_group_id"],
         sorted(dt, key=lambda x: (x[4], x[0])))
    copy("equipment_status_history", ["equipment_id", "status", "started_at", "ended_at"], sorted(st, key=lambda x: (x[2], x[0])))

    vi = [r for r in rows if r["kind"] == "VI"]
    if not morning_only:
        if SHIFT_MODE:
            LINES = TODAY_LINES
        past_reports(day, rows, breaks, tl, vi, alarms)
        LINES = ALL_LINES
    summary.append(dict(day=day.isoformat() + ((f" {args.shift} 중 ~{args.now} (재생 CSV {csv_counts})" if SHIFT_MODE
                                                 else f" {ts(day, TODAY_OPEN)[11:19]}~{args.now}" if NOW_MODE else " 오전") if morning_only else ""), input=n_sl, coils=rolls,
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
