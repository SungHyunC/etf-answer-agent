"""① 발화 전처리 — 오타 교정, 엔티티 식별, 사용자 수준 판별.

요구 기능 '발화분석': 다양한 언어형태 분석, 엔티티 식별·토큰화, 맞춤법·오타 교정.
"""
from __future__ import annotations

import re

from ..data import etf_db
from ..state import AgentState

# 상담 로그에서 관측되는 대표 오타 (운영 시 VOC 기반으로 확장)
TYPO_MAP = {
    "ETF으": "ETF은", "이티에프": "ETF", "etf": "ETF",
    "총보스": "총보수", "총 보수": "총보수", "총보수률": "총보수율",
    "분배근": "분배금", "분배 금": "분배금",
    "나스닥100지수": "나스닥100", "s&p": "S&P",
    "리벨런싱": "리밸런싱", "리발란싱": "리밸런싱", "리밸랜싱": "리밸런싱",
    "괴리률": "괴리율", "추척오차": "추적오차",
    "얼마애요": "얼마예요", "머예요": "뭐예요", "먼가요": "뭔가요",
    "알려조": "알려줘", "알려주세여": "알려주세요",
}

# 초보자 신호 / 숙련자 신호
BEGINNER_HINTS = ["뭔가요", "뭐예요", "무엇인가요", "처음", "초보", "쉽게", "차이가 뭐", "어떻게 사", "설명해"]
EXPERT_HINTS = ["괴리율", "추적오차", "리밸런싱", "정기변경", "실부담비용", "환헤지", "NAV", "LP", "듀레이션"]


def normalize(text: str) -> tuple[str, list[tuple[str, str]]]:
    # 배포 환경에서는 빈 입력·None 이 실제로 들어온다(폼 재전송, 잘린 요청 등).
    # 여기서 죽으면 그래프 전체가 500 으로 떨어지므로 입구에서 흡수한다.
    out = (text or "").strip()
    fixes: list[tuple[str, str]] = []
    for wrong, right in TYPO_MAP.items():
        if wrong in out:
            out = out.replace(wrong, right)
            fixes.append((wrong, right))
    out = re.sub(r"\s+", " ", out)
    return out, fixes


def detect_level(text: str) -> str:
    if any(h in text for h in EXPERT_HINTS):
        return "expert"
    if any(h in text for h in BEGINNER_HINTS):
        return "beginner"
    return "beginner"


def run(state: AgentState) -> AgentState:
    q = state["question"]
    normalized, fixes = normalize(q)
    entities = etf_db.resolve(normalized)
    level = detect_level(normalized)

    trace = list(state.get("trace", []))
    trace.append(
        f"① 전처리 · 오타교정 {len(fixes)}건 · 엔티티 {entities or '없음'} · 수준 {level}"
    )
    return {
        **state,
        "normalized": normalized,
        "corrections": fixes,
        "entities": entities,
        "level": level,
        "trace": trace,
        "regenerate_count": state.get("regenerate_count", 0),
    }
