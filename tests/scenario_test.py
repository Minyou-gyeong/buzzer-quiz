"""경기 규칙 시나리오 테스트: 사람이 하는 대신 스크립트가 두 참가자를 조종해서
정답 / 오답→상대에게 넘김 / 답변 시간 초과 / 버저 시간 초과 / 오답 노트를 확인한다.

사용법 (짧은 제한 시간으로 서버를 켜야 빨리 끝난다):
    python server.py --buzz-time 1 --answer-time 1 --reveal-time 0.5
    python tests/scenario_test.py

questions.json의 샘플 5문제 기준으로 작성되어 있다.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from protocol import DEFAULT_PORT, Connection  # noqa: E402


def wait_for(conn: Connection, msg_type: str) -> dict:
    while True:
        msg = conn.recv()
        if msg is None:
            raise ConnectionError(f"{msg_type}을 기다리는 중 연결 종료")
        if msg["type"] == msg_type:
            return msg


def check(cond: bool, label: str) -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label)
    if not cond:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    with open(os.path.join(ROOT, "questions.json"), encoding="utf-8") as f:
        answers = [q["answer"] for q in json.load(f)["questions"]]
    wrong = [a % 4 + 1 for a in answers]  # 정답이 아닌 아무 번호

    a, b = [], []
    for name, holder in (("A", a), ("B", b)):
        s = socket.create_connection((args.host, args.port))
        s.settimeout(10)
        c = Connection(s)
        c.send({"type": "HELLO", "name": name})
        holder.append(c)
        holder.append(wait_for(c, "WELCOME")["player"])
    (A, pa), (B, pb) = a, b

    print("문제 1: A 버저 → 정답")
    wait_for(A, "QUESTION"); wait_for(B, "QUESTION")
    A.send({"type": "BUZZ"})
    g = wait_for(B, "BUZZ_GRANTED")
    check(g["player"] == pa, "B 화면에도 A의 답변권이 표시됨")
    A.send({"type": "ANSWER", "choice": answers[0]})
    r = wait_for(B, "ANSWER_RESULT")
    check(r["correct"] and r["scores"][pa - 1] == 10, "A 정답 +10점")
    wait_for(A, "REVEAL"); wait_for(B, "REVEAL")

    print("문제 2: A 오답 → B에게 넘어감 → B 정답")
    wait_for(A, "QUESTION"); wait_for(B, "QUESTION")
    A.send({"type": "BUZZ"})
    wait_for(A, "BUZZ_GRANTED")
    B.send({"type": "ANSWER", "choice": answers[1]})  # 답변권 없는 B가 몰래 답 → 거절돼야 함
    e = wait_for(B, "ERROR")
    check("답변권" in e["message"], "답변권 없는 참가자의 답은 거절")
    A.send({"type": "ANSWER", "choice": wrong[1]})
    t = wait_for(B, "TURN_PASSED")
    check(t["player"] == pb and t["reason"] == "wrong", "오답 후 답변권이 B로 넘어감")
    B.send({"type": "ANSWER", "choice": answers[1]})
    rv = wait_for(A, "REVEAL")
    check(rv["scores"] == [10, 10], "점수 10:10")
    wait_for(B, "REVEAL")

    print("문제 3: B 버저 후 답하지 않음 → 시간 초과로 A에게 → A도 시간 초과")
    wait_for(A, "QUESTION"); wait_for(B, "QUESTION")
    B.send({"type": "BUZZ"})
    t = wait_for(A, "TURN_PASSED")
    check(t["player"] == pa and t["reason"] == "timeout", "시간 초과 후 A에게 넘어감")
    rv = wait_for(A, "REVEAL")
    check(True, "둘 다 시간 초과 → 정답 공개")
    wait_for(B, "REVEAL")

    print("문제 4: 아무도 버저를 누르지 않음")
    wait_for(A, "QUESTION"); wait_for(B, "QUESTION")
    rv = wait_for(A, "REVEAL")
    check(rv["answer"] == answers[3], "버저 시간 초과 → 정답 공개")
    wait_for(B, "REVEAL")

    print("문제 5: 정답 공개 중 버저 → 거절")
    wait_for(A, "QUESTION"); wait_for(B, "QUESTION")
    A.send({"type": "BUZZ"})
    wait_for(A, "BUZZ_GRANTED")
    A.send({"type": "ANSWER", "choice": answers[4]})
    wait_for(A, "REVEAL")
    B.send({"type": "BUZZ"})
    rj = wait_for(B, "BUZZ_REJECTED")
    check(rj["reason"] == "REVEAL", "정답 공개 중 버저는 거절")

    print("경기 종료")
    oa = wait_for(A, "GAME_OVER")
    ob = wait_for(B, "GAME_OVER")
    check(oa["scores"][pa - 1] == 20 and oa["scores"][pb - 1] == 10, "최종 점수 A 20 : B 10")
    check(oa["winner"] == pa, "승자 A")
    check([n["index"] for n in oa["wrong_notes"]] == [2, 3], "A 오답 노트: 2번(오답), 3번(시간 초과)")
    check([n["index"] for n in ob["wrong_notes"]] == [3], "B 오답 노트: 3번(시간 초과)")
    check(oa["wrong_notes"][1]["your_choice"] is None, "시간 초과는 your_choice=None")
    A.close(); B.close()
    print("\n모든 시나리오 통과")


if __name__ == "__main__":
    main()
