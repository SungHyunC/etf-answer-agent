"""CLI 데모.

    python cli.py                       대화형 (데모 시나리오 후 입력 대기)
    python cli.py "질문"                단발 실행
    python cli.py --no-trace "질문"     처리 경로 없이 답변만 (이해관계자 시연용)
"""
from __future__ import annotations

import sys

from src.graph import ask, backend_info


def show(q: str, trace: bool = True) -> None:
    r = ask(q)
    print(f"\n👤 {q}")
    if trace:
        print("   ┌─ 처리 경로")
        for t in r.get("trace", []):
            print(f"   │  {t}")
        print("   └─")
    print(f"\n🤖 {r.get('answer','')}")
    cits = r.get("citations", [])
    if cits:
        print("\n   근거: " + " / ".join(cits))
    print("-" * 70)


def main() -> None:
    args = sys.argv[1:]
    show_trace = True
    if "--no-trace" in args:            # 비개발 이해관계자에게 최종 답변만 보여줄 때
        show_trace = False
        args = [a for a in args if a != "--no-trace"]

    print("=" * 70)
    print("  ETF Answer Agent — 프로토타입")
    print(f"  백엔드: {backend_info()}")
    print("=" * 70)

    if args:
        show(" ".join(args), trace=show_trace)
        return

    demo = [
        "ETF가 뭔가요?",
        "TIGER 미국나스닥100 총보스 얼마애요?",
        "나스닥100 분배금 언제 지급되나요",
        "ETF 세금은 어떻게 되나요",
        "지금 어떤 ETF 사는게 좋을까요?",
    ]
    print("\n[데모 시나리오 실행]")
    for q in demo:
        show(q, trace=show_trace)

    print("\n[대화 모드] 종료하려면 빈 줄 또는 'exit'")
    while True:
        try:
            q = input("\n질문> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("exit", "quit"):
            break
        show(q, trace=show_trace)
    print("종료합니다.")


if __name__ == "__main__":
    main()
