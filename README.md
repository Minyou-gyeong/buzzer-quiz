# TCP 소켓 기반 컴퓨터네트워크 기말 대비 2인 퀴즈 배틀

컴퓨터네트워크 Term Project. 두 참가자에게 같은 문제를 내고, 서버가 먼저 처리한 버저 요청에
답변권을 준다. 오답이거나 시간이 지나면 상대에게 기회가 넘어가고, 경기 후 점수와 오답 해설을 보여 준다.

- Python 3 표준 라이브러리만 사용 (`socket`, `threading`, `json`, GUI는 `tkinter` 예정)
- 메시지 형식과 경기 규칙: [docs/protocol.md](docs/protocol.md)

## 실행 방법

터미널 3개를 연다.

```bash
# 1) 서버
python server.py

# 2) 참가자 1
python client_cli.py --name 유경

# 3) 참가자 2
python client_cli.py --name 친구
```

다른 PC에서 접속할 때는 `--host <서버 PC의 IP>`를 붙인다. (Windows 방화벽에서 Python 허용 필요)

서버 옵션: `--port`, `--questions`, `--buzz-time`(기본 15초), `--answer-time`(기본 10초), `--reveal-time`(기본 3초)

CLI 클라이언트 입력: `b` 버저, `1`~`4` 답 제출, `q` 종료

## 테스트

```bash
# 동시 버저: 두 클라이언트가 같은 순간 BUZZ → 답변권이 한 명에게만 가는지 반복 확인
python server.py
python tests/buzz_race.py --trials 50

# 경기 규칙 시나리오 (제한 시간을 짧게 해서 서버 실행)
python server.py --buzz-time 1 --answer-time 1 --reveal-time 0.5
python tests/scenario_test.py
```

## 파일 구조

```
server.py           게임 서버 (연결 관리, 답변권, 타이머, 채점)
client_cli.py       터미널 테스트 클라이언트
protocol.py         메시지 송수신 (JSON Lines 프레이밍)
questions.json      문제은행 (answer는 1부터 시작하는 보기 번호)
tests/              동시 버저 / 시나리오 테스트
docs/protocol.md    메시지 형식, 상태 흐름
docs/devlog.md      작업 기록
```

## 진행 상황

- [x] 1주차: TCP 연결, 메시지 형식, 버저 판정, 제한 시간, 채점, 오답 노트 (CLI)
- [ ] 2주차: Tkinter GUI, 기말 범위 문제은행
- [ ] 3주차: 연결 끊김 등 예외 상황 점검, 주석, 최종 검증
