"""LangGraph 조립 — 5단계 파이프라인 + 컴플라이언스 반려 루프.

  ① preprocess → ② classify → ③ retrieve → ④ generate → ⑤ compliance
                                                 ↑                │
                                                 └── reject ──────┘
                                                        pass → END

반려 루프가 이 그래프의 핵심이다. 검증을 통과하지 못한 답변은 고객에게 나가지 않고
④로 되돌아가 재생성되며, 한도(MAX_REGENERATE) 초과 시 안전 문구로 대체된다.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from .config import Config
from .nodes import classify, compliance, generate, preprocess, retrieve
from .state import AgentState


def _route_after_compliance(state: AgentState) -> str:
    if state.get("verdict") == "pass":
        return "end"
    if state.get("answer"):          # 한도 초과 → 안전 응답 확정
        return "end"
    return "regenerate"


def build():
    g = StateGraph(AgentState)
    g.add_node("preprocess", preprocess.run)
    g.add_node("classify", classify.run)
    g.add_node("retrieve", retrieve.run)
    g.add_node("generate", generate.run)
    g.add_node("compliance", compliance.run)

    g.set_entry_point("preprocess")
    g.add_edge("preprocess", "classify")
    g.add_edge("classify", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "compliance")
    g.add_conditional_edges(
        "compliance",
        _route_after_compliance,
        {"regenerate": "generate", "end": END},
    )
    return g.compile()


_APP = None


def ask(question: str) -> AgentState:
    """단일 질문 실행."""
    global _APP
    if _APP is None:
        _APP = build()
    init: AgentState = {"question": question, "trace": [], "regenerate_count": 0}
    return _APP.invoke(init)


def backend_info() -> str:
    return Config.describe()
