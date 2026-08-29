"""웹 서비스 진입점 — 표준 라이브러리만 사용(별도 설치 불필요).

    python app.py        → http://localhost:8000

엔드포인트
    GET  /            데모 UI (ui_template.render)
    GET  /health      헬스체크 — 컨테이너/클라우드 probe 용
    GET  /metrics     운영 지표(JSON)
    GET  /metrics/prometheus   Prometheus 노출 형식
    POST /ask         질의응답  {q} -> {answer, citations, trace, intent, request_id}
    POST /feedback    사용자 피드백 {request_id, useful}

모든 /ask 요청은 src.monitoring 에 지연·의도·차단 여부가 기록된다.
"""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src import monitoring
from src.config import Config
from src.graph import ask, backend_info
from ui_template import render

HOST = os.environ.get("HOST", "0.0.0.0")   # 컨테이너 외부에서 접근 가능해야 한다
PORT = int(os.environ.get("PORT", "8000"))


class Handler(BaseHTTPRequestHandler):
    server_version = "ETFAnswerAgent/1.0"

    # ── 공통 ──────────────────────────────────────────────
    def _send(self, code: int, body: str, ctype: str = "application/json; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    # ── GET ───────────────────────────────────────────────
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, render(backend_info()), "text/html; charset=utf-8")

        if self.path == "/health":
            # 그래프가 로드 가능한 상태인지까지 확인한다.
            return self._json(200, {
                "status": "ok",
                "backend": Config.BACKEND,
                "model": backend_info(),
                "uptime_sec": monitoring.snapshot().get("uptime_sec", 0),
            })

        if self.path == "/metrics":
            return self._json(200, monitoring.snapshot())

        if self.path == "/metrics/prometheus":
            return self._send(200, monitoring.prometheus_text(), "text/plain; charset=utf-8")

        if self.path == "/recent":
            return self._json(200, {"events": monitoring.recent(30)})

        return self._json(404, {"error": "not found"})

    # ── POST ──────────────────────────────────────────────
    def do_POST(self):
        if self.path == "/ask":
            q = (self._body().get("q") or "").strip()
            if not q:
                return self._json(400, {"error": "q is required"})

            t0 = time.perf_counter()
            try:
                r = ask(q)
            except Exception as e:  # 파이프라인 장애가 서비스를 죽이지 않게 한다
                monitoring.record({"question": q, "intent": "error", "verdict": "error",
                                   "latency_ms": (time.perf_counter() - t0) * 1000,
                                   "blocked": True, "error": type(e).__name__})
                return self._json(500, {"error": "internal", "detail": type(e).__name__})

            latency_ms = (time.perf_counter() - t0) * 1000
            request_id = monitoring.record({
                "question": q,
                "intent": r.get("intent"),
                "verdict": r.get("verdict"),
                "latency_ms": latency_ms,
                "regenerate_count": r.get("regenerate_count", 0),
                "n_evidence": len(r.get("evidence", [])) + len(r.get("db_records", [])),
                "store_used": r.get("store_used"),
                "blocked": r.get("intent") == "out_of_scope" or r.get("verdict") == "reject",
            })
            return self._json(200, {
                "answer": r.get("answer", ""),
                "citations": r.get("citations", []),
                "trace": r.get("trace", []),
                "intent": r.get("intent", ""),
                "latency_ms": round(latency_ms, 1),
                "request_id": request_id,
            })

        if self.path == "/feedback":
            b = self._body()
            rid, useful = b.get("request_id"), b.get("useful")
            if not rid or useful is None:
                return self._json(400, {"error": "request_id and useful are required"})
            monitoring.feedback(str(rid), bool(useful))
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass  # 접근 로그는 monitoring 이 담당한다


def main() -> None:
    print("=" * 66)
    print("  ETF Answer Agent")
    print(f"  백엔드 : {backend_info()}")
    print(f"  주소   : http://localhost:{PORT}   (bind {HOST}:{PORT})")
    print(f"  헬스   : http://localhost:{PORT}/health")
    print(f"  지표   : http://localhost:{PORT}/metrics")
    print("=" * 66)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
