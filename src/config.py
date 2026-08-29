"""모델 백엔드 설정 — RFP의 '로컬 LLM 보유' 요건을 고려해 백엔드를 교체 가능하게 분리."""
import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


class Config:
    # rule | openai | local
    BACKEND = _env("LLM_BACKEND", "rule").lower()

    OPENAI_API_KEY = _env("OPENAI_API_KEY")
    OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o-mini")

    LOCAL_BASE_URL = _env("LOCAL_BASE_URL", "http://localhost:11434/v1")
    LOCAL_MODEL = _env("LOCAL_MODEL", "qwen2.5:14b-instruct")
    LOCAL_API_KEY = _env("LOCAL_API_KEY", "not-needed")

    # LLM 호출 타임아웃(초) — 로컬 GPU 추론은 수십 초가 걸릴 수 있다.
    LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "120"))

    # 컴플라이언스 게이트 재생성 최대 횟수
    MAX_REGENERATE = 2
    # 검색 근거 채택 최소 유사도
    MIN_SIMILARITY = 0.08

    @classmethod
    def describe(cls) -> str:
        if cls.BACKEND == "openai":
            return f"openai / {cls.OPENAI_MODEL}"
        if cls.BACKEND == "local":
            return f"local(OpenAI 호환) / {cls.LOCAL_MODEL} @ {cls.LOCAL_BASE_URL}"
        return "rule (LLM 없이 규칙 기반 — 키 불필요)"
