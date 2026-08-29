# docs/ — GitHub Pages 데모

이 폴더는 **GitHub Pages 로 공개되는 브라우저 데모**입니다.
Python 백엔드나 API 키 없이, 방문자의 브라우저에서 에이전트가 그대로 실행됩니다.

공개 주소: <https://sunghyunc.github.io/etf-answer-agent/>
저장소: <https://github.com/SungHyunC/etf-answer-agent>

---

## 1. 구성 파일

| 파일 | 역할 |
|---|---|
| `index.html` | 채팅 UI. `data.json` 을 fetch 해 `window.ETFAgent.init(data)` 로 주입한 뒤, 질문마다 `ETFAgent.ask()` 를 호출해 **본문 · 근거 칩 · 5단계 처리 경로 배지**를 렌더링합니다. 외부 CDN 없이 인라인 CSS/JS 만 사용합니다. |
| `agent.js` | Python 파이프라인(`src/`)을 옮긴 브라우저용 구현. `window.ETFAgent` 를 노출합니다. |
| `data.json` | ETF 정형 데이터와 지식 문서 스냅샷. `tools/export_data.py` 가 `src/data/` 에서 생성합니다. |

`index.html` 은 답변마다 다음을 함께 보여 줍니다.

- **본문** — 검색 근거 범위 안에서 생성된 답변
- **근거 칩** — 인용한 정형 DB 레코드와 지식 문서 출처(`citations`)
- **처리 경로** — ① 발화 전처리 → ② 의도 분류 → ③ 기능별 검색 → ④ 답변 생성 → ⑤ 컴플라이언스 검증 (`trace`)
- **반려 표시** — ⑤에서 반려가 발생하면 적색으로 눈에 띄게 표기합니다.
  재생성 후 통과한 경우와 재생성 한도를 초과해 안전 응답으로 대체된 경우를 구분해 보여 줍니다.

예시 질문 칩은 일반 질문 4개와 **규제 차단 예시 2개**(적색)로 구성되어 있어,
투자 권유성 질문이 게이트에서 차단되는 동작을 클릭 한 번으로 확인할 수 있습니다.

---

## 2. 로컬에서 보는 법

`index.html` 을 파일 탐색기에서 더블클릭하면 **동작하지 않습니다.**
브라우저가 `file://` 에서의 `fetch('./data.json')` 을 보안 정책으로 막기 때문입니다.
(이 경우 페이지에 안내 메시지가 표시됩니다.)

저장소 **최상위 폴더**에서 로컬 서버를 띄우세요.

```bash
python -m http.server 8080
```

그런 다음 브라우저에서 접속합니다.

```
http://localhost:8080/docs/
```

중지는 터미널에서 `Ctrl + C` 입니다.

---

## 3. 데이터 재생성

`src/data/` 의 ETF 원장·지식 문서를 수정했다면, 저장소 **최상위 폴더**에서 아래를 실행해
`docs/data.json` 을 다시 만듭니다.

```bash
python -m tools.export_data
```

`data.json` 은 생성물이므로 직접 손으로 고치지 말고, `src/data/etf_db.py` 와
`src/data/knowledge.py` 를 수정한 뒤 위 명령을 다시 실행하세요.

---

## 4. GitHub Pages 배포 설정

저장소 **Settings → Pages** 에서 다음과 같이 지정합니다.

- **Source**: `Deploy from a branch`
- **Branch**: `main` / `/docs`

`main` 브랜치에 푸시하면 몇 분 안에 반영됩니다.
`docs/` 는 정적 파일만 있으므로 별도의 빌드 단계가 필요 없습니다.

---

## 5. Python 버전과의 관계

브라우저 데모는 API 키가 필요 없는 **`rule` 백엔드**와 동일한 경로로 동작합니다.
LLM 백엔드(`openai` / `local`)를 포함한 전체 파이프라인은 저장소 최상위에서 실행하세요.

```bash
python cli.py            # CLI 데모
python app.py            # 로컬 웹 데모 → http://localhost:8000
python -m src.evaluate   # 평가 하네스
```

---

> ⚠️ ETF 데이터는 데모용 샘플이며 실제 상품 정보가 아닙니다.
> 투자 판단의 근거로 사용할 수 없습니다.
