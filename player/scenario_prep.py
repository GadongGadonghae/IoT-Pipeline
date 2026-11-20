"""시연 시나리오 준비 (11/20): 가동하지 않는 라인을 계획정지(S)로 만들어요.
사용 (재생 전에, demo_reset.sql 다음):
    python scenario_prep.py s1      → 시나리오 1: E·F라인 계획정지 (A~D 가동)
    python scenario_prep.py s2      → 시나리오 2: D·E·F라인 계획정지 (A~C 가동)
- 백엔드(S-02 계획정지 시작)를 불러요. 백엔드가 켜져 있어야 해요
- 로그인 계정·비밀번호를 물어봐요 (아무 권한이나 돼요)
- 계획정지 라인은 AI 보고서에서 "멈춘 라인"으로 보고 그 몫을 가동 라인에 나눠요
- 끝내기: demo_reset.sql이 끝나지 않은 계획정지를 지워요
"""
import getpass
import json
import sys
import urllib.error
import urllib.request

API = "http://localhost:8080/api"
STOP = {"s1": ["E", "F"], "s2": ["D", "E", "F"]}


def call(method, path, body=None, token=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in STOP:
        sys.exit("사용: python scenario_prep.py s1   또는   python scenario_prep.py s2")
    lines = STOP[sys.argv[1]]
    user = input("로그인 아이디: ").strip()
    pw = getpass.getpass("비밀번호: ")
    try:
        token = call("POST", "/auth/login", {"loginId": user, "password": pw})["accessToken"]
    except urllib.error.HTTPError as e:
        sys.exit(f"[오류] 로그인 실패 ({e.code})")
    except urllib.error.URLError:
        sys.exit("[오류] 백엔드에 연결할 수 없어요. gradlew bootRun으로 켰는지 확인해 주세요")
    for line in lines:
        try:
            r = call("POST", "/planned-stops", {"lineId": line, "reasonCode": "PL_SHIFT"}, token)
            print(f"  {line}라인 계획정지 시작 (stopId {r['stopId']})")
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            print(f"  {line}라인: 이미 계획정지 중이거나 실패 ({e.code}) {msg[:120]}")
    print(f"완료. 이제 python player.py --config config_{sys.argv[1]}.json  (PLC 없으면 config_{sys.argv[1]}_noplc.json)")


if __name__ == "__main__":
    main()
