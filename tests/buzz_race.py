"""동시 버저 테스트: 두 클라이언트가 '같은 순간'에 BUZZ를 보내도
답변권이 정확히 한 명에게만 가는지 여러 번 반복해서 확인한다.

사용법 (서버를 먼저 켜 둔다):
    python server.py
    python tests/buzz_race.py --trials 50

매 회차마다 두 클라이언트가 접속 → 첫 문제를 받을 때까지 대기 →
threading.Barrier로 동시에 출발해서 BUZZ 전송 → 결과 확인 → 연결 종료.
(연결을 끊으면 서버는 경기를 중단하고 다음 2명을 기다린다.)
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from protocol import DEFAULT_PORT, Connection  # noqa: E402


def wait_for(conn: Connection, msg_type: str) -> dict:
    while True:
        msg = conn.recv()
        if msg is None:
            raise ConnectionError(f"{msg_type}을 기다리는 중 연결 종료")
        if msg["type"] == msg_type:
            return msg


def run_trial(host: str, port: int) -> dict:
    conns, pids = [], []
    for i in range(2):
        sock = socket.create_connection((host, port))
        sock.settimeout(5)
        c = Connection(sock)
        c.send({"type": "HELLO", "name": f"race{i + 1}"})
        pids.append(wait_for(c, "WELCOME")["player"])
        conns.append(c)

    for c in conns:
        wait_for(c, "QUESTION")

    barrier = threading.Barrier(2)
    seen = [None, None]  # 각 클라이언트가 본 (BUZZ_GRANTED의 player, 거절 여부)

    def contender(idx: int) -> None:
        c = conns[idx]
        barrier.wait()            # 두 스레드가 여기서 동시에 풀린다
        c.send({"type": "BUZZ"})
        granted, rejected = None, False
        # 이긴 쪽은 BUZZ_GRANTED만, 진 쪽은 BUZZ_GRANTED + BUZZ_REJECTED를 받는다
        while granted is None or (granted != pids[idx] and not rejected):
            msg = c.recv()
            if msg is None:
                break
            if msg["type"] == "BUZZ_GRANTED":
                granted = msg["player"]
            elif msg["type"] == "BUZZ_REJECTED":
                rejected = True
        seen[idx] = (granted, rejected)

    threads = [threading.Thread(target=contender, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in conns:
        c.close()

    (g1, r1), (g2, r2) = seen
    ok = (g1 == g2                       # 두 화면이 같은 사람을 답변자로 표시
          and g1 in pids
          and (r1, r2).count(True) == 1  # 정확히 한 명만 거절됨
          and seen[pids.index(g1)][1] is False)  # 거절된 쪽은 승자가 아님
    return {"ok": ok, "winner": g1, "seen": seen}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--trials", type=int, default=30)
    args = ap.parse_args()

    winners = Counter()
    failures = 0
    for i in range(1, args.trials + 1):
        r = run_trial(args.host, args.port)
        winners[r["winner"]] += 1
        if not r["ok"]:
            failures += 1
            print(f"#{i} 실패: {r['seen']}")
    print(f"\n{args.trials}회 중 실패 {failures}회")
    print(f"답변권 획득 분포: P1 {winners[1]}회 / P2 {winners[2]}회")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
