"""서버와 클라이언트가 함께 쓰는 메시지 송수신 모듈.

TCP는 바이트 '스트림'이라 send() 한 번이 recv() 한 번으로 그대로 도착한다는
보장이 없다. 메시지 두 개가 붙어서 오거나(합쳐짐), 하나가 쪼개져서 올 수 있다(분할).
그래서 메시지 경계를 직접 정해야 한다.

이 프로젝트의 규칙 (JSON Lines):
    - 메시지 1개 = JSON 객체 1개를 UTF-8로 인코딩한 뒤 끝에 '\n'을 붙인 것
    - 받는 쪽은 버퍼에 쌓아 두었다가 '\n'이 나올 때마다 한 메시지씩 잘라 낸다
json.dumps는 문자열 안의 줄바꿈을 '\\n'으로 이스케이프하므로
실제 '\n' 바이트는 메시지 끝에만 나타난다.
"""

from __future__ import annotations

import json
import socket
import threading

ENCODING = "utf-8"
DELIM = b"\n"
RECV_SIZE = 4096
MAX_MESSAGE_BYTES = 64 * 1024  # '\n' 없이 이보다 길게 들어오면 비정상 연결로 판단

DEFAULT_PORT = 5000


class ProtocolError(Exception):
    """형식이 잘못된 메시지를 받았을 때."""


def encode(msg: dict) -> bytes:
    return json.dumps(msg, ensure_ascii=False).encode(ENCODING) + DELIM


class Connection:
    """소켓 하나를 감싸서 '메시지 단위'로 주고받게 해 주는 클래스."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buf = b""
        # 서버에서는 여러 스레드(수신 스레드, 타이머 스레드)가 같은 소켓에
        # 동시에 send할 수 있다. 두 메시지의 바이트가 섞이지 않도록 송신을 잠근다.
        self._send_lock = threading.Lock()

    def send(self, msg: dict) -> None:
        data = encode(msg)
        with self._send_lock:
            self.sock.sendall(data)  # send()는 일부만 보낼 수 있으므로 sendall 사용

    def recv(self) -> dict | None:
        """다음 메시지 1개를 dict로 반환. 상대가 연결을 끊으면 None."""
        while DELIM not in self._buf:
            if len(self._buf) > MAX_MESSAGE_BYTES:
                raise ProtocolError("메시지가 너무 깁니다")
            chunk = self.sock.recv(RECV_SIZE)
            if not chunk:  # recv가 b""를 반환 = 상대가 연결을 정상 종료(FIN)
                return None
            self._buf += chunk

        line, self._buf = self._buf.split(DELIM, 1)
        try:
            msg = json.loads(line.decode(ENCODING))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ProtocolError(f"JSON 해석 실패: {e}") from e
        if not isinstance(msg, dict) or "type" not in msg:
            raise ProtocolError("'type' 필드가 없는 메시지")
        return msg

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
