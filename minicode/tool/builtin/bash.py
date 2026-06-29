"""Bash 工具：执行 shell 命令。"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from minicode.tool.base import Tool, ToolContext, ToolResult, ToolKind


class BashParams(BaseModel):
    command: str = Field(..., description="要执行的 shell 命令")
    description: str = Field(..., description="5-10 个字描述这条命令做什么")
    timeout: Optional[int] = Field(None, description="超时毫秒，默认 120000")


# 危险命令检测（生产环境应配权限系统）
# 覆盖：rm -rf /, rm -r -f /, rm -rf /*, mkfs, dd, format, curl|sh, wget|sh, chmod 777 /, > /dev/
_DANGEROUS = re.compile(
    r"\b(rm\s+-[r-]*f\s+/(\*|\S*$)?|"          # rm -rf / 或 rm -r -f / 或 rm -rf /*
    r"mkfs\.?\w*\s|"                             # mkfs / mkfs.ext4 等
    r"format\s+[a-zA-Z]:|"                       # format C: 等
    r"dd\s+if=.*\s+of=/dev/|"                    # dd if=... of=/dev/...
    r">\s*/dev/sd[a-z]|"                         # > /dev/sda 等
    r"chmod\s+-R\s+777\s+/|"                     # chmod -R 777 /
    r"curl\s+\S+\s*\|\s*(ba)?sh|"                # curl ... | sh / curl ... | bash
    r"wget\s+\S+\s*-O\s*-\s*\|\s*(ba)?sh|"      # wget ... -O - | sh
    r"git\s+push\s+--force\s+origin\s+main|"     # git push --force origin main
    r"git\s+push\s+-f\s+origin\s+main|"           # git push -f origin main
    r">\s*/etc/(passwd|shadow|sudoers|hosts))",   # 覆盖系统关键文件
    re.I,
)


class BashTool(Tool):
    """运行 shell 命令，返回 stdout+stderr。

    设计说明：
    - 不做语法树解析（mimo code 用 tree-sitter），用 shlex 简单 tokenize
    - 路径权限询问通过 ctx.ask 实现，本工具不内置
    - 跨平台：优先用当前 shell（cmd.exe on Windows / sh on *nix）
    """

    kind = ToolKind.BUILTIN

    @property
    def id(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "执行一条 shell 命令并返回 stdout+stderr。适用于编译、跑测试、git 操作等。"

    @property
    def parameters(self):
        return BashParams

    async def execute(self, args: BashParams, ctx: ToolContext) -> ToolResult:
        if _DANGEROUS.search(args.command):
            return ToolResult(
                title="blocked: dangerous command",
                output=f"Refused to run dangerous command:\n{args.command}",
                metadata={"exit": -1, "blocked": True},
            )

        cwd = Path(ctx.cwd)
        env = os.environ.copy()
        timeout_s = (args.timeout or 120_000) / 1000

        try:
            proc = await asyncio.create_subprocess_shell(
                args.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(cwd),
                env=env,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(
                    title=args.description,
                    output=f"Timeout after {timeout_s}s",
                    metadata={"exit": -1, "timeout": True},
                )
        except Exception as e:
            return ToolResult(
                title=args.description,
                output=f"Failed to start: {e}",
                metadata={"exit": -1, "error": str(e)},
            )

        raw = stdout or b""
        # Windows cmd.exe 默认输出 GBK，优先 utf-8 失败后回退系统默认编码
        for enc in ("utf-8", sys.getdefaultencoding(), "gbk", "gb2312"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        exit_code = proc.returncode if proc.returncode is not None else 0
        return ToolResult(
            title=args.description,
            output=text or "(no output)",
            metadata={"exit": exit_code, "command": args.command},
        )
