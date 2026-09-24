"""
Client used across this app for every text/vision LLM call.

Despite the filename (kept as-is deliberately -- see below), this no longer
talks to a self-hosted Ollama instance. It now proxies every call to the
XeAI Gateway (xeai_gateway_client.py) -- gpt-oss-20b for text, qwen2.5-vl-7b
for vision -- per the shared-server migration: everyone on the devserver is
standardizing on the gateway instead of each project running its own model
on the shared GPU.

The public interface (generate(), generate_json(), OllamaError) is kept
byte-for-byte identical to the old Ollama-backed version on purpose: every
existing call site across this codebase (service.py, agents.py,
part_identifier.py, document_ocr.py) calls these two functions and nothing
else, so rewriting the internals here means the whole app moves to the
gateway without touching 20+ call sites individually -- lower risk than a
mechanical find-and-replace across every file, and the same reasoning the
`gpu_retries` backoff logic below inherits from the old implementation. The
module keeps its old name for the same reason: a rename would touch every
importer for zero functional benefit. New code should prefer calling
xeai_gateway_client.py directly.

`model` is still accepted for backward compatibility but no longer does
anything -- the gateway has exactly one text model and one vision model, so
there's no longer a choice to make; which one gets used is decided by
whether `images` is passed, not by this argument.
"""

import json
import logging
from typing import List, Optional

import xeai_gateway_client as gateway

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Raised when the gateway is unreachable or fails to produce usable
    output. Name kept for backward compatibility -- every existing except
    clause across the codebase catches this specific exception type."""


def generate(
    prompt: str,
    model: str = None,
    json_mode: bool = False,
    timeout: int = 90,
    images: list = None,
    gpu_retries: int = 4,
    system: str = None,
    num_predict: int = None,
    reasoning_effort: str = "low",
) -> str:
    """
    Call the XeAI Gateway and return the raw text response.

    `images`, if given, is a list of raw image bytes -- routes this call to
    the vision model (qwen2.5-vl-7b) instead of the text model, the same
    role this played when the underlying backend was a single vision-capable
    Ollama model (gemma4:26b) handling both text and vision. The gateway
    accepts max 2 images per call; see xeai_gateway_client.generate_vision.

    `system`, if given, is sent as a proper system message.

    `num_predict`, if given, maps onto the gateway's max_tokens -- kept
    under its old name so every existing call site (which was tuning this
    for Ollama's multi-field JSON schemas that otherwise got cut off
    mid-string) didn't need to change. Defaults to 1024 for the same reason
    the old DEFAULT_OPTIONS did: most of this app's prompts return
    multi-field JSON, and a short default cap was the actual root cause of
    the old "Expecting ',' delimiter" truncation errors.

    `reasoning_effort` controls gpt-oss-20b's thinking budget ("low"/
    "medium"/"high") -- defaults to "low" since most calls in this app are
    structured extraction/classification, matching the gateway guide's own
    guidance for high-volume trivial tasks. Callers doing genuinely hard
    reasoning (e.g. the AI Advisory consolidation) pass "high" explicitly.
    Meaningless for vision calls (qwen2.5-vl-7b isn't a reasoning model) --
    silently ignored there.

    `gpu_retries` is passed straight through to the gateway client's own
    retry/backoff, which already handles the gateway's documented failure
    modes (429 rate limit, 5xx with auto-failover between GPUs).
    """
    # "high" reasoning effort means gpt-oss-20b spends materially more of
    # its budget thinking before the visible answer -- confirmed live: a
    # coverage-check call at reasoning_effort="high" with the old flat
    # 1024-token default came back with genuinely empty content (all budget
    # spent on reasoning, none left for the JSON answer) on every retry,
    # not just occasionally. A vision call ignores reasoning_effort
    # entirely, so this only raises the default for text calls that
    # actually asked for it.
    if num_predict is not None:
        max_tokens = num_predict
    elif reasoning_effort == "high" and not images:
        max_tokens = 3072
    else:
        max_tokens = 1024

    try:
        if images:
            return gateway.generate_vision(
                prompt, images, max_tokens=max_tokens, json_mode=json_mode,
                timeout=timeout, retries=gpu_retries,
            )

        return gateway.generate_text(
            prompt, system=system, max_tokens=max_tokens, reasoning_effort=reasoning_effort,
            json_mode=json_mode, timeout=timeout, retries=gpu_retries,
        )
    except gateway.GatewayError as e:
        # Every existing call site across this codebase catches OllamaError
        # specifically (agents.py, service.py, document_ocr.py) -- re-raise
        # under that name so their error handling keeps working unchanged
        # rather than letting a gateway.GatewayError propagate uncaught.
        raise OllamaError(str(e)) from e


def generate_json(
    prompt: str,
    model: str = None,
    retries: int = 1,
    timeout: int = 90,
    images: list = None,
    system: str = None,
    reasoning_effort: str = "low",
    num_predict: int = None,
) -> dict:
    """
    Call the gateway with structured JSON output enabled and parse it.
    Retries on malformed output -- kept even though the gateway's
    response_format=json_object is far more reliable than Ollama's bare
    format="json" ever was, since a retry here is cheap insurance against
    the rare genuinely-malformed response.

    `num_predict`, if given, overrides generate()'s own reasoning_effort-
    based default -- most callers won't need this (the "high" effort default
    of 3072 already covers it), but a schema with many fields/nested arrays
    may need more.
    """

    last_error = None
    for attempt in range(retries + 1):
        try:
            text = generate(
                prompt, model=model, json_mode=True, timeout=timeout, images=images,
                system=system, reasoning_effort=reasoning_effort, num_predict=num_predict,
            )
            return json.loads(text)
        except (OllamaError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning(f"Gateway JSON generation attempt {attempt + 1} failed: {e}")

    raise OllamaError(f"Gateway failed to produce valid JSON after {retries + 1} attempts: {last_error}")
