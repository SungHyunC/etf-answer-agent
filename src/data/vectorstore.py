"""업무별로 분리된 Vector Store.

임베딩은 외부 API 대신 TF-IDF(문자 n-gram)를 사용한다.
  - 입찰 자격에 로컬 LLM 보유가 포함되므로, 고객 발화를 외부로 전송하지 않는 구성이 요건에 부합한다.
  - 한국어 형태소 분석기 없이도 오탈자·조사 변형에 강하도록 char_wb n-gram을 썼다.
운영 단계에서는 동일한 search() 인터페이스를 유지한 채 사내 임베딩 모델로 교체한다.
"""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .knowledge import STORES, STORE_LABEL


class Store:
    def __init__(self, name: str, docs: list[dict]):
        self.name = name
        self.label = STORE_LABEL.get(name, name)
        self.docs = docs
        self._corpus = [f"{d['title']} {d['text']}" for d in docs]
        self._vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
        self._matrix = self._vec.fit_transform(self._corpus)

    def search(self, query: str, k: int = 3, min_score: float = 0.0) -> list[dict]:
        qv = self._vec.transform([query])
        scores = cosine_similarity(qv, self._matrix)[0]
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in ranked[:k]:
            if scores[i] < min_score:
                continue
            d = dict(self.docs[i])
            d["score"] = round(float(scores[i]), 4)
            d["store"] = self.label
            out.append(d)
        return out


_STORES: dict[str, Store] = {name: Store(name, docs) for name, docs in STORES.items()}


def get(name: str) -> Store:
    return _STORES[name]


def search(store_name: str, query: str, k: int = 3, min_score: float = 0.0) -> list[dict]:
    return get(store_name).search(query, k=k, min_score=min_score)


def stats() -> dict[str, int]:
    return {s.label: len(s.docs) for s in _STORES.values()}
