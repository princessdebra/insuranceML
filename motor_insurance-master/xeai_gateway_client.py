"""
Client for the XeAI Gateway -- the shared internal OpenAI-compatible LLM
endpoint (see API_GUIDE-2.pdf). This replaces this app's own dedicated
Ollama instance and Gemini API usage for text reasoning, vision/OCR, and
embeddings, so we're not running a separate model on the shared devserver
GPU ourselves -- everyone's calls go through one gateway that handles
GPU routing, failover, and rate limiting.

Three models, three thin wrappers, matching the gateway's own catalog:
  - generate_text() / generate_json()   -> gpt-oss-20b    (chat/reasoning)
  - generate_vision() / generate_vision_json() -> qwen2.5-vl-7b (OCR/images, max 2/call)
  - embed_text()                        -> nomic-embed-text

Verified against the live gateway before this was wired into any call site:
plain text, JSON via response_format, vision (plain + JSON), and embeddings
all work, typically in well under a second per call.
"""

import base64
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI, APIConnectionError, APIError, APITimeoutError, RateLimitError

logger = logging.getLogger(__name__)

GATEWAY_BASE_URL = os.environ.get("XEAI_GATEWAY_BASE_URL", "http://192.168.100.133:4000/v1")
GATEWAY_API_KEY = os.environ.get("XEAI_GATEWAY_API_KEY", "")

TEXT_MODEL = "gpt-oss-20b"
VISION_MODEL = "qwen2.5-vl-7b"
EMBED_MODEL = "nomic-embed-text"

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not GATEWAY_API_KEY:
            raise RuntimeError(
                "XEAI_GATEWAY_API_KEY is not set -- required to call the XeAI Gateway. "
                "Set it in .env.gmail (or wherever this app's other secrets live)."
            )
        _client = OpenAI(base_url=GATEWAY_BASE_URL, api_key=GATEWAY_API_KEY)
    return _client


class GatewayError(Exception):
    """Raised when the gateway call fails after all retries, or returns
    something a caller can't use (e.g. empty content)."""


def _with_retries(fn, retries: int, base_delay: float = 1.5):
    """Retry on rate limits (429) and transient/5xx errors -- the gateway
    auto-fails-over between GPUs on its own, so a retry alone can land on a
    healthy backend without any special handling here."""
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            return fn()
        except RateLimitError as e:
            last_err = e
            wait = base_delay * (2 ** attempt)
            logger.warning(f"XeAI Gateway rate-limited (429), attempt {attempt + 1}/{retries}, backing off {wait}s")
            time.sleep(wait)
        except (APIConnectionError, APITimeoutError) as e:
            last_err = e
            wait = base_delay * (2 ** attempt)
            logger.warning(f"XeAI Gateway connection/timeout, attempt {attempt + 1}/{retries}, backing off {wait}s: {e}")
            time.sleep(wait)
        except APIError as e:
            status = getattr(e, "status_code", 500) or 500
            if status >= 500:
                last_err = e
                wait = base_delay * (2 ** attempt)
                logger.warning(f"XeAI Gateway {status}, attempt {attempt + 1}/{retries}, backing off {wait}s: {e}")
                time.sleep(wait)
            else:
                raise
    raise GatewayError(f"XeAI Gateway request failed after {retries} attempts: {last_err}")


def generate_text(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 512,
    reasoning_effort: str = "low",
    json_mode: bool = False,
    timeout: int = 60,
    retries: int = 3,
) -> str:
    """Text/reasoning call via gpt-oss-20b.

    reasoning_effort: "low" for simple extraction/classification (default
    here, matches the guide's own cost/latency guidance for high-volume
    trivial tasks), "medium" for balanced, "high" for genuinely hard
    reasoning (fraud analysis, tricky multi-step judgment) -- pass it
    explicitly per call site rather than leaving every call on the default.

    gpt-oss-20b "thinks" before answering, spending part of max_tokens on
    that before the visible reply -- an empty response usually means
    max_tokens was too low for the reasoning it needed, not that the model
    failed; raise max_tokens rather than retrying blindly.
    """
    client = _get_client()
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    def _call() -> str:
        kwargs: Dict[str, Any] = dict(
            model=TEXT_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_body={"reasoning_effort": reasoning_effort},
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content
        if not content:
            raise GatewayError(
                "Empty content from gpt-oss-20b -- likely ran out of max_tokens "
                "mid-reasoning; caller should raise max_tokens."
            )
        return content

    return _with_retries(_call, retries=retries)


def generate_json(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 768,
    reasoning_effort: str = "low",
    timeout: int = 60,
    retries: int = 3,
) -> dict:
    """Same as generate_text, parsed as JSON (via response_format, verified
    working against the live gateway for both models)."""
    text = generate_text(
        prompt, system=system, max_tokens=max_tokens, reasoning_effort=reasoning_effort,
        json_mode=True, timeout=timeout, retries=retries,
    )
    return json.loads(text)


def generate_vision(
    prompt: str,
    images: List[bytes],
    max_tokens: int = 1024,
    json_mode: bool = False,
    timeout: int = 60,
    retries: int = 3,
) -> str:
    """Vision call via qwen2.5-vl-7b. The gateway allows max 2 images per
    request -- callers passing more get only the first 2, logged loudly
    rather than silently dropped, since a caller relying on a 3rd/4th image
    being seen needs to know it wasn't."""
    if len(images) > 2:
        logger.warning(f"generate_vision got {len(images)} images -- qwen2.5-vl-7b allows max 2 per request; using the first 2 only.")
        images = images[:2]

    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img_bytes in images:
        b64 = base64.b64encode(img_bytes).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    client = _get_client()

    def _call() -> str:
        kwargs: Dict[str, Any] = dict(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        out = resp.choices[0].message.content
        if not out:
            raise GatewayError("Empty content from qwen2.5-vl-7b.")
        return out

    return _with_retries(_call, retries=retries)


def generate_vision_json(
    prompt: str,
    images: List[bytes],
    max_tokens: int = 1024,
    timeout: int = 60,
    retries: int = 3,
) -> dict:
    text = generate_vision(prompt, images, max_tokens=max_tokens, json_mode=True, timeout=timeout, retries=retries)
    return json.loads(text)


def embed_text(text: str, retries: int = 3) -> List[float]:
    """768-dim embedding via nomic-embed-text -- same underlying model as
    Ollama's nomic-embed-text, so vectors already stored elsewhere stay
    compatible if/when embed calls get repointed here."""
    client = _get_client()

    def _call() -> List[float]:
        resp = client.embeddings.create(model=EMBED_MODEL, input=text)
        return resp.data[0].embedding

    return _with_retries(_call, retries=retries)
