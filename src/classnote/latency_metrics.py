from __future__ import annotations

from collections import deque


class LatencyWindow:
    """Bounded session-only timing samples; no transcript or audio is retained."""

    STAGES = ("english", "chinese_first", "chinese_complete", "speech_to_chinese_first")

    def __init__(self, size: int = 5000) -> None:
        self.samples: dict[str, deque[int]] = {
            name: deque(maxlen=max(1, size)) for name in self.STAGES
        }

    def add(self, payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        stage = str(payload.get("stage", ""))
        milliseconds = payload.get("ms")
        if stage not in self.samples or isinstance(milliseconds, bool):
            return False
        try:
            value = int(milliseconds)
        except (TypeError, ValueError):
            return False
        if value < 0 or value > 300_000:
            return False
        self.samples[stage].append(value)
        if stage == "chinese_first" and "from_speech_end_ms" in payload:
            self.add({
                "stage": "speech_to_chinese_first",
                "ms": payload["from_speech_end_ms"],
            })
        return True

    def percentile(self, stage: str, percent: int) -> int | None:
        values = sorted(self.samples.get(stage, ()))
        if not values:
            return None
        index = max(0, min(len(values) - 1, (len(values) * percent + 99) // 100 - 1))
        return values[index]

    def count(self, stage: str) -> int:
        return len(self.samples.get(stage, ()))

    def summary(self, protected_mismatch_count: int = 0) -> dict[str, object]:
        return {
            "version": 1,
            "stages": {
                stage: {
                    "count": self.count(stage),
                    "p50_ms": self.percentile(stage, 50),
                    "p95_ms": self.percentile(stage, 95),
                }
                for stage in self.STAGES if self.count(stage)
            },
            "protected_mismatch_count": max(0, int(protected_mismatch_count)),
        }


def format_quality_summary(summary: dict[str, object]) -> str:
    stages = summary.get("stages")
    if not isinstance(stages, dict):
        return ""
    lines = []
    for stage, title in (
        ("english", "说话结束→英文确认（估算）"),
        ("chinese_first", "英文确认→中文首片段返回"),
        ("chinese_complete", "英文确认→中文完成"),
        ("speech_to_chinese_first", "说话结束→中文首片段返回（估算）"),
    ):
        data = stages.get(stage)
        if not isinstance(data, dict):
            continue
        try:
            count, median, slow = int(data["count"]), int(data["p50_ms"]), int(data["p95_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        if count <= 0 or min(median, slow) < 0:
            continue
        line = f"- {title}：中位数 {median / 1000:.2f}s"
        if count >= 5:
            line += f"，P95 {slow / 1000:.2f}s"
        line += f"（{count} 次）"
        lines.append(line)
    mismatch = summary.get("protected_mismatch_count", 0)
    try:
        mismatch_count = max(0, int(mismatch))
    except (TypeError, ValueError):
        mismatch_count = 0
    if mismatch_count:
        lines.append(f"- 数字或缩写建议核对：{mismatch_count} 句；不代表译文一定错误。")
    if lines:
        lines.append("仅统计本次运行收到的样本；没有参考逐字稿，不能据此计算识别准确率。")
    return "\n".join(lines)
