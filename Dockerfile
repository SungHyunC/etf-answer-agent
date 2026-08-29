# ETF Answer Agent — 단일 서비스 이미지.
# 빌드 산출물이 없으므로 멀티스테이지를 쓰지 않고 단일 스테이지로 유지한다.
# 진입점 하나로 두 모드를 띄운다 — APP_MODE=streamlit(기본, 대고객 UI) / api(JSON+지표).
FROM python:3.11-slim

# PYTHONDONTWRITEBYTECODE : 읽기 전용 파일시스템에서도 돌도록 .pyc 를 만들지 않는다
# PYTHONUNBUFFERED        : 컨테이너 로그가 즉시 stdout 으로 흘러나오게 한다
# LLM_BACKEND=rule        : 키 없이 그대로 뜨는 기본 백엔드 (운영에서는 local 로 교체)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LLM_BACKEND=rule

WORKDIR /app

# 의존성 목록을 먼저 복사한다. 소스만 바뀐 빌드에서는 이 레이어가 캐시에 걸려
# pip install 을 건너뛴다.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 소스 (제외 대상은 .dockerignore 참고)
COPY . .

# 비root 실행 — 컨테이너가 뚫렸을 때 피해 범위를 줄인다.
# logs 는 볼륨 마운트 지점이라 미리 만들어 소유권을 넘겨둔다.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/logs \
 && chmod +x /app/docker-entrypoint.sh \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# slim 이미지에는 curl 이 없으므로 표준 라이브러리 urllib 로 확인한다.
# urlopen 은 200 이 아니면 예외를 던지므로 종료 코드가 그대로 판정이 된다.
# 헬스 경로는 모드마다 다르다 — Streamlit 은 /_stcore/health, API 는 /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import os,urllib.request as u; p=os.environ.get('PORT','8080'); path='/health' if os.environ.get('APP_MODE')=='api' else '/_stcore/health'; u.urlopen(f'http://127.0.0.1:{p}{path}', timeout=3)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
