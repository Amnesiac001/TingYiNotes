from __future__ import annotations

import time
from dataclasses import dataclass

from .services import CompatibleTextProcessor, OpenAITextProcessor, create_text_processor


@dataclass(frozen=True)
class TextProbeResult:
    latency_ms: int
    reply: str


def probe_text_connection(
    provider: str, model: str, api_key: str, base_url: str | None
) -> TextProbeResult:
    """Make one small real request to validate the selected text model."""
    processor = create_text_processor(provider, model, api_key, base_url)
    started = time.perf_counter()
    if isinstance(processor, OpenAITextProcessor):
        client = processor.client.with_options(timeout=12.0, max_retries=0)
        response = client.responses.create(
            model=model,
            instructions="Reply only OK.",
            input="Connection check.",
            max_output_tokens=128,
            store=False,
        )
        reply = str(response.output_text or "").strip()
    elif isinstance(processor, CompatibleTextProcessor):
        client = processor.client.with_options(timeout=12.0, max_retries=0)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply only OK."}],
            max_tokens=64,
            stream=False,
            extra_body=processor.extra_body,
        )
        reply = str(response.choices[0].message.content or "").strip()
    else:
        raise RuntimeError("当前文本服务不支持连接测试。")
    if not reply:
        raise RuntimeError("文本服务已响应，但模型没有返回文字；请确认模型可用于翻译。")
    return TextProbeResult(max(0, round((time.perf_counter() - started) * 1000)), reply[:80])
