"""RAGAS / RAGChecker 스타일 RAG 품질 평가 — 외부 API 없이 로컬로 측정한다.

evaluate.py 가 '답변이 정답과 얼마나 닮았는가'(end-to-end)를 본다면,
이 모듈은 '검색-생성 파이프라인의 어디가 약한가'를 분해해서 본다.

  faithfulness       답변 클레임 중 검색 근거가 뒷받침하는 비율   (생성 단계 환각)
  answer_relevancy   답변이 질문에 실제로 답하고 있는가            (생성 단계 동문서답)
  context_precision  검색 근거 중 답변에 실제로 쓰인 비율          (검색 노이즈)
  context_recall     정답에 담긴 내용이 검색 근거에 있는 비율      (검색 누락)
  claim_recall       정답 클레임 중 답변이 담아낸 비율            (RAGChecker)
  hallucination_rate 근거에도 정답에도 없는 클레임 비율           (RAGChecker)

채점 경로는 둘이며 어느 쪽이든 단독으로 동작한다.
  LLM judge : llm.available() 이면 클레임 단위 entailment 판정을 LLM 에게 맡긴다.
  어휘 기반 : rule 백엔드이거나 LLM 호출이 실패하면 폴백한다.
              한국어 형태소 분석기 없이 동작해야 하므로 vectorstore.py 와 같은
              문자 n-gram 계열 신호를 쓴다. 구체적으로 문자 바이그램의
              IDF 가중 커버리지와 TF-IDF(char_wb) 코사인을 섞는다.

RAGChecker 의 claim-level entailment 개념을 따라, 답변과 정답을 모두 문장(클레임)
단위로 분해한 뒤 개별 클레임의 뒷받침 여부를 판정하는 것이 두 경로의 공통 뼈대다.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .. import llm
from ..config import Config
from ..graph import ask, backend_info

CASES_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "tests" / "ragas_cases.json"

# 어휘 기반 판정 임계값 — 실측 분포를 보고 조정한 값이다.
SUPPORT_THRESHOLD = 0.55   # 클레임이 근거에 의해 뒷받침된다고 볼 최소 점수
USE_THRESHOLD = 0.50       # 개별 근거 문서가 답변에 '쓰였다'고 볼 최소 점수
COSINE_FULL = 0.50         # 문장 대 문장 코사인의 실질 상한 (정규화 기준)
REL_COSINE_FULL = 0.30     # 짧은 질문 대 긴 답변 코사인의 실질 상한 (분모가 다르다)

# 파이프라인이 내보내는 정형 회피 문구 — RAGAS 는 이런 답변의 relevancy 를 0 으로 본다.
NONCOMMITTAL_MARKS = ("범위를 벗어납니다", "찾지 못했습니다", "안내를 드리기 어렵습니다")
REFUSAL_MARK = "범위를 벗어납니다"

_LIST_MARK = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+", re.M)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
# 사실 주장이 아닌 인사·안내 문구는 클레임에서 제외한다(채점 대상은 사실 진술뿐).
_BOILERPLATE = re.compile(r"(궁금하신|말씀해\s*주세요|도움이\s*되셨|무엇을\s*도와)")

_TOKEN_RE = re.compile(r"[가-힣]+|[A-Za-z]+|\d[\d,]*(?:\.\d+)?")
_HANGUL_RE = re.compile(r"^[가-힣]+$")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 질문의 의문형 꼬리 — 답변이 그대로 되풀이할 이유가 없는 부분이라 비교 대상에서 뺀다.
_QUESTION_TAIL = re.compile(
    r"\s*(알려\s*주세요|알려줘|설명해\s*주세요|말씀해\s*주세요|"
    r"어떻게\s*되나요|어떻게\s*하나요|얼마나\s*되나요|어떻게\s*되죠|"
    r"뭐가\s*다른가요|뭐가\s*달라요|무엇인가요|무엇인지|뭔가요|뭐예요|뭐야|"
    r"어떻게|얼마나|언제|어디|누가|왜|"
    r"되나요|하나요|있나요|있었나요|인가요|다른가요|가요|나요|까요|해요|요)?[\?？!.\s]*$"
)
# 문장 중간에 오는 의문사도 같은 이유로 제거한다.
_QUESTION_WORDS = re.compile(r"(어떻게|어떤|얼마나|얼마|언제|어디|무엇|왜|몇)")

JUDGE_SYSTEM = """당신은 RAG 시스템의 평가자입니다.
[기준 텍스트]만을 근거로 [판정 대상]의 각 항목을 판정하고, 아래 형식으로만 출력하세요.

1: 예
2: 아니오

설명이나 다른 문장은 절대 출력하지 마세요. 항목 수와 출력 줄 수는 반드시 같아야 합니다."""

ENTAIL_INSTR = "각 문장이 기준 텍스트의 내용으로 뒷받침되면 '예', 기준 텍스트에 없는 내용이면 '아니오'로 판정하세요."
USED_INSTR = "각 근거 자료가 기준 텍스트(답변)를 작성하는 데 실제로 사용되었으면 '예', 답변과 무관한 노이즈면 '아니오'로 판정하세요."

RELEVANCY_SYSTEM = """당신은 RAG 시스템의 평가자입니다.
답변이 질문에 실제로 답하고 있는지 0.0 ~ 1.0 사이 숫자 하나로만 채점하세요.

1.0 질문에 직접적이고 완결되게 답함
0.5 관련은 있으나 부분적이거나 초점이 어긋남
0.0 질문에 답하지 않음(회피·동문서답)

숫자 외에는 아무것도 출력하지 마세요."""


# ── 텍스트 분해 ────────────────────────────────────────────────────────────
def _sentences(text: str) -> list[str]:
    """문장 분해. '0.07%' 처럼 숫자 안의 마침표는 문장 끝으로 보지 않는다."""
    if not text:
        return []
    body = _LIST_MARK.sub("", text.strip())
    out = []
    for chunk in _SENT_SPLIT.split(body):
        s = chunk.strip()
        if len(s.replace(" ", "")) >= 6:
            out.append(s)
    return out


def split_claims(text: str) -> list[str]:
    """답변/정답을 클레임(사실 진술 문장) 단위로 분해한다.

    RAGChecker 는 LLM 으로 클레임을 추출하지만, 본 구성은 외부 호출 없이
    문장 분해 + 인사말 제거로 근사한다(한 문장 = 한 클레임).
    """
    return [s for s in _sentences(text) if not _BOILERPLATE.search(s)]


def _terms(text: str) -> list[str]:
    """비교용 term 목록. 형태소 분석기 없이 쓰므로 한글은 문자 바이그램으로 쪼갠다.

    세 글자 이상 어절은 마지막 바이그램을 버린다. '세금은→세금/금은' 처럼 조사에
    걸친 바이그램이 생겨 같은 단어가 서로 다른 term 으로 갈리는 것을 막기 위함이다.
    한 글자 어절(대부분 조사·의존명사)은 변별력이 없어 제외한다.
    """
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text or ""):
        if _HANGUL_RE.match(tok):
            if len(tok) == 1:
                continue
            grams = [tok[i:i + 2] for i in range(len(tok) - 1)]
            out.extend(grams[:-1] if len(tok) >= 3 else grams)
        else:
            out.append(tok.replace(",", "").lower())
    return out


def _question_focus(question: str) -> str:
    """질문에서 의문형 꼬리를 걷어내고 실제로 묻는 대상만 남긴다."""
    s = _QUESTION_WORDS.sub(" ", (question or "")).strip()
    for _ in range(3):
        nxt = _QUESTION_TAIL.sub("", s).strip()
        if nxt == s or not nxt:
            break
        s = nxt
    return s or (question or "").strip()


def _numbers(text: str) -> set[str]:
    """수치 주장 대조용. 한 자리 숫자는 변별력이 없어 제외한다."""
    got = set()
    for m in _NUM_RE.finditer(text or ""):
        v = m.group(0).replace(",", "")
        if len(v) >= 2:
            got.add(v)
    return got


# ── 어휘 기반 신호 ─────────────────────────────────────────────────────────
def _idf(docs: list[str]) -> dict[str, float]:
    """케이스 안의 문장들을 배경 코퍼스로 삼아 term 별 IDF 를 만든다.
    '니다', '습니' 같은 한국어 어미 바이그램의 가중치를 자동으로 낮추기 위함이다."""
    n = max(len(docs), 1)
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(_terms(d)))
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def _coverage(src: str, tgt_terms: set[str], idf: dict[str, float]) -> float:
    """src 의 term 중 tgt 에 존재하는 비율 (IDF 가중 재현율)."""
    st = set(_terms(src))
    if not st:
        return 0.0
    total = sum(idf.get(t, 1.0) for t in st)
    hit = sum(idf.get(t, 1.0) for t in st if t in tgt_terms)
    return hit / total if total else 0.0


def _max_cosine(text: str, candidates: list[str]) -> float:
    """text 와 후보 문장들 사이 TF-IDF(char_wb) 코사인 최댓값."""
    cands = [c for c in candidates if c.strip()]
    if not text.strip() or not cands:
        return 0.0
    try:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        m = vec.fit_transform([text] + cands)
    except ValueError:      # 어휘가 비면 비교 불가
        return 0.0
    return float(cosine_similarity(m[0], m[1:]).max())


def _support_score(claim: str, terms: set[str], sents: list[str],
                   nums: set[str], idf: dict[str, float]) -> float:
    """클레임이 대상 텍스트에 의해 뒷받침되는 정도 (0~1).

    커버리지(내용어가 대상에 있는가)를 주 신호로, 코사인(문장 전체가 닮았는가)을
    보조 신호로 쓴다. 근거에 없는 수치가 있으면 환각 신호로 보고 상한을 건다.
    """
    cov = _coverage(claim, terms, idf)
    cos = min(_max_cosine(claim, sents) / COSINE_FULL, 1.0)
    score = 0.65 * cov + 0.35 * cos
    unmatched = _numbers(claim) - nums
    if unmatched:
        score = min(score, 0.35)
    return score


def _profile(texts: list[str]) -> tuple[set[str], list[str], set[str]]:
    """비교 대상(근거 묶음 또는 답변)을 term/문장/수치로 미리 풀어 둔다."""
    terms: set[str] = set()
    sents: list[str] = []
    nums: set[str] = set()
    for t in texts:
        if not t:
            continue
        terms |= set(_terms(t))
        sents.extend(_sentences(t) or [t])
        nums |= _numbers(t)
    return terms, sents, nums


def _is_noncommittal(answer: str) -> bool:
    return any(m in (answer or "") for m in NONCOMMITTAL_MARKS)


# ── LLM judge 경로 (실패 시 None 을 돌려 어휘 기반으로 폴백) ────────────────
def _llm_binary(items: list[str], reference_text: str, instruction: str) -> list[bool] | None:
    if not items or not llm.available():
        return None
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(items))
    user = (f"[기준 텍스트]\n{reference_text.strip()}\n\n"
            f"[판정 대상]\n{numbered}\n\n{instruction}")
    try:
        raw = llm.complete(JUDGE_SYSTEM, user, temperature=0.0, max_tokens=16 * len(items) + 64)
    except Exception:
        return None
    verdicts: list[bool] = []
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+)\s*[:.)]\s*(.+)", line)
        if m:
            verdicts.append("예" in m.group(2) and "아니" not in m.group(2))
    return verdicts if len(verdicts) == len(items) else None


def _llm_relevancy(question: str, answer: str) -> float | None:
    if not llm.available() or not answer.strip():
        return None
    try:
        raw = llm.complete(RELEVANCY_SYSTEM, f"[질문]\n{question}\n\n[답변]\n{answer}",
                           temperature=0.0, max_tokens=8)
    except Exception:
        return None
    m = re.search(r"[01](?:\.\d+)?", raw)
    return min(max(float(m.group(0)), 0.0), 1.0) if m else None


# ── 케이스 단위 채점 ───────────────────────────────────────────────────────
def evaluate_case(question: str, answer: str, contexts: list[str],
                  reference: str | None = None) -> dict:
    """한 건의 (질문, 답변, 검색 근거, 정답)을 RAGAS 4대 지표 + RAGChecker 지표로 채점한다."""
    ctxs = [c for c in (contexts or []) if c and c.strip()]
    answer = answer or ""
    # 회피 응답(범위 외 거절·근거 없음)은 사실 주장을 담지 않는다.
    # 클레임 0건으로 두어 환각률이 부풀지 않게 하고, 적합성 0 으로 '답하지 않음'을 드러낸다.
    noncommittal = _is_noncommittal(answer) or not answer.strip()
    claims = [] if noncommittal else split_claims(answer)
    ref_claims = split_claims(reference) if reference else []

    # IDF 배경 코퍼스 — 케이스 안에 등장하는 모든 문장
    corpus = [question] + claims + ref_claims
    for c in ctxs:
        corpus.extend(_sentences(c) or [c])
    idf = _idf([c for c in corpus if c])

    ctx_terms, ctx_sents, ctx_nums = _profile(ctxs)
    ans_terms, ans_sents, ans_nums = _profile([answer])
    ref_terms, ref_sents, ref_nums = _profile([reference] if reference else [])

    used_llm = False

    # ① faithfulness — 답변 클레임이 검색 근거로 뒷받침되는가
    v = _llm_binary(claims, "\n\n".join(ctxs), ENTAIL_INSTR) if ctxs else None
    if v is not None:
        supported, used_llm = v, True
    else:
        supported = [_support_score(c, ctx_terms, ctx_sents, ctx_nums, idf) >= SUPPORT_THRESHOLD
                     for c in claims]
    n_claims = len(claims)
    n_supported = sum(supported)
    faithfulness = n_supported / n_claims if n_claims else 0.0

    # ② answer_relevancy — 답변이 질문에 답하고 있는가
    if noncommittal:
        answer_relevancy = 0.0      # 회피 응답은 RAGAS 정의상 0
    else:
        r = _llm_relevancy(question, answer)
        if r is not None:
            answer_relevancy, used_llm = r, True
        else:
            # 질문이 묻는 대상이 답변에 얼마나 담겼는가를 주 신호로 쓰고,
            # 문장 전체의 TF-IDF 코사인을 보조로 섞는다.
            cov = _coverage(_question_focus(question), ans_terms, idf)
            cos = min(_max_cosine(question, [answer]) / REL_COSINE_FULL, 1.0)
            answer_relevancy = min(0.75 * cov + 0.25 * cos, 1.0)

    # ③ context_precision — 검색된 근거 중 답변에 실제로 쓰인 비율
    if not ctxs:
        context_precision = 0.0
    else:
        v = _llm_binary([c[:600] for c in ctxs], answer, USED_INSTR) if answer.strip() else None
        if v is not None:
            hit_flags, used_llm = v, True
        else:
            hit_flags = []
            for c in ctxs:
                t, s, nm = _profile([c])
                hit_flags.append(
                    any(_support_score(cl, t, s, nm, idf) >= USE_THRESHOLD for cl in claims)
                )
        context_precision = sum(hit_flags) / len(ctxs)

    # ④ context_recall — 정답 내용이 검색 근거에 담겨 있는가
    if not ref_claims:
        context_recall = None
    else:
        v = _llm_binary(ref_claims, "\n\n".join(ctxs), ENTAIL_INSTR) if ctxs else None
        if v is not None:
            ref_in_ctx, used_llm = v, True
        else:
            ref_in_ctx = [_support_score(c, ctx_terms, ctx_sents, ctx_nums, idf) >= SUPPORT_THRESHOLD
                          for c in ref_claims]
        context_recall = sum(ref_in_ctx) / len(ref_claims)

    # ⑤ claim_recall (RAGChecker) — 정답 클레임 중 답변이 담아낸 비율
    if not ref_claims:
        claim_recall = None
    else:
        v = _llm_binary(ref_claims, answer, ENTAIL_INSTR) if answer.strip() else None
        if v is not None:
            ref_in_ans, used_llm = v, True
        else:
            ref_in_ans = [_support_score(c, ans_terms, ans_sents, ans_nums, idf) >= SUPPORT_THRESHOLD
                          for c in ref_claims]
        claim_recall = sum(ref_in_ans) / len(ref_claims)

    # ⑥ hallucination_rate (RAGChecker) — 근거에도 정답에도 없는 클레임
    #    faithfulness 의 여집합이 아니라, 정답으로 구제되는 클레임을 빼고 다시 센다.
    unsupported = [cl for cl, ok in zip(claims, supported) if not ok]
    if not n_claims:
        hallucination_rate = 0.0
    elif not unsupported:
        hallucination_rate = 0.0
    else:
        # 근거엔 없지만 정답과는 일치하는 클레임은 환각에서 제외한다.
        rescued: list[bool] = [False] * len(unsupported)
        if reference:
            v = _llm_binary(unsupported, reference, ENTAIL_INSTR)
            if v is not None:
                rescued, used_llm = v, True
            else:
                rescued = [_support_score(cl, ref_terms, ref_sents, ref_nums, idf) >= SUPPORT_THRESHOLD
                           for cl in unsupported]
        hallucination_rate = sum(1 for r in rescued if not r) / n_claims

    return {
        "faithfulness": round(faithfulness, 4),
        "answer_relevancy": round(answer_relevancy, 4),
        "context_precision": round(context_precision, 4),
        "context_recall": round(context_recall, 4) if context_recall is not None else None,
        "claim_recall": round(claim_recall, 4) if claim_recall is not None else None,
        "hallucination_rate": round(hallucination_rate, 4),
        "n_claims": n_claims,
        "supported_claims": int(n_supported),
        "backend": f"llm-judge ({Config.describe()})" if used_llm else "lexical (TF-IDF char n-gram)",
    }


# ── 스위트 실행 ────────────────────────────────────────────────────────────
METRICS = ["faithfulness", "answer_relevancy", "context_precision",
           "context_recall", "claim_recall", "hallucination_rate"]


def _contexts_of(state: dict) -> list[str]:
    """검색 근거 = 정형 DB 레코드 + 벡터 검색 문서 본문."""
    return list(state.get("db_records", [])) + [e["text"] for e in state.get("evidence", [])]


def run_suite(verbose: bool = True) -> dict:
    """tests/ragas_cases.json 전체를 실제 파이프라인(src.graph.ask)으로 돌려 채점한다."""
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    scored: list[dict] = []
    oos: list[dict] = []

    if verbose:
        print(f"백엔드: {backend_info()}")
        print(f"채점기: {'LLM judge' if llm.available() else '어휘 기반(TF-IDF char n-gram)'}")
        print(f"평가 케이스 {len(cases)}건 "
              f"(범위 외 {sum(1 for c in cases if c.get('out_of_scope'))}건은 지표에서 제외)\n")
        print(f"{'질문':24s} {'클레임':>6s} {'충실':>6s} {'적합':>6s} {'정밀':>6s} "
              f"{'재현':>6s} {'클레임R':>7s} {'환각':>6s}")
        print("-" * 78)

    for c in cases:
        state = ask(c["question"])
        answer = state.get("answer", "")

        if c.get("out_of_scope"):
            blocked = state.get("intent") == "out_of_scope" or REFUSAL_MARK in answer
            oos.append({"question": c["question"], "blocked": bool(blocked), "answer": answer})
            continue

        m = evaluate_case(c["question"], answer, _contexts_of(state), c.get("reference"))
        scored.append({"question": c["question"], "answer": answer,
                       "n_contexts": len(_contexts_of(state)), **m})
        if verbose:
            print(f"{c['question'][:22]:24s} {m['supported_claims']}/{m['n_claims']:<4d} "
                  f"{m['faithfulness']:6.2f} {m['answer_relevancy']:6.2f} "
                  f"{m['context_precision']:6.2f} "
                  f"{(m['context_recall'] if m['context_recall'] is not None else 0):6.2f} "
                  f"{(m['claim_recall'] if m['claim_recall'] is not None else 0):7.2f} "
                  f"{m['hallucination_rate']:6.2f}")

    means: dict[str, float] = {}
    for k in METRICS:
        vals = [s[k] for s in scored if s.get(k) is not None]
        means[k] = round(sum(vals) / len(vals), 4) if vals else 0.0

    result = {
        "backend": backend_info(),
        "judge": scored[0]["backend"] if scored else ("llm-judge" if llm.available() else "lexical"),
        "n_cases": len(cases),
        "n_scored": len(scored),
        "means": means,
        "cases": scored,
        "out_of_scope": {
            "total": len(oos),
            "blocked": sum(1 for o in oos if o["blocked"]),
            "cases": oos,
        },
    }

    if verbose:
        print("\n" + "=" * 78)
        print(f"RAGAS / RAGChecker 지표 평균 (채점 {len(scored)}건)")
        print("-" * 78)
        print(f"  faithfulness       충실성   {means['faithfulness']:6.3f}   답변 클레임 중 근거가 뒷받침한 비율")
        print(f"  answer_relevancy   적합성   {means['answer_relevancy']:6.3f}   답변이 질문에 답하고 있는 정도")
        print(f"  context_precision  정밀도   {means['context_precision']:6.3f}   검색 근거 중 답변에 쓰인 비율")
        print(f"  context_recall     재현율   {means['context_recall']:6.3f}   정답 내용이 근거에 포함된 비율")
        print(f"  claim_recall       클레임R  {means['claim_recall']:6.3f}   정답 클레임을 답변이 담아낸 비율")
        print(f"  hallucination_rate 환각률   {means['hallucination_rate']:6.3f}   근거·정답 어디에도 없는 클레임 비율")
        print("-" * 78)
        b = result["out_of_scope"]
        print(f"  범위 외 질문 차단   {b['blocked']}/{b['total']}건 (지표 계산 제외)")
        for o in b["cases"]:
            print(f"    {'차단' if o['blocked'] else '미차단 ✗'}  {o['question']}")
        print("=" * 78)
        print("  * 채점기가 'lexical' 이면 문자 바이그램 IDF 커버리지 + TF-IDF 코사인 기반 근사치다.")
        print("    LLM_BACKEND 를 openai/local 로 두면 동일 인터페이스로 LLM judge 가 채점한다.")
    return result


if __name__ == "__main__":
    run_suite()
