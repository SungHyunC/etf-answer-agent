# 배포 가이드

ETF Answer Agent 를 로컬 · 컨테이너 · 클라우드 · 온프레미스(폐쇄망)에 올리는 절차입니다.

> **검증 범위에 대한 고지**
> 이 문서를 작성한 개발 환경에는 Docker 가 설치되어 있지 않아 **로컬에서 `docker build` 를
> 실행해 이미지 빌드를 검증하지 못했습니다.** 대신 GitHub Actions 의 `docker` job 에서
> 매 푸시마다 실제로 이미지를 빌드해 Dockerfile 이 깨지지 않았는지 확인합니다.
> 아래 Docker / compose 절차는 **CI 의 빌드 검증까지만 확인된 상태**이며,
> 컨테이너를 띄워 요청을 받아보는 런타임 검증은 아직 수행하지 않았습니다.

---

## 0. 컨테이너로 띄우기 전 선행 조건 (미해결)

현재 `app.py` 는 다음 두 가지 때문에 컨테이너에서 그대로는 서비스되지 않습니다.
이미지·compose·헬스체크는 아래가 해결된 상태를 전제로 작성했습니다.

| 항목 | 현재 코드 | 필요한 상태 | 이유 |
|---|---|---|---|
| 바인드 주소 | `HTTPServer(("127.0.0.1", 8000), ...)` | `0.0.0.0` (또는 `HOST` 환경변수) | 컨테이너 내부 루프백만 열려 있으면 `-p 8000:8000` 으로 매핑해도 외부에서 닿지 않습니다. |
| `/health` 엔드포인트 | 없음 (`/`, `/index.html` 외 404) | `GET /health` → 200 | Dockerfile · compose · 클라우드 로드밸런서의 헬스체크가 모두 이 경로를 봅니다. |

`app.py` 는 이 문서의 담당 범위 밖이라 수정하지 않았습니다. 위 두 가지가 반영되기 전까지
컨테이너는 기동은 되지만 `unhealthy` 로 남고 외부 요청을 받지 못합니다.

---

## 1. 로컬 실행 (컨테이너 없이)

```bash
pip install -r requirements.txt

python app.py                    # 웹 데모 → http://localhost:8000
python cli.py                    # CLI 데모
python -m src.evaluate           # 평가 하네스
python tests/test_pipeline.py    # 단위 검증
```

기본 백엔드가 `rule` 이라 **API 키 없이 그대로 돕니다.**

---

## 2. Docker 실행

```bash
docker build -t etf-answer-agent:local .
docker run -d --name etf-agent -p 8000:8000 \
  -e LLM_BACKEND=rule \
  etf-answer-agent:local

docker ps                        # STATUS 의 (healthy) 확인
docker logs -f etf-agent
```

이미지 특징

- `python:3.11-slim` 기반 단일 스테이지 (빌드 산출물이 없어 멀티스테이지가 불필요)
- `requirements.txt` 를 먼저 복사해 **의존성 레이어를 캐시** — 소스만 고치면 재설치하지 않음
- **비root(`appuser`, uid 10001)** 로 실행
- `HEALTHCHECK` 는 slim 이미지에 curl 이 없으므로 표준 라이브러리 `urllib` 로 `/health` 확인
- `tests/` 는 이미지에 포함합니다 — `src/evaluate.py` 가 런타임에 `tests/eval_set.json` 을
  읽으므로, 배포된 컨테이너에서 그대로 평가를 돌릴 수 있습니다.

```bash
# 배포된 컨테이너 안에서 목표 지표 재측정 (8절 롤백 판단 기준 2번)
docker exec etf-agent python -m src.evaluate
```

---

## 3. docker compose 실행

```bash
cp .env.example .env             # (선택) 백엔드를 바꿀 때만
docker compose up -d --build
docker compose ps                # healthy 여부
docker compose logs -f
docker compose down
```

- `.env` 는 **선택**입니다. 없으면 `LLM_BACKEND=rule` 로 뜹니다.
  (`env_file` 의 `required: false` 는 Compose **v2.24 이상**이 필요합니다. 구버전이면
  해당 블록을 `env_file: [.env]` 로 바꾸고 `.env` 를 반드시 만들어야 합니다.)
- `./logs` 를 `/app/logs` 에 마운트합니다. `src/monitoring.py` 가 이 경로에 실제로 파일을
  쓰므로, **바인드 마운트 후에는 호스트 디렉터리 소유권을 컨테이너 사용자에 맞춰야 합니다.**
  이미지 안에서 `chown` 해둔 것은 마운트가 덮어씁니다.

  ```bash
  mkdir -p logs && sudo chown 10001:10001 logs
  ```
- `restart: unless-stopped` — 호스트 재부팅 후 자동 기동, 수동 `stop` 은 존중합니다.

---

## 4. 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_BACKEND` | `rule` | `rule` / `openai` / `local`. `rule` 은 LLM 없이 규칙 기반 — 키 불필요 |
| `OPENAI_API_KEY` | (없음) | `LLM_BACKEND=openai` 일 때 필수 |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI 모델명 |
| `LOCAL_BASE_URL` | `http://localhost:11434/v1` | `LLM_BACKEND=local` 일 때 OpenAI 호환 엔드포인트 |
| `LOCAL_MODEL` | `qwen2.5:7b-instruct` | 사내 모델명 |
| `LOCAL_API_KEY` | `not-needed` | 엔드포인트가 키를 요구하지 않으면 그대로 둡니다 |

키는 이미지에 굽지 않습니다. `.dockerignore` 가 `.env` 를 빌드 컨텍스트에서 제외하며,
값은 런타임에 `-e` / `env_file` / 시크릿 매니저로 주입합니다.

---

## 5. 클라우드 배포

### AWS ECS (Fargate)

```bash
# 1) ECR 로그인 · 리포지터리 준비
aws ecr get-login-password --region ap-northeast-2 \
  | docker login --username AWS --password-stdin <ACCOUNT>.dkr.ecr.ap-northeast-2.amazonaws.com
aws ecr create-repository --repository-name etf-answer-agent --region ap-northeast-2

# 2) 태깅 · 푸시
docker build -t etf-answer-agent:$(git rev-parse --short HEAD) .
docker tag etf-answer-agent:$(git rev-parse --short HEAD) \
  <ACCOUNT>.dkr.ecr.ap-northeast-2.amazonaws.com/etf-answer-agent:$(git rev-parse --short HEAD)
docker push <ACCOUNT>.dkr.ecr.ap-northeast-2.amazonaws.com/etf-answer-agent:<TAG>

# 3) 태스크 정의 갱신 후 서비스 롤링 배포
aws ecs update-service --cluster etf --service etf-agent \
  --task-definition etf-agent:<REV> --region ap-northeast-2
```

- 태스크 정의: 컨테이너 포트 `8000`, ALB 타깃 그룹 헬스체크 경로 `/health`
- 비밀값은 태스크 정의의 `secrets` 로 Secrets Manager / SSM Parameter Store 에서 주입
- 최소 사양 기준점: 0.5 vCPU / 1GB (scikit-learn·numpy 로딩 여유). **실측값이 아니라 추정치입니다.**

### Google Cloud Run

```bash
gcloud builds submit --tag gcr.io/<PROJECT>/etf-answer-agent
gcloud run deploy etf-agent \
  --image gcr.io/<PROJECT>/etf-answer-agent \
  --port 8000 \
  --set-env-vars LLM_BACKEND=rule \
  --region asia-northeast3
```

- Cloud Run 은 `PORT` 환경변수로 포트를 지정하므로, 포트를 8000 으로 고정하려면
  `--port 8000` 을 명시하거나 `app.py` 가 `PORT` 를 읽도록 해야 합니다.
- Cloud Run 은 컨테이너의 `HEALTHCHECK` 지시어를 쓰지 않고 자체 startup/liveness probe 를 씁니다.

---

## 6. 온프레미스 · 폐쇄망 배포

자산운용사 내부망은 인터넷이 막혀 있다고 가정합니다.

**이미지 반입**

```bash
# 반출 가능한 망에서 빌드 후 tar 로 저장
docker build -t etf-answer-agent:1.0.0 .
docker save etf-answer-agent:1.0.0 | gzip > etf-answer-agent-1.0.0.tar.gz

# 매체 반입 후 내부망에서 적재 → 사내 레지스트리로 푸시
gunzip -c etf-answer-agent-1.0.0.tar.gz | docker load
docker tag etf-answer-agent:1.0.0 registry.internal/etf/etf-answer-agent:1.0.0
docker push registry.internal/etf/etf-answer-agent:1.0.0
```

- 베이스 이미지 `python:3.11-slim` 도 함께 반입하거나 사내 미러에 올려두어야
  내부망에서 재빌드가 가능합니다.
- `pip install` 이 인터넷을 타므로, 내부망 재빌드가 필요하면 사내 PyPI 미러
  (Nexus / Artifactory)를 `PIP_INDEX_URL` 로 지정합니다.

**로컬 LLM 연결**

```bash
docker run -d --name etf-agent -p 8000:8000 \
  -e LLM_BACKEND=local \
  -e LOCAL_BASE_URL=http://llm.internal:8000/v1 \
  -e LOCAL_MODEL=qwen2.5-7b-instruct \
  -e LOCAL_API_KEY=not-needed \
  registry.internal/etf/etf-answer-agent:1.0.0
```

`openai` 와 `local` 은 동일한 OpenAI 호환 인터페이스를 쓰므로, 사내 vLLM / Ollama 로
전환할 때 **`LOCAL_BASE_URL` 과 `LOCAL_MODEL` 두 값만 바꾸면 됩니다.**
LLM 엔드포인트에 닿지 못하면 `src/llm.py` 가 `LLMUnavailable` 을 던지고 규칙 기반으로
폴백하므로, LLM 장애가 서비스 중단으로 번지지 않습니다.

**그 외 고려사항**

- 컨테이너에서 나가는 통신은 사내 LLM 엔드포인트 하나뿐입니다. 이 외 아웃바운드는 차단해도 됩니다.
- 답변·근거·처리 경로 로그는 `/app/logs` 볼륨으로 남겨 감사 추적에 사용합니다.
- 이미지 태그는 `latest` 대신 **버전 또는 커밋 해시**로 고정해야 롤백이 가능합니다.

---

## 7. 헬스체크

| 위치 | 판정 방식 |
|---|---|
| Dockerfile `HEALTHCHECK` | 30초 간격, 20초 유예 후 `GET /health` 200 아니면 3회 만에 `unhealthy` |
| docker compose | 동일 조건을 `healthcheck:` 에 명시 |
| ALB / Cloud Run | 타깃 그룹 · probe 경로를 `/health` 로 설정 |

```bash
curl -f http://localhost:8000/health     # 200 이면 정상
docker inspect --format '{{.State.Health.Status}}' etf-agent
```

`unhealthy` 라면 위 **0절의 선행 조건**부터 확인하십시오.

---

## 8. 롤백

```bash
# compose — 직전 태그로 되돌린 뒤 재기동
docker compose down
docker tag etf-answer-agent:1.0.0 etf-answer-agent:local
docker compose up -d

# ECS — 이전 태스크 정의 리비전으로 서비스 갱신
aws ecs update-service --cluster etf --service etf-agent --task-definition etf-agent:<이전REV>

# Cloud Run — 이전 리비전으로 트래픽 100% 전환
gcloud run services update-traffic etf-agent --to-revisions <이전리비전>=100
```

롤백 판단 기준

1. 헬스체크가 유예 시간(20초) 내에 통과하지 못한다
2. `python -m src.evaluate` 의 **규제 차단률이 100% 미만**이거나 **미검증 답변 노출이 1건 이상**
3. 응답에 컴플라이언스 위반 문구가 섞여 나온다

2번은 배포 전 CI 에서 이미 걸러지지만, 데이터·모델 교체 후에는 배포된 환경에서 다시 돌려
확인하는 것을 권장합니다.

---

## 9. CI/CD

`.github/workflows/ci.yml` — `push` / `pull_request` 마다 두 job 이 병렬로 돕니다.

| job | 내용 |
|---|---|
| `test` | Python 3.11 셋업(pip 캐시) → 의존성 설치 → `python tests/test_pipeline.py` → `python -m src.evaluate` |
| `docker` | Buildx 로 이미지 빌드 검증. **푸시하지 않으며** 레이어 캐시는 GitHub Actions 캐시(`type=gha`)에 저장 |

두 명령 모두 종료 코드가 0이 아니면 CI 가 실패합니다. 개발 환경에서 마지막으로 측정한 값은
`RESULTS.txt` 와 `README.md` 를 참고하십시오.

배포까지 자동화하려면 `docker` job 뒤에 `main` 브랜치 한정으로 레지스트리 로그인 →
`push: true` → 배포 명령을 붙이면 됩니다. 레지스트리 자격증명이 필요해 이번 범위에서는
빌드 검증까지만 구성했습니다.
