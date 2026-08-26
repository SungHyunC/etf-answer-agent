"""그래프 전역 상태."""
from __future__ import annotations

from typing import Any, Literal, TypedDict


Intent = Literal["etf_info", "faq", "disclosure", "general", "out_of_scope"]


class AgentState(TypedDict, total=False):
    # 입력
    question: str
    # ① 발화 전처리
    normalized: str
    corrections: list[tuple[str, str]]
    entities: list[str]
    level: Literal["beginner", "expert"]
    # ② 의도 분류
    intent: Intent
    intent_confidence: float
    intent_reason: str
    # ③ 기능별 검색
    store_used: str
    evidence: list[dict[str, Any]]
    db_records: list[str]
    # ④ 답변 생성
    draft: str
    # ⑤ 컴플라이언스 게이트
    verdict: Literal["pass", "reject"]
    violations: list[str]
    regenerate_count: int
    # 출력
    answer: str
    citations: list[str]
    trace: list[str]
