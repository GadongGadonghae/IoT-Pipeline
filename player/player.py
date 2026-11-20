"""KAMP CSV 재생 프로그램 (가상 설비) · API 명세서 v3 기준 · DB v0.3.8 (제품 추적)

미리 만든 CSV를 정해진 시각에 한 줄씩 MQTT로 보내요.
- 가상 설비 39대: 슬리팅 6, 프레스 24, 용접 6, 도장 2, 비전 검사 1
- 코일 입고: 입고 일정(config.json)대로 sfaas/raw/COIL (기본 0초에 4롤)
- 상태 신호(heartbeat): 39대 모두 1초마다 (sfaas/hb/{설비ID}), 안에 고장 여부 fault true/false
- 고장·복구: 따로 메시지를 보내지 않고 상태 신호의 fault 값을 바꿔요 (자동 일정 + 키보드 f/r)
- 통신 끊김 흉내: 키보드 c/u
- 제품 번호(productId): CSV의 product_id를 모든 측정값 메시지에 붙여요 (날짜는 product_date 설정대로)
- 프레스: 프레스마다 자기 항목 1개만 보내요 (PR1 좌 압력, PR2 우 압력, PR3 압력 차, PR4 가압 시간)

사용 예:
    python player.py                    # 실제 전송 (Mosquitto 필요)
    python player.py --dry-run          # 브로커 없이 화면에만 출력
    python player.py --dry-run --speed 10
    python player.py --manual           # 자동 고장 일정 끄고 키보드로만 조작
"""
import argparse
import csv
import json
import os
import queue
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent

# 설비 종류 (요약 출력 순서 = F6 순서)
KIND_NAMES = {"slitting": "슬리팅", "press": "프레스", "welding": "용접", "painting": "도장",
              "inspection": "검사", "coil": "코일", "fault": "고장 전환", "heartbeat": "상태 신호",
              "start": "투입"}   # v0.3.9: 투입 메시지(event=start) 수. 위 공정 개수는 완료(end)만 셈
CSV_KINDS = ["slitting", "press", "welding", "painting", "inspection"]
# 측정값 칸 (CSV 칸 이름 = DB 컬럼명)
VALUE_COLS = {
    "slitting": [],  # 가상 슬리팅은 값 없음
    "press": ["pressure_left", "pressure_right", "pressure_diff", "press_time"],   # 프레스마다 1개만 채워져 있음
    "welding": ["press_force", "current", "voltage", "weld_time"],
    "painting": ["ph", "temperature", "current", "voltage"],
}
LINES = list("ABCDEF")
LINE_STAGES = ["SL", "PR1", "PR2", "PR3", "PR4", "WD"]   # 라인 안 공정 순서
# 설비 39대 (라인 A~F × 슬리팅·프레스 4·용접) + 도장 2 + 검사 1
ALL_EQS = [f"{l}-{s}" for l in LINES for s in LINE_STAGES] + ["PT1", "PT2", "VI"]

# ---------------- 화면 색 (Windows 터미널에서도 동작) ----------------
os.system("")  # Windows 콘솔에서 ANSI 색 켜기
try:  # 출력 못 하는 글자가 있어도 프로그램이 멈추지 않게
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass
RED, YEL, GRN, CYN, DIM, RST = "\033[91m", "\033[93m", "\033[92m", "\033[96m", "\033[2m", "\033[0m"

print_lock = threading.Lock()


def say(msg=""):
    with print_lock:
        print(msg, flush=True)


# ---------------- 설정 ----------------
def load_config(path):
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    cfg.setdefault("broker", {"host": "localhost", "port": 1883})
    cfg.setdefault("topics", {})
    cfg["topics"].setdefault("raw", "sfaas/raw")
    cfg["topics"].setdefault("hb", "sfaas/hb")
    cfg["topics"].setdefault("ctl", "sfaas/ctl")   # 재생 시작·끝 알림 ({ctl}/replay)
    cfg.setdefault("csv_dir", "../rehearsal_3min")
    cfg.setdefault("csv_suffix", "3min")
    cfg.setdefault("duration_sec", 180)
    cfg.setdefault("value_naming", "camel")
    cfg.setdefault("product_date", "today")
    cfg.setdefault("faults", [])
    cfg.setdefault("slitting", {})
    cfg["slitting"].setdefault("enabled", True)
    cfg["slitting"].setdefault("disabled", [])
    cfg.setdefault("heartbeat", {})
    cfg["heartbeat"].setdefault("enabled", True)
    cfg["heartbeat"].setdefault("interval_sec", 1)
    cfg["heartbeat"].setdefault("disabled", [])
    cfg.setdefault("coil", {})
    cfg["coil"].setdefault("enabled", True)
    cfg["coil"].setdefault("product_qty", 20)
    cfg["coil"].setdefault("schedule", [{"at_sec": 0, "rolls": 4}])
    cfg.setdefault("hold_lines", [])   # 🆕 11/20 처음에 멈춰 둘 라인 (추가 주문으로 나중에 가동, --hold 와 같음)
    if cfg["value_naming"] not in ("snake", "camel"):
        sys.exit("[설정 오류] value_naming은 \"camel\" 또는 \"snake\"이어야 해요.")
    if float(cfg["heartbeat"]["interval_sec"]) <= 0:
        sys.exit("[설정 오류] heartbeat.interval_sec는 0보다 커야 해요.")
    # 토픽 앞부분 끝의 / 는 떼기
    for k in ("raw", "hb", "ctl"):
        cfg["topics"][k] = cfg["topics"][k].rstrip("/")
    return cfg


def to_camel(name):
    head, *rest = name.split("_")
    return head + "".join(w.capitalize() for w in rest)


def num(s):
    """CSV 숫자 → float (548.0999999999999 같은 꼬리는 정리)"""
    return round(float(s), 6)


def now_ts():
    """실제로 보낸 시각 (ISO-8601 한국 시간, 밀리초까지)"""
    return datetime.now(KST).isoformat(timespec="milliseconds")


# =====================================================================
#  메시지 만들기 (API 명세서 v3)  ← 메시지 모양이 바뀌면 이 부분만 고치면 돼요
#  각 함수는 (토픽, 내용 dict, QoS)를 돌려줘요.
# =====================================================================
TOPICS = {"raw": "sfaas/raw", "hb": "sfaas/hb"}   # 시작할 때 config.json 값으로 바뀜
def msg_values(eq, values, line_id=None, product_id=None, event="end"):
    """Q-01 측정값 (슬리팅·프레스·용접·도장)
    v0.3.9: event = "start"(제품이 설비에 들어감, 값 없음) / "end"(값을 쓰고 내보냄). ts = 그 순간"""
    body = {"equipmentId": eq, "ts": now_ts(), "source": "REPLAY", "event": event}
    if product_id:                    # v0.3.8: 제품 번호 (슬리팅 투입 때 생김)
        body["productId"] = product_id
    if line_id is not None:           # 도장: 어느 라인 제품인지
        body["lineId"] = line_id
    if event == "end":
        body["values"] = values
    return f"{TOPICS['raw']}/{eq}", body, 0


def msg_vision(product_id, line_id, paint_id, vision_pass, event="end"):
    """Q-02 비전 검사 (v0.3.9: start = 검사에 들어감, end = 결과)"""
    body = {"ts": now_ts(), "event": event, "productId": product_id, "lineId": line_id, "paintId": paint_id}
    if event == "end":
        body["visionPass"] = vision_pass
    return f"{TOPICS['raw']}/VI", body, 1


def msg_heartbeat(eq, fault):
    """Q-04 상태 신호 + 고장 여부 (고장 중 true, 평소 false)
    고장·복구는 따로 메시지를 보내지 않고 이 fault 값으로만 알려요 (4차 확정)."""
    return f"{TOPICS['hb']}/{eq}", {"equipmentId": eq, "ts": now_ts(), "fault": bool(fault)}, 0


def msg_coil(material_id, product_qty):
    """코일 입고 (1롤 = 1건)"""
    body = {"materialId": material_id, "ts": now_ts(), "productQty": product_qty}
    return f"{TOPICS['raw']}/COIL", body, 1
# =====================================================================


# ---------------- 재생할 일(이벤트) 만들기 ----------------
def stream_key(kind, eq, line_id):
    """수동 고장 때 멈출 단위. 라인 설비는 설비별, 공용 설비는 '설비:라인'별."""
    if kind == "painting":
        return f"{eq}:{line_id}"
    if kind == "inspection":
        return f"VI:{line_id}"
    return eq


def key_line(key):
    """스트림이 속한 라인 (A-PR1 → A, PT1:C → C, VI:E → E)"""
    return key.split(":")[1] if ":" in key else key[0]


def key_stage(key):
    """라인 안 공정 순서 번호 (슬리팅 0 … 용접 5, 도장 6, 검사 7)"""
    if key.startswith("VI:"):
        return 7
    if key.startswith("PT"):
        return 6
    return LINE_STAGES.index(key.split("-")[1])


def load_events(cfg, auto_faults):
    """CSV 5개 + 코일 입고 + 자동 고장 일정을 이벤트 목록으로 만들어요.
    이벤트: dict(t=예정 초, kind, eq, key=스트림, data=메시지 재료, star=불량 줄 여부)"""
    csv_dir = (HERE / cfg["csv_dir"]).resolve()
    events = []

    # 제품 ID 날짜 바꾸기
    pd_cfg = str(cfg["product_date"]).strip()
    if pd_cfg.lower() == "today":
        new_date = datetime.now(KST).strftime("%Y%m%d")
    elif re.fullmatch(r"\d{8}", pd_cfg):
        new_date = pd_cfg
    else:
        new_date = None  # 빈 값 등 → 그대로 둠

    def vname(c):
        return to_camel(c) if cfg["value_naming"] == "camel" else c

    sl_off = set(cfg["slitting"]["disabled"])
    for kind in CSV_KINDS:
        if kind == "slitting" and not cfg["slitting"]["enabled"]:
            continue
        path = csv_dir / f"{kind}_{cfg['csv_suffix']}.csv"
        if not path.exists():
            sys.exit(f"[오류] CSV 파일이 없어요: {path}\n  config.json의 csv_dir·csv_suffix를 확인해 주세요.")
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                eq = row["equipment_id"]
                if kind == "slitting" and eq in sl_off:
                    continue  # PLC가 대신 보내는 슬리팅
                note = row.get("scenario_note", "") or ""
                line_id = row.get("line_id")
                if kind == "inspection":
                    pid = row["product_id"]
                    if new_date:
                        pid = re.sub(r"-\d{8}-", f"-{new_date}-", pid, count=1)
                    data = dict(product_id=pid, line_id=line_id,
                                paint_id=row["paint_equipment_id"],
                                vision_pass=(row["result"].strip().upper() == "OK"))
                else:
                    pid = (row.get("product_id") or "").strip() or None
                    if pid and new_date:
                        pid = re.sub(r"-\d{8}-", f"-{new_date}-", pid, count=1)
                    data = dict(values={vname(c): num(row[c]) for c in VALUE_COLS[kind]
                                        if (row.get(c) or "").strip() != ""},   # 빈 칸은 보내지 않음
                                line_id=line_id if kind == "painting" else None, product_id=pid)
                events.append(dict(t=float(row["planned_offset_sec"]), kind=kind, eq=eq,
                                   key=stream_key(kind, eq, line_id), data=data,
                                   star=note.startswith("★"), note=note))
                # v0.3.9: 투입(start) 메시지 — CSV에 planned_start_sec가 있을 때만 (옛 CSV면 완료만 보냄)
                if (row.get("planned_start_sec") or "").strip():
                    events.append(dict(t=float(row["planned_start_sec"]), kind=kind, eq=eq,
                                       key=stream_key(kind, eq, line_id), data=dict(data, start=True),
                                       star=False, note=""))

    # 코일 입고 (materialId: COIL-날짜-001부터, 날짜는 제품 ID와 같은 규칙)
    coil = cfg["coil"]
    if coil["enabled"]:
        coil_date = new_date or datetime.now(KST).strftime("%Y%m%d")
        n = 0
        for s in sorted(coil["schedule"], key=lambda s: float(s.get("at_sec", 0))):
            for _ in range(int(s.get("rolls", 1))):
                n += 1
                events.append(dict(t=float(s.get("at_sec", 0)), kind="coil", eq="COIL", key="COIL",
                                   data=dict(material_id=f"COIL-{coil_date}-{n:03d}",
                                             product_qty=int(s.get("product_qty", coil["product_qty"]))),
                                   star=False, note=""))

    # 자동 고장 일정 (메시지가 아니라 상태 신호의 fault 값을 바꾸는 이벤트)
    for f in auto_faults:
        for t, val in ((f.get("on"), 1), (f.get("off"), 0)):
            if t is None:
                continue
            events.append(dict(t=float(t), kind="fault", eq=f["equipment"], key="AUTO_FAULT",
                               data=dict(fault=val), star=False, note=""))

    # 시각 순서로 정렬 (같은 시각이면 고장 전환 → 코일 → 측정값 순)
    order = {"fault": 0, "coil": 1}
    # 같은 시각이면 완료(end)가 투입(start)보다 먼저 (앞 단을 나가야 다음 단에 들어가요)
    events.sort(key=lambda e: (e["t"], order.get(e["kind"], 3 if e["data"].get("start") else 2)))
    return events


def build_message(e):
    """이벤트 → (토픽, 내용, QoS). 보내는 순간에 만들어서 ts가 실제 보낸 시각이 돼요."""
    d = e["data"]
    ev = "start" if d.get("start") else "end"
    if e["kind"] == "inspection":
        return msg_vision(d["product_id"], d["line_id"], d["paint_id"], d["vision_pass"], ev)
    if e["kind"] == "coil":
        return msg_coil(d["material_id"], d["product_qty"])
    return msg_values(e["eq"], d["values"], d["line_id"], d.get("product_id"), ev)


# ---------------- 보내기 (MQTT 또는 화면) ----------------
class Sender:
    def __init__(self, cfg, dry_run):
        self.dry = dry_run
        self.client = None
        if dry_run:
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            sys.exit("[오류] paho-mqtt가 설치되지 않았어요.\n"
                     "  conda activate sfaas-player 를 했는지 확인하고, pip install -r requirements.txt\n"
                     "  (브로커 없이 확인만 하려면 --dry-run)")
        host, port = cfg["broker"]["host"], int(cfg["broker"]["port"])
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sfaas-player")
        except AttributeError:  # paho-mqtt 1.x
            self.client = mqtt.Client(client_id="sfaas-player")
        if cfg["broker"].get("username"):
            self.client.username_pw_set(cfg["broker"]["username"], cfg["broker"].get("password"))
        # 재생 시작·끝 알림: Node-RED 인버터 탭(A-SL)이 이걸 보고 재생 중일 때만 상태 신호를 보내요
        # 유언 메시지(will): player가 갑자기 죽어도 브로커가 대신 running=false를 보내요
        self.ctl_topic = f"{cfg['topics']['ctl']}/replay"
        self.client.will_set(self.ctl_topic, self._ctl_text(False), qos=1, retain=True)
        try:
            self.client.connect(host, port, keepalive=30)
        except Exception as e:
            sys.exit(f"[오류] MQTT 브로커({host}:{port})에 연결할 수 없어요: {e}\n"
                     "  Mosquitto가 켜져 있는지 확인해 주세요. (브로커 없이 확인: --dry-run)")
        self.client.loop_start()
        say(f"{GRN}MQTT 브로커 연결: {host}:{port}{RST}")
        self.client.publish(self.ctl_topic, self._ctl_text(True), qos=1, retain=True)
        say(f"{CYN}재생 시작 알림: {self.ctl_topic} running=true{RST}")

    def listen_line_commands(self, cmd_q):
        """🆕 11/20 라인 가동 명령 받기: {ctl}/line 에 {"lineId":"D","action":"start"} 가 오면 키보드 명령 "start D"처럼 처리해요.
        백엔드가 추가 주문 배정 보고서를 '승인하고 적용'하면 보내요 (R-05). hold 는 다시 멈추기"""
        if self.dry or not self.client:
            return
        topic = f"{TOPICS['ctl']}/line"

        def on_message(_client, _userdata, msg):
            try:
                body = json.loads(msg.payload.decode("utf-8"))
                line = str(body.get("lineId", "")).upper()
                action = str(body.get("action", "")).lower()
            except Exception:
                say(f"{YEL}라인 명령을 읽지 못했어요: {msg.payload[:80]!r}{RST}")
                return
            if line in LINES and action in ("start", "hold"):
                cmd_q.put(f"{action} {line}")
            else:
                say(f"{YEL}모르는 라인 명령이에요: {body}{RST}")

        self.client.on_message = on_message
        self.client.subscribe(topic, qos=1)
        say(f"{CYN}라인 가동 명령 받기: {topic} (예: {{\"lineId\":\"D\",\"action\":\"start\"}}){RST}")

    @staticmethod
    def _ctl_text(running):
        return json.dumps({"running": running, "ts": now_ts()})

    def send(self, msg):
        """msg = (토픽, 내용 dict, QoS) → 보내고 (토픽, JSON 글자) 돌려줌"""
        topic, body, qos = msg
        text = json.dumps(body, ensure_ascii=False)
        if not self.dry:
            self.client.publish(topic, text, qos=qos)
        return topic, text

    def close(self):
        if self.client:
            # 재생 끝 알림 (완료·q·Ctrl+C 모두) → 인버터 탭이 A-SL 상태 신호를 멈춰요
            self.client.publish(self.ctl_topic, self._ctl_text(False), qos=1, retain=True)
            say(f"{CYN}재생 끝 알림: {self.ctl_topic} running=false{RST}")
            time.sleep(0.3)  # QoS 1 메시지 마무리 시간
            self.client.loop_stop()
            self.client.disconnect()


# ---------------- 키보드 입력 ----------------
def keyboard_thread(cmd_q):
    while True:
        try:
            line = input()
        except Exception:
            return  # 입력이 없는 환경(파이프 등)이면 조용히 끝
        cmd_q.put(line.strip())


HELP = (f"{CYN}키보드 명령 (입력 후 Enter)\n"
        f"  f 설비ID → 고장 (예: f C-PR3) | r 설비ID → 복구\n"
        f"  c 설비ID → 통신 끊김 흉내 (예: c C-PR1) | u 설비ID → 통신 다시 연결\n"
        f"  start 라인 → 멈춰 둔 라인 가동 (예: start D) | hold 라인 → 라인 멈추기 (값만 멈춤, 상태 신호는 계속)\n"
        f"  s → 현재 상태 | h → 도움말 | q → 종료{RST}")


# ---------------- 메인 ----------------
def main():
    ap = argparse.ArgumentParser(description="KAMP CSV 재생 프로그램 (가상 설비)")
    ap.add_argument("--config", default=str(HERE / "config.json"), help="설정 파일 (기본: config.json)")
    ap.add_argument("--dry-run", action="store_true", help="MQTT로 보내지 않고 화면에만 출력")
    ap.add_argument("--speed", type=float, default=1.0, help="재생 속도 배율 (예: 2 → 2배 빠르게)")
    ap.add_argument("--manual", action="store_true", help="자동 고장 일정을 끄고 키보드로만 고장·복구")
    ap.add_argument("--quiet", action="store_true", help="--dry-run 때 메시지 한 줄씩 출력하지 않기")
    ap.add_argument("--hold", nargs="*", default=None, metavar="라인",
                    help="처음에 멈춰 둘 라인 (예: --hold D F). 'start D' 명령이나 백엔드 적용(MQTT {ctl}/line)으로 가동")
    args = ap.parse_args()
    if args.speed <= 0:
        sys.exit("[오류] --speed는 0보다 커야 해요.")

    cfg = load_config(args.config)
    TOPICS.update(cfg["topics"])
    auto_faults = [] if args.manual else cfg["faults"]
    events = load_events(cfg, auto_faults)
    duration = float(cfg["duration_sec"])

    # 스트림별 줄 (수동 고장 때 그 설비와 뒤 공정만 멈췄다가 이어서 보내려고 나눠 둬요)
    streams = {}
    for e in events:
        streams.setdefault(e["key"], deque()).append(e)
    shift = {k: 0.0 for k in streams}      # 스트림별로 밀린 시간(초, 재생 시각 기준)
    pause_causes = {}                      # 스트림 → 멈추게 한 수동 고장 설비들
    pause_start = {}                       # 스트림 → 멈춘 시작 시각
    faulted = set()                        # 지금 고장 중인 설비
    disconnected = set()                   # 통신 끊김 흉내 중인 설비
    # 🆕 11/20 멈춰 둔 라인: 그 라인의 슬리팅~검사 스트림을 0초부터 멈춰 두고, start 명령을 받은 때부터 처음 줄부터 보내요
    held = [l.upper() for l in (args.hold if args.hold is not None else cfg["hold_lines"])]
    bad = [l for l in held if l not in LINES]
    if bad:
        sys.exit(f"[오류] 없는 라인이에요: {', '.join(bad)} (A~F)")
    for line in held:
        for k in streams:
            if k != "AUTO_FAULT" and k != "COIL" and key_line(k) == line:
                pause_causes.setdefault(k, set()).add("HOLD:" + line)
                pause_start.setdefault(k, 0.0)
    dropped = 0                            # 통신 끊김으로 안 보낸 메시지 수

    # 상태 신호를 보낼 설비 (끈 슬리팅은 PLC가 대신하므로 상태 신호도 안 보냄)
    hb_cfg = cfg["heartbeat"]
    hb_off = set(hb_cfg["disabled"]) | set(cfg["slitting"]["disabled"])
    if not cfg["slitting"]["enabled"]:
        hb_off |= {f"{l}-SL" for l in LINES}
    hb_eqs = [eq for eq in ALL_EQS if eq not in hb_off] if hb_cfg["enabled"] else []
    hb_interval = float(hb_cfg["interval_sec"])
    next_hb = 0.0 if hb_eqs else None      # 다음 상태 신호 시각 (0초부터)

    counts = {k: 0 for k in KIND_NAMES}
    sender = Sender(cfg, args.dry_run)
    verbose = args.dry_run and not args.quiet

    say(f"{CYN}=== KAMP CSV 재생 시작 ==={RST}  CSV {cfg['csv_dir']} (*_{cfg['csv_suffix']}.csv) · "
        f"메시지 {len(events)}개 · 길이 {duration:g}초 · 속도 x{args.speed:g}"
        f"{' · DRY-RUN (전송 안 함)' if args.dry_run else ''}"
        f"{' · 수동 모드' if args.manual else ' · 자동 고장 일정 ' + str(len(auto_faults)) + '건'}")
    say(f"토픽: {TOPICS['raw']}/{{설비ID}} (값) · {TOPICS['hb']}/{{설비ID}} (상태 신호) · "
        f"값 이름: {cfg['value_naming']}")
    if hb_eqs:
        say(f"상태 신호: {len(hb_eqs)}대 · {hb_interval:g}초마다 (고장 여부 fault 포함)"
            f"{' (끈 설비: ' + ', '.join(sorted(hb_off)) + ')' if hb_off else ''}")
    else:
        say(f"{YEL}상태 신호: 꺼짐 → 고장·복구도 알릴 수 없어요{RST}")
    n_coil = sum(1 for e in events if e["kind"] == "coil")
    say(f"코일 입고: {n_coil}롤" if n_coil else f"{YEL}코일 입고: 꺼짐{RST}")
    if held:
        say(f"{YEL}멈춰 둔 라인: {', '.join(held)} → 상태 신호만 보내고 값은 'start 라인' 명령을 받을 때부터 보내요{RST}")
    say(HELP)

    cmd_q = queue.Queue()
    threading.Thread(target=keyboard_thread, args=(cmd_q,), daemon=True).start()
    sender.listen_line_commands(cmd_q)

    t0 = time.perf_counter()

    def now_offset():
        return (time.perf_counter() - t0) * args.speed

    def send_heartbeats(off):
        n = 0
        for eq in hb_eqs:
            if eq in disconnected:
                continue
            sender.send(msg_heartbeat(eq, eq in faulted))
            n += 1
        counts["heartbeat"] += n
        if verbose:
            cut = f" (끊김: {', '.join(sorted(disconnected))})" if disconnected else ""
            flt = f" (fault: true → {', '.join(sorted(faulted))})" if faulted else ""
            say(f"{DIM}[{off:6.1f}s] ♥ 상태 신호 {n}대{cut}{flt}{RST}")

    def set_fault(eq, val, how):
        """고장(1)·복구(0) 전환: 메시지는 따로 없고, 다음 상태 신호부터 fault 값이 바뀌어요."""
        if val:
            faulted.add(eq)
        else:
            faulted.discard(eq)
        counts["fault"] += 1
        label, color = ("고장 ▼", RED) if val else ("복구 ▲", GRN)
        note = " (통신 끊김 중이라 다시 연결되면 알려져요)" if eq in disconnected else ""
        say(f"{color}[{now_offset():6.1f}s] ■ {label} {eq} ({how}) → {TOPICS['hb']}/{eq} "
            f"상태 신호 \"fault\": {'true' if val else 'false'}{note}{RST}")

    def status_line(off):
        parts = " · ".join(f"{KIND_NAMES[k]} {counts[k]}" for k in KIND_NAMES)
        extra = f"  {RED}고장 중: {', '.join(sorted(faulted))}{RST}" if faulted else ""
        if disconnected:
            extra += f"  {YEL}통신 끊김: {', '.join(sorted(disconnected))}{RST}"
        say(f"{DIM}[{off:6.1f}s] 보낸 개수: {parts}{RST}{extra}")

    def affected_streams(eq):
        """수동 고장 때 멈출 스트림: 그 설비 + 뒤 공정 (앞 공정은 계속)"""
        if eq == "VI":
            return [k for k in streams if k.startswith("VI:")]
        if eq.startswith("PT"):
            lines = {key_line(k) for k in streams if k.startswith(eq + ":")}
            return [k for k in streams if k.startswith(eq + ":") or (k.startswith("VI:") and key_line(k) in lines)]
        line, stage = eq[0], key_stage(eq)
        return [k for k in streams if k != "AUTO_FAULT" and key_line(k) == line and key_stage(k) >= stage]

    def handle_cmd(line):
        """키보드 명령 처리. False면 종료."""
        nonlocal dropped
        parts = line.split()
        if not parts:
            return True
        c = parts[0].lower()
        if c in ("q", "quit", "exit"):
            say(f"{YEL}종료 명령을 받았어요.{RST}")
            return False
        if c in ("h", "help", "?"):
            say(HELP)
            return True
        if c == "s":
            status_line(now_offset())
            return True
        if c in ("start", "hold") and len(parts) == 2:
            line = parts[1].upper().replace("라인", "")
            if line not in LINES:
                say(f"{YEL}알 수 없는 라인이에요: {parts[1]} (A~F){RST}")
                return True
            off = now_offset()
            cause = "HOLD:" + line
            keys = [k for k in streams if k not in ("AUTO_FAULT", "COIL") and key_line(k) == line]
            if c == "start":
                if not any(cause in pause_causes.get(k, ()) for k in keys):
                    say(f"{YEL}{line}라인은 멈춰 있지 않아요.{RST}")
                    return True
                for k in keys:
                    if cause not in pause_causes.get(k, ()):
                        continue
                    pause_causes[k].discard(cause)
                    if not pause_causes[k]:
                        shift[k] += off - pause_start.pop(k)
                        del pause_causes[k]
                say(f"{GRN}[{off:6.1f}s] ▶ {line}라인 가동 → 처음 제품부터 값을 보내요 (추가 주문 적용){RST}")
            else:
                for k in keys:
                    if not pause_causes.get(k):
                        pause_causes[k] = set()
                        pause_start[k] = off
                    pause_causes[k].add(cause)
                say(f"{YEL}[{off:6.1f}s] ■ {line}라인 멈춤 → 값 전송을 멈춰요 (상태 신호는 계속){RST}")
            return True
        if c not in ("f", "r", "c", "u") or len(parts) != 2:
            say(f"{YEL}명령을 모르겠어요: '{line}'{RST}")
            say(HELP)
            return True
        eq = parts[1].upper()
        if eq not in ALL_EQS:
            say(f"{YEL}알 수 없는 설비예요: {eq} (예: C-PR3, A-SL, PT1, VI){RST}")
            return True
        off = now_offset()
        if c == "c":
            if eq in disconnected:
                say(f"{YEL}{eq}는 이미 통신 끊김 상태예요.{RST}")
                return True
            disconnected.add(eq)
            say(f"{YEL}[{off:6.1f}s] ✂ 통신 끊김 흉내 {eq} → 값·상태 신호를 모두 보내지 않아요 "
                f"(약 10초 뒤 DC로 판정돼야 해요).{RST}")
        elif c == "u":
            if eq not in disconnected:
                say(f"{YEL}{eq}는 통신 끊김 상태가 아니에요.{RST}")
                return True
            disconnected.discard(eq)
            say(f"{GRN}[{off:6.1f}s] ↺ 통신 다시 연결 {eq} → 값·상태 신호를 다시 보내요.{RST}")
        elif c == "f":
            if eq in faulted:
                say(f"{YEL}{eq}는 이미 고장 상태예요.{RST}")
                return True
            set_fault(eq, 1, "수동")
            for k in affected_streams(eq):
                if not pause_causes.get(k):
                    pause_causes[k] = set()
                    pause_start[k] = off
                pause_causes[k].add(eq)
            say(f"{YEL}  → {eq}와 뒤 공정의 값 전송을 멈춰요 (앞 공정은 계속).{RST}")
        else:  # r
            if eq not in faulted:
                say(f"{YEL}{eq}는 고장 상태가 아니에요.{RST}")
                return True
            set_fault(eq, 0, "수동")
            moved = 0.0
            for k in list(pause_causes):
                pause_causes[k].discard(eq)
                if not pause_causes[k]:   # 다른 고장이 남아 있지 않으면 이어서 보내기
                    moved = off - pause_start.pop(k)
                    shift[k] += moved
                    del pause_causes[k]
            if moved:
                say(f"{GRN}  → 밀린 줄부터 이어서 보내요 (남은 줄이 {moved:.1f}초 더 밀려요).{RST}")
        return True

    stopped_early = False
    next_status = 5.0
    try:
        while True:
            # 1) 키보드 명령
            while not cmd_q.empty():
                if not handle_cmd(cmd_q.get()):
                    raise StopIteration
            off = now_offset()
            # 진행 상황 (5초마다)
            if off >= next_status:
                status_line(off)
                next_status = (int(off) // 5 + 1) * 5.0
            # 2) 다음에 보낼 이벤트 고르기 (멈춘 스트림 제외)
            best_k, best_t = None, None
            left = False
            for k, dq in streams.items():
                if not dq:
                    continue
                causes = pause_causes.get(k)
                if causes and off >= duration and all(c.startswith("HOLD:") for c in causes):
                    continue  # 끝까지 가동하지 않은 라인은 기다리지 않아요
                left = True
                if causes:
                    continue
                t = dq[0]["t"] + shift[k]
                if best_t is None or t < best_t:
                    best_k, best_t = k, t
            # 상태 신호: 이벤트가 남아 있거나 리허설 길이 안이면 계속
            hb_due = next_hb if next_hb is not None and (left or next_hb <= duration) else None
            if not left and hb_due is None and off >= duration:
                break  # 다 보냈어요
            # 같은 시각이면 이벤트 먼저 (예: 90초 고장 전환이 90초 상태 신호에 바로 반영되게)
            do_hb = hb_due is not None and (best_t is None or hb_due < best_t)
            due = hb_due if do_hb else best_t
            if due is None or due > off:
                wait = 0.05 if due is None else min(0.05, (due - off) / args.speed)
                time.sleep(max(wait, 0.001))
                continue
            # 3) 보내기
            if do_hb:
                send_heartbeats(off)
                next_hb += hb_interval
                continue
            e = streams[best_k].popleft()
            if e["kind"] == "fault":
                set_fault(e["eq"], e["data"]["fault"], "자동")
                continue
            if e["kind"] == "coil":
                topic, text = sender.send(build_message(e))
                counts["coil"] += 1
                say(f"{CYN}[{off:6.1f}s] ◆ 코일 입고  {topic} {text}{RST}")
                continue
            if e["eq"] in disconnected:
                dropped += 1
                if e["star"] or verbose:
                    say(f"{YEL}[{off:6.1f}s] ✂ {e['eq']} 통신 끊김 중 → 안 보냄"
                        f"{' (★ 불량 줄)' if e['star'] else ''}{RST}")
                continue
            topic, text = sender.send(build_message(e))
            counts["start" if e["data"].get("start") else e["kind"]] += 1
            if e["star"]:
                say(f"{RED}[{off:6.1f}s] ★ 불량 줄 {e['eq']}  {topic} {text}\n"
                    f"           ({e['note']}){RST}")
            elif verbose:
                say(f"{DIM}[{off:6.1f}s] {topic} {text}{RST}")
    except (KeyboardInterrupt, StopIteration) as ex:
        stopped_early = True
        if isinstance(ex, KeyboardInterrupt):
            say(f"\n{YEL}Ctrl+C → 재생을 멈추고 정리할게요.{RST}")
    finally:
        elapsed = time.perf_counter() - t0  # 연결 끊는 시간은 빼고 잼
        sender.close()

    say("")
    say(f"{CYN}=== 재생 {'중단' if stopped_early else '완료'} 요약 ==={RST}  "
        f"(실제 {elapsed:.1f}초 · 재생 시각 {elapsed * args.speed:.1f}초)")
    say("  " + " · ".join(f"{KIND_NAMES[k]} {counts[k]}" for k in KIND_NAMES))
    if faulted:
        say(f"  {YEL}⚠ 복구 신호를 보내지 않은 설비: {', '.join(sorted(faulted))}{RST}")
    if dropped:
        say(f"  {YEL}통신 끊김 흉내로 보내지 않은 메시지: {dropped}개 (상태 신호 제외){RST}")
    if disconnected:
        say(f"  {YEL}⚠ 끝날 때까지 통신 끊김 상태였던 설비: {', '.join(sorted(disconnected))}{RST}")
    sys.stdout.flush()
    # 키보드 입력 대기 스레드가 남아 있어도 바로 끝내기
    os._exit(0)


if __name__ == "__main__":
    main()
