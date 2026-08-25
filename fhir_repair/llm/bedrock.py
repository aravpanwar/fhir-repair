"""AWS Bedrock provider adapter.

Bedrock is the deployment path for teams whose data cannot leave their AWS
account. It differs from the direct providers in two ways that matter here:

  - Authentication is AWS SigV4 through the standard credential chain
    (environment, shared config, instance role), not a bearer token. There
    is no API key to supply, so the BYOK rule takes a different shape: the
    deployer configures AWS credentials the way they configure them for
    every other AWS call, and this adapter never reads or stores one.
  - Model ids are Bedrock-specific and region-qualified, e.g.
    `anthropic.claude-sonnet-4-5-20250929-v1:0` or an inference profile ARN.

The Converse API is used rather than InvokeModel: it normalises the request
and response shape across model families, so this adapter does not need a
per-family branch. Converse also carries prompt caching via `cachePoint`
blocks, which is how stable segments are marked.

`boto3` is an optional dependency; install with
`pip install "fhir-repair[bedrock]"`.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fhir_repair.core.models import PromptSegment
from fhir_repair.llm.base import Completion


class BedrockProvider:
    """Thin wrapper around the Bedrock Runtime Converse API."""

    def __init__(
        self,
        model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0",
        region: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        # Imported lazily so the package loads on systems without the
        # optional `boto3` dependency installed.
        try:
            import boto3
        except ImportError as exc:
            raise ImportError(
                "BedrockProvider requires the optional `boto3` package. "
                "Install with: pip install 'fhir-repair[bedrock]'"
            ) from exc

        resolved_region = (
            region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        )
        if not resolved_region:
            raise ValueError(
                "BedrockProvider requires a region. Set AWS_REGION in the "
                "environment, or pass region="
            )

        # No api_key argument: boto3 resolves credentials from the standard
        # AWS chain. A deployer running on an instance role supplies nothing
        # at all, which is the point of using Bedrock.
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=resolved_region,
            endpoint_url=endpoint,
        )
        self._model = model

    def complete(
        self,
        segments: list[PromptSegment],
        **kwargs: Any,
    ) -> Completion:
        """Send segments to Bedrock Converse and return a Completion.

        Converse splits system prompts from message turns, the same shape
        the Anthropic adapter builds. Stable segments are followed by a
        `cachePoint` block, which tells Bedrock to cache the prefix up to
        that point.
        """
        system_blocks: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = []

        for seg in segments:
            block = {"text": seg.text}
            if seg.role == "system":
                system_blocks.append(block)
                if seg.stable:
                    system_blocks.append({"cachePoint": {"type": "default"}})
            else:
                content: list[dict[str, Any]] = [block]
                if seg.stable:
                    content.append({"cachePoint": {"type": "default"}})
                messages.append({"role": seg.role, "content": content})

        if not messages:
            # Converse requires at least one message turn. Reject early so
            # the failure matches the other adapters.
            raise ValueError(
                "BedrockProvider received only system segments; the runner "
                "must include at least one user segment."
            )

        request: dict[str, Any] = {
            "modelId": self._model,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": kwargs.get("max_tokens", 1024),
                "temperature": kwargs.get("temperature", 0.0),
            },
        }
        if system_blocks:
            request["system"] = system_blocks

        start = time.perf_counter()
        response = self._client.converse(**request)
        latency_ms = int((time.perf_counter() - start) * 1000)

        return Completion(
            text=_extract_text(response),
            input_tokens=_usage_field(response, "inputTokens"),
            output_tokens=_usage_field(response, "outputTokens"),
            cached_tokens=_usage_field(response, "cacheReadInputTokens"),
            latency_ms=latency_ms,
            model=self._model,
            provider="bedrock",
        )

    def supports_caching(self) -> bool:
        return True


def _extract_text(response: dict[str, Any]) -> str:
    """Concatenate the text blocks of a Converse response.

    Converse returns `output.message.content` as a list of blocks. Only
    text blocks are of interest; anything else (tool use, images) is
    ignored the way the Anthropic adapter ignores them.
    """
    message = response.get("output", {}).get("message", {})
    parts = [block["text"] for block in message.get("content", []) if "text" in block]
    return "".join(parts)


def _usage_field(response: dict[str, Any], name: str) -> int:
    """Read one token count from the Converse usage block.

    Cache fields are absent on models or regions without prompt caching, so
    a missing key reports 0 rather than raising.
    """
    return int(response.get("usage", {}).get(name, 0) or 0)
