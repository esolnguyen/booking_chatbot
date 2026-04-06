"""Unified LLM client — Azure OpenAI or Google Gemini.

Usage:
    from app.agents.llm import sync_chat, async_chat

    # Sync (response_verifier, chatbot main loop)
    text = sync_chat(messages, temperature=0.0)

    # Async (recommendation agent, verification agent)
    text = await async_chat(messages, temperature=0.1)

messages format (same as OpenAI):
    [
        {"role": "system",    "content": "..."},
        {"role": "user",      "content": "..."},
        {"role": "assistant", "content": "..."},
        ...
    ]

Provider is read from settings.llm_provider ("azure" or "google").
Pass provider= explicitly to override per-call.
"""

import logging
from typing import Optional

from openai import AsyncAzureOpenAI, AzureOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────
_azure_sync_client:  AzureOpenAI      | None = None
_azure_async_client: AsyncAzureOpenAI | None = None
_google_client_instance = None


def _get_azure_sync() -> AzureOpenAI:
    global _azure_sync_client
    if _azure_sync_client is None:
        _azure_sync_client = AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )
    return _azure_sync_client


def _get_azure_async() -> AsyncAzureOpenAI:
    global _azure_async_client
    if _azure_async_client is None:
        _azure_async_client = AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )
    return _azure_async_client


def _get_google_client():
    global _google_client_instance
    if _google_client_instance is None:
        from google import genai
        _google_client_instance = genai.Client(api_key=settings.google_ai_api_key)
    return _google_client_instance


# ── Message helpers ───────────────────────────────────────────────

def _split_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Extract the system prompt and return the remaining history."""
    system  = ""
    history = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            history.append(m)
    return system, history


def _to_gemini_contents(history: list[dict]):
    """Convert OpenAI-style message list to Gemini Content objects."""
    from google.genai import types
    contents = []
    for m in history:
        role = "user" if m["role"] == "user" else "model"
        contents.append(
            types.Content(role=role, parts=[types.Part(text=m["content"])])
        )
    return contents


# ── Public API ────────────────────────────────────────────────────

def sync_chat(
    messages: list[dict],
    temperature: float = 0.0,
    provider: Optional[str] = None,
) -> str:
    """Synchronous chat completion."""
    p = provider or settings.llm_provider
    try:
        if p == "google":
            return _google_sync(messages, temperature)
        return _azure_sync_chat(messages, temperature)
    except Exception as exc:
        logger.error("sync_chat error (%s): %s", p, exc)
        raise


async def async_chat(
    messages: list[dict],
    temperature: float = 0.1,
    provider: Optional[str] = None,
) -> str:
    """Asynchronous chat completion."""
    p = provider or settings.llm_provider
    try:
        if p == "google":
            return await _google_async(messages, temperature)
        return await _azure_async_chat(messages, temperature)
    except Exception as exc:
        logger.error("async_chat error (%s): %s", p, exc)
        raise


# ── Azure implementations ─────────────────────────────────────────

def _azure_sync_chat(messages: list[dict], temperature: float) -> str:
    response = _get_azure_sync().chat.completions.create(
        model=settings.azure_openai_chat_deployment,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content


async def _azure_async_chat(messages: list[dict], temperature: float) -> str:
    response = await _get_azure_async().chat.completions.create(
        model=settings.azure_openai_chat_deployment,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content


# ── Google Gemini implementations ─────────────────────────────────

def _google_sync(messages: list[dict], temperature: float) -> str:
    from google.genai import types
    client = _get_google_client()
    system, history = _split_messages(messages)
    contents = _to_gemini_contents(history)

    cfg: dict = {"temperature": temperature}
    if system:
        cfg["system_instruction"] = system

    response = client.models.generate_content(
        model=settings.google_ai_model,
        contents=contents,
        config=types.GenerateContentConfig(**cfg),
    )
    return response.text


async def _google_async(messages: list[dict], temperature: float) -> str:
    from google.genai import types
    client = _get_google_client()
    system, history = _split_messages(messages)
    contents = _to_gemini_contents(history)

    cfg: dict = {"temperature": temperature}
    if system:
        cfg["system_instruction"] = system

    response = await client.aio.models.generate_content(
        model=settings.google_ai_model,
        contents=contents,
        config=types.GenerateContentConfig(**cfg),
    )
    return response.text
