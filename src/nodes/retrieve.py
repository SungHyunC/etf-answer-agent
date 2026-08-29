"""③ 기능별 검색 — 의도에 따라 담당 창고만 조회한다.

설계 원칙: 단일 창고는 문서가 늘수록 검색 품질이 떨어지므로 업무별로 분리하고
의도 분류 결과로 라우팅한다.
"""
from __future__ import annotations

from ..config import Config
from ..data import etf_db, vectorstore
from ..state import AgentState

# 의도 → 조회할 창고 (앞이 주 창고)
# 앞이 주 창고, 뒤는 보조. dev 측정에서 etf_info 와 disclosure 가 서로 혼동되는 사례가 많아
# (예: "구성종목이 최근에 바뀐 게 있나요" → 정기변경 공시가 정답인데 상품 스펙으로 라우팅)
# 두 의도가 서로의 창고를 보조로 조회하도록 넓혔다. 의도가 흔들려도 근거는 잡힌다.
ROUTE: dict[str, list[str]] = {
    "etf_info": ["product", "disclosure"],
    "disclosure": ["disclosure", "product"],
    "faq": ["faq", "product"],
    "general": ["product", "faq"],
    "out_of_scope": [],
}


def run(state: AgentState) -> AgentState:
    intent = state.get("intent", "general")
    query = state["normalized"]
    entities = state.get("entities", [])

    db_records: list[str] = []
    evidence: list[dict] = []

    # 정형 DB 조회 (엔티티가 식별된 경우)
    if intent in ("etf_info", "disclosure", "faq", "general") and entities:
        db_records = [etf_db.format_record(e) for e in entities]

    stores = ROUTE.get(intent, [])
    for s in stores:
        hits = vectorstore.search(s, query, k=3, min_score=Config.MIN_SIMILARITY)
        evidence.extend(hits)
        if len(evidence) >= 5:
            break
    evidence = sorted(evidence, key=lambda d: d["score"], reverse=True)[:5]

    used = ", ".join(vectorstore.get(s).label for s in stores) if stores else "조회 안 함"
    trace = list(state.get("trace", []))
    trace.append(
        f"③ 검색 · 창고[{used}] · 문서 {len(evidence)}건 · DB레코드 {len(db_records)}건"
    )
    return {**state, "store_used": used, "evidence": evidence,
            "db_records": db_records, "trace": trace}
