"""② 의도 분류 — 질문 유형을 판별해 담당 기능으로 라우팅.

요구 기능 '의도분류': 자연어 질문의 문맥과 의도를 이해하고 담당 기능을 호출한다.
LLM 백엔드가 있으면 LLM으로 분류하고, 없으면 규칙 기반으로 폴백한다(동일 라벨 체계).
"""
from __future__ import annotations

from .. import llm
from ..state import AgentState

LABELS = {
    "etf_info": "특정 ETF 상품의 수치·속성 조회 (총보수, 기초지수, 구성종목, 순자산 등)",
    "disclosure": "공시·뉴스 등 시점이 반영된 정보 (분배금 일정, 정기변경, 시장 동향)",
    "faq": "거래 방법·세금·계좌 등 절차성 문의",
    "general": "ETF 일반 개념·용어 설명",
    "out_of_scope": "투자 권유·종목 추천·수익률 예측·매매 실행 요청, 또는 ETF와 무관한 질문",
}

# 규칙 기반 신호어
RULES: list[tuple[str, list[str]]] = [
    # 규제 경로 — 투자권유로 해석될 수 있는 요청은 가장 먼저, 가장 넓게 잡는다.
    ("out_of_scope", ["추천", "뭐 사", "뭘 사", "사는게", "사는 게", "사야 하나", "사도 될까",
                      "살까", "팔까", "팔아야", "매수해", "매도해", "골라", "고르면",
                      "좋을까", "좋을까요", "괜찮을까", "나을까", "유망", "유리한가",
                      "얼마나 오를", "오를까", "떨어질까", "전망", "수익률 보장", "얼마 벌",
                      "투자해도", "손실 안", "대신 사", "얼마 넣", "비중 얼마",
                      "어디에 넣", "어디다 넣", "넣으면", "원금 보장", "보장되나", "보장 되나",
                      "손해 안", "안전한가", "묻어두", "굴리면"]),
    ("disclosure", ["분배금", "공시", "뉴스", "정기변경", "리밸런싱", "일정", "언제 지급", "기준일"]),
    ("faq", ["어떻게 사", "어떻게 매수", "세금", "과세", "계좌", "수수료 환급", "환매",
             "거래시간", "입금", "상담원", "문의"]),
    ("etf_info", ["총보수", "기초지수", "구성종목", "순자산", "상장일", "티커", "종목코드",
                  "보수", "유형", "몇 퍼센트", "얼마"]),
    ("general", ["뭔가요", "뭐예요", "무엇인가요", "차이", "설명", "개념", "괴리율", "추적오차",
                 "환헤지", "상장폐지"]),
]

SYSTEM = """당신은 자산운용사 ETF 상담 챗봇의 의도 분류기입니다.
사용자 질문을 아래 라벨 중 정확히 하나로 분류하고, 라벨만 출력하세요.

etf_info: 특정 ETF 상품의 수치·속성 조회
disclosure: 공시·뉴스 등 시점 정보
faq: 거래 방법·세금·계좌 등 절차 문의
general: ETF 일반 개념·용어 설명
out_of_scope: 투자 권유·종목 추천·수익률 예측·매매 실행 요청 또는 ETF 무관 질문

라벨 외 다른 텍스트는 절대 출력하지 마세요."""


DEFINITION_HINTS = ["뭔가요", "뭐예요", "무엇인가요", "무엇인지", "뭐야", "차이", "설명해", "개념"]


def _rule_classify(text: str, entities: list[str]) -> tuple[str, float, str]:
    # 규제 경로는 항상 최우선으로 검사한다.
    for k in RULES[0][1]:
        if k in text:
            return "out_of_scope", 0.85, f"규칙: 규제 신호어 '{k}'"
    # 특정 상품을 지목하지 않은 정의형 질문은 개념 설명으로 본다.
    if not entities and any(h in text for h in DEFINITION_HINTS):
        return "general", 0.7, "규칙: 엔티티 없는 정의형 질문"
    for label, keys in RULES:
        for k in keys:
            if k in text:
                return label, 0.75, f"규칙: '{k}' 신호어 매칭"
    if entities:
        return "etf_info", 0.6, "규칙: 상품 엔티티 존재"
    return "general", 0.4, "규칙: 기본값"


def run(state: AgentState) -> AgentState:
    text = state["normalized"]
    entities = state.get("entities", [])

    label, conf, reason = _rule_classify(text, entities)

    # LLM 사용 가능 시 재분류 (규칙은 폴백 겸 안전망으로 유지)
    if llm.available():
        try:
            raw = llm.complete(SYSTEM, text, temperature=0.0, max_tokens=10).strip().lower()
            for cand in LABELS:
                if cand in raw:
                    # out_of_scope 는 규칙이 잡았으면 규칙을 우선(보수적 차단)
                    if label == "out_of_scope":
                        break
                    label, conf, reason = cand, 0.9, "LLM 분류"
                    break
        except Exception as e:  # 폴백
            reason += f" (LLM 분류 실패: {type(e).__name__})"

    trace = list(state.get("trace", []))
    trace.append(f"② 의도분류 · {label} (conf {conf:.2f}) · {reason}")
    return {**state, "intent": label, "intent_confidence": conf,
            "intent_reason": reason, "trace": trace}
