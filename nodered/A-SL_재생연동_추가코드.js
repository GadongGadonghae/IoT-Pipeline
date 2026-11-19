// =====================================================================
// A-SL 인버터 탭: "player 재생 중일 때만 상태 신호 보내기" 추가 코드 (11/18)
// =====================================================================
// 왜: player가 끝나도 인버터 탭이 sfaas/hb/A-SL을 계속 보내서 A-SL만 R로 남았어요.
// 어떻게: player가 sfaas/ctl/replay 로 {"running": true/false} 를 보내요 (retain, 유언 메시지 포함).
//         인버터 탭은 running=true일 때만 상태 신호를 보내요 → 재생이 끝나면 10초 뒤 DC.
//
// 적용 순서 (Node-RED 화면, "A-SL 슬리팅: 인버터 → MQTT · PLC" 탭)
//
//  1) 왼쪽 팔레트에서 [mqtt in] 노드를 끌어다 놓고 더블클릭
//       Server : Mosquitto (localhost)   ← 이 탭의 다른 MQTT 노드와 같은 것
//       Topic  : sfaas/ctl/replay
//       QoS    : 1
//       Output : a parsed JSON object
//       Name   : sfaas/ctl/replay
//
//  2) [function] 노드를 끌어다 놓고 더블클릭
//       Name : 재생 상태 저장
//       On Message 칸에 아래 [코드 A]를 붙여 넣기
//     → 1)의 mqtt in 출력 점을 이 function 입력 점에 선으로 연결 (출력은 연결 안 함)
//
//  3) 기존 [상태 신호 (fault=false)] function을 더블클릭
//       On Message 칸 맨 위(첫 줄 주석 바로 아래)에 [코드 B]를 붙여 넣기
//
//  4) 오른쪽 위 [Deploy] 클릭
//
// 확인
//  - player를 켜기 전: "재생 상태 저장" 노드 아래 "재생 끝" (또는 아무 표시 없음),
//                      "상태 신호" 노드 아래 회색 "재생 안 함 → 신호 멈춤"
//  - python player.py 시작: "재생 중" 으로 바뀌고 상태 신호 노드가 초록 "신호 보내는 중"
//  - player 끝: 다시 "재생 끝" → 약 10초 뒤 화면에서 A-SL도 DC
//
// ※ 측정값(sfaas/raw/A-SL) 노드는 원래 꺼져 있어서 손대지 않아요 (A-SL 제품 신호는 player가 보냄)
// =====================================================================


// ---------------- [코드 A] "재생 상태 저장" function ----------------
const running = msg.payload && msg.payload.running === true;
flow.set('replayRunning', running);
node.status(running
    ? { fill: 'green', shape: 'dot', text: '재생 중' }
    : { fill: 'grey', shape: 'ring', text: '재생 끝' });
return null;


// ---------------- [코드 B] "상태 신호 (fault=false)" 맨 위에 추가 ----------------
// player가 재생 중일 때만 보내요 (재생이 끝나면 다른 설비처럼 DC가 되게)
if (!flow.get('replayRunning')) {
    node.status({ fill: 'grey', shape: 'ring', text: '재생 안 함 → 신호 멈춤' });
    return null;
}
