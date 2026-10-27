from __future__ import annotations

import httpx

from .config import Settings


class ProviderError(RuntimeError):
    """Safe error codes only; authenticated objects/provider bodies never escape."""


class DeepSeekProvider:
    def __init__(self, settings: Settings):
        if not settings.api_key:
            raise ProviderError("missing_deepseek_api_key")
        self.settings = settings
        self.model = settings.model
        self.client = httpx.Client(
            base_url=settings.base_url + "/",
            timeout=settings.timeout,
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )

    def request(self, route: str, body: dict | None = None) -> dict:
        try:
            r = self.client.get(route) if body is None else self.client.post(route, json=body)
            if r.status_code != 200:
                raise ProviderError(f"deepseek_http_{r.status_code}")
            value = r.json()
            if not isinstance(value, dict):
                raise ProviderError("deepseek_invalid_json")
            return value
        except ProviderError:
            raise
        except httpx.TimeoutException:
            raise ProviderError("deepseek_timeout") from None
        except (httpx.HTTPError, ValueError):
            raise ProviderError("deepseek_transport_or_json_error") from None

    def discover(self) -> list[str]:
        values = self.request("models").get("data", [])
        names = [
            item["id"]
            for item in values
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if not self.model:
            self.model = next((n for n in ("deepseek-flash", "deepseek-chat") if n in names), "")
        if not self.model or self.model not in names:
            raise ProviderError("set_DEEPSEEK_MODEL_to_a_supported_catalog_model")
        return names

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        if not self.model:
            self.discover()
        r = self.request(
            "chat/completions",
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "max_tokens": 12000,
                "stream": False,
            },
        )
        try:
            message = r["choices"][0]["message"]
            if message["role"] != "assistant":
                raise ProviderError("deepseek_invalid_role")
            clean = {"role": "assistant", "content": message.get("content")}
            if message.get("tool_calls"):
                clean["tool_calls"] = message["tool_calls"]
            return {"message": clean, "usage": r.get("usage", {})}
        except (KeyError, IndexError, TypeError):
            raise ProviderError("deepseek_invalid_response_schema") from None

    def close(self) -> None:
        self.client.close()
