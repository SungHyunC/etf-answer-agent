"""held-out 평가 — 규칙 튜닝에 쓰지 않은 세트로 일반화 성능을 잰다.

기존 tests/eval_set.json 은 규칙을 그 세트에 맞춰 고쳤기 때문에 100% 가 나온다.
그 수치는 성능이 아니라 과적합이다. 이 모듈은 별도로 만든 held-out 세트로 다시 잰다.

  python -m src.eval.holdout
"""
from __future__ import annotations

import json
import pathlib
from collections import Counter

from .. import llm
from ..graph import ask, backend_info
from ..nodes.compliance import check as compliance_check

FACT_SYSTEM = """당신은 답변 채점기입니다.
[답변]이 [확인할 사실]을 담고 있는지 판정하세요.

- 표현이 달라도 같은 내용을 전달하면 담긴 것으로 봅니다.
  예: 확인할 사실 "HTS·MTS" / 답변 "HTS나 MTS에서" → YES
  예: 확인할 사실 "매매차익 비과세" / 답변 "매매차익에 대한 세금이 비과세" → YES
- 수치·날짜는 값이 같아야 합니다. 다르거나 없으면 NO.
- 답변이 그 사실을 언급조차 하지 않으면 NO.

YES 또는 NO 한 단어만 출력하세요."""


def _fact_in_answer(answer: str, fact: str) -> bool:
    """사실 포함 판정. LLM 이 있으면 의미로, 없으면 문자열로 판정한다.

    문자열 완전 일치는 LLM 이 의역하면 정답도 실패로 센다(실측 확인).
    2주차의 코사인 유사도 함정과 같은 문제라 같은 방식으로 고쳤다.
    """
    if fact.replace(" ", "") in answer.replace(" ", ""):
        return True
    if not llm.available():
        return False
    try:
        prompt = "[확인할 사실]\n" + fact + "\n\n[답변]\n" + answer
        out = llm.complete(FACT_SYSTEM, prompt, temperature=0.0, max_tokens=5)
        return "YES" in out.strip().upper()
    except Exception:
        return False

import os
_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
# HOLDOUT_SPLIT=dev|test|all  (기본 dev — test 는 최종 검증에만 쓴다)
_SPLIT = os.environ.get("HOLDOUT_SPLIT", "dev")
PATH = _ROOT / "tests" / ("holdout_set.json" if _SPLIT == "all" else f"holdout_{_SPLIT}.json")
REFUSAL_MARK = "범위를 벗어납니다"


def run(verbose: bool = True) -> dict:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    cases, comp = data["cases"], data["compliance_cases"]

    intent_ok, fact_hit, fact_total, leaks = 0, 0, 0, 0
    strict_hit = [0]
    confusion: Counter = Counter()
    intent_errors, fact_misses = [], []

    if verbose:
        print(f"백엔드: {backend_info()}  |  분할: {_SPLIT}")
        print(f"held-out 채점 {len(cases)}건 / 규제 {len(comp)}건\n")
        print(f"{'질문':40s} {'예측':>12s} {'정답':>12s} {'사실':>6s}")
        print("-" * 76)

    for c in cases:
        r = ask(c["q"])
        pred = r.get("intent", "")
        hit = pred == c["intent"]
        intent_ok += hit
        if not hit:
            confusion[(c["intent"], pred)] += 1
            intent_errors.append((c["q"], c["intent"], pred))

        ans = r.get("answer") or ""
        need = c.get("must_include", [])
        hits = [_fact_in_answer(ans, k) for k in need]
        got = sum(hits)
        fact_hit += got
        fact_total += len(need)
        if need and got < len(need):
            fact_misses.append((c["q"], [k for k, h in zip(need, hits) if not h]))

        strict = sum(1 for k in need if k.replace(" ", "") in ans.replace(" ", ""))
        strict_hit[0] += strict

        if compliance_check(r.get("answer", ""), r):
            leaks += 1
        if verbose:
            print(f"{c['q'][:38]:40s} {pred:>12s} {c['intent']:>12s} {'✗' if not hit else ' '}{got}/{len(need)}")

    blocked, block_fail = 0, []
    if verbose:
        print(f"\n규제 차단 {len(comp)}건")
        print("-" * 76)
    for c in comp:
        r = ask(c["q"])
        ok = r.get("intent") == "out_of_scope" or REFUSAL_MARK in (r.get("answer") or "")
        if compliance_check(r.get("answer", ""), r):
            leaks += 1
            ok = False
        blocked += ok
        if not ok:
            block_fail.append((c["q"], r.get("intent")))
        if verbose:
            print(f"{c['q'][:38]:40s} {'차단' if ok else '미차단 ✗':>12s}")

    res = {
        "backend": backend_info(),
        "intent_accuracy": intent_ok / len(cases),
        "fact_coverage": fact_hit / fact_total if fact_total else 0.0,
        "fact_coverage_strict": strict_hit[0] / fact_total if fact_total else 0.0,
        "compliance_block_rate": blocked / len(comp),
        "unverified_answer_leaks": leaks,
        "intent_errors": intent_errors,
        "block_failures": block_fail,
    }

    if verbose:
        print("\n" + "=" * 76)
        print(f"의도 분류 정확도    {res['intent_accuracy']*100:6.1f}%   ({intent_ok}/{len(cases)})")
        print(f"핵심 사실 포함률    {res['fact_coverage']*100:6.1f}%   ({fact_hit}/{fact_total})  ← 의미 판정")
        print(f"  (문자열 완전일치  {res['fact_coverage_strict']*100:6.1f}%   {strict_hit[0]}/{fact_total} — 참고용, 의역을 실패로 셈)")
        print(f"규제 차단률        {res['compliance_block_rate']*100:6.1f}%   ({blocked}/{len(comp)})")
        print(f"미검증 답변 노출    {leaks:6d}건")
        print("=" * 76)
        if intent_errors:
            print("\n[의도 오분류]")
            for q, exp, got in intent_errors:
                print(f"  - {q[:52]}\n      정답 {exp} / 예측 {got}")
        if block_fail:
            print("\n[규제 미차단] — 가장 위험한 실패")
            for q, got in block_fail:
                print(f"  - {q[:52]}  (→ {got})")
        if fact_misses:
            print(f"\n[사실 누락] {len(fact_misses)}건")
            for q, miss in fact_misses[:8]:
                print(f"  - {q[:44]} → 빠진 것: {', '.join(miss)}")
    return res


if __name__ == "__main__":
    run()
