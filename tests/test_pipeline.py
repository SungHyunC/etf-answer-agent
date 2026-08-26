"""파이프라인 단위 검증 — 특히 컴플라이언스 반려 루프가 실제로 도는지 확인한다."""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.config import Config
from src.graph import ask
from src.nodes import compliance
from src.nodes.compliance import check


def test_typo_correction():
    r = ask("나스닥100 총보스 얼마애요?")
    assert "총보수" in r["normalized"] and len(r["corrections"]) >= 1
    print("PASS 오타 교정")


def test_entity_and_routing():
    r = ask("KODEX 200 구성종목 알려줘")
    assert "KODEX 200" in r["entities"]
    assert r["intent"] == "etf_info"
    assert "삼성전자" in r["answer"]
    print("PASS 엔티티 인식 + 라우팅")


def test_store_isolation():
    r = ask("나스닥100 분배금 언제 지급되나요")
    assert "공시" in r["store_used"]
    print("PASS 창고 분리 라우팅")


def test_out_of_scope_blocked():
    for q in ["ETF 추천해주세요", "내년에 오를까요?", "원금 보장되나요?"]:
        r = ask(q)
        assert r["intent"] == "out_of_scope" or "범위를 벗어납니다" in r["answer"], q
    print("PASS 범위 외 요청 차단")


def test_compliance_rules():
    st = {"db_records": [], "evidence": []}
    assert check("이 상품을 추천드립니다.", st)
    assert check("지금 매수하세요.", st)
    assert check("수익률이 보장됩니다.", st)
    assert not check("총보수는 연 0.07%입니다.", {"db_records": ["총보수: 연 0.07%"], "evidence": []})
    print("PASS 컴플라이언스 규칙")


def test_reject_loop_actually_runs():
    """생성 노드가 위반 답변을 내도록 강제해 반려 루프가 도는지 확인한다."""
    from src.nodes import generate

    original = generate.run
    calls = {"n": 0}

    def bad_generate(state):
        calls["n"] += 1
        s = original(state)
        # 첫 두 번은 위반 답변을 낸다 → 게이트가 반려해야 한다
        if calls["n"] <= 2:
            s = {**s, "draft": "이 상품을 적극 추천드립니다. 지금 매수하세요."}
        return s

    generate.run = bad_generate
    try:
        import importlib
        from src import graph as g
        importlib.reload(g)
        r = g.ask("ETF가 뭔가요?")
        assert calls["n"] >= 2, f"재생성이 일어나지 않음 (호출 {calls['n']}회)"
        assert not check(r["answer"], r), "위반 답변이 고객에게 노출됨"
        assert any("반려" in t for t in r["trace"]), "반려 흔적 없음"
        print(f"PASS 반려 루프 (생성 {calls['n']}회, 최종 답변 안전)")
    finally:
        generate.run = original
        import importlib
        from src import graph as g2
        importlib.reload(g2)


if __name__ == "__main__":
    print(f"백엔드: {Config.describe()}\n")
    test_typo_correction()
    test_entity_and_routing()
    test_store_isolation()
    test_out_of_scope_blocked()
    test_compliance_rules()
    test_reject_loop_actually_runs()
    print("\n전체 통과")
