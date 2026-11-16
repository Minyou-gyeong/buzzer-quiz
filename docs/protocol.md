# 메시지 프로토콜

## 1. 전송 형식 (프레이밍)

- 전송 계층: TCP (Python `socket`, `AF_INET`, `SOCK_STREAM`), 기본 포트 5000
- 응용 계층 메시지: **JSON Lines**. JSON 객체 1개를 UTF-8로 인코딩하고 끝에 `\n`을 붙인다.
- 모든 메시지에는 `type` 필드가 있다.

TCP는 메시지 경계를 보존하지 않는 바이트 스트림이므로, 수신 측(`protocol.Connection.recv`)은
받은 바이트를 버퍼에 쌓아 두고 `\n` 단위로 잘라 한 메시지씩 꺼낸다.
이렇게 해서 메시지가 여러 개 붙어서 오거나 하나가 쪼개져서 와도 올바르게 처리한다.

## 2. 클라이언트 → 서버

| type | 필드 | 설명 |
|---|---|---|
| `HELLO` | `name` | 접속 직후 1회. 5초 안에 보내지 않으면 서버가 연결을 끊음 |
| `BUZZ` | - | 버저 요청 |
| `ANSWER` | `choice` (1~4) | 답 제출. 답변권이 있을 때만 유효 |

## 3. 서버 → 클라이언트

| type | 대상 | 필드 | 설명 |
|---|---|---|---|
| `WELCOME` | 본인 | `player`(1/2), `name` | 참가 번호 배정 |
| `WAITING` | 본인 | `message` | 상대 접속 대기 중 |
| `GAME_START` | 양쪽 | `players`, `total` | 경기 시작 |
| `QUESTION` | 양쪽 | `index`, `total`, `question`, `choices`, `time_limit`, `scores` | 문제 출제 (정답은 보내지 않음) |
| `BUZZ_GRANTED` | 양쪽 | `player`, `time_limit` | 답변권 부여 |
| `BUZZ_REJECTED` | 본인 | `reason` | 버저 거절 (`ANSWERING`/`REVEAL` 등 당시 단계) |
| `ANSWER_RESULT` | 양쪽 | `player`, `choice`, `correct`, `timeout`, `scores` | 채점 결과 |
| `TURN_PASSED` | 양쪽 | `player`, `reason`(`wrong`/`timeout`), `time_limit` | 상대에게 답변권 이동 |
| `REVEAL` | 양쪽 | `answer`, `explanation`, `scores` | 정답·해설 공개 |
| `GAME_OVER` | 각자 | `scores`, `winner`(0=무승부), `wrong_notes` | 경기 종료 + 본인 오답 노트 |
| `OPPONENT_LEFT` | 남은 쪽 | `player`, `scores` | 상대 연결 끊김으로 경기 중단 |
| `ERROR` | 본인 | `message` | 잘못된 요청 |

`scores`는 `[P1 점수, P2 점수]` 형태의 리스트다.

## 4. 경기 진행 (서버 상태)

```
            QUESTION 전송
                 │
                 ▼
   ┌──────────  OPEN  ──────────┐
   │ 첫 번째 BUZZ                │ 버저 시간 초과
   ▼                             ▼
ANSWERING ── 정답 ──────────▶ REVEAL ── reveal_time 후 ──▶ 다음 문제 (없으면 END)
   │                             ▲
   │ 오답 / 답변 시간 초과        │
   ├─ 상대가 아직 기회 안 씀 → 상대에게 ANSWERING (TURN_PASSED)
   └─ 둘 다 기회 씀 ─────────────┘
```

## 5. 동시 버저 처리

- 서버의 경기 상태(`phase`, `answerer`, 점수 등)는 하나의 `threading.Lock` 안에서만 바뀐다.
- 두 참가자의 수신 스레드가 거의 동시에 BUZZ를 넘겨도, lock을 먼저 잡은 쪽만 `phase == OPEN`을 보고
  답변권을 받는다. 그 순간 `phase`가 `ANSWERING`으로 바뀌므로 다른 쪽은 `BUZZ_REJECTED`를 받는다.
- 판정 기준은 "클라이언트가 누른 시각"이 아니라 **서버가 먼저 처리한 요청**이다 (제안서 규칙과 동일).
- 타이머 콜백도 같은 lock을 잡고, 토큰 번호로 취소된 옛날 타이머를 무시한다.

## 6. 오답 노트 기록 규칙

- 답변권을 가진 상태에서 틀리거나 시간 초과가 나면 해당 참가자의 오답으로 기록한다.
- 버저를 누르지 않은 문제는 오답으로 기록하지 않는다.
