"""프롬프트 변형 A/B 하네스 — 3차 요구 '프롬프트 엔지니어링으로 출력 결과를 정확하게 유도'.

src/prompts.py 의 변형을 하나씩 적용하고 기존 평가 하네스(src/evaluate.py)를 그대로
돌려 지표를 비교한다.

[측정 가능 범위에 대한 전제 — 리포트에도 그대로 출력한다]
프롬프트는 LLM 을 호출할 때만 생성에 관여한다. 따라서
  · rule 백엔드에서는 SYSTEM 프롬프트가 쓰이지 않으므로 변형 간 지표 차이가 나타나지 않는다.
  · generate.py 가 아직 prompts.py 를 사용하지 않으면, LLM 이 있어도 변형이 반영되지 않는다.
이 두 조건을 매 실행마다 확인해서 출력하고, 조건이 갖춰지지 않았으면
'차이 없음'이 아니라 '이 구성에서는 측정 불가'로 읽어야 한다고 명시한다.

프롬프트 자체의 정적 분석(길이, 금지 지시 수, 규제 규칙 언급 수, 구조 지시 유무 등)은
백엔드와 무관하게 비교할 수 있으므로 항상 표로 제시한다.

실행:
    python -m src.eval.prompt_ab
"""
from __future__ import annotations

import inspect
import os
import re
import unicodedata

from .. import llm, prompts
from ..config import Config
from ..evaluate import run as evaluate_run
from ..nodes import compliance, generate

# 비교에 쓸 지표 — (키, 표시명, 서식)
METRIC_COLS: list[tuple[str, str, str]] = [
    ("intent_accuracy", "의도정확도", "{:.3f}"),
    ("fact_coverage", "사실포함률", "{:.3f}"),
    ("compliance_block_rate", "규제차단률", "{:.3f}"),
    ("unverified_answer_leaks", "미검증노출", "{:d}"),
    ("mean_similarity", "평균유사도", "{:.3f}"),
]

# 정적 분석용 패턴
PROHIBIT_RE = re.compile(r"금지|마세요|절대|쓰지 않|하지 마|쓰지 마")
STRUCTURE_RE = re.compile(r"다음 순서|순서로 씁니다|핵심 답변")
LEVEL_RE = re.compile(r"beginner|expert")
# '근거가 부족할 때 어떻게 행동하라'는 지시만 잡는다.
# '지어내지 마세요'(환각 금지)는 성격이 다르므로 규제언급 쪽에서 센다.
LACK_RE = re.compile(r"근거가 부족|확인되지 않|모른다고|추론하지|추정하지")

# 컴플라이언스 규칙별 대표어 — compliance.RULES 의 정규식을 사람이 읽고 손으로 매핑한 표다.
# (정규식에서 자동 추출한 것이 아니므로, 규칙이 늘어나면 여기도 함께 갱신해야 한다.
#  누락은 _rule_ids() 가 실행 시점에 잡아준다.)
RULE_KEYWORDS: dict[str, list[str]] = {
    "C-01": ["추천"],
    "C-02": ["매수", "매도", "권유", "투자하세요"],
    "C-03": ["보장", "확실", "무조건"],
    "C-04": ["오를 것", "상승할 것", "하락할 것", "가격 전망"],
    "C-05": ["유망", "기대됩니다", "좋은 기회", "적기"],
    "C-06": ["사시면", "담으시면", "비중을 늘리"],
    "C-07": ["반드시", "100%"],
    "C-08": ["없는 수치", "지어내지", "그대로 옮"],   # 근거 없는 수치 주장(NUM_RE)
}


def _rule_ids() -> list[str]:
    """검사 대상 규칙 ID — compliance.RULES + 수치 환각 규칙(C-08)."""
    ids = [rid for rid, _, _ in compliance.RULES]
    if "C-08" not in ids:
        ids.append("C-08")
    return ids


def _unmapped_rules() -> list[str]:
    """RULE_KEYWORDS 에 대표어가 없는 규칙 — 규제언급 수치가 과소 집계되는 구간."""
    return [rid for rid in _rule_ids() if not RULE_KEYWORDS.get(rid)]


def _w(text: str) -> int:
    """터미널 표시 폭 — 한글/전각 문자는 2칸으로 센다."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    """표시 폭 기준 정렬. 한글 열 머리글과 숫자 열을 맞추기 위해 필요하다."""
    gap = " " * max(0, width - _w(text))
    return gap + text if right else text + gap


def integrated() -> bool:
    """generate.py 가 실제로 prompts.py 를 쓰고 있는지 소스에서 확인한다.

    통합은 다른 담당이 하므로, 이 하네스는 상태를 가정하지 않고 매번 소스를 본다.
    (소스 문자열 검사라는 점에서 휴리스틱이다 — 통합 방식이 바뀌면 패턴도 손봐야 한다.)
    """
    try:
        src = inspect.getsource(generate)
    except OSError:
        return False
    return bool(re.search(r"prompts\s+import|import\s+prompts|prompts\.get\s*\(", src))


def analyze(system: str) -> dict:
    """프롬프트 문자열만 보고 셀 수 있는 값 — 백엔드와 무관하게 비교 가능하다.

    주의: 여기서 나오는 수치는 '프롬프트가 무엇을 지시하는가'일 뿐,
    '출력이 실제로 좋아지는가'가 아니다. 후자는 LLM 실행으로만 측정된다.
    """
    lines = [l for l in system.splitlines() if l.strip()]
    hit = LACK_RE.search(system)
    covered = [
        rid for rid in _rule_ids()
        if any(k in system for k in RULE_KEYWORDS.get(rid, []))
    ]
    return {
        "chars": len(system),
        "lines": len(lines),
        "prohibit_lines": sum(1 for l in lines if PROHIBIT_RE.search(l)),
        "rule_mentions": len(covered),
        "rule_total": len(_rule_ids()),
        "rules_covered": covered,
        "has_structure": bool(STRUCTURE_RE.search(system)),
        "level_lines": sum(1 for l in lines if LEVEL_RE.search(l)),
        # 근거 부족 관련 지시가 프롬프트의 몇 % 지점에서 처음 나오는가 (0=맨 앞)
        "lack_pos": round(hit.start() / len(system), 2) if hit else None,
    }


def run_variant(name: str) -> dict:
    """변형 하나를 적용한 상태로 기존 평가 하네스를 돌리고 결과를 모은다."""
    v = prompts.get(name)

    prev = os.environ.get(prompts.ENV_KEY)
    os.environ[prompts.ENV_KEY] = name
    try:
        metrics = evaluate_run(verbose=False)
    finally:
        if prev is None:
            os.environ.pop(prompts.ENV_KEY, None)
        else:
            os.environ[prompts.ENV_KEY] = prev

    used = llm.available() and integrated()
    return {
        "name": name,
        "note": v["note"],
        "backend": Config.describe(),
        "llm_available": llm.available(),
        "integrated": integrated(),
        "prompt_effective": used,     # 이 실행에서 프롬프트가 생성에 실제로 반영되었는가
        "static": analyze(v["system"]),
        "metrics": metrics,
    }


def _metric_tuple(r: dict) -> tuple:
    return tuple(r["metrics"].get(k) for k, _, _ in METRIC_COLS)


def compare(names: list[str] | None = None, verbose: bool = True) -> dict:
    """변형들을 차례로 실행해 정적 분석 + 파이프라인 지표를 비교한다."""
    targets = names or prompts.names()
    results = {n: run_variant(n) for n in targets}

    tuples = [_metric_tuple(results[n]) for n in targets]
    identical = len(set(tuples)) == 1 if len(tuples) > 1 else None
    effective = llm.available() and integrated()

    if verbose:
        _report(targets, results, identical, effective)

    return {
        "backend": Config.describe(),
        "llm_available": llm.available(),
        "integrated": integrated(),
        "prompt_effective": effective,
        "metrics_identical": identical,
        "variants": results,
    }


def _report(targets: list[str], results: dict, identical: bool | None, effective: bool) -> None:
    def yn(b: bool) -> str:
        return "예" if b else "아니오"

    # 현재 프로세스 상태가 아니라 '측정 당시' 기록을 그대로 보고한다.
    first = results[targets[0]]
    has_llm, is_integrated = first["llm_available"], first["integrated"]

    print("=" * 78)
    print(" 프롬프트 변형 A/B 실험")
    print("=" * 78)
    print(f"백엔드                : {first['backend']}")
    print(f"LLM 호출 가능          : {yn(has_llm)}")
    print(f"generate.py 연동 여부  : {yn(is_integrated)}  (generate.py 소스에서 prompts 사용 여부 확인)")
    print(f"→ 프롬프트가 생성에 반영: {yn(effective)}")
    print(f"비교 변형              : {', '.join(targets)}")

    print()
    print("[1] 프롬프트 정적 분석 — 백엔드와 무관하게 비교 가능")
    print("-" * 78)
    cols = [("길이", 6), ("줄수", 6), ("금지지시", 10), ("규제언급", 10),
            ("구조지시", 10), ("수준분기", 10), ("근거부족위치", 14)]
    print(_pad("변형", 16) + "".join(_pad(h, w, right=True) for h, w in cols))
    for n in targets:
        s = results[n]["static"]
        cells = [
            str(s["chars"]),
            str(s["lines"]),
            f"{s['prohibit_lines']}줄",
            f"{s['rule_mentions']}/{s['rule_total']}",
            "있음" if s["has_structure"] else "없음",
            f"{s['level_lines']}줄",
            "없음" if s["lack_pos"] is None else f"{s['lack_pos']:.2f}",
        ]
        print(_pad(n, 16) + "".join(_pad(c, w, right=True) for c, (_, w) in zip(cells, cols)))
    print("-" * 78)
    print("  금지지시     = '금지/마세요/절대' 등이 포함된 줄 수")
    print("  규제언급     = 컴플라이언스 규칙(C-01~C-08) 중 프롬프트가 대표어로 언급한 개수")
    print("  근거부족위치 = '근거가 부족/확인되지 않/모른다고' 지시가 처음 나오는 상대 위치(0=맨 앞)")
    print("  * 이 표는 프롬프트가 '무엇을 지시하는가'만 센 것이며, 출력 품질의 측정이 아니다.")
    unmapped = _unmapped_rules()
    if unmapped:
        print(f"  ! 대표어가 없어 규제언급 집계에서 빠진 규칙: {', '.join(unmapped)}"
              " — RULE_KEYWORDS 갱신 필요")

    print()
    print("[2] 파이프라인 지표 — 변형을 바꿔가며 src.evaluate 를 실제로 실행한 결과")
    print("-" * 78)
    print(_pad("변형", 16) + "".join(_pad(label, 12, right=True) for _, label, _ in METRIC_COLS))
    for n in targets:
        m = results[n]["metrics"]
        row = _pad(n, 16)
        for key, _, fmt in METRIC_COLS:
            row += _pad(fmt.format(m[key]), 12, right=True)
        print(row)
    print("-" * 78)

    print()
    print("[3] 해석")
    reasons = []
    if not has_llm:
        reasons.append(
            "rule 백엔드는 LLM 을 호출하지 않는다. generate.py 는 템플릿으로 답변을 만들므로\n"
            "      SYSTEM 프롬프트가 생성 경로에 전혀 개입하지 않는다."
        )
    if not is_integrated:
        reasons.append(
            "generate.py 가 아직 prompts.py 를 사용하지 않는다(자체 SYSTEM 상수 사용).\n"
            "      통합 전에는 PROMPT_VARIANT 를 바꿔도 생성 경로가 동일하다."
        )

    if reasons:
        print("  - [2] 표는 프롬프트 변형의 효과를 측정한 값이 아니다. 사유:")
        for r in reasons:
            print(f"    · {r}")
        if identical:
            print("  - 실제로 모든 변형의 지표가 동일하게 측정되었다 — 위 사유와 일치한다.")
        elif identical is False:
            diff = [
                label for i, (key, label, _) in enumerate(METRIC_COLS)
                if len({_metric_tuple(results[n])[i] for n in targets}) > 1
            ]
            print(f"  - 다만 지표가 달라진 항목이 있다: {', '.join(diff)}."
                  " 프롬프트 외의 비결정 요인을 확인해야 한다.")
        print("  - 결론: 이 구성에서 변형 간 차이는 '없음'이 아니라 '측정 불가'다.")
        print("  - 실제 A/B 를 하려면 두 조건을 모두 갖춘 뒤 재실행한다.")
        print("      (1) generate.py 에서 from ..prompts import get 로 SYSTEM 을 교체")
        print("      (2) LLM_BACKEND=local (또는 openai) 로 실행")
    else:
        print("  - 프롬프트가 생성에 반영되는 구성이다. [2] 표의 차이는 변형의 효과로 해석할 수 있다.")
        if identical:
            print("  - 다만 이번 실행에서는 모든 변형의 지표가 동일하게 나왔다.")
        print("  - 단일 실행 결과이므로, 채택 판단 전에 반복 실행으로 분산을 확인할 것.")

    print()
    print("[변형 의도]")
    for n in targets:
        print(f"  {n:16s} {results[n]['note']}")
    print("=" * 78)


if __name__ == "__main__":
    compare()
