"""④ 답변 생성 — 검색 근거 안에서만 생성하고 출처를 함께 표기.

요구사항: 답변 정합성과 근거 제시. 근거가 없으면 모른다고 답한다(I don't know).
사용자 수준(초보/숙련)에 따라 서술 방식을 바꾼다.
"""
from __future__ import annotations

from .. import llm
from ..state import AgentState

REFUSAL = (
    "죄송합니다. 해당 내용은 제가 안내해 드릴 수 있는 범위를 벗어납니다.\n\n"
    "저는 ETF 상품 정보와 거래 절차 안내를 도와드리는 챗봇으로, "
    "특정 종목 추천이나 투자 판단, 수익률 전망은 제공하지 않습니다.\n"
    "투자 판단이 필요하신 경우 영업점 또는 투자권유자문인력과 상담해 주세요."
)

NO_EVIDENCE = (
    "죄송합니다. 문의하신 내용은 제가 확인할 수 있는 자료에서 찾지 못했습니다.\n"
    "질문을 조금 더 구체적으로 남겨주시거나, 영업시간 중 고객센터로 문의해 주세요."
)

SYSTEM = """당신은 자산운용사의 ETF 안내 챗봇입니다. 아래 규칙을 반드시 지키세요.

1. 제공된 [근거 자료]에 있는 내용만으로 답변합니다. 자료에 없는 사실을 지어내지 마세요.
2. 특정 종목 추천, 매수/매도 권유, 수익률 전망이나 보장 표현은 절대 사용하지 마세요.
3. "투자하세요", "유망합니다", "수익이 기대됩니다" 같은 표현을 금지합니다.
4. 사용자 수준이 beginner면 용어를 풀어서, expert면 수치와 근거를 먼저 제시하세요.
5. 한국어 존댓말로 3~6문장 이내로 간결하게 답변하세요.
6. 근거가 부족하면 모른다고 답하세요."""


def _summarize_record(rec: str) -> str:
    """정형 DB 레코드를 한 문장으로 서술한다."""
    lines = [l.strip("- ").strip() for l in rec.splitlines()]
    name = lines[0].strip("[]")
    fields = {}
    for l in lines[1:]:
        if ":" in l:
            k, v = l.split(":", 1)
            fields[k.strip()] = v.strip()
    bits = []
    if "기초지수" in fields:
        bits.append(f"기초지수는 {fields['기초지수']}")
    if "총보수" in fields:
        bits.append(f"총보수는 {fields['총보수']}")
    if "순자산" in fields:
        bits.append(f"순자산은 {fields['순자산']}")
    if "분배금 주기" in fields:
        bits.append(f"분배금은 {fields['분배금 주기']} 지급")
    head = (f"{name}의 " + ", ".join(bits) + "입니다.") if bits else f"{name} 정보입니다."
    if "주요 구성종목" in fields:
        head += f" 주요 구성종목은 {fields['주요 구성종목']} 등입니다."
    return head


def _template_answer(state: AgentState) -> str:
    """rule 백엔드용 — 검색 근거를 자연문으로 요약(LLM 없이 동작)."""
    parts = []
    for rec in state.get("db_records", []):
        parts.append(_summarize_record(rec))
    for d in state.get("evidence", [])[:2]:
        parts.append(d["text"])
    if not parts:
        return NO_EVIDENCE
    body = " ".join(parts)
    if state.get("level") == "beginner":
        return f"{body} 추가로 궁금하신 점이 있으면 말씀해 주세요."
    return body


def run(state: AgentState) -> AgentState:
    trace = list(state.get("trace", []))

    if state.get("intent") == "out_of_scope":
        trace.append("④ 생성 · 범위 외 요청 → 정중한 거절문 사용")
        return {**state, "draft": REFUSAL, "trace": trace}

    if not state.get("evidence") and not state.get("db_records"):
        trace.append("④ 생성 · 근거 없음 → I don't know 응답")
        return {**state, "draft": NO_EVIDENCE, "trace": trace}

    retry_note = ""
    if state.get("regenerate_count", 0) > 0:
        retry_note = (
            "\n\n[재생성 지시] 직전 답변이 컴플라이언스 검증에서 반려되었습니다. "
            f"사유: {', '.join(state.get('violations', []))}. "
            "권유·전망·보장으로 읽힐 표현을 모두 제거하고 사실 안내만 하세요."
        )

    if llm.available():
        ctx = []
        for rec in state.get("db_records", []):
            ctx.append(f"[정형 DB]\n{rec}")
        for d in state.get("evidence", []):
            ctx.append(f"[{d['store']} · {d['source']}]\n{d['text']}")
        user = (
            f"사용자 수준: {state.get('level')}\n"
            f"질문: {state['normalized']}\n\n"
            f"[근거 자료]\n" + "\n\n".join(ctx) + retry_note
        )
        try:
            draft = llm.complete(SYSTEM, user, temperature=0.1)
            trace.append(f"④ 생성 · LLM · 근거 {len(ctx)}건")
            return {**state, "draft": draft, "trace": trace}
        except Exception as e:
            trace.append(f"④ 생성 · LLM 실패({type(e).__name__}) → 템플릿 폴백")

    draft = _template_answer(state)
    trace.append("④ 생성 · 템플릿(rule 백엔드)")
    return {**state, "draft": draft, "trace": trace}
