#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目统一的中文命令行解析器。"""

from __future__ import annotations

import argparse
import sys
from typing import NoReturn


class ChineseArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置参数"
        self._optionals.title = "选项"
        self.add_argument(
            "-h",
            "--帮助",
            action="help",
            default=argparse.SUPPRESS,
            help="显示帮助并退出",
        )

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法：", 1)

    def format_help(self) -> str:
        text = super().format_help()
        return (
            text.replace("usage:", "用法：", 1)
            .replace("positional arguments:", "位置参数：")
            .replace("options:", "选项：")
            .replace("位置参数:", "位置参数：")
            .replace("选项:", "选项：")
        )

    @staticmethod
    def _translate_error(message: str) -> str:
        replacements = (
            ("the following arguments are required:", "缺少必填参数："),
            ("unrecognized arguments:", "无法识别的参数："),
            ("invalid choice:", "选项无效："),
            ("invalid int value:", "整数值无效："),
            ("expected one argument", "需要一个参数值"),
            ("argument ", "参数 "),
        )
        translated = str(message)
        for source, target in replacements:
            translated = translated.replace(source, target)
        return translated

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 参数错误：{self._translate_error(message)}\n")


__all__ = ["ChineseArgumentParser"]
