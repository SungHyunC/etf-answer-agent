"""Streamlit Cloud 배포용 진입점.

    로컬:  streamlit run streamlit_app.py
    배포:  Streamlit Community Cloud 에서 이 저장소를 연결하고
           Main file path 를 streamlit_app.py 로 지정한다.

백엔드는 기본 rule(키 불필요)이며, Streamlit Secrets 에 OPENAI_API_KEY 를 넣으면
LLM 경로로 자동 전환된다. 클라우드에는 로컬 GPU 가 없으므로 local 백엔드는 쓰지 않는다.
"""
from __future__ import annotations

import os
import time

import streamlit as st

st.set_page_config(page_title="ETF Answer Agent", page_icon="📊", layout="centered")

# ── 백엔드 결정 (모듈 import 전에 환경변수를 세팅해야 Config 가 읽는다) ──
def _secret(key: str, default: str = "") -> str:
    """Secrets 가 아예 설정되지 않은 환경에서도 죽지 않게 감싼다.

    st.secrets 는 secrets.toml 이 한 개도 없으면 조회 자체가 예외를 던진다.
    로컬 실행과 Secrets 미설정 배포가 둘 다 이 경로를 탄다.
    """
    try:
        return str(st.secrets.get(key, default) or default).strip()
    except Exception:
        return default


_key = _secret("OPENAI_API_KEY")
if _key:
    os.environ["LLM_BACKEND"] = "openai"
    os.environ["OPENAI_API_KEY"] = _key
    os.environ["OPENAI_MODEL"] = _secret("OPENAI_MODEL", "gpt-4o-mini")
else:
    os.environ.setdefault("LLM_BACKEND", "rule")

from src import monitoring          # noqa: E402
from src.graph import ask, backend_info   # noqa: E402

INK, TEAL, ALERT = "#16212B", "#0F5C52", "#A32E22"

st.markdown(f"""<style>
.stApp {{ background:#F4F6F8; }}
.hdr {{ background:{INK}; color:#fff; padding:18px 22px; border-radius:10px; margin-bottom:6px; }}
.hdr h1 {{ margin:0; font-size:20px; }}
.hdr p {{ margin:4px 0 0; font-size:12px; color:#8FA8B4; }}
.pipe {{ font-size:12px; color:#5C7285; margin:10px 2px 16px; }}
.cite {{ display:inline-block; background:#E6F2EF; color:{TEAL}; border-radius:12px;
        padding:3px 10px; font-size:11.5px; margin:2px 4px 2px 0; }}
.blk {{ background:#FBF3F2; border-left:3px solid {ALERT}; padding:10px 14px;
       border-radius:6px; margin-bottom:8px; }}
.blk b {{ color:{ALERT}; }}
.rej {{ background:#FFF6E8; border-left:3px solid #A8801F; padding:8px 12px;
       border-radius:6px; font-size:12.5px; margin-bottom:8px; }}
.stepbox {{ background:#fff; border:1px solid #DCE2E6; border-radius:8px;
           padding:8px 10px; font-size:11.5px; height:100%; }}
.stepbox .t {{ font-weight:700; color:{INK}; }}
.stepbox .d {{ color:#63727F; }}
.bad {{ border-color:{ALERT}; }}
.foot {{ color:#7C8A94; font-size:11px; margin-top:22px; }}
</style>""", unsafe_allow_html=True)

st.markdown(f"""<div class="hdr">
  <h1>ETF Answer Agent</h1>
  <p>백엔드: {backend_info()} &nbsp;·&nbsp; 5단계 파이프라인 + 컴플라이언스 검증 게이트</p>
</div>""", unsafe_allow_html=True)

st.markdown('<div class="pipe">① 발화 전처리 → ② 의도 분류 → ③ 기능별 검색 → '
            '④ 답변 생성 → ⑤ 컴플라이언스 검증 &nbsp;|&nbsp; '
            '⑤에서 반려되면 ④로 되돌아가 재생성</div>', unsafe_allow_html=True)

EXAMPLES = [
    ("ETF가 뭔가요?", False),
    ("TIGER 미국나스닥100 총보스 얼마애요?", False),
    ("나스닥100 분배금 언제 지급되나요", False),
    ("ETF 세금은 어떻게 되나요", False),
    ("지금 어떤 ETF 사는게 좋을까요?", True),
    ("제 상황에 맞는 걸로 알려주세요", True),
]

if "history" not in st.session_state:
    st.session_state.history = []
if "pending" not in st.session_state:
    st.session_state.pending = None

st.caption("예시 질문 — ⛔ 는 규제 차단 대상입니다")
cols = st.columns(3)
for i, (q, blocked) in enumerate(EXAMPLES):
    label = ("⛔ " if blocked else "") + (q[:20] + "…" if len(q) > 20 else q)
    if cols[i % 3].button(label, key=f"ex{i}", use_container_width=True):
        st.session_state.pending = q

typed = st.chat_input("ETF에 대해 물어보세요")
if typed:
    st.session_state.pending = typed


def render_turn(r: dict, latency: float) -> None:
    """한 턴의 답변을 그린다. 새 질문과 히스토리 재생에 같은 함수를 쓴다."""
    banners = []
    if r.get("intent") == "out_of_scope":
        banners.append('<div class="blk"><b>⛔ 규제 차단 — 범위 외 요청</b><br>'
                       '투자 권유·종목 추천·수익률 전망으로 해석될 수 있어 '
                       '답변하지 않고 정중히 거절했습니다.</div>')
    if r.get("regenerate_count", 0) > 0:
        banners.append(f'<div class="rej">⟳ 컴플라이언스 반려 {r["regenerate_count"]}회 — '
                       f'1차 생성 답변은 고객에게 노출되지 않았습니다. '
                       f'{", ".join(r.get("violations", []))}</div>')
    if banners:
        st.markdown("".join(banners), unsafe_allow_html=True)

    st.write(r.get("answer", ""))

    if r.get("citations"):
        st.markdown("근거 " + "".join(f'<span class="cite">{c}</span>'
                                      for c in r["citations"]), unsafe_allow_html=True)

    st.caption(f"의도 {r.get('intent', '')} · 응답 {latency:.0f}ms")
    if r.get("trace"):
        with st.expander("처리 경로 5단계"):
            for t in r["trace"]:
                cls = "stepbox bad" if "반려" in t else "stepbox"
                st.markdown(f'<div class="{cls}">{t}</div>', unsafe_allow_html=True)


for turn in st.session_state.history:
    with st.chat_message("user"):
        st.write(turn["q"])
    with st.chat_message("assistant"):
        render_turn(turn["r"], turn["latency"])

if st.session_state.pending:
    q = st.session_state.pending
    st.session_state.pending = None
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant"):
        with st.spinner("5단계 파이프라인 처리 중…"):
            t0 = time.perf_counter()
            try:
                r = ask(q)
            except Exception as e:                      # 한 건의 오류로 서비스가 죽지 않게
                st.error(f"일시적인 오류가 발생했습니다. ({type(e).__name__})")
                st.stop()
            latency = (time.perf_counter() - t0) * 1000

        monitoring.record({
            "question": q, "intent": r.get("intent"), "verdict": r.get("verdict"),
            "latency_ms": latency, "regenerate_count": r.get("regenerate_count", 0),
            "n_evidence": len(r.get("evidence", [])) + len(r.get("db_records", [])),
            "store_used": r.get("store_used"),
            "blocked": r.get("intent") == "out_of_scope" or r.get("verdict") == "reject",
        })
        render_turn(r, latency)
        st.session_state.history.append({"q": q, "r": r, "latency": latency})

# 사이드바는 이번 턴의 record() 이후에 그려야 지표가 한 턴 밀리지 않는다.
with st.sidebar:
    st.subheader("운영 지표")
    snap = monitoring.snapshot()
    c1, c2 = st.columns(2)
    c1.metric("총 요청", snap.get("total_requests", 0))
    c2.metric("차단율", f"{snap.get('block_rate', 0) * 100:.0f}%")
    c1.metric("p95 지연", f"{snap.get('latency_p95_ms', 0):.0f}ms")
    c2.metric("재생성률", f"{snap.get('regenerate_rate', 0) * 100:.0f}%")
    if snap.get("intent_distribution"):
        st.caption("의도 분포")
        st.json(snap["intent_distribution"], expanded=False)
    st.divider()
    st.caption(
        "**규제 차단이 이 서비스의 핵심입니다.** 투자 권유·종목 추천·수익률 전망으로 "
        "해석될 수 있는 요청은 ⑤ 게이트에서 차단되며, 검증을 통과하지 못한 답변은 "
        "고객에게 나가지 않습니다."
    )

st.markdown('<div class="foot">⚠️ ETF 데이터와 문서는 <b>데모용 샘플</b>이며 실제 상품 정보가 '
            '아닙니다. 투자 판단의 근거로 사용할 수 없습니다. &nbsp;·&nbsp; '
            '<a href="https://github.com/SungHyunC/etf-answer-agent">GitHub 저장소</a></div>',
            unsafe_allow_html=True)
