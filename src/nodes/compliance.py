"""⑤ 컴플라이언스 검증 게이트 — 1차 기획의 핵심 안전장치.

요구 기능 '오류 대응': 최종발화 적정성 검증 및 컴플라이언스 준수 검증.

자본시장법상 투자권유로 해석될 수 있는 표현이 있으면 답변을 반려하고 재생성시킨다.
게이트를 통과하지 못한 답변은 고객에게 나가지 않는다.
"""
from __future__ import annotations

import re

from ..config import Config
from ..state import AgentState

# (규칙 ID, 정규식, 사유)
RULES: list[tuple[str, str, str]] = [
    ("C-01", r"(추천\s*(합니다|해\s*드립|드립니다|드려요))", "종목 추천 표현"),
    # 부정 lookahead 로 법정 직함·기관명을 패턴 단계에서 배제한다.
    # (화이트리스트를 계속 늘리는 대신 규칙 자체를 좁히는 방향 — 3차 피드백 반영)
    ("C-02", r"(매수|매도|투자)\s*(하세요|하시길|추천|권해|권장|권유(?!\s*(자문인력|대행인|준칙|규정|규제)))", "매매 권유 표현"),
    ("C-03", r"(수익(률)?|이익|원금)\s*(이|을|은)?\s*(보장|확실|무조건)", "수익 보장 표현"),
    ("C-04", r"(오를\s*것|상승할\s*것|하락할\s*것|떨어질\s*것)\s*(입니다|이에요|예요|같습니다)", "가격 전망 단정"),
    ("C-05", r"(유망|전망이\s*밝|기대됩니다|좋은\s*기회|지금이\s*적기)", "투자 유인 표현"),
    ("C-06", r"(사시면|사시는\s*것이|담으시면|비중을\s*늘리)", "매수 유도 표현"),
    ("C-07", r"(반드시|틀림없이|100%)\s*(수익|오릅|이익)", "확정적 단정"),
]

# 근거 없는 수치 주장 탐지 (환각 방어)
NUM_RE = re.compile(r"\d[\d,]*\.?\d*\s*(?:%|원|억|조|배)")

# 규제 문맥에서 정상적으로 쓰이는 법정 용어 — 검사 전 마스킹해 오탐을 막는다.
# (예: '투자권유자문인력' 은 자본시장법상 직함이며 권유 행위가 아니다)
# 2차 피드백 반영: 정규식(C-02)에 부정 lookahead 를 넣어 직함류는 패턴 단계에서 걸러진다.
# 아래 목록은 다른 규칙에 걸릴 여지를 남기지 않기 위한 2중 안전장치로만 유지한다.
SAFE_TERMS = ["투자권유자문인력", "투자권유대행인", "투자권유준칙"]

SAFE_FALLBACK = (
    "죄송합니다. 문의하신 내용에 대해 정확한 안내를 드리기 어렵습니다.\n"
    "정확한 상담을 위해 영업시간 중 고객센터 또는 영업점으로 문의해 주세요."
)


def _approved_texts() -> set[str]:
    """준법 검토를 마친 정형 응답 — 게이트를 통과시킨다."""
    from .generate import NO_EVIDENCE, REFUSAL

    return {REFUSAL.strip(), NO_EVIDENCE.strip(), SAFE_FALLBACK.strip()}


def _evidence_text(state: AgentState) -> str:
    parts = list(state.get("db_records", []))
    parts += [d["text"] for d in state.get("evidence", [])]
    return "\n".join(parts)


def check(draft: str, state: AgentState) -> list[str]:
    # 사전 승인된 정형 문구는 검사 대상에서 제외한다.
    if draft.strip() in _approved_texts():
        return []

    scan = draft
    for t in SAFE_TERMS:
        scan = scan.replace(t, "○" * len(t))

    violations: list[str] = []
    for rid, pattern, reason in RULES:
        if re.search(pattern, scan):
            violations.append(f"{rid} {reason}")

    ev = _evidence_text(state)
    if ev:
        flat_ev = ev.replace(" ", "")
        for m in set(x.group(0).replace(" ", "") for x in NUM_RE.finditer(scan)):
            if m not in flat_ev:
                violations.append(f"C-08 근거에 없는 수치 사용({m})")
                break
    return violations



# ── C-09 근거 이탈(환각) 검사 ─────────────────────────────────
# 규칙(C-08)은 근거에 없는 '수치'만 본다. 실측에서 서술형 환각(RAGAS 환각률 0.100)이
# 규칙을 그대로 통과하는 것이 확인되어, LLM 판정을 2차 검증으로 얹었다.
# 규칙과 LLM 중 하나라도 문제를 잡으면 반려한다(보수적 OR).
FAITHFUL_SYSTEM = """당신은 자산운용사 챗봇의 사실 검증관입니다.
[답변]의 모든 내용이 [근거]에 담겨 있는지 확인하세요.

- 근거에 없는 사실·수치·날짜·상품명을 답변이 새로 만들어냈다면 NO
- 근거 내용을 다르게 표현했을 뿐이면 YES
- 일반 상식 수준의 연결어나 안내 문구(추가 문의 안내 등)는 문제 삼지 않습니다
- 근거에 없는 내용을 단 한 문장이라도 단정했다면 NO

YES 또는 NO 한 단어만 출력하세요."""


def _faithfulness_violation(draft: str, state: AgentState) -> str | None:
    """근거 이탈이면 사유 문자열, 아니면 None."""
    from .. import llm

    if not llm.available():
        return None
    ev = _evidence_text(state)
    if not ev.strip():
        return None
    try:
        prompt = "[근거]" + "\n" + ev + "\n" + "\n" + "[답변]" + "\n" + draft
        out = llm.complete(FAITHFUL_SYSTEM, prompt, temperature=0.0, max_tokens=5)
        if "NO" in out.strip().upper():
            return "C-09 근거 이탈(환각) 의심"
    except Exception:
        return None
    return None


def run(state: AgentState) -> AgentState:
    draft = state.get("draft", "")
    violations = check(draft, state)

    # 규칙을 통과했더라도 LLM 사실 검증을 한 번 더 거친다.
    if not violations and draft.strip() not in _approved_texts():
        hallu = _faithfulness_violation(draft, state)
        if hallu:
            violations = [hallu]
    trace = list(state.get("trace", []))
    count = state.get("regenerate_count", 0)

    if not violations:
        citations = []
        for r in state.get("db_records", []):
            citations.append(r.splitlines()[0].strip("[]"))
        for d in state.get("evidence", []):
            citations.append(f"{d['store']} · {d['source']}")
        trace.append("⑤ 검증 게이트 · 통과")
        return {**state, "verdict": "pass", "violations": [],
                "answer": draft, "citations": citations, "trace": trace}

    if count >= Config.MAX_REGENERATE:
        trace.append(
            f"⑤ 검증 게이트 · 반려({', '.join(violations)}) · 한도 초과 → 안전 응답 대체"
        )
        return {**state, "verdict": "reject", "violations": violations,
                "answer": SAFE_FALLBACK, "citations": [], "trace": trace}

    trace.append(
        f"⑤ 검증 게이트 · 반려({', '.join(violations)}) → ④로 재생성 (#{count + 1})"
    )
    return {**state, "verdict": "reject", "violations": violations,
            "regenerate_count": count + 1, "trace": trace}
