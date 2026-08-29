#!/bin/sh
# 컨테이너 진입점 — 하나의 이미지로 두 가지를 띄운다.
#
#   APP_MODE=streamlit (기본)  대고객 UI. Streamlit. 클라우드 배포는 이쪽이다.
#   APP_MODE=api               JSON API + /health + /metrics (표준 라이브러리 서버)
#
# PORT 는 Cloud Run / App Runner / Container Apps 가 런타임에 주입하는 값이다.
# 주입되지 않으면 8080 을 쓴다.
set -e
PORT="${PORT:-8080}"
export PORT

case "${APP_MODE:-streamlit}" in
  api)
    echo "[entrypoint] API 모드 :${PORT}"
    exec python app.py   # app.py 가 PORT 환경변수를 직접 읽는다
    ;;
  streamlit)
    echo "[entrypoint] Streamlit 모드 :${PORT}"
    exec streamlit run streamlit_app.py \
      --server.port="${PORT}" \
      --server.address=0.0.0.0 \
      --server.headless=true \
      --browser.gatherUsageStats=false
    ;;
  *)
    echo "[entrypoint] 알 수 없는 APP_MODE='${APP_MODE}' (streamlit|api)" >&2
    exit 64
    ;;
esac
