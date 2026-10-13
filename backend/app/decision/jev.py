"""TypeSafe 原生判断接口；错误不带响应正文，避免上游回显制度或密钥。"""

import math

import httpx

from app.crypto import decrypt
from app.decision.base import DecisionRequest, DecisionResponse
from app.llm.models import LLMProviderConfig
from app.llm.providers.base import ProviderError, classify
from app.llm.providers.factory import DEFAULT_BASE_URLS


class JevProvider:
    def __init__(self, config: LLMProviderConfig) -> None:
        self.base_url = (config.base_url or DEFAULT_BASE_URLS[config.kind]).rstrip("/")
        self.api_key = decrypt(config.api_key_encrypted)

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        payload = {
            "model": request.model,
            "state": request.state,
            "questions": {
                key: {"type": "noul", "instructions": instruction}
                for key, instruction in request.questions.items()
            },
        }
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
                response = await client.post(
                    f"{self.base_url}/systemone", json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.HTTPError as exc:
            raise ProviderError("decision_transport_error", retryable=True) from exc
        if response.status_code != 200:
            raise ProviderError(
                f"decision_http_{response.status_code}",
                retryable=classify(response.status_code),
            )
        try:
            data = response.json()
            answers = data["answers"]
            if not isinstance(answers, dict) or set(answers) != set(request.questions):
                raise ValueError("answer_ids")
            probabilities = {}
            for key, answer in answers.items():
                value = answer["noul"]
                if (answer["type"] != "noul" or type(value) not in (float, int)
                        or not math.isfinite(value) or not 0 <= value <= 1):
                    raise ValueError("probability")
                probabilities[key] = float(value)
            usage = data["usage"]
            for field in ("input_tokens", "output_tokens"):
                if type(usage[field]) is not int or usage[field] < 0:
                    raise ValueError("usage")
            if not isinstance(data["model"], str) or not data["model"] or len(data["model"]) > 200:
                raise ValueError("model")
            return DecisionResponse(
                probabilities, data["model"], usage["input_tokens"], usage["output_tokens"],
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ProviderError("decision_invalid_response", retryable=False) from exc
