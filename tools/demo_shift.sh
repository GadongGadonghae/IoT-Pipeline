#!/usr/bin/env bash
# =====================================================================
# 하루 근무 시연 (11/21): 근무 09:00~18:00, 목표 1000개를 A~D 4라인이 만드는 "느린 공장"을 가상 시계로 돌려요
#   준비:  bash demo_shift.sh prep            → 지난 3일 + 오늘 09:00~11:00 기록을 DB에 (백엔드는 꺼 두세요)
#   시작:  bash demo_shift.sh start [배속]     → 가상 시계 11:00·배속(기본 10)으로 맞추고 재생기 시작 (그다음 백엔드를 켜요)
#   배속:  bash demo_shift.sh speed 배속       → 촬영 중 배속 바꾸기 (백엔드·재생기·화면이 같이 바뀜)
#   끄기:  bash demo_shift.sh off              → 가상 시계 끄기 (실제 시계로)
# 화면 시계·보고서 계산·재생 메시지 시각이 모두 같은 가상 시각이라, 실제로 언제 찍어도 09:00~18:00 근무로 보여요.
# 배속 ×10이면 가상 1시간 = 실제 6분 (11:00~18:00 = 42분), ×20이면 21분
# =====================================================================
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
PSQL="${PSQL:-/c/Program Files/PostgreSQL/18/bin/psql.exe}"
PY="${PY:-python}"
PLAYER_PY="${PLAYER_PY:-/c/Users/kim30/miniconda3/envs/sfaas-player/python.exe}"
MQPUB="${MQPUB:-/c/Program Files/mosquitto/mosquitto_pub.exe}"
export PGPASSWORD="${PGPASSWORD:-pw1234}"
DB="-U postgres -d sfaas"
FROM="11:00"          # 재생을 이어서 시작하는 가상 시각 (준비 기록 끝)
FROM_SEC=7200         # 근무 시작(09:00)부터 초

sql() { "$PSQL" $DB -q -v ON_ERROR_STOP=1 -At -c "$1"; }

case "$1" in
  prep)
    cd "$HERE"
    PYTHONIOENCODING=utf-8 "$PY" make_history.py --shift 09:00-18:00 --prep-until "$FROM" --target 1000 | tail -1
    "$PSQL" $DB -q -v ON_ERROR_STOP=1 -f "$HERE/../history/history_$(date +%Y%m%d).sql" > /dev/null
    sql "INSERT INTO sim_clock (id, virtual_at, real_at, speed, active) VALUES (1, CURRENT_DATE + TIME '$FROM', LOCALTIMESTAMP, 1, FALSE)
         ON CONFLICT (id) DO UPDATE SET active = FALSE"
    echo "준비 끝: 오늘 09:00~$FROM 기록. 시작은 bash demo_shift.sh start [배속]"
    ;;
  start)
    SPEED="${2:-10}"
    # 3초 뒤를 기준으로 DB(백엔드·화면)와 재생기를 같은 시각에 맞춰요
    REAL=$(( $(date +%s) + 3 ))
    sql "INSERT INTO sim_clock (id, virtual_at, real_at, speed, active)
         VALUES (1, CURRENT_DATE + TIME '$FROM', to_timestamp($REAL) AT TIME ZONE 'Asia/Seoul', $SPEED, TRUE)
         ON CONFLICT (id) DO UPDATE SET virtual_at = EXCLUDED.virtual_at, real_at = EXCLUDED.real_at, speed = EXCLUDED.speed, active = TRUE"
    echo "가상 시계: $FROM 부터 ×$SPEED"
    cd "$HERE/../player"
    exec env PYTHONIOENCODING=utf-8 "$PLAYER_PY" -u player.py --config config_shift_noplc.json --quiet --speed "$SPEED" --from $FROM_SEC --real-start $REAL < /dev/null
    ;;
  speed)
    SPEED="${2:?배속을 적어 주세요 (예: 20)}"
    # 지금 가상 시각에서 새 배속으로 이어요
    sql "UPDATE sim_clock SET virtual_at = virtual_at + (LOCALTIMESTAMP - real_at) * speed, real_at = LOCALTIMESTAMP, speed = $SPEED WHERE id = 1"
    "$MQPUB" -h localhost -t sfaas/ctl/clock -q 1 -m "{\"speed\": $SPEED}"
    echo "배속 ×$SPEED"
    ;;
  off)
    sql "UPDATE sim_clock SET active = FALSE WHERE id = 1"
    echo "가상 시계 끔 (실제 시계)"
    ;;
  *)
    sed -n 2,11p "$0"; exit 1 ;;
esac
