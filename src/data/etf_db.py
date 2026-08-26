"""정형 ETF 데이터 — 요구사항의 '데이터베이스 연계'에 해당.

실제 사업에서는 발주사 ETF 원장 DB를 조회한다. 프로토타입은 동일한 인터페이스를
유지한 채 샘플 데이터로 대체했다. (수치는 데모용 예시값)
"""
from __future__ import annotations

ETFS = {
    "ACE 미국S&P500": {
        "ticker": "360200",
        "기초지수": "S&P 500 (원화환산)",
        "총보수": "0.07%",
        "유형": "해외주식형",
        "순자산": "1조 4,820억 원",
        "분배금주기": "분기",
        "상장일": "2020-08-07",
        "구성종목": ["Apple", "Microsoft", "NVIDIA", "Amazon", "Meta"],
    },
    "TIGER 미국나스닥100": {
        "ticker": "133690",
        "기초지수": "NASDAQ-100",
        "총보수": "0.07%",
        "유형": "해외주식형",
        "순자산": "3조 1,050억 원",
        "분배금주기": "분기",
        "상장일": "2010-10-18",
        "구성종목": ["NVIDIA", "Apple", "Microsoft", "Broadcom", "Tesla"],
    },
    "KODEX 200": {
        "ticker": "069500",
        "기초지수": "코스피200",
        "총보수": "0.15%",
        "유형": "국내주식형",
        "순자산": "6조 2,400억 원",
        "분배금주기": "분기",
        "상장일": "2002-10-14",
        "구성종목": ["삼성전자", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스", "현대차"],
    },
    "KODEX 미국채10년선물": {
        "ticker": "308620",
        "기초지수": "10Y US Treasury Note Futures",
        "총보수": "0.09%",
        "유형": "해외채권형",
        "순자산": "4,180억 원",
        "분배금주기": "없음",
        "상장일": "2018-10-19",
        "구성종목": ["US 10Y T-Note Futures"],
    },
    "TIGER 리츠부동산인프라": {
        "ticker": "329200",
        "기초지수": "FnGuide 리츠부동산인프라",
        "총보수": "0.29%",
        "유형": "국내리츠",
        "순자산": "3,260억 원",
        "분배금주기": "월",
        "상장일": "2019-07-19",
        "구성종목": ["맥쿼리인프라", "SK리츠", "ESR켄달스퀘어리츠", "롯데리츠"],
    },
}

# 별칭 → 정식 상품명
ALIASES = {
    "s&p500": "ACE 미국S&P500", "sp500": "ACE 미국S&P500", "에스앤피": "ACE 미국S&P500",
    "나스닥": "TIGER 미국나스닥100", "나스닥100": "TIGER 미국나스닥100", "qqq": "TIGER 미국나스닥100",
    "코스피200": "KODEX 200", "kodex200": "KODEX 200", "코덱스200": "KODEX 200",
    "미국채": "KODEX 미국채10년선물", "국채": "KODEX 미국채10년선물",
    "리츠": "TIGER 리츠부동산인프라", "부동산": "TIGER 리츠부동산인프라",
}


def resolve(text: str) -> list[str]:
    """발화에서 ETF 상품명을 식별한다 (엔티티 인식)."""
    low = text.lower().replace(" ", "")
    hits: list[str] = []
    for name in ETFS:
        if name.lower().replace(" ", "") in low:
            hits.append(name)
    for alias, name in ALIASES.items():
        if alias in low and name not in hits:
            hits.append(name)
    return hits


def lookup(name: str) -> dict | None:
    return ETFS.get(name)


def format_record(name: str) -> str:
    r = ETFS[name]
    return (
        f"[{name} ({r['ticker']})]\n"
        f"- 기초지수: {r['기초지수']}\n"
        f"- 유형: {r['유형']}\n"
        f"- 총보수: 연 {r['총보수']}\n"
        f"- 순자산: {r['순자산']}\n"
        f"- 분배금 주기: {r['분배금주기']}\n"
        f"- 상장일: {r['상장일']}\n"
        f"- 주요 구성종목: {', '.join(r['구성종목'])}"
    )
