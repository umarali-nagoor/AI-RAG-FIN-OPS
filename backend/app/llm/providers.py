"""Provider abstraction for chat completion and embeddings.

Providers: offline (no network, deterministic), openai, azure_openai, watsonx.

Security:
  * API keys come only from settings (SecretStr) and are never logged.
  * All outbound calls use HTTPS with certificate validation and explicit timeouts.
  * Retrieved repository content is untrusted: it is always passed as delimited *data*, never as instructions.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from abc import ABC, abstractmethod

import httpx

from app.core.config import Settings

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Raised when an upstream LLM/embedding call fails. Message is safe to log (no secrets)."""


# ---------------------------------------------------------------------------------------------
# Chat / completion
# ---------------------------------------------------------------------------------------------
class LLMProvider(ABC):
    name: str = "base"
    available: bool = True

    @abstractmethod
    def complete(self, system: str, user: str) -> str: ...


class OfflineLLM(LLMProvider):
    """No model. Services fall back to their deterministic, evidence-based report templates."""

    name = "offline"
    available = False

    def complete(self, system: str, user: str) -> str:
        raise ProviderError("offline provider has no generative model")


class OpenAILLM(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        from openai import OpenAI

        if not settings.openai_api_key:
            raise ValueError("FINOPS_OPENAI_API_KEY is required for the openai provider")
        self._client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(), timeout=settings.llm_timeout_s, max_retries=2
        )
        self._model = settings.openai_model
        self._max_tokens = settings.llm_max_tokens
        self._temperature = settings.llm_temperature

    def complete(self, system: str, user: str) -> str:
        try:
            r = self._client.chat.completions.create(
                model=self._model,
                temperature=self._temperature,
                max_completion_tokens=self._max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001 - provider SDKs raise many types
            raise ProviderError(f"openai call failed: {type(exc).__name__}") from exc
        return (r.choices[0].message.content or "").strip()


class AzureOpenAILLM(LLMProvider):
    name = "azure_openai"

    def __init__(self, settings: Settings):
        from openai import AzureOpenAI

        if not (settings.azure_openai_endpoint and settings.azure_openai_api_key and settings.azure_openai_deployment):
            raise ValueError("Azure OpenAI endpoint, api key and deployment are required")
        if not settings.azure_openai_endpoint.startswith("https://"):
            raise ValueError("Azure OpenAI endpoint must be https")
        self._client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
            timeout=settings.llm_timeout_s,
            max_retries=2,
        )
        self._deployment = settings.azure_openai_deployment
        self._max_tokens = settings.llm_max_tokens
        self._temperature = settings.llm_temperature

    def complete(self, system: str, user: str) -> str:
        try:
            r = self._client.chat.completions.create(
                model=self._deployment,
                temperature=self._temperature,
                max_completion_tokens=self._max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"azure_openai call failed: {type(exc).__name__}") from exc
        return (r.choices[0].message.content or "").strip()


class _IAMToken:
    """IBM Cloud IAM bearer token, cached until shortly before expiry."""

    def __init__(self, settings: Settings):
        if not settings.watsonx_api_key:
            raise ValueError("FINOPS_WATSONX_API_KEY is required for the watsonx provider")
        if not settings.iam_url.startswith("https://"):
            raise ValueError("IAM url must be https")
        self._settings = settings
        self._token: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def get(self, client: httpx.Client) -> str:
        with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            try:
                r = client.post(
                    self._settings.iam_url,
                    data={
                        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                        "apikey": self._settings.watsonx_api_key.get_secret_value(),  # type: ignore[union-attr]
                    },
                    headers={"Accept": "application/json"},
                )
                r.raise_for_status()
                body = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"IAM token request failed: {type(exc).__name__}") from exc
            self._token = body["access_token"]
            self._expires = time.time() + float(body.get("expires_in", 3000))
            return self._token  # type: ignore[return-value]


class WatsonxLLM(LLMProvider):
    """watsonx.ai chat API: POST {url}/ml/v1/text/chat?version=... (bearer token from IBM Cloud IAM)."""

    name = "watsonx"

    def __init__(self, settings: Settings):
        if not settings.watsonx_project_id:
            raise ValueError("FINOPS_WATSONX_PROJECT_ID is required for the watsonx provider")
        if not settings.watsonx_url.startswith("https://"):
            raise ValueError("watsonx url must be https")
        self._s = settings
        self._http = httpx.Client(timeout=settings.llm_timeout_s, verify=True)
        self._iam = _IAMToken(settings)

    def complete(self, system: str, user: str) -> str:
        s = self._s
        payload = {
            "model_id": s.watsonx_model_id,
            "project_id": s.watsonx_project_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": s.llm_max_tokens,
            "temperature": s.llm_temperature,
        }
        try:
            r = self._http.post(
                f"{s.watsonx_url.rstrip('/')}/ml/v1/text/chat",
                params={"version": s.watsonx_api_version},
                json=payload,
                headers={"Authorization": f"Bearer {self._iam.get(self._http)}", "Accept": "application/json"},
            )
            r.raise_for_status()
            return (r.json()["choices"][0]["message"]["content"] or "").strip()
        except ProviderError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"watsonx chat failed: {type(exc).__name__}") from exc


def build_llm(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case "openai":
            return OpenAILLM(settings)
        case "azure_openai":
            return AzureOpenAILLM(settings)
        case "watsonx":
            return WatsonxLLM(settings)
        case _:
            return OfflineLLM()


# ---------------------------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------------------------
class Embedder(ABC):
    name: str = "base"
    dim: int = 0

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    @property
    def signature(self) -> str:
        return f"{self.name}-{self.dim}"


_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]+|\d+")
_SPLIT = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_STOP = {"the", "a", "an", "of", "to", "in", "is", "and", "or", "for", "on", "with", "this", "that", "it", "as", "by"}


class LocalHashingEmbedder(Embedder):
    """Deterministic feature-hashing embedder (code-aware tokenisation, unigrams + bigrams, L2-normalised).

    Needs no network or model download. Good enough for lexical-semantic retrieval over logging code and
    docs; configure watsonx/openai/azure embeddings for stronger semantic recall.
    """

    name = "local-hash"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        toks: list[str] = []
        for raw in _TOKEN.findall(text):
            parts = _SPLIT.findall(raw)
            low = raw.lower()
            if low not in _STOP:
                toks.append(low)
            if len(parts) > 1:
                toks.extend(p.lower() for p in parts if p.lower() not in _STOP and len(p) > 1)
        feats = list(toks)
        feats.extend(f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False))
        return feats

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for f in self._features(text):
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class OpenAIEmbedder(Embedder):
    def __init__(self, settings: Settings, azure: bool = False):
        from openai import AzureOpenAI, OpenAI

        if azure:
            if not (
                settings.azure_openai_endpoint
                and settings.azure_openai_api_key
                and settings.azure_openai_embedding_deployment
            ):
                raise ValueError("Azure OpenAI endpoint, api key and embedding deployment are required")
            self._client = AzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key.get_secret_value(),
                api_version=settings.azure_openai_api_version,
                timeout=settings.llm_timeout_s,
                max_retries=2,
            )
            self._model = settings.azure_openai_embedding_deployment
            self.name = "azure-openai"
        else:
            if not settings.openai_api_key:
                raise ValueError("FINOPS_OPENAI_API_KEY is required for openai embeddings")
            self._client = OpenAI(
                api_key=settings.openai_api_key.get_secret_value(), timeout=settings.llm_timeout_s, max_retries=2
            )
            self._model = settings.openai_embedding_model
            self.name = "openai"
        self.dim = len(self.embed_documents(["dimension probe"])[0])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), 64):
                r = self._client.embeddings.create(model=self._model, input=texts[i : i + 64])
                out.extend(d.embedding for d in r.data)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"{self.name} embeddings failed: {type(exc).__name__}") from exc
        return out


class WatsonxEmbedder(Embedder):
    """watsonx.ai embeddings: POST {url}/ml/v1/text/embeddings?version=... (up to 1000 inputs per call)."""

    name = "watsonx"

    def __init__(self, settings: Settings):
        if not settings.watsonx_project_id:
            raise ValueError("FINOPS_WATSONX_PROJECT_ID is required for watsonx embeddings")
        if not settings.watsonx_url.startswith("https://"):
            raise ValueError("watsonx url must be https")
        self._s = settings
        self._http = httpx.Client(timeout=settings.llm_timeout_s, verify=True)
        self._iam = _IAMToken(settings)
        self.dim = len(self.embed_documents(["dimension probe"])[0])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        s = self._s
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), 100):
                r = self._http.post(
                    f"{s.watsonx_url.rstrip('/')}/ml/v1/text/embeddings",
                    params={"version": s.watsonx_api_version},
                    json={
                        "inputs": texts[i : i + 100],
                        "model_id": s.watsonx_embedding_model_id,
                        "project_id": s.watsonx_project_id,
                        "parameters": {"truncate_input_tokens": 512},
                    },
                    headers={"Authorization": f"Bearer {self._iam.get(self._http)}", "Accept": "application/json"},
                )
                r.raise_for_status()
                out.extend(item["embedding"] for item in r.json()["results"])
        except ProviderError:
            raise
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ProviderError(f"watsonx embeddings failed: {type(exc).__name__}") from exc
        return out


def build_embedder(settings: Settings) -> Embedder:
    match settings.embedding_provider:
        case "openai":
            return OpenAIEmbedder(settings)
        case "azure_openai":
            return OpenAIEmbedder(settings, azure=True)
        case "watsonx":
            return WatsonxEmbedder(settings)
        case _:
            return LocalHashingEmbedder()
