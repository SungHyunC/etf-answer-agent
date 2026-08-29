"""운영 모니터링 지표 수집기 — 3차 요구 '실시간 성능 평가 및 모델 모니터링을 위한 지표 설정'.

평가 하네스(evaluate.py)가 배포 전 오프라인 품질을 재는 도구라면, 이 모듈은 배포 후
실제 트래픽을 재는 도구다. 요청 1건을 이벤트로 남기고 운영 대시보드·알람이 바로 쓸 수
있는 형태로 집계한다. 외부 APM 없이 표준 라이브러리만 사용한다.

  기록 : 메모리 링버퍼(최근 1000건) + JSONL 파일(logs/events.jsonl) 이중 기록
  집계 : snapshot() — 처리량 · 차단율 · 재생성율 · 지연 분위수 · 의도 분포 · 근거 없음 비율
  노출 : prometheus_text() — Prometheus 스크레이프 형식 (운영 전환 시 /metrics 에 그대로 연결)
  품질 : feedback() — 상담원·고객의 좋아요/싫어요를 요청 ID 에 붙여 사후 추적

감시 대상 지표의 의미
  block_rate       규제 게이트 차단 비율. 급등하면 생성 품질 저하 또는 공격성 질의 유입 신호.
  regenerate_rate  재생성 발생 비율. 게이트 통과에 드는 비용이며 지연 상승의 선행 지표.
  no_evidence_rate 검색 근거 0건 비율. 환각 위험이 가장 높은 구간이라 별도로 본다.
  latency p50/p95  체감 응답 속도. p95 가 SLA 판단 기준이다.

[시그니처 안내]
record() 는 요구 명세의 `-> None` 대신 **생성한 request_id(str) 를 반환**한다.
feedback(request_id, useful) 로 사후 피드백을 붙이려면 호출 측이 ID 를 알아야 하기 때문이다.
반환값을 무시해도 동작에는 영향이 없다.

카운터(총 요청 수·차단 수 등)는 프로세스 시작 이후 누적값이고,
지연 분위수는 링버퍼에 남아 있는 최근 구간(최대 1000건) 기준이다.
"""
from __future__ import annotations

import json
import math
import pathlib
import threading
import time
import uuid
from collections import Counter, deque
from datetime import datetime
from typing import Any

__all__ = ["record", "snapshot", "recent", "reset", "feedback",
           "prometheus_text", "event_from_state", "LOG_PATH", "MAX_EVENTS"]

# 링버퍼 용량 — 메모리 사용을 상수로 묶어 두고, 장기 이력은 JSONL 로 남긴다.
MAX_EVENTS = 1000
LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "logs"
LOG_PATH = LOG_DIR / "events.jsonl"
# 질문 원문은 지표용이므로 앞부분만 남긴다(로그 비대화·개인정보 노출 최소화).
QUESTION_MAX_LEN = 300

METRIC_PREFIX = "etf_agent"

_LOCK = threading.RLock()

_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_intents: Counter[str] = Counter()
_counters: dict[str, int] = {}
_seq = 0
_started_at = ""
_started_mono = 0.0


def _init_state() -> None:
    """모듈 상태 초기화 — import 시점과 reset() 에서 함께 쓴다."""
    global _seq, _started_at, _started_mono
    _events.clear()
    _intents.clear()
    _counters.clear()
    _counters.update({
        "total": 0,            # 처리한 요청 수
        "blocked": 0,          # 게이트 차단(반려 확정) 수
        "regenerated": 0,      # 재생성이 1회 이상 발생한 요청 수
        "regenerate_sum": 0,   # 재생성 횟수 합계
        "no_evidence": 0,      # 검색 근거 0건 요청 수
        "feedback_total": 0,
        "feedback_useful": 0,
        "log_errors": 0,       # JSONL 기록 실패 횟수
    })
    _seq = 0
    _started_at = _now().isoformat(timespec="seconds")
    _started_mono = time.monotonic()


def _now() -> datetime:
    """로컬 타임존이 붙은 현재 시각."""
    return datetime.now().astimezone()


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    """지연 시간처럼 '없을 수도 있는' 수치 — 실패하면 None(집계에서 제외)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _percentile(values: list[float], q: float) -> float:
    """nearest-rank 백분위수. 표본이 1건뿐인 운영 초기에도 예외 없이 동작한다."""
    if not values:
        return 0.0
    xs = sorted(values)
    k = max(1, math.ceil(q * len(xs)))
    return xs[min(k, len(xs)) - 1]


def _ratio(numer: int, denom: int) -> float:
    return round(numer / denom, 4) if denom else 0.0


def _append_jsonl(record_obj: dict[str, Any]) -> None:
    """JSONL 추가 기록. 디스크 문제로 서비스가 죽으면 안 되므로 모든 예외를 삼킨다."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record_obj, ensure_ascii=False, default=str) + "\n")
    except Exception:  # 권한·디스크·경로 문제 모두 포함
        _counters["log_errors"] = _counters.get("log_errors", 0) + 1


_KNOWN_KEYS = {"question", "intent", "verdict", "blocked", "latency_ms",
               "regenerate_count", "n_evidence", "store_used"}


def record(event: dict) -> str:
    """요청 1건을 기록하고 request_id 를 돌려준다(명세의 -> None 에서 확장).

    event 예:
      {"question": ..., "intent": ..., "verdict": ..., "latency_ms": ...,
       "regenerate_count": ..., "n_evidence": ..., "store_used": ..., "blocked": bool}

    누락된 키는 안전한 기본값으로 채우므로 부분 이벤트도 그대로 받는다.
    blocked 를 주지 않으면 verdict == "reject" 로 판정한다.
    타임스탬프와 ID 는 내부에서 붙인다.
    """
    global _seq
    ev = dict(event or {})
    verdict = str(ev.get("verdict") or "")
    blocked = bool(ev["blocked"]) if "blocked" in ev else (verdict == "reject")
    regen = _as_int(ev.get("regenerate_count"))
    n_evidence = _as_int(ev.get("n_evidence"))

    with _LOCK:
        _seq += 1
        rid = f"req-{_seq:05d}-{uuid.uuid4().hex[:6]}"
        row: dict[str, Any] = {
            "request_id": rid,
            "ts": _now().isoformat(timespec="seconds"),
            "question": str(ev.get("question") or "")[:QUESTION_MAX_LEN],
            "intent": str(ev.get("intent") or "unknown"),
            "verdict": verdict,
            "blocked": blocked,
            "latency_ms": _as_float(ev.get("latency_ms")),
            "regenerate_count": regen,
            "n_evidence": n_evidence,
            "store_used": str(ev.get("store_used") or ""),
            "feedback": None,
        }
        extra = {k: v for k, v in ev.items() if k not in _KNOWN_KEYS}
        if extra:
            row["extra"] = extra

        _events.append(row)
        _intents[row["intent"]] += 1
        _counters["total"] += 1
        _counters["blocked"] += 1 if blocked else 0
        _counters["regenerated"] += 1 if regen > 0 else 0
        _counters["regenerate_sum"] += regen
        _counters["no_evidence"] += 1 if n_evidence == 0 else 0

        _append_jsonl({"type": "request", **row})
    return rid


def feedback(request_id: str, useful: bool) -> None:
    """사용자 피드백(좋아요/싫어요) 수집.

    링버퍼에 남아 있는 요청이면 해당 이벤트에도 표시한다. 이미 밀려난(또는 다른 프로세스의)
    ID 라도 집계에는 반영하고 JSONL 에는 남긴다 — 사후에 request_id 로 조인할 수 있다.
    """
    rid = str(request_id or "")
    flag = bool(useful)
    with _LOCK:
        _counters["feedback_total"] += 1
        _counters["feedback_useful"] += 1 if flag else 0
        for row in reversed(_events):
            if row.get("request_id") == rid:
                row["feedback"] = flag
                break
        _append_jsonl({"type": "feedback", "request_id": rid, "useful": flag,
                       "ts": _now().isoformat(timespec="seconds")})


def snapshot() -> dict:
    """현재까지의 집계 지표. 대시보드·헬스체크가 그대로 JSON 으로 내보낼 수 있는 형태."""
    with _LOCK:
        total = _counters["total"]
        lats = [row["latency_ms"] for row in _events if row["latency_ms"] is not None]
        return {
            "started_at": _started_at,
            "uptime_sec": round(time.monotonic() - _started_mono, 1),
            "total_requests": total,
            "blocked_count": _counters["blocked"],
            "block_rate": _ratio(_counters["blocked"], total),
            "regenerate_rate": _ratio(_counters["regenerated"], total),
            "regenerate_avg": round(_counters["regenerate_sum"] / total, 3) if total else 0.0,
            "no_evidence_count": _counters["no_evidence"],
            "no_evidence_rate": _ratio(_counters["no_evidence"], total),
            "latency_p50_ms": round(_percentile(lats, 0.50), 1),
            "latency_p95_ms": round(_percentile(lats, 0.95), 1),
            "latency_avg_ms": round(sum(lats) / len(lats), 1) if lats else 0.0,
            "latency_samples": len(lats),          # 지연 지표는 링버퍼 구간 기준
            "intent_distribution": dict(_intents.most_common()),
            "feedback_total": _counters["feedback_total"],
            "feedback_useful": _counters["feedback_useful"],
            "useful_rate": _ratio(_counters["feedback_useful"], _counters["feedback_total"]),
            "buffered_events": len(_events),
            "log_path": str(LOG_PATH),
            "log_write_errors": _counters["log_errors"],
        }


def recent(n: int = 50) -> list[dict]:
    """최근 이벤트 n건(최신순). 장애 조사 시 바로 눈으로 확인하는 용도."""
    with _LOCK:
        if n <= 0:
            return []
        rows = list(_events)[-n:]
        return [dict(row) for row in reversed(rows)]


def reset() -> None:
    """메모리 지표 초기화. JSONL 파일은 감사 추적을 위해 지우지 않는다."""
    with _LOCK:
        _init_state()


def _esc(value: str) -> str:
    """Prometheus 라벨 값 이스케이프."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def prometheus_text() -> str:
    """Prometheus 노출 형식 텍스트. HTTP /metrics 응답 본문으로 그대로 쓴다."""
    s = snapshot()
    p = METRIC_PREFIX
    lines: list[str] = []

    def emit(name: str, kind: str, help_text: str, value: Any, labels: str = "") -> None:
        lines.append(f"# HELP {p}_{name} {help_text}")
        lines.append(f"# TYPE {p}_{name} {kind}")
        lines.append(f"{p}_{name}{labels} {value}")

    emit("requests_total", "counter", "처리한 요청 수", s["total_requests"])
    emit("blocked_total", "counter", "컴플라이언스 게이트가 차단한 요청 수", s["blocked_count"])
    emit("block_rate", "gauge", "차단율", s["block_rate"])
    emit("regenerate_rate", "gauge", "재생성 발생 비율", s["regenerate_rate"])
    emit("regenerate_avg", "gauge", "요청당 평균 재생성 횟수", s["regenerate_avg"])
    emit("no_evidence_rate", "gauge", "검색 근거 0건 비율", s["no_evidence_rate"])
    emit("uptime_seconds", "gauge", "수집기 가동 시간(초)", s["uptime_sec"])
    emit("feedback_total", "counter", "수집한 사용자 피드백 수", s["feedback_total"])
    emit("feedback_useful_rate", "gauge", "'도움이 됨' 응답 비율", s["useful_rate"])
    emit("log_write_errors_total", "counter", "이벤트 파일 기록 실패 횟수", s["log_write_errors"])

    # 지연 시간 — 링버퍼 구간의 분위수/평균
    lines.append(f"# HELP {p}_latency_ms 응답 지연(ms, 최근 구간)")
    lines.append(f"# TYPE {p}_latency_ms gauge")
    lines.append(f'{p}_latency_ms{{quantile="0.5"}} {s["latency_p50_ms"]}')
    lines.append(f'{p}_latency_ms{{quantile="0.95"}} {s["latency_p95_ms"]}')
    lines.append(f'{p}_latency_ms{{quantile="avg"}} {s["latency_avg_ms"]}')

    # 의도 분포
    lines.append(f"# HELP {p}_intent_total 의도별 요청 수")
    lines.append(f"# TYPE {p}_intent_total counter")
    for intent, count in s["intent_distribution"].items():
        lines.append(f'{p}_intent_total{{intent="{_esc(str(intent))}"}} {count}')

    return "\n".join(lines) + "\n"


def event_from_state(state: dict, latency_ms: float | None = None) -> dict:
    """AgentState 를 record() 이벤트로 변환하는 보조 함수.

    호출 측이 상태 키 이름을 다시 옮겨 적지 않도록 한 곳에 모아 둔다.
    """
    return {
        "question": state.get("question", ""),
        "intent": state.get("intent", ""),
        "verdict": state.get("verdict", ""),
        "latency_ms": latency_ms,
        "regenerate_count": state.get("regenerate_count", 0),
        "n_evidence": len(state.get("evidence", []) or []) + len(state.get("db_records", []) or []),
        "store_used": state.get("store_used", ""),
        "blocked": state.get("verdict") == "reject",
    }


_init_state()
