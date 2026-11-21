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
import random
import re
import sys
import threading
import time
import zlib
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
# 🆕 11/21 슬리팅 더미 값 (slitting.values = "dummy"): 항목 → [가운데 값, 흔들림 폭]. ⚠️ 가짜 시연 값 (실측 아님)
#   같은 제품 번호면 늘 같은 값 (다시 재생해도 같은 그래프). 설비마다 가운데 값이 조금씩 달라요
# 11/21 실측(LS SV-iG5A, 11/20 밤 A-SL) 근처로: 가동 중 전압 약 185 V · 전류 약 0.1 A
#   토크는 이 인버터가 주지 않는 값이라 보내지 않아요 (0을 보내면 "토크 0"으로 잘못 읽혀요 → DB는 빈 값, 화면은 "측정 안 함")
SLITTING_DUMMY = {"current": [0.1, 0.02], "voltage": [185.0, 3.0]}
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
    # 🆕 11/21 슬리팅 측정값: "plc" = 값 없이 보내고 Node-RED가 실제 인버터(A-SL) 값으로 채움 (기본, 예전 그대로)
    #                         "dummy" = 이 프로그램이 토크·전류·전압 더미 값을 넣음 (시연 영상용, 인버터 없이)
    cfg["slitting"].setdefault("values", "plc")
    cfg["slitting"].setdefault("dummy", {})
    for k, v in SLITTING_DUMMY.items():
        cfg["slitting"]["dummy"].setdefault(k, v)
    if cfg["slitting"]["values"] not in ("plc", "dummy"):
        sys.exit("[설정 오류] slitting.values는 \"plc\" 또는 \"dummy\"이어야 해요.")
    cfg.setdefault("heartbeat", {})
    cfg["heartbeat"].setdefault("enabled", True)
    cfg["heartbeat"].setdefault("interval_sec", 1)
    cfg["heartbeat"].setdefault("disabled", [])
    cfg.setdefault("coil", {})
    cfg["coil"].setdefault("enabled", True)
    cfg["coil"].setdefault("product_qty", 20)
    cfg["coil"].setdefault("schedule", [{"at_sec": 0, "rolls": 4}])
    # 🆕 11/21 이어서 시작: 시나리오 앞 N초를 시작하자마자 한 번에 보내요 → 재생 시작 순간 모든 공정에 제품이 걸려 있어 바로 가동(R)
    #   (오늘 중간부터 채운 기록 make_history.py --now 뒤에 이어서 돌릴 때. 멈춰 둔 라인·코일 입고는 그대로)
    cfg.setdefault("warm_start_sec", 0)
    # 🆕 11/21 하루 근무 재생: shift_start = CSV 0초의 가상 시각("09:00"), start_from_sec = 여기서부터 재생 (그 앞은 DB에 미리 넣어 둠)
    cfg.setdefault("shift_start", None)
    cfg.setdefault("start_from_sec", 0)
    cfg.setdefault("hold_lines", [])   # 🆕 11/20 처음에 멈춰 둘 라인 (추가 주문으로 나중에 가동, --hold 와 같음)
    # 🆕 11/20 도장 경로 전환 기다리기: {"paint": "PT2", "lines": ["A","B","C"], "at": 150}
    #   at초부터 그 라인들의 PT2·검사 값을 붙잡아 두고, 경로 전환 명령({ctl}/route 또는 키보드 "route A PT2")을 받으면 그때부터 보내요
    cfg.setdefault("route_hold", None)
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


# 🆕 11/21 시연용 가상 시계: shift_start(예: 09:00)가 있으면 메시지 시각 = 오늘 근무 시작 + 재생 시각(초)
#   백엔드(sim_clock)·화면과 같은 시계라서 실제로는 밤이어도 09:00~18:00 근무로 흘러요. 배속(--speed)만큼 빨리
VCLOCK = {"base": None, "offset": None}


def now_ts():
    """보낸 시각 (ISO-8601 한국 시간, 밀리초까지). 가상 시계면 근무 시작 + 재생 시각"""
    if VCLOCK["base"] is not None and VCLOCK["offset"] is not None:
        return (VCLOCK["base"] + timedelta(seconds=VCLOCK["offset"]())).isoformat(timespec="milliseconds")
    return datetime.now(KST).isoformat(timespec="milliseconds")


# =====================================================================
#  메시지 만들기 (API 명세서 v3)  ← 메시지 모양이 바뀌면 이 부분만 고치면 돼요
#  각 함수는 (토픽, 내용 dict, QoS)를 돌려줘요.
# =====================================================================
TOPICS = {"raw": "sfaas/raw", "hb": "sfaas/hb"}   # 시작할 때 config.json 값으로 바뀜
SLITTING_MODE = {"values": "plc", "dummy": SLITTING_DUMMY}   # 시작할 때 config.json slitting 값으로 바뀜
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
                               data=dict(fault=val, pause=bool(f.get("pause"))), star=False, note=""))

    # 시각 순서로 정렬 (같은 시각이면 고장 전환 → 코일 → 측정값 순)
    order = {"fault": 0, "coil": 1}
    # 같은 시각이면 완료(end)가 투입(start)보다 먼저 (앞 단을 나가야 다음 단에 들어가요)
    events.sort(key=lambda e: (e["t"], order.get(e["kind"], 3 if e["data"].get("start") else 2)))
    return events


def slitting_dummy(eq, product_id, spec):
    """슬리팅 더미 측정값 (slitting.values = "dummy"). 제품 번호로 씨앗을 정해 다시 재생해도 같은 값이에요.
    설비마다 가운데 값을 조금 옮기고(라인 A→F), 제품마다 흔들림 폭 안에서 정규분포처럼 흔들어요"""
    rnd = random.Random(zlib.crc32(f"{eq}|{product_id}".encode()))
    shift = (LINES.index(eq[0]) - 2.5) / 2.5 if eq[0] in LINES else 0.0   # -1 ~ +1
    out = {}
    for item, (center, spread) in spec.items():
        v = center + shift * spread * 0.5 + rnd.gauss(0, spread / 2)
        v = max(center - spread * 2, min(center + spread * 2, v))   # 너무 튀지 않게
        out[item] = round(v, 2)
    return out


def build_message(e):
    """이벤트 → (토픽, 내용, QoS). 보내는 순간에 만들어서 ts가 실제 보낸 시각이 돼요."""
    d = e["data"]
    ev = "start" if d.get("start") else "end"
    if e["kind"] == "slitting" and ev == "end" and SLITTING_MODE["values"] == "dummy" and not d["values"]:
        d = dict(d, values=slitting_dummy(e["eq"], d.get("product_id") or "", SLITTING_MODE["dummy"]))
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
        clock_topic = f"{TOPICS['ctl']}/clock"   # 🆕 11/21 배속 바꾸기 {"speed": 10} (시연 스크립트가 보냄)
        route_topic = f"{TOPICS['ctl']}/route"   # 🆕 11/20 도장 경로 전환 (AI 보고서 결재 → 백엔드가 보냄)

        def on_message(_client, _userdata, msg):
            try:
                body = json.loads(msg.payload.decode("utf-8"))
                line = str(body.get("lineId", "")).upper()
                action = str(body.get("action", "")).lower()
                paint = str(body.get("paintId", "")).upper()
            except Exception:
                say(f"{YEL}명령을 읽지 못했어요: {msg.payload[:80]!r}{RST}")
                return
            if msg.topic == clock_topic:
                if body.get("speed"):
                    cmd_q.put(f"speed {body['speed']}")
                return
            if msg.topic == route_topic:
                if line in LINES and paint:
                    cmd_q.put(f"route {line} {paint}")
                else:
                    say(f"{YEL}모르는 경로 명령이에요: {body}{RST}")
            elif line in LINES and action in ("start", "hold"):
                cmd_q.put(f"{action} {line}")
            else:
                say(f"{YEL}모르는 라인 명령이에요: {body}{RST}")

        self.client.on_message = on_message
        self.client.subscribe(topic, qos=1)
        self.client.subscribe(route_topic, qos=1)
        self.client.subscribe(clock_topic, qos=1)
        say(f"{CYN}라인 가동 명령 받기: {topic} · 도장 경로 전환 받기: {route_topic}{RST}")

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
        f"  route 라인 도장 → 도장 경로 전환 (예: route A PT2, 붙잡아 둔 도장 값을 보내기 시작)\n"
        f"  s → 현재 상태 | h → 도움말 | q → 종료{RST}")


# ---------------- 메인 ----------------
def main():
    ap = argparse.ArgumentParser(description="KAMP CSV 재생 프로그램 (가상 설비)")
    ap.add_argument("--config", default=str(HERE / "config.json"), help="설정 파일 (기본: config.json)")
    ap.add_argument("--dry-run", action="store_true", help="MQTT로 보내지 않고 화면에만 출력")
    ap.add_argument("--speed", type=float, default=1.0, help="재생 속도 배율 (예: 2 → 2배 빠르게)")
    ap.add_argument("--warm", type=float, default=None, metavar="초",
                    help="이어서 시작: 시나리오 앞 N초를 시작하자마자 보내 모든 공정이 바로 가동 (설정 warm_start_sec보다 우선)")
    ap.add_argument("--from", dest="start_from", type=float, default=None, metavar="초",
                    help="이 재생 시각(근무 시작 뒤 초)부터 재생 (그 앞 기록은 DB에 미리 넣어 둠). 설정 start_from_sec보다 우선")
    ap.add_argument("--real-start", type=float, default=None, metavar="epoch",
                    help="이 실제 시각(유닉스 초)에 시작 (백엔드 가상 시계와 딱 맞추려고)")
    ap.add_argument("--manual", action="store_true", help="자동 고장 일정을 끄고 키보드로만 고장·복구")
    ap.add_argument("--quiet", action="store_true", help="--dry-run 때 메시지 한 줄씩 출력하지 않기")
    ap.add_argument("--hold", nargs="*", default=None, metavar="라인",
                    help="처음에 멈춰 둘 라인 (예: --hold D F). 'start D' 명령이나 백엔드 적용(MQTT {ctl}/line)으로 가동")
    args = ap.parse_args()
    if args.speed <= 0:
        sys.exit("[오류] --speed는 0보다 커야 해요.")

    cfg = load_config(args.config)
    TOPICS.update(cfg["topics"])
    SLITTING_MODE.update(values=cfg["slitting"]["values"], dummy=cfg["slitting"]["dummy"])
    auto_faults = [] if args.manual else cfg["faults"]
    events = load_events(cfg, auto_faults)
    duration = float(cfg["duration_sec"])

    held0 = [l.upper() for l in (args.hold if args.hold is not None else cfg["hold_lines"])]
    start_from = float(args.start_from if args.start_from is not None else cfg["start_from_sec"])
    if start_from > 0:
        # 그 앞은 DB에 미리 넣어 둔 기록 → 보내지 않아요. 멈춰 둔 라인은 가동할 때 처음부터 보내야 해서 남겨요
        events = [e for e in events if e["t"] >= start_from
                  or (e["key"] not in ("AUTO_FAULT", "COIL") and key_line(e["key"]) in held0)]
    # 스트림별 줄 (수동 고장 때 그 설비와 뒤 공정만 멈췄다가 이어서 보내려고 나눠 둬요)
    streams = {}
    for e in events:
        streams.setdefault(e["key"], deque()).append(e)
    shift = {k: 0.0 for k in streams}      # 스트림별로 밀린 시간(초, 재생 시각 기준)
    pause_causes = {}                      # 스트림 → 멈추게 한 수동 고장 설비들
    pause_start = {}                       # 스트림 → 멈춘 시작 시각
    faulted = set()                        # 지금 고장 중인 설비
    # 🆕 11/21 고장 뒤 공정: 통째로 멈추지 않고 "고장 설비를 이미 지난 제품"은 끝까지 처리 → 다음 제품이 안 오면 대기(I)
    gates = {}                             # 뒤 공정 스트림 → 막고 있는 고장 설비들
    gate_start = {}                        # 뒤 공정 스트림 → 다음 제품을 기다리기 시작한 재생 시각
    passed = {}                            # 설비 → 그 설비 처리를 끝낸(end 보낸) 제품 번호들
    # 🆕 11/21 도장 경로 (라인 → 지금 도장). 경로 전환 명령을 받으면 그 라인의 다음 도장 제품부터 새 도장으로 보내요
    #   CSV는 기본 경로(A·B·C → PT1, D·E·F → PT2)로 만들어져 있어서, 보낼 때 설비 ID만 바꿔요 (도장 CT는 두 대가 같아요)
    route_now = {l: ("PT1" if l in "ABC" else "PT2") for l in LINES}
    painted_by = {}                        # 제품 번호 → 실제로 칠한 도장 (검사 메시지 paintId · 검사 앞 공정 판단)
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
    # 🆕 11/21 이어서 시작: 멈춰 둔 라인·코일을 뺀 스트림을 warm초 앞당겨요 → 그 앞 메시지는 시작하자마자 한꺼번에 나가요
    warm = float(args.warm if args.warm is not None else cfg["warm_start_sec"])
    if warm > 0:
        for k in streams:
            if k != "COIL" and not pause_causes.get(k):
                shift[k] -= warm
        duration = max(0.0, duration - warm)   # 데이터가 끝나는 때 재생도 끝
    dropped = 0                            # 통신 끊김으로 안 보낸 메시지 수
    route_hold = cfg["route_hold"]         # 🆕 11/20 도장 경로 전환 기다리기 (at초가 되면 붙잡기 시작)
    route_armed = bool(route_hold)

    # 상태 신호를 보낼 설비 (끈 슬리팅은 PLC가 대신하므로 상태 신호도 안 보냄)
    hb_cfg = cfg["heartbeat"]
    hb_off = set(hb_cfg["disabled"]) | set(cfg["slitting"]["disabled"])
    if not cfg["slitting"]["enabled"]:
        hb_off |= {f"{l}-SL" for l in LINES}
    hb_eqs = [eq for eq in ALL_EQS if eq not in hb_off] if hb_cfg["enabled"] else []
    hb_interval = float(hb_cfg["interval_sec"])
    next_hb = 0.0 if hb_eqs else None      # 다음 상태 신호 시각 (0초부터)
    if next_hb is not None:
        next_hb = float(args.start_from if args.start_from is not None else cfg["start_from_sec"])

    counts = {k: 0 for k in KIND_NAMES}
    sender = Sender(cfg, args.dry_run)
    verbose = args.dry_run and not args.quiet

    say(f"{CYN}=== KAMP CSV 재생 시작 ==={RST}  CSV {cfg['csv_dir']} (*_{cfg['csv_suffix']}.csv) · "
        f"메시지 {len(events)}개 · 길이 {duration:g}초 · 속도 x{args.speed:g}"
        f"{' · DRY-RUN (전송 안 함)' if args.dry_run else ''}"
        f"{' · 수동 모드' if args.manual else ' · 자동 고장 일정 ' + str(len(auto_faults)) + '건'}")
    say(f"토픽: {TOPICS['raw']}/{{설비ID}} (값) · {TOPICS['hb']}/{{설비ID}} (상태 신호) · "
        f"값 이름: {cfg['value_naming']}")
    if warm > 0:
        say(f"{CYN}이어서 시작: 시나리오 앞 {warm:g}초를 바로 보내요 → 모든 공정이 처음부터 가동 (재생 길이 {duration:g}초){RST}")
    if cfg["slitting"]["enabled"]:
        sl = cfg["slitting"]
        say(f"슬리팅 측정값: " + ("더미 (토크·전류·전압, 시연용 가짜 값)" if sl["values"] == "dummy"
                                 else "값 없이 보냄 → Node-RED가 실제 인버터(A-SL) 값으로 채움")
            + (f" · 이 프로그램이 안 보내는 슬리팅: {', '.join(sl['disabled'])} (Node-RED가 보냄)" if sl["disabled"] else ""))
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

    if args.real_start:
        wait = args.real_start - time.time()
        if wait > 0:
            time.sleep(wait)
    t0 = time.perf_counter()
    clock = {"p0": t0, "off0": start_from, "speed": args.speed}

    def now_offset():
        return clock["off0"] + (time.perf_counter() - clock["p0"]) * clock["speed"]

    def set_speed(v):
        """배속 바꾸기: 지금 재생 시각에서 새 배속으로 이어서"""
        clock["off0"] = now_offset()
        clock["p0"] = time.perf_counter()
        clock["speed"] = v

    if cfg["shift_start"]:
        hh, mm = [int(x) for x in str(cfg["shift_start"]).split(":")[:2]]
        VCLOCK["base"] = datetime.now(KST).replace(hour=hh, minute=mm, second=0, microsecond=0)
        VCLOCK["offset"] = now_offset

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

    def prev_stage(k, e):
        """스트림의 바로 앞 공정 설비 (C-PR4 → C-PR3, C-WD → C-PR4, PT1:C → C-WD, VI:C → 그 제품의 도장 설비)"""
        if k.startswith("VI:"):
            pid = e["data"].get("product_id")
            return painted_by.get(pid) or e["data"].get("paint_id") or ""
        if ":" in k:   # 도장
            return f"{key_line(k)}-WD"
        i = LINE_STAGES.index(k.split("-")[1])
        return f"{k[0]}-{LINE_STAGES[i - 1]}" if i > 0 else ""

    def affected_streams(eq):
        """수동 고장 때 멈출 스트림: 그 설비 + 뒤 공정 (앞 공정은 계속)"""
        if eq == "VI":
            return [k for k in streams if k.startswith("VI:")]
        if eq.startswith("PT"):
            # 🆕 11/21 지금 경로로 이 도장을 쓰는 라인 (경로를 바꿨으면 CSV 설비 ID가 아니라 지금 경로 기준)
            lines = {L for L in LINES if route_now.get(L) == eq}
            return [k for k in streams if (k.startswith("PT") and ":" in k and key_line(k) in lines)
                    or (k.startswith("VI:") and key_line(k) in lines)]
        line, stage = eq[0], key_stage(eq)
        # 11/21: 코일 입고 줄(COIL)은 설비가 아니라서 빼요 (key_line("COIL") = "C"로 C라인 고장 때 멈추던 버그)
        return [k for k in streams if k not in ("AUTO_FAULT", "COIL") and key_line(k) == line and key_stage(k) >= stage]

    def handle_cmd(line, how="수동"):
        parts0 = line.strip().split()
        if len(parts0) == 2 and parts0[0].lower() == "speed":
            try:
                v = float(parts0[1])
            except ValueError:
                v = 0
            if v > 0:
                set_speed(v)
                say(f"{CYN}[{now_offset():8.1f}s] ⏩ 배속 ×{v:g}{RST}")
            return True
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
        if c == "route" and len(parts) >= 2:
            line = parts[1].upper().replace("라인", "")
            if line not in LINES:
                say(f"{YEL}없는 라인이에요: {line} (A~F){RST}")
                return True
            new_pt = parts[2].upper() if len(parts) > 2 else None
            off = now_offset()
            # 🆕 11/21 경로 바꾸기: 다음 도장 제품부터 새 도장으로. 옛 도장 고장 때문에 멈춰 있던 이 라인 도장·검사 줄은 풀어요
            if new_pt in ("PT1", "PT2"):
                old = route_now.get(line)
                route_now[line] = new_pt
                for k in streams:
                    if key_line(k) != line or not (k.startswith("PT") and ":" in k or k.startswith("VI:")):
                        continue
                    if gates.get(k):
                        gates[k] = {g for g in gates[k] if not g.startswith("PT")}
                        if not gates[k]:
                            del gates[k]
                            if k in gate_start:
                                shift[k] += max(0.0, off - gate_start.pop(k))
                    cs = pause_causes.get(k)
                    if cs and any(x.startswith("PT") for x in cs):
                        for x in [x for x in cs if x.startswith("PT")]:
                            cs.discard(x)
                        if not cs:
                            shift[k] += off - pause_start.pop(k)
                            del pause_causes[k]
                if old != new_pt:
                    say(f"{GRN}[{off:6.1f}s] ⇄ {line}라인 도장 경로 {old} → {new_pt} (다음 도장 제품부터){RST}")
            cause = "ROUTE:" + line
            keys = [k for k in streams if cause in pause_causes.get(k, ())]
            if not keys:
                if not new_pt:
                    say(f"{YEL}{line}라인은 도장 경로 전환을 기다리고 있지 않아요. (바꾸려면 route {line} PT2){RST}")
                return True
            for k in keys:
                pause_causes[k].discard(cause)
                if not pause_causes[k]:
                    shift[k] += off - pause_start.pop(k)
                    del pause_causes[k]
            say(f"{GRN}[{off:6.1f}s] ▶ {line}라인 도장 경로 전환 → {parts[2] if len(parts) > 2 else '새 도장'}에서 칠하기 시작 (AI 보고서 결재){RST}")
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
            set_fault(eq, 1, how)
            for k in affected_streams(eq):
                own = k == eq or (k.startswith("PT") and ":" in k and route_now.get(key_line(k)) == eq) or eq == "VI"
                if own:   # 고장 설비 자신: 안에 든 제품도 복구 때까지 그대로
                    if not pause_causes.get(k):
                        pause_causes[k] = set()
                        pause_start[k] = off
                    pause_causes[k].add(eq)
                else:     # 뒤 공정: 이미 지나간 제품은 끝까지, 그다음 제품부터 기다림 → 자기 일 끝나면 대기(I)
                    gates.setdefault(k, set()).add(eq)
            say(f"{YEL}  → {eq} 멈춤. 뒤 공정은 하던 제품을 끝내고 다음 제품을 기다려요 (앞 공정은 계속).{RST}")
        else:  # r
            if eq not in faulted:
                say(f"{YEL}{eq}는 고장 상태가 아니에요.{RST}")
                return True
            set_fault(eq, 0, how)
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
            off = now_offset()
            # 🆕 도장 경로 전환 기다리기: at초부터 새 도장·검사 값을 붙잡아 둬요 (결재 → route 명령으로 풀림)
            if route_armed and off >= float(route_hold["at"]):
                route_armed = False
                paint = str(route_hold["paint"]).upper()
                for L in [x.upper() for x in route_hold["lines"]]:
                    for k in streams:
                        if k in (f"{paint}:{L}", f"VI:{L}"):
                            if not pause_causes.get(k):
                                pause_causes[k] = set()
                                pause_start[k] = float(route_hold["at"])
                            pause_causes[k].add("ROUTE:" + L)
                say(f"{YEL}[{off:6.1f}s] ■ {', '.join(route_hold['lines'])}라인 도장 경로 전환 대기 → {paint} 값은 결재(route 명령)를 받으면 보내요{RST}")
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
                if causes and off >= duration and all(c.startswith(("HOLD:", "ROUTE:")) for c in causes):
                    continue  # 끝까지 가동하지 않은 라인은 기다리지 않아요
                left = True
                # 🆕 고장 뒤 공정: 맨 앞 제품이 고장 설비를 아직 못 지났으면 기다림 (지났으면 그대로 처리)
                if gates.get(k):
                    pid = dq[0]["data"].get("product_id")
                    # 바로 앞 공정을 지났는지로 판단 (복구 뒤에도 PR3 → PR4 → 용접 → 도장 → 검사 순서가 지켜지게)
                    if pid and pid not in passed.get(prev_stage(k, dq[0]), ()):
                        if dq[0]["t"] + shift[k] <= off and k not in gate_start:
                            gate_start[k] = off
                        continue
                    if all(g not in faulted for g in gates[k]):   # 복구 뒤 첫 제품이 지나옴 → 기다린 만큼 밀고 막기 끝
                        if k in gate_start:
                            shift[k] += max(0.0, off - gate_start.pop(k))
                        del gates[k]
                # 경로 전환 대기만 걸린 스트림: 대기 시작(at초) 전에 나갈 값은 그대로 보내요 (재생이 잠깐 밀려도 순서 유지)
                if causes and not (route_hold and all(c.startswith("ROUTE:") for c in causes)
                                   and dq[0]["t"] + shift[k] < float(route_hold["at"])):
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
                wait = 0.05 if due is None else min(0.05, (due - off) / clock["speed"])
                time.sleep(max(wait, 0.001))
                continue
            # 3) 보내기
            if do_hb:
                send_heartbeats(off)
                next_hb += max(hb_interval, clock["speed"])   # 상태 신호는 실제 1초마다 (배속이 커도 메시지가 쏟아지지 않게)
                continue
            e = streams[best_k].popleft()
            # 🆕 11/21 도장 경로: 도장 값은 지금 경로의 도장 ID로, 검사 결과의 paintId는 실제로 칠한 도장으로
            if e["kind"] == "painting":
                L = e["data"].get("line_id") or key_line(best_k)
                if route_now.get(L) and route_now[L] != e["eq"]:
                    e = dict(e, eq=route_now[L])
                if not e["data"].get("start") and e["data"].get("product_id"):
                    painted_by[e["data"]["product_id"]] = e["eq"]
            elif e["kind"] == "inspection":
                pt = painted_by.get(e["data"].get("product_id"))
                if pt and pt != e["data"].get("paint_id"):
                    e = dict(e, data=dict(e["data"], paint_id=pt))
            if e["kind"] == "fault":
                if e["data"].get("pause"):
                    # 🆕 11/21 "pause": true 고장 → 키보드 f/r과 같이 그 설비와 뒤 공정의 값 전송을 멈췄다가 복구 때 이어서 보내요
                    #   (CSV를 고장에 맞춰 다시 만들지 않아도 뒤 공정이 실제로 멈춰요)
                    handle_cmd(f"{'f' if e['data']['fault'] else 'r'} {e['eq']}", "자동")
                else:
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
            if not e["data"].get("start") and e["data"].get("product_id"):
                passed.setdefault(e["eq"], set()).add(e["data"]["product_id"])   # 고장 뒤 공정 기다림 판단용
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
        f"(실제 {elapsed:.1f}초 · 재생 시각 {now_offset():.1f}초)")
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
