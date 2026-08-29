"""파이썬 단일 진실 공급원(src/) → docs/data.json 내보내기.

JS 데모가 파이썬 파이프라인과 똑같은 데이터·규칙으로 동작하도록,
src/ 안의 상수를 그대로 import 해서 직렬화한다.
값을 이 파일에 손으로 복사하지 않는 것이 핵심이다 — 원본은 언제나 src/ 하나뿐이고,
데이터가 바뀌면 이 스크립트를 다시 돌리기만 하면 된다.

실행:  python -m tools.export_data        (프로젝트 루트에서)
출력:  docs/data.json                     (ensure_ascii=False, indent=2)

최상위 키
  etfs, aliases, product_docs, faq_docs, disclosure_docs, store_label,
  typo_map, beginner_hints, expert_hints, classify_rules, definition_hints,
  compliance_rules, safe_terms, safe_fallback, refusal, no_evidence, route

  · classify_rules   = [[intent, [신호어, ...]], ...]   순서가 곧 우선순위(0번이 규제 경로)
  · compliance_rules = [[rule_id, 정규식, 사유], ...]   정규식은 JS(ECMAScript) 문법으로 변환
  · route            = {intent: [창고, ...]}            앞이 주 창고

생성 시각 같은 필드는 일부러 넣지 않는다. 소스가 같으면 결과 바이트도 같아야
재현성 확인(diff)이 가능하기 때문이다.
"""
from __future__ import annotations

import ast
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "docs" / "data.json"

# 최상위 키 순서 — JSON 에 이 순서대로 기록한다.
TOP_LEVEL_KEYS = [
    "etfs", "aliases", "product_docs", "faq_docs", "disclosure_docs", "store_label",
    "typo_map", "beginner_hints", "expert_hints", "classify_rules", "definition_hints",
    "compliance_rules", "safe_terms", "safe_fallback", "refusal", "no_evidence", "route",
]


# ────────────────────────────────────────────────────────────────
# 1. 소스에서 상수 읽기
# ────────────────────────────────────────────────────────────────
def _load_via_ast(module_name: str, names: tuple[str, ...]) -> dict[str, Any]:
    """import 가 막혔을 때의 폴백 — 같은 .py 를 AST 로 파싱해 최상위 리터럴만 꺼낸다.

    코드를 실행하지 않으므로 sklearn 같은 선택 의존성이 없어도 동작한다.
    어느 경로로 읽든 값의 출처는 동일한 파이썬 소스 한 곳이다.
    """
    path = ROOT / (module_name.replace(".", "/") + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, Any] = {}
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for t in targets:
            if t in names and node.value is not None:
                found[t] = ast.literal_eval(node.value)
    missing = [n for n in names if n not in found]
    if missing:
        raise RuntimeError(f"{module_name} 에서 {missing} 를 읽지 못했습니다.")
    return found


def load(module_name: str, *names: str) -> dict[str, Any]:
    """모듈을 import 해서 심볼을 가져온다(정상 경로). 실패하면 AST 폴백."""
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:  # 무거운 선택 의존성(sklearn 등) 미설치 상황
        print(f"  [경고] {module_name} import 실패 ({type(e).__name__}: {e}) → AST 폴백 사용")
        return _load_via_ast(module_name, names)
    return {n: getattr(mod, n) for n in names}


# ────────────────────────────────────────────────────────────────
# 2. 정규식: 파이썬 → JS(ECMAScript) 변환
# ────────────────────────────────────────────────────────────────
# (탐지 패턴, 치환, 설명) — JS 에 대응 문법이 있는 것은 자동 변환한다.
_REGEX_REWRITES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\(\?P<(\w+)>"), r"(?<\1>", "명명 그룹 (?P<name>…) → (?<name>…)"),
    (re.compile(r"\(\?P=(\w+)\)"), r"\\k<\1>", "명명 역참조 (?P=name) → \\k<name>"),
    (re.compile(r"\\A"), "^", r"문자열 시작 \A → ^"),
    (re.compile(r"\\Z"), "$", r"문자열 끝 \Z → $"),
]

# 대응 문법이 없거나 의미가 달라지는 것 — 변환하지 않고 경고만 출력한다.
_REGEX_WARNINGS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\(\?#"), "인라인 주석 (?#…) 은 JS 에 없음 — 수동 제거 필요"),
    (re.compile(r"\(\?[aiLmsux]+[:)]"),
     "인라인 플래그 (?i)·(?i:…) 는 JS 에 없음 — new RegExp 의 플래그 인자로 옮길 것"),
    (re.compile(r"\(\?>"), "원자 그룹 (?>…) 은 JS 에 없음"),
    (re.compile(r"\(\?\("), "조건부 그룹 (?(id)…) 은 JS 에 없음"),
    (re.compile(r"[*+?}]\+"), "소유 수량자(*+, ++ …)로 보임 — JS 에서 동작이 다름"),
    (re.compile(r"\\[wb]"), r"\w·\b 는 JS 에서 ASCII 기준 — 한글에 대해 파이썬과 다르게 동작"),
]


def to_js_regex(pattern: str, label: str, notes: list[str]) -> str:
    """파이썬 정규식 문자열을 JS 에서 쓸 수 있는 형태로 바꾼다(문자열 그대로 유지가 원칙)."""
    try:
        re.compile(pattern)
    except re.error as e:
        notes.append(f"{label}: 오류 — 파이썬에서도 컴파일 실패 ({e})")

    out = pattern
    for rx, repl, desc in _REGEX_REWRITES:
        if rx.search(out):
            out = rx.sub(repl, out)
            notes.append(f"{label}: 변환 — {desc}")
    for rx, msg in _REGEX_WARNINGS:
        if rx.search(out):
            notes.append(f"{label}: 경고 — {msg}")
    return out


# ────────────────────────────────────────────────────────────────
# 3. 조립
# ────────────────────────────────────────────────────────────────
def build() -> tuple[dict[str, Any], list[str]]:
    etf = load("src.data.etf_db", "ETFS", "ALIASES")
    kb = load("src.data.knowledge", "PRODUCT_DOCS", "FAQ_DOCS", "DISCLOSURE_DOCS", "STORE_LABEL")
    pre = load("src.nodes.preprocess", "TYPO_MAP", "BEGINNER_HINTS", "EXPERT_HINTS")
    cls = load("src.nodes.classify", "RULES", "DEFINITION_HINTS")
    comp = load("src.nodes.compliance", "RULES", "SAFE_TERMS", "SAFE_FALLBACK")
    gen = load("src.nodes.generate", "REFUSAL", "NO_EVIDENCE")
    ret = load("src.nodes.retrieve", "ROUTE")

    notes: list[str] = []
    compliance_rules = [
        [rid, to_js_regex(pattern, rid, notes), reason]
        for rid, pattern, reason in comp["RULES"]
    ]

    data: dict[str, Any] = {
        # 정형 ETF 데이터
        "etfs": etf["ETFS"],
        "aliases": etf["ALIASES"],
        # 비정형 지식 창고 3종
        "product_docs": kb["PRODUCT_DOCS"],
        "faq_docs": kb["FAQ_DOCS"],
        "disclosure_docs": kb["DISCLOSURE_DOCS"],
        "store_label": kb["STORE_LABEL"],
        # ① 발화 전처리
        "typo_map": pre["TYPO_MAP"],
        "beginner_hints": pre["BEGINNER_HINTS"],
        "expert_hints": pre["EXPERT_HINTS"],
        # ② 의도 분류 (리스트 순서 = 우선순위)
        "classify_rules": [[label, list(keys)] for label, keys in cls["RULES"]],
        "definition_hints": cls["DEFINITION_HINTS"],
        # ⑤ 컴플라이언스 게이트
        "compliance_rules": compliance_rules,
        "safe_terms": comp["SAFE_TERMS"],
        "safe_fallback": comp["SAFE_FALLBACK"],
        # ④ 답변 생성 — 준법 검토를 마친 정형 응답
        "refusal": gen["REFUSAL"],
        "no_evidence": gen["NO_EVIDENCE"],
        # ③ 기능별 검색 라우팅
        "route": ret["ROUTE"],
    }

    # 키 누락·오타·순서 어긋남 방지
    assert list(data) == TOP_LEVEL_KEYS, f"최상위 키 구성이 다릅니다: {list(data)}"
    return data, notes


def _describe(value: Any) -> str:
    """검증 출력용 — 값의 크기를 사람이 읽을 형태로."""
    if isinstance(value, dict):
        return f"{len(value)}개 항목"
    if isinstance(value, list):
        return f"{len(value)}건"
    if isinstance(value, str):
        return f"{len(value)}자"
    return type(value).__name__


def main() -> int:
    # 콘솔 인코딩이 cp949 여도 한글·기호가 깨지지 않게 한다.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"[1/3] src/ 에서 상수 로드 (루트: {ROOT})")
    data, notes = build()

    print("[2/3] 정규식 파이썬 → JS 변환 검사")
    if notes:
        for n in notes:
            print(f"  - {n}")
    else:
        print("  - 파이썬 전용 문법 없음 · 모든 정규식을 그대로 new RegExp() 에 사용 가능")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    OUT_PATH.write_text(text, encoding="utf-8")

    print(f"[3/3] 저장 완료 → {OUT_PATH}")
    size = OUT_PATH.stat().st_size
    print(f"  파일 크기: {size:,} bytes ({size / 1024:.1f} KB)")

    # 되읽어 검증 — 쓴 직후 실제로 파싱되는지, 키가 그대로인지 확인한다.
    loaded = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    assert list(loaded) == TOP_LEVEL_KEYS, "저장된 JSON 의 최상위 키가 다릅니다."
    print(f"  최상위 키 {len(loaded)}개:")
    for k in TOP_LEVEL_KEYS:
        print(f"    - {k:<17} {_describe(loaded[k])}")
    print("  검증: JSON 파싱 OK · 최상위 키 일치 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
