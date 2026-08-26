"""LLM 어댑터.

입찰 자격에 로컬 LLM 보유가 포함되므로, 프로토타입도 외부 API에 종속되지 않도록
백엔드를 3종으로 분리했다.

  rule   : LLM 호출 없이 검색 근거를 템플릿으로 요약 (키 없이 즉시 실행 가능)
  openai : OpenAI API
  local  : 사내 폐쇄망 vLLM / Ollama 등 OpenAI 호환 엔드포인트 (운영 목표 구성)

openai 와 local 은 동일한 OpenAI 호환 인터페이스를 쓰므로,
운영 전환 시 base_url 과 model 만 바꾸면 된다.
"""
from __future__ import annotations

from .config import Config


class LLMUnavailable(RuntimeError):
    pass


def _client():
    from openai import OpenAI

    if Config.BACKEND == "openai":
        if not Config.OPENAI_API_KEY:
            raise LLMUnavailable("OPENAI_API_KEY 가 설정되지 않았습니다.")
        return OpenAI(api_key=Config.OPENAI_API_KEY), Config.OPENAI_MODEL
    return OpenAI(base_url=Config.LOCAL_BASE_URL, api_key=Config.LOCAL_API_KEY), Config.LOCAL_MODEL


def available() -> bool:
    return Config.BACKEND in ("openai", "local")


def complete(system: str, user: str, temperature: float = 0.2, max_tokens: int = 700) -> str:
    """단일 턴 생성. rule 백엔드에서는 호출되지 않는다."""
    if not available():
        raise LLMUnavailable("현재 백엔드는 'rule' 입니다.")
    client, model = _client()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()
