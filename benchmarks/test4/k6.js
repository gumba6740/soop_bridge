import ws from 'k6/ws';
import { Trend } from 'k6/metrics';

const delay = new Trend('chat_delay_ms');  // 지연 기록용 커스텀 지표

export const options = {
    vus: 2000,        // 가상 유저(구독자) 수 — 이걸 바꿔가며 실험
    duration: '30s', // 30초 동안
};

// export const options = {
//     stages: [
//         { duration: '1m', target: 2000 },   // 1분에 걸쳐 0→2000명
//         { duration: '1m', target: 4000 },   // 다음 1분에 2000→4000
//         { duration: '1m', target: 6000 },   // 4000→6000
//         { duration: '30s', target: 0 },     // 마무리로 내림
//     ],
// };

export default function () {
    const url = 'ws://localhost:8000/streams/bid_1/live';
    ws.connect(url, {}, function (socket) {
    socket.on('message', function (msg) {
        // msg 예: "[bid_1] unick(uid): 1754051696.789"
        const sent = parseFloat(msg.split(': ')[1]);  // 뒤 타임스탬프 떼기
        const now = Date.now() / 1000;                 // 현재시각(초)
        delay.add((now - sent) * 1000);                // 지연을 ms로 기록
    });
    socket.setTimeout(function () { socket.close(); }, 30000); // 30초 후 닫기
    });
}