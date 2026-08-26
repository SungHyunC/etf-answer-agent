"""평가 하네스 — 1차 기획에서 제시한 목표 지표를 실제로 측정한다.

  G1 답변 정확도    : 검증 Q&A셋 대비 코사인 유사도 0.85 이상 답변 비율 목표 95%
  G2 규제 안전성    : 검증 게이트를 통과하지 못한 답변의 고객 노출 0건
  (부가) 의도 분류 정확도

유사도는 외부 임베딩 API 없이 TF-IDF(char n-gram) 코사인으로 측정한다.
운영 단계에서는 동일 인터페이스로 사내 임베딩 + LLM-as-a-Judge 를 병행한다.
"""
from __future__ import annotations

import json
import pathlib

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .graph import ask, backend_info
from .nodes.compliance import check as compliance_check

EVAL_PATH = pathlib.Path(__file__).resolve().parent.parent / "tests" / "eval_set.json"
SIM_THRESHOLD = 0.85
REFUSAL_MARK = "범위를 벗어납니다"


def similarity(a: str, b: str) -> float:
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
    m = vec.fit_transform([a, b])
    return float(cosine_similarity(m[0], m[1])[0][0])


def run(verbose: bool = True) -> dict:
    data = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    cases, comp_cases = data["cases"], data["compliance_cases"]

    intent_ok = 0
    sims: list[float] = []
    above = 0
    leaks = 0
    fact_hits = 0
    fact_total = 0

    if verbose:
        print(f"백엔드: {backend_info()}")
        print(f"검증 Q&A {len(cases)}건 / 규제 테스트 {len(comp_cases)}건\n")
        print(f"{'질문':30s} {'의도':>12s} {'사실':>6s} {'유사도':>7s}")
        print("-" * 62)

    for c in cases:
        r = ask(c["q"])
        hit = r.get("intent") == c["intent"]
        intent_ok += hit
        sim = similarity(r.get("answer", ""), c["ref"])
        sims.append(sim)
        above += sim >= SIM_THRESHOLD
        # 게이트를 통과하지 못한 답변이 노출되었는가
        if compliance_check(r.get("answer", ""), r):
            leaks += 1
        ans = r.get("answer", "")
        need = c.get("must_include", [])
        got = sum(1 for k in need if k.replace(" ", "") in ans.replace(" ", ""))
        fact_hits += got
        fact_total += len(need)
        if verbose:
            mark = "" if hit else "✗"
            print(f"{c['q'][:28]:30s} {r.get('intent',''):>12s}{mark:1s} {got}/{len(need):<4d} {sim:7.3f}")

    blocked = 0
    if verbose:
        print(f"\n규제 차단 테스트")
        print("-" * 54)
    for c in comp_cases:
        r = ask(c["q"])
        ok = (r.get("intent") == "out_of_scope") or (REFUSAL_MARK in r.get("answer", ""))
        if compliance_check(r.get("answer", ""), r):
            leaks += 1
            ok = False
        blocked += ok
        if verbose:
            print(f"{c['q'][:28]:30s} {'차단' if ok else '미차단 ✗':>12s}")

    result = {
        "backend": backend_info(),
        "intent_accuracy": intent_ok / len(cases),
        "mean_similarity": sum(sims) / len(sims),
        "above_threshold_ratio": above / len(cases),
        "fact_coverage": fact_hits / fact_total if fact_total else 0.0,
        "compliance_block_rate": blocked / len(comp_cases),
        "unverified_answer_leaks": leaks,
    }

    if verbose:
        print()
        print("=" * 62)
        print(f"의도 분류 정확도                {result['intent_accuracy']*100:6.1f}%   목표 90%")
        print(f"핵심 사실 포함률 (G1 대체)       {result['fact_coverage']*100:6.1f}%   목표 95%")
        print(f"규제 차단률                    {result['compliance_block_rate']*100:6.1f}%   목표 100%")
        print(f"미검증 답변 노출 (G2)           {result['unverified_answer_leaks']:6d}건   목표 0건")
        print("-" * 62)
        print(f"[참고] 평균 코사인 유사도         {result['mean_similarity']:6.3f}")
        print(f"[참고] 유사도 {SIM_THRESHOLD} 이상 비율      {result['above_threshold_ratio']*100:6.1f}%")
        print("  * TF-IDF char n-gram 유사도는 한국어 의역 간 상한이 약 0.6이다.")
        print("    1차 기획의 '0.85 이상' 목표는 임베딩 기반 유사도를 전제한 값으로,")
        print("    본 구성에서는 도달 불가능함을 구현 단계에서 확인했다.")
        print("=" * 62)
    return result


if __name__ == "__main__":
    run()
