// ===== sfaas/raw/# 정제 → DB 저장용 SQL 만들기 (v0.3.9 확정판 전체, 11/18) =====
// 적용: Node-RED "SFaaS 수집" 탭 → [정제 + SQL 만들기] 더블클릭 → On Message 내용을 메모장에 백업
//       → 전부 지우고 이 파일 내용을 통째로 붙여 넣기 → Done → Deploy
//
// 출력 1: DB 저장 (msg.query + msg.params)   출력 2: 버린 메시지 (이유 포함)
// 판정(OK/NG, 상태, 알람)은 하지 않아요. 측정값 result는 비워서 저장 → 백엔드가 채움
// v0.3.8: 측정값마다 productId → product_id. 프레스는 제품 1개 = 1줄 (PR1이면 새 줄, PR2~PR4는 같은 줄에 그 값만)
// v0.3.9: 메시지 event — "start" = 설비에 들어감 (값 없음), "end" = 값을 쓰고 내보냄 (없으면 end)
//   start → product_flow에만 줄 생성 (started_at). 공정 테이블은 건드리지 않아요
//   end   → 공정 테이블에 값 쓰기 + 같은 SQL 문장으로 product_flow.ended_at (같은 시각 값)
//   공정 테이블에는 started_at·ended_at 칸이 없어요 (들어간·나간 시각은 product_flow에만)
//   measured_at = 처리 끝난 시각 고정 (프레스는 PR1 끝난 시각, PR2~PR4는 덮어쓰지 않음)

const id = msg.topic.split('/').pop();          // 정제 1: 토픽에서 설비 ID
const p = msg.payload;
const drop = (why) => [null, { topic: msg.topic, payload: p, reason: why }];

if (!p || typeof p !== 'object') return drop('JSON 아님');

// 정제 1: 시각 → 한국 시간 'YYYY-MM-DD HH:mm:ss.SSS' (DB는 시간대 없는 TIMESTAMP)
const t = new Date(p.ts);
if (isNaN(t)) return drop('ts 형식 오류');
const ts = new Date(t.getTime() + 9 * 3600 * 1000).toISOString().replace('T', ' ').slice(0, 23);

// 정제 6: QoS 1 재전송 중복 제거 (최근 3000건 기억)
const key = msg.topic + '|' + p.ts + '|' + (p.productId || p.materialId || '') + '|' + (p.event || 'end');
let seen = context.get('seen') || [];
if (seen.includes(key)) return drop('중복');
seen.push(key);
if (seen.length > 3000) seen = seen.slice(-3000);
context.set('seen', seen);

// ---- 코일 입고 ----
if (id === 'COIL') {
    const qty = Number(p.productQty ?? 20);
    if (!p.materialId || !Number.isInteger(qty) || qty <= 0) return drop('코일 필수값 오류');
    return [{ query: 'INSERT INTO coil_receipt (material_id, received_at, product_qty) VALUES ($1, $2, $3)',
              params: [p.materialId, ts, qty], topic: msg.topic }, null];
}

// ---- v0.3.9 도우미 ----
// 공정 테이블 SQL과 같은 문장으로 product_flow에 나간 시각(ended_at)을 써요
function withFlowEnd(o, pid, eq, line, when) {
    if (!pid || !line) return o;
    const n = o.params.length;
    o.query = `WITH m AS (${o.query}) INSERT INTO product_flow (product_id, equipment_id, line_id, ended_at) ` +
              `VALUES ($${n + 1}, $${n + 2}, $${n + 3}, $${n + 4}) ` +
              `ON CONFLICT (product_id, equipment_id) DO UPDATE SET ended_at = EXCLUDED.ended_at`;
    o.params = o.params.concat([pid, eq, line, when]);
    return o;
}
// 들어갈 때는 product_flow에만 줄을 만들어요 (공정 테이블엔 아무것도 안 씀)
function flowStart(pid, eq, line, when) {
    return { query: 'INSERT INTO product_flow (product_id, equipment_id, line_id, started_at) VALUES ($1, $2, $3, $4) ' +
                    'ON CONFLICT (product_id, equipment_id) DO UPDATE SET started_at = EXCLUDED.started_at',
             params: [pid, eq, line, when], topic: msg.topic };
}
// 제품이 온 라인 = 제품 ID 앞자리 (A-20261118-0001 → A)
function lineOf(pid) {
    return (typeof pid === 'string' && /^[A-F]-/.test(pid)) ? pid[0] : null;
}

// ---- 비전 검사 (visionPass → OK/NG 형식 변환, 판정은 검사기가 이미 함) ----
if (id === 'VI') {
    if (!p.productId || !p.lineId) return drop('검사 필수값 오류');
    const vline = lineOf(p.productId) || p.lineId;
    if (p.event === 'start') return [flowStart(p.productId, 'VI', vline, ts), null];
    if (typeof p.visionPass !== 'boolean') return drop('검사 필수값 오류');
    return [withFlowEnd({ query: 'INSERT INTO inspection_result (equipment_id, product_id, line_id, paint_equipment_id, result, inspected_at) ' +
                                 'VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (product_id) DO NOTHING',
                          params: ['VI', p.productId, p.lineId, p.paintId || null, p.visionPass ? 'OK' : 'NG', ts], topic: msg.topic },
                        p.productId, 'VI', vline, ts), null];
}

// ---- 측정값 (슬리팅·프레스·용접·도장) ----
// 정제 2: camelCase → DB 컬럼명 (모르는 항목은 무시)
const MAP = {
    slitting_data: { torque: 'torque', current: 'current', voltage: 'voltage' },
    press_data:    { pressureLeft: 'pressure_left', pressureRight: 'pressure_right', pressureDiff: 'pressure_diff', pressTime: 'press_time', pressureTotal: 'pressure_total' },
    welding_data:  { pressForce: 'press_force', current: 'current', voltage: 'voltage', weldTime: 'weld_time' },
    painting_data: { ph: 'ph', temperature: 'temperature', current: 'current', voltage: 'voltage' },
};
let table = null;
if (/^[A-F]-SL$/.test(id)) table = 'slitting_data';
else if (/^[A-F]-PR[1-4]$/.test(id)) table = 'press_data';
else if (/^[A-F]-WD$/.test(id)) table = 'welding_data';
else if (/^PT[12]$/.test(id)) table = 'painting_data';
if (!table) return drop('모르는 설비 ID: ' + id);

const pid = (typeof p.productId === 'string' && p.productId.trim()) ? p.productId.trim() : null;
if (table === 'press_data' && !pid) return drop('프레스 productId 없음');   // 프레스는 제품 번호로 줄을 찾아요

// v0.3.9: 투입(start) — product_flow에만 들어간 시각을 써요. 공정 테이블 행은 처리가 끝날 때 만들어요
const flowLine = lineOf(pid) || (/^[A-F]-/.test(id) ? id[0] : (p.lineId || null));
if (p.event === 'start') {
    if (!pid || !flowLine) return drop('투입 productId·라인 없음');
    return [flowStart(pid, id, flowLine, ts), null];
}

// ---- 완료(end): 공정 테이블에 값 쓰기 ----
const values = p.values || {};
const cols = ['product_id', 'equipment_id', 'measured_at'];
const params = [pid, id, ts];
for (const [k, v] of Object.entries(values)) {
    const col = MAP[table][k];
    if (!col) continue;
    const n = Number(v);                        // 정제 1: "275.1" → 275.1
    if (v === null || v === '' || !Number.isFinite(n)) return drop('숫자 아님: ' + k);   // 정제 4
    // 정제 3: 단위 맞추기 — 슬리팅 PLC 레지스터 값 변환은 PLC 확정 후 여기에 추가
    cols.push(col);
    params.push(n);
}
// 정제 5: 센서 오류만 버림 (관리값 이탈은 버리지 않음! 백엔드가 판정·알람)
// (압력 차 pressure_diff는 음수가 정상일 수 있어서 제외)
if (table === 'press_data' && cols.some((c, i) => ['pressure_left', 'pressure_right', 'pressure_total'].includes(c) && params[i] < 0)) return drop('센서 오류: 음수 압력');

// ★ 임시 (11/17): 슬리팅 A~F는 재생이 시각·제품 번호만 보내요 → 실제 인버터(A-SL) 16초 평균을 6대 모두에 채워요
if (table === 'slitting_data') {
    const src = global.get('aslAvg') || global.get('aslLatest') || { current: 0, voltage: 0 };
    if (!cols.includes('current')) { cols.push('current'); params.push(Number(src.current) || 0); }
    if (!cols.includes('voltage')) { cols.push('voltage'); params.push(Number(src.voltage) || 0); }
}

// 정제 6: 공용 도장은 lineId 필수
if (table === 'painting_data') {
    if (!/^[A-F]$/.test(p.lineId || '')) return drop('도장 lineId 없음');
    cols.push('line_id');
    params.push(p.lineId);
}
// ※ 가상 슬리팅은 values가 비어 있어도 저장해요 (생산 1개 기록 → 재공 계산에 필요)

const ph = params.map((_, i) => '$' + (i + 1)).join(', ');
let query = `INSERT INTO ${table} (${cols.join(', ')}) VALUES (${ph})`;
if (table === 'press_data') {
    // 같은 제품 줄에 이번 프레스 값만 채워요. measured_at(PR1 끝난 시각)은 덮어쓰지 않아요 (v0.3.9)
    // equipment_id는 더 뒤의 단으로만 바꿔요 (순서가 바뀌어 와도 안전). 값이 붙을 때마다 result를 비워요 (NG면 그대로)
    const valCols = cols.slice(3);
    const sets = valCols.map(c => `${c} = EXCLUDED.${c}`).concat([
        'equipment_id = CASE WHEN right(EXCLUDED.equipment_id, 1) > right(press_data.equipment_id, 1) THEN EXCLUDED.equipment_id ELSE press_data.equipment_id END',
        "result = CASE WHEN press_data.result = 'NG' THEN 'NG' ELSE NULL END",
    ]);
    query += ` ON CONFLICT (product_id) DO UPDATE SET ${sets.join(', ')}`;
} else if (pid) {
    query += ' ON CONFLICT (product_id) DO NOTHING';   // 같은 제품이 두 번 오면 1줄만
}
// v0.3.9: 같은 문장으로 product_flow 나간 시각 (공정 테이블과 같은 ts)
return [withFlowEnd({ query, params, topic: msg.topic }, pid, id, flowLine, ts), null];
