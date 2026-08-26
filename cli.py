"""CLI 데모 — python cli.py  (대화형)  |  python cli.py "질문"  (단발)"""
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
    print("=" * 70)
    print("  ETF Answer Agent — 프로토타입")
    print(f"  백엔드: {backend_info()}")
    print("=" * 70)

    if len(sys.argv) > 1:
        show(" ".join(sys.argv[1:]))
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
        show(q)

    print("\n[대화 모드] 종료하려면 빈 줄 또는 'exit'")
    while True:
        try:
            q = input("\n질문> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("exit", "quit"):
            break
        show(q)
    print("종료합니다.")


if __name__ == "__main__":
    main()
