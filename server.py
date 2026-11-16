"""2인 퀴즈 배틀 게임 서버.

실행:  python server.py            (기본 0.0.0.0:5000)
       python server.py --port 6000 --answer-time 10

구조
    - 메인 스레드: 참가자 2명이 접속할 때까지 accept → 경기 진행 → 끝나면 다시 대기
    - 참가자별 수신 스레드: 메시지를 받아 Game.on_message()로 넘김
    - 타이머 스레드(threading.Timer): 버저 제한 시간 / 답변 제한 시간 / 다음 문제 넘김

동시성 핵심
    Game의 모든 상태 변경은 self.lock 안에서만 일어난다.
    두 참가자의 BUZZ가 거의 동시에 도착해도 lock을 먼저 잡은 쪽의 요청만
    phase == OPEN 상태를 보게 되고, 그 즉시 phase가 ANSWERING으로 바뀌므로
    늦게 들어온 BUZZ는 거절된다. → 답변권은 항상 한 명에게만 간다.
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time

from protocol import DEFAULT_PORT, Connection, ProtocolError

POINTS_CORRECT = 10


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}] {text}", flush=True)


def load_questions(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    questions = data["questions"]
    for q in questions:  # 문제은행 실수를 서버 시작 시점에 잡는다
        n = len(q["choices"])
        if not (1 <= q["answer"] <= n):
            raise ValueError(f"{q['id']}: answer는 1~{n} 사이여야 합니다")
    return questions


class Player:
    def __init__(self, pid: int, name: str, conn: Connection, addr):
        self.pid = pid  # 1 또는 2
        self.name = name
        self.conn = conn
        self.addr = addr


class Game:
    # 경기 단계
    OPEN = "OPEN"            # 문제 공개, 버저 대기
    ANSWERING = "ANSWERING"  # 누군가 답변권을 가짐
    REVEAL = "REVEAL"        # 정답 공개 후 다음 문제 대기
    END = "END"

    def __init__(self, players: list[Player], questions: list[dict],
                 buzz_time: float, answer_time: float, reveal_time: float):
        self.players = {p.pid: p for p in players}
        self.questions = questions
        self.buzz_time = buzz_time
        self.answer_time = answer_time
        self.reveal_time = reveal_time

        self.lock = threading.Lock()
        self.finished = threading.Event()

        self.phase = None
        self.q_index = -1
        self.answerer: int | None = None  # 현재 답변권을 가진 참가자
        self.tried: set[int] = set()      # 이번 문제에서 이미 답변 기회를 쓴 참가자
        self.scores = {1: 0, 2: 0}
        self.wrong = {1: [], 2: []}       # (문제 번호, 고른 보기 or None)

        self._timer: threading.Timer | None = None
        self._timer_token = 0

    # ------------------------------------------------------------------ 송신
    def _send(self, pid: int, msg: dict) -> None:
        try:
            self.players[pid].conn.send(msg)
        except OSError:
            pass  # 끊긴 연결은 수신 스레드가 감지해서 player_left()로 처리

    def _broadcast(self, msg: dict) -> None:
        for pid in self.players:
            self._send(pid, msg)

    def _score_list(self) -> list[int]:
        return [self.scores[1], self.scores[2]]

    # ------------------------------------------------------------------ 타이머
    def _schedule(self, delay: float, fn) -> None:
        """기존 타이머를 무효화하고 새 타이머를 건다.

        Timer.cancel()은 이미 실행이 시작된 콜백은 막지 못한다.
        그래서 토큰 번호를 같이 넘기고, 콜백이 lock을 잡은 뒤 토큰이
        최신인지 확인해서 '늦게 깨어난 옛날 타이머'를 무시한다.
        """
        if self._timer:
            self._timer.cancel()
        self._timer_token += 1
        token = self._timer_token
        self._timer = threading.Timer(delay, self._on_timer, args=(token, fn))
        self._timer.daemon = True
        self._timer.start()

    def _on_timer(self, token: int, fn) -> None:
        with self.lock:
            if self.finished.is_set() or token != self._timer_token:
                return
            fn()

    def _cancel_timer(self) -> None:
        self._timer_token += 1
        if self._timer:
            self._timer.cancel()

    # ------------------------------------------------------------------ 진행
    def start(self) -> None:
        with self.lock:
            self._broadcast({
                "type": "GAME_START",
                "players": {str(p.pid): p.name for p in self.players.values()},
                "total": len(self.questions),
            })
            self._next_question()

    def _next_question(self) -> None:
        self.q_index += 1
        if self.q_index >= len(self.questions):
            self._game_over()
            return
        q = self.questions[self.q_index]
        self.phase = self.OPEN
        self.answerer = None
        self.tried = set()
        log(f"문제 {self.q_index + 1}/{len(self.questions)} 출제 ({q['id']})")
        self._broadcast({
            "type": "QUESTION",
            "index": self.q_index + 1,
            "total": len(self.questions),
            "question": q["question"],
            "choices": q["choices"],     # 정답은 보내지 않는다
            "time_limit": self.buzz_time,
            "scores": self._score_list(),
        })
        self._schedule(self.buzz_time, self._on_buzz_timeout)

    def _on_buzz_timeout(self) -> None:
        log("버저 시간 초과 - 아무도 누르지 않음")
        self._reveal()

    def _give_turn(self, pid: int, msg_type: str, reason: str | None = None) -> None:
        self.phase = self.ANSWERING
        self.answerer = pid
        msg = {"type": msg_type, "player": pid, "time_limit": self.answer_time}
        if reason:
            msg["reason"] = reason
        self._broadcast(msg)
        self._schedule(self.answer_time, self._on_answer_timeout)

    def _on_answer_timeout(self) -> None:
        pid = self.answerer
        log(f"P{pid} 답변 시간 초과")
        self.tried.add(pid)
        self.wrong[pid].append((self.q_index, None))
        self._broadcast({"type": "ANSWER_RESULT", "player": pid, "choice": None,
                         "correct": False, "timeout": True, "scores": self._score_list()})
        self._pass_or_reveal("timeout")

    def _pass_or_reveal(self, reason: str) -> None:
        other = 3 - self.answerer
        if other not in self.tried:
            log(f"답변권 P{self.answerer} → P{other} ({reason})")
            self._give_turn(other, "TURN_PASSED", reason)
        else:
            self._reveal()

    def _reveal(self) -> None:
        q = self.questions[self.q_index]
        self.phase = self.REVEAL
        self.answerer = None
        self._broadcast({"type": "REVEAL", "answer": q["answer"],
                         "explanation": q.get("explanation", ""),
                         "scores": self._score_list()})
        self._schedule(self.reveal_time, self._next_question)

    def _game_over(self) -> None:
        self.phase = self.END
        self._cancel_timer()
        s1, s2 = self.scores[1], self.scores[2]
        winner = 1 if s1 > s2 else 2 if s2 > s1 else 0  # 0 = 무승부
        log(f"경기 종료 점수 {s1}:{s2}, 승자 {'무승부' if winner == 0 else f'P{winner}'}")
        for pid in self.players:
            notes = []
            for qi, choice in self.wrong[pid]:
                q = self.questions[qi]
                notes.append({"index": qi + 1, "question": q["question"],
                              "choices": q["choices"], "your_choice": choice,
                              "answer": q["answer"],
                              "explanation": q.get("explanation", "")})
            self._send(pid, {"type": "GAME_OVER", "scores": self._score_list(),
                             "winner": winner, "wrong_notes": notes})
        self.finished.set()

    # ------------------------------------------------------------------ 수신 처리
    def on_message(self, pid: int, msg: dict) -> None:
        with self.lock:
            if self.finished.is_set():
                return
            t = msg.get("type")
            if t == "BUZZ":
                self._handle_buzz(pid)
            elif t == "ANSWER":
                self._handle_answer(pid, msg.get("choice"))
            else:
                self._send(pid, {"type": "ERROR", "message": f"알 수 없는 메시지: {t}"})

    def _handle_buzz(self, pid: int) -> None:
        if self.phase != self.OPEN:
            # 상대가 먼저 눌러 이미 ANSWERING이 됐거나, 정답 공개 중
            log(f"P{pid} BUZZ 거절 (phase={self.phase})")
            self._send(pid, {"type": "BUZZ_REJECTED", "reason": self.phase})
            return
        log(f"P{pid} BUZZ 수락 → 답변권 부여")
        self.tried.add(pid)
        self._give_turn(pid, "BUZZ_GRANTED")

    def _handle_answer(self, pid: int, choice) -> None:
        if self.phase != self.ANSWERING or pid != self.answerer:
            self._send(pid, {"type": "ERROR", "message": "답변권이 없습니다"})
            return
        q = self.questions[self.q_index]
        if not isinstance(choice, int) or not (1 <= choice <= len(q["choices"])):
            self._send(pid, {"type": "ERROR",
                             "message": f"choice는 1~{len(q['choices'])} 정수여야 합니다"})
            return

        self._cancel_timer()  # 답이 왔으니 답변 타이머 중지
        correct = choice == q["answer"]
        if correct:
            self.scores[pid] += POINTS_CORRECT
        else:
            self.wrong[pid].append((self.q_index, choice))
        log(f"P{pid} 답 {choice} → {'정답' if correct else '오답'}")
        self._broadcast({"type": "ANSWER_RESULT", "player": pid, "choice": choice,
                         "correct": correct, "timeout": False,
                         "scores": self._score_list()})
        if correct:
            self._reveal()
        else:
            self._pass_or_reveal("wrong")

    def player_left(self, pid: int) -> None:
        with self.lock:
            if self.finished.is_set():
                return
            log(f"P{pid} 연결 끊김 → 경기 중단")
            self.phase = self.END
            self._cancel_timer()
            self._send(3 - pid, {"type": "OPPONENT_LEFT", "player": pid,
                                 "scores": self._score_list()})
            self.finished.set()


def reader_loop(game: Game, player: Player) -> None:
    """참가자 한 명의 메시지를 계속 받아 Game에 넘기는 스레드."""
    try:
        while True:
            msg = player.conn.recv()
            if msg is None:
                break
            game.on_message(player.pid, msg)
    except (OSError, ProtocolError) as e:
        log(f"P{player.pid} 수신 오류: {e}")
    game.player_left(player.pid)


def accept_player(server_sock: socket.socket, pid: int) -> Player | None:
    # Windows에서는 accept()가 막혀 있는 동안 Ctrl+C가 먹지 않는다.
    # 1초 타임아웃으로 깨어나면서 기다리면 Ctrl+C로 서버를 끌 수 있다.
    while True:
        try:
            sock, addr = server_sock.accept()
            break
        except socket.timeout:
            continue
    conn = Connection(sock)
    sock.settimeout(5)  # HELLO를 5초 안에 안 보내면 버린다
    try:
        hello = conn.recv()
    except (OSError, ProtocolError):
        hello = None
    if not hello or hello.get("type") != "HELLO":
        log(f"{addr} HELLO 없음 → 연결 종료")
        conn.close()
        return None
    sock.settimeout(None)
    name = str(hello.get("name") or f"Player{pid}")[:20]
    conn.send({"type": "WELCOME", "player": pid, "name": name})
    log(f"P{pid} 접속: {name} {addr}")
    return Player(pid, name, conn, addr)


def serve(host: str, port: int, questions: list[dict], args) -> None:
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen()
    server_sock.settimeout(1.0)
    log(f"서버 시작 {host}:{port} (문제 {len(questions)}개)")

    while True:
        players: list[Player] = []
        log("참가자 대기 중...")
        while len(players) < 2:
            p = accept_player(server_sock, len(players) + 1)
            if p:
                players.append(p)
                if len(players) == 1:
                    p.conn.send({"type": "WAITING", "message": "상대를 기다리는 중"})

        game = Game(players, questions, args.buzz_time, args.answer_time, args.reveal_time)
        for p in players:
            threading.Thread(target=reader_loop, args=(game, p), daemon=True).start()
        game.start()
        while not game.finished.wait(0.5):  # 타임아웃을 줘야 Ctrl+C가 먹는다
            pass

        time.sleep(0.3)  # 마지막 메시지가 전달될 시간
        for p in players:
            p.conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="2인 퀴즈 배틀 서버")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--questions", default="questions.json")
    ap.add_argument("--buzz-time", type=float, default=15.0, help="버저 제한 시간(초)")
    ap.add_argument("--answer-time", type=float, default=10.0, help="답변 제한 시간(초)")
    ap.add_argument("--reveal-time", type=float, default=3.0, help="정답 공개 후 대기(초)")
    args = ap.parse_args()

    questions = load_questions(args.questions)
    try:
        serve(args.host, args.port, questions, args)
    except KeyboardInterrupt:
        log("서버 종료")


if __name__ == "__main__":
    main()
