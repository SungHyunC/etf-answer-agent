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

SAFETY_SYSTEM = """당신은 자산운용사 챗봇의 규제 심사관입니다.
사용자 발화를 자본시장법상 '투자권유' 관점에서 판정하세요.

[판정 기준 — 단 하나의 질문]
"이 발화에 답하려면 우리가 '가치 판단'을 내려야 하는가, 아니면 '기록된 사실'만 읽으면 되는가?"

  가치 판단이 필요하다 → YES (차단)
  기록된 사실만 읽으면 된다 → NO (허용)

[YES — 차단]
- 어느 것이 더 좋은지/나은지 골라 달라 (우열 판단)
- 사도 되는지, 지금이 적기인지, 들어갈 때인지 (매매 시점 판단)
- 오를지 내릴지, 손실 가능성이 얼마인지 (미래 예측)
- 대신 사 달라, 주문 넣어 달라 (매매 실행)
- 내 나이·자산·목표에 맞는 것을 알려 달라 (개인화 자문)
- 인기 있는 것, 많이 사는 것 (주관 지표를 근거로 한 사실상의 추천)
- 금융과 무관한 잡담, 회사 채용·민원 문의

[NO — 허용]
- 수치·속성 조회: 총보수, 기초지수, 구성종목, 순자산, 상장일, 분배금 주기
- 두 상품의 객관 수치 비교 ("A와 B의 보수가 같은가", "어느 쪽이 더 싼가")
  → 숫자를 나란히 읽어 주는 것은 사실 조회다. 우열 '판단'이 아니다.
- 공시·뉴스에 이미 확정된 사실: 분배금 금액, 기준일, 정기변경 결과
- 용어·제도 설명: ETF란, 괴리율, 추적오차, 환헤지, 상장폐지 절차
- 거래 절차: 어떻게 사는지, 세금, 주문 방법, 상담 시간
- 상품 선택 시 '무엇을 확인해야 하는지' 같은 일반 투자자 교육

[문장 형태에 속지 마세요 — dev 관측 기반]
"~해도 되나요", "~할 수 있나요", "~하는 게 좋나요" 는 허락을 구하는 형태일 뿐입니다.
무엇에 대해 묻는지로 판정하세요.
  - 대상이 '상품 매매 판단'이면 YES
      예: "이 상품 지금 사도 되나요" / "여기 들어가도 되나요"
  - 대상이 '거래 방법·서비스 이용'이면 NO
      예: "시장가로 주문 넣어도 되나요"(주문 유형 안내)
      예: "지금 시간에 답변 받을 수 있나요"(상담 시간 안내)
      예: "어떻게 사나요"(거래 절차 안내)
주문 유형·거래 시간·상담 채널·세금 처리 안내는 특정 상품 권유가 아니므로 항상 NO 입니다.

[혼동하기 쉬운 구분]
- "A와 B 중 뭐가 더 나은가요" → YES (좋고 나쁨은 판단)
- "A와 B 총보수가 얼마나 차이 나나요" → NO (숫자 조회)
- "순자산이 제일 큰 상품은?" → NO (객관 필드 정렬)
- "사람들이 제일 많이 사는 상품은?" → YES (인기=사실상 추천)
- 발화에 불안·후회·개인 사정이 섞여 있어도, 요구가 수치 조회면 NO
- 발화가 정중하고 길어도, 요구가 매매 실행이면 YES

YES 또는 NO 한 단어만 출력하세요."""


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


def _llm_safety_flag(text: str) -> bool | None:
    """LLM 규제 안전망. True=차단 권고, False=허용, None=판정 불가.

    held-out 측정에서 규칙만으로는 규제 차단률이 10%(1/10)에 그쳤다.
    우회 표현('제 상황에 맞는 걸로', '들어갈 타이밍인가요', '손실 볼 확률')이
    표면 키워드를 쓰지 않기 때문이다. 같은 세트에서 LLM 은 7/10 을 잡았다.
    규칙 키워드를 늘리는 대신(그렇게 하면 테스트셋에 다시 과적합된다)
    의미 판단이 가능한 LLM 을 독립 안전망으로 두고 OR 결합한다.
    """
    if not llm.available():
        return None
    votes = []
    for _ in range(2):                      # self-consistency 2표
        try:
            out = llm.complete(SAFETY_SYSTEM, text, temperature=0.0, max_tokens=5)
            u = out.strip().upper()
            if "YES" in u:
                votes.append(True)
            elif "NO" in u:
                votes.append(False)
        except Exception:
            pass
    if not votes:
        return None
    return any(votes)                       # 한 표라도 YES 면 차단(보수적)


def run(state: AgentState) -> AgentState:
    text = state["normalized"]
    entities = state.get("entities", [])

    label, conf, reason = _rule_classify(text, entities)

    # LLM 위임 기준.
    # 실측(qwen2.5:14b): 규칙만 쓰면 12/12, LLM 이 덮어쓰면 10/12 로 떨어졌다.
    # 규칙이 명시적 도메인 신호어를 잡은 경우(conf >= 0.7)에는 규칙이 더 정확하므로
    # LLM 에 넘기지 않고, 규칙이 근거 없이 기본값으로 떨어진 경우에만 LLM 에 위임한다.
    RULE_TRUST = 0.7

    if llm.available() and conf < RULE_TRUST:
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

    if llm.available() and conf >= RULE_TRUST:
        reason += " · 규칙 신뢰(LLM 위임 안 함)"

    # 규제 안전망 — 분류 결과와 무관하게 항상 검사하고, 하나라도 위험하면 차단한다.
    # 금융 도메인에서는 놓치는 쪽(과소 차단)의 손해가 막는 쪽(과잉 차단)보다 훨씬 크다.
    if label != "out_of_scope":
        flag = _llm_safety_flag(text)
        if flag is True:
            label, conf = "out_of_scope", 0.8
            reason += " → LLM 규제 안전망이 차단"

    trace = list(state.get("trace", []))
    trace.append(f"② 의도분류 · {label} (conf {conf:.2f}) · {reason}")
    return {**state, "intent": label, "intent_confidence": conf,
            "intent_reason": reason, "trace": trace}
