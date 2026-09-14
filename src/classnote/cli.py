from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .app import process_course
from .branding import APP_NAME


def parse_terms(values: list[str]) -> dict[str, str]:
    terms: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"术语格式应为 英文=中文：{value}")
        source, target = value.split("=", 1)
        if not source.strip() or not target.strip():
            raise ValueError(f"术语两侧不能为空：{value}")
        terms[source.strip()] = target.strip()
    return terms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="英文课堂翻译与笔记工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="处理真实音频或视频文件")
    process.add_argument("audio", type=Path)
    process.add_argument("--title", required=True, help="课程名称")
    process.add_argument("--subject", default="通用课程", help="课程领域")
    process.add_argument("--term", action="append", default=[], help="术语：英文=中文")

    demo = subparsers.add_parser("demo", help="运行不调用 API 的离线演示")
    demo.add_argument("--title", default=f"{APP_NAME}演示课程")
    demo.add_argument("--subject", default="计算机网络")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        demo = args.command == "demo"
        audio = Path("offline-demo") if demo else args.audio
        terms = {} if demo else parse_terms(args.term)
        result, exported = process_course(
            audio,
            args.title,
            args.subject,
            terms=terms,
            demo=demo,
            progress=lambda message: print(f"[{APP_NAME}] {message}"),
        )
        print(f"[{APP_NAME}] 已保存课程：{result.id}")
        print(f"[{APP_NAME}] Markdown：{exported}")
        return 0
    except Exception as exc:
        print(f"[{APP_NAME}] 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
