"""터미널용 테스트 클라이언트 (1주차 검증용, GUI 완성 전까지 사용).

실행:  python client_cli.py --name 유경
       python client_cli.py --host 192.168.0.10 --name 친구

입력
    b      버저
    1~4    답 제출 (답변권이 있을 때)
    q      종료
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading

from protocol import DEFAULT_PORT, Connection, ProtocolError

my_pid = None


def show(msg: dict) -> None:
    t = msg["type"]
    if t == "WELCOME":
        global my_pid
        my_pid = msg["player"]
        print(f"\n접속 완료: 나는 P{my_pid} ({msg['name']})")
    elif t == "WAITING":
        print(msg["message"])
    elif t == "GAME_START":
        names = ", ".join(f"P{k} {v}" for k, v in msg["players"].items())
        print(f"\n=== 경기 시작 ({names}) / 총 {msg['total']}문제 ===")
    elif t == "QUESTION":
        print(f"\n[문제 {msg['index']}/{msg['total']}] {msg['question']}")
        for i, c in enumerate(msg["choices"], 1):
            print(f"   {i}. {c}")
        print(f"   → {msg['time_limit']:.0f}초 안에 b 를 눌러 버저!  점수 {msg['scores']}")
    elif t == "BUZZ_GRANTED":
        who = "내가" if msg["player"] == my_pid else f"P{msg['player']}가"
        print(f"** {who} 답변권 획득 ({msg['time_limit']:.0f}초)")
    elif t == "BUZZ_REJECTED":
        print(f"   (버저 거절: {msg['reason']})")
    elif t == "TURN_PASSED":
        who = "나에게" if msg["player"] == my_pid else f"P{msg['player']}에게"
        why = "오답" if msg["reason"] == "wrong" else "시간 초과"
        print(f"** {why}! 답변권이 {who} 넘어감 ({msg['time_limit']:.0f}초)")
    elif t == "ANSWER_RESULT":
        if msg["timeout"]:
            print(f"   P{msg['player']} 시간 초과")
        else:
            print(f"   P{msg['player']} → {msg['choice']}번: {'정답!' if msg['correct'] else '오답'}")
    elif t == "REVEAL":
        print(f"   정답 {msg['answer']}번 | {msg['explanation']}")
        print(f"   점수 {msg['scores']}")
    elif t == "GAME_OVER":
        w = msg["winner"]
        result = "무승부" if w == 0 else ("승리!" if w == my_pid else "패배")
        print(f"\n=== 경기 종료: {msg['scores']} → {result} ===")
        if msg["wrong_notes"]:
            print("--- 오답 노트 ---")
            for n in msg["wrong_notes"]:
                mine = "시간 초과" if n["your_choice"] is None else f"{n['your_choice']}번"
                print(f"[{n['index']}] {n['question']}")
                print(f"    내 답: {mine} / 정답: {n['answer']}번 {n['choices'][n['answer'] - 1]}")
                print(f"    해설: {n['explanation']}")
        print("Enter를 누르면 종료합니다.")
    elif t == "OPPONENT_LEFT":
        print(f"\n상대(P{msg['player']})가 나갔습니다. 경기 중단. Enter를 누르면 종료합니다.")
    elif t == "ERROR":
        print(f"   [오류] {msg['message']}")
    else:
        print(f"   (알 수 없는 메시지) {msg}")


def receiver(conn: Connection, done: threading.Event) -> None:
    try:
        while True:
            msg = conn.recv()
            if msg is None:
                break
            show(msg)
            if msg["type"] in ("GAME_OVER", "OPPONENT_LEFT"):
                break
    except (OSError, ProtocolError) as e:
        print(f"연결 오류: {e}")
    done.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    sock = socket.create_connection((args.host, args.port))
    conn = Connection(sock)
    conn.send({"type": "HELLO", "name": args.name})

    done = threading.Event()
    threading.Thread(target=receiver, args=(conn, done), daemon=True).start()

    try:
        for line in sys.stdin:
            if done.is_set():
                break
            cmd = line.strip().lower()
            if cmd == "q":
                break
            elif cmd == "b":
                conn.send({"type": "BUZZ"})
            elif cmd.isdigit():
                conn.send({"type": "ANSWER", "choice": int(cmd)})
            elif cmd:
                print("b=버저, 1~4=답, q=종료")
    except (KeyboardInterrupt, OSError):
        pass
    conn.close()


if __name__ == "__main__":
    main()
