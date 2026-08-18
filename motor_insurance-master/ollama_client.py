"""
Thin client for a self-hosted Ollama instance, used in place of the Gemini API.

Configure via env vars:
    OLLAMA_BASE_URL  (default: http://127.0.0.1:11434)
    OLLAMA_MODEL     (default: claims-advisory-v1 -- a LoRA fine-tune of
                     qwen2.5:7b trained on the synthetic claims dataset to
                     produce the Xenova AI Advisory JSON schema reliably;
                     see nlp/scripts/export_to_ollama.md for how it was built.
                     Set OLLAMA_MODEL=qwen2.5:7b to fall back to the base
                     model if needed.)
"""

import base64
import json
import logging
import os
import random
import time

import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "claims-advisory-v1")

# Lower temperature + a repeat penalty measurably cut down on the degenerate
# token-looping (e.g. "l_2_im_2_logic-" repeated forever) seen from gemma4:26b
# on long/complex prompts — structured-output calls want determinism, not
# creativity. num_predict is set generously so multi-field JSON schemas don't
# get cut off mid-object (seen as "Expecting ',' delimiter" at the tail end).
DEFAULT_OPTIONS = {"temperature": 0.2, "repeat_penalty": 1.3, "num_predict": 1024}


class OllamaError(Exception):
    """Raised when Ollama is unreachable or fails to produce usable output."""


def _post_generate(
    prompt: str,
    model: str,
    json_mode: bool,
    timeout: int,
    num_gpu: int = None,
    images_b64: list = None,
    system: str = None,
) -> str:
    options = dict(DEFAULT_OPTIONS)
    if num_gpu is not None:
        options["num_gpu"] = num_gpu

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
        # gemma4:26b supports a "thinking" mode that burns num_predict on
        # hidden reasoning before the actual answer — for structured JSON
        # output we want the answer directly, not chain-of-thought eating
        # the token budget and truncating the response.
        "think": False,
    }
    if json_mode:
        payload["format"] = "json"
    if images_b64:
        payload["images"] = images_b64
    if system is not None:
        # Only meaningful for models whose Modelfile TEMPLATE actually
        # branches on {{ if .System }} (e.g. claims-advisory-v1 -- see
        # nlp/scripts/export_to_ollama.md). Without this, /api/generate's
        # bare prompt renders as a plain user turn with no system message,
        # which does NOT match how claims-advisory-v1 was fine-tuned
        # (system + user, both required) and produces off-schema output.
        payload["system"] = system

    resp = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["response"]


def generate(
    prompt: str,
    model: str = None,
    json_mode: bool = False,
    timeout: int = 90,
    images: list = None,
    gpu_retries: int = 4,
    system: str = None,
) -> str:
    """
    Call Ollama's /api/generate and return the raw text response.

    `images`, if given, is a list of raw image bytes — passed to a
    vision-capable model (e.g. gemma4:26b) the same way Gemini's
    generate_content([prompt, image]) took a PIL image.

    `system`, if given, is sent as a separate system message (see
    claims-advisory-v1's usage in service.py's AI Advisory consolidation --
    it was fine-tuned on system+user pairs, not a single flattened prompt).

    This is a shared, multi-tenant Ollama instance (a dev GPU other
    developers also use) — capacity is limited, and the team is
    standardized on one model, so we don't fall back to a different/smaller
    model or force CPU-only inference (empirically, forcing num_gpu=0 on
    gemma4:26b produces degenerate repeated-token garbage, not just slower
    output — it's actively wrong, not just slow). Instead we retry on GPU
    with backoff, since most failures here are transient contention from
    other jobs on the shared GPU.

    The service is not actually going down (checked directly: systemd unit,
    6+ days continuous uptime, healthy on every check) -- the failures seen
    in practice are transient connection resets mid-request from other
    tenants' load, often on BOTH attempts within a couple of seconds of each
    other. A flat 3s gap wasn't giving those enough room to clear, so this
    backs off exponentially (with jitter, to avoid every concurrent request
    retrying in lockstep) and tries more times before giving up.
    """

    resolved_model = model or OLLAMA_MODEL
    images_b64 = [base64.b64encode(img).decode() for img in images] if images else None

    last_error = None
    for attempt in range(gpu_retries):
        try:
            return _post_generate(prompt, resolved_model, json_mode, timeout, images_b64=images_b64, system=system)
        except (requests.RequestException, KeyError, ValueError) as e:
            last_error = e
            logger.warning(f"Ollama generate attempt {attempt + 1}/{gpu_retries} failed: {e}")
            if attempt < gpu_retries - 1:
                backoff = min(2 ** attempt, 10) + random.uniform(0, 1.5)
                time.sleep(backoff)

    raise OllamaError(f"Ollama request failed after {gpu_retries} attempts: {last_error}")


def generate_json(
    prompt: str,
    model: str = None,
    retries: int = 1,
    timeout: int = 90,
    images: list = None,
    system: str = None,
) -> dict:
    """
    Call Ollama with format=json (forces syntactically valid JSON) and parse it.
    Retries on malformed output since open models are less consistent than Gemini here.
    """

    last_error = None
    for attempt in range(retries + 1):
        try:
            text = generate(prompt, model=model, json_mode=True, timeout=timeout, images=images, system=system)
            return json.loads(text)
        except (OllamaError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning(f"Ollama JSON generation attempt {attempt + 1} failed: {e}")

    raise OllamaError(f"Ollama failed to produce valid JSON after {retries + 1} attempts: {last_error}")
