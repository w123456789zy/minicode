"""
加载 .minicode/config.yaml。

约定格式（单 provider，类似 ClaudeCode 的设计）：

    provider: openai                    # openai | anthropic    必填
    api_key:  ${OPENAI_API_KEY}         # 必填
    base_url: https://api.openai.com/v1 # 必填
    model:    gpt-4o                    # 必填
    context_window: 128K                # 可选，模型上下文窗口，支持 128K / 1M / 128000
    extra:                              # 可选，透传给 provider
      temperature: 0.7
      max_tokens: 8000                  # 整数字段也支持 K/M 后缀（"8K" → 8000）

设计要点：
- 启动时必须能加载出**完整有效**的配置，缺一字段直接报错退出
- 校验失败时给出**具体哪一项错了** + **怎么改**的提示（参考 ClaudeCode）
- 配置文件不存在 / 为空 → 报错，让用户知道要建
- api_key 允许 ${ENV_VAR} 展开；未定义的环境变量展开成空串 → 同样报错（避免静默失败）
- 大小字段（context_window）支持 K/M 后缀：128K = 128000，1M = 1000000
- 异常大的 context_window（> 8M）打到 stderr 警告（不报错，未来模型可能更大）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


_VALID_PROVIDERS = ("openai", "anthropic")
# provider 字段名 → 内部 Model.type
_PROVIDER_TYPE_MAP = {
    "openai": "openai-compat",
    "anthropic": "anthropic",
}


# ─────────────────────────────────────────────────────────────
# 错误 + 配置数据类
# ─────────────────────────────────────────────────────────────


@dataclass
class ConfigError:
    """配置加载 / 校验错误。聚合后一起报，方便用户一次性看全。"""
    field: str       # "provider" / "api_key" / ... / "<file>"
    message: str     # 人话

    def render(self, config_path: Path) -> str:
        return f"  - {self.field}: {self.message}"


@dataclass
class LLMConfig:
    """单 provider 配置，启动时构造好。"""
    provider: str              # "openai" | "anthropic"
    type: str                  # "openai-compat" | "anthropic"（内部协议标识）
    api_key: str
    base_url: str
    model: str
    extra: Dict[str, Any] = field(default_factory=dict)
    # 模型上下文窗口大小（token 数）。0 表示未知，由 budget 用默认值。
    # 用户可在 config.yaml 顶层写 context_window，或写在 extra 里。
    context_window: int = 0


# ─────────────────────────────────────────────────────────────
# 大小解析：支持 K/M 后缀（如 "128K" / "1M" / 128000）
# ─────────────────────────────────────────────────────────────

# 经验上限：超过此值认为配置异常（8M ≈ Claude/GPT 当前的 1M context × 8）
# 不会硬报错（未来模型可能更大），但打到 stderr 警告。
_SANE_UPPER_BOUND = 8_000_000


def _parse_size(value: Any) -> Optional[int]:
    """把用户写的大小转成 int。支持：
        纯整数     "128000"  → 128000
        K 后缀     "128K"    → 128000
        M 后缀     "1M"      → 1000000
        小数       "1.5M"    → 1500000
        空格       "128 K"   → 128000
    返回 None 表示解析失败。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip().upper().replace(" ", "")
    if not s:
        return None
    # 抓数字部分（含可选小数点）
    m = re.match(r"^(\d+(?:\.\d+)?)([KM])?$", s)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K":
        num *= 1_000
    elif suffix == "M":
        num *= 1_000_000
    return int(num)


# ─────────────────────────────────────────────────────────────
# extra 字段归一化
# ─────────────────────────────────────────────────────────────

# 已知需要整型 + 支持 K/M 后缀的 extra 字段
# 这些字段最终会透传给 LLM API，必须是 int
_INT_EXTRA_FIELDS = frozenset({"max_tokens", "max_output_tokens", "n"})


def _normalize_extra_int_fields(extra: Dict[str, Any]) -> Dict[str, Any]:
    """对 extra 里已知整数字段做类型归一化。

    LLM API（OpenAI / Anthropic）对 max_tokens 等字段要求 int，不接受字符串。
    但用户在 yaml 里写 "8K" / "1M" 比 8000 / 1000000 直观，所以遇到已知整数字段
    且是字符串时，自动用 _parse_size 展开。

    解析失败 → 保留原值（不静默篡改），调用方在用的时候会报错，自然把锅甩给用户。
    """
    out: Dict[str, Any] = {}
    for k, v in extra.items():
        if k in _INT_EXTRA_FIELDS and isinstance(v, str):
            parsed = _parse_size(v)
            if parsed is not None:
                out[k] = parsed
                continue
        out[k] = v
    return out


# ─────────────────────────────────────────────────────────────
# 环境变量展开
# ─────────────────────────────────────────────────────────────


_ENV_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


# 自动修正的告警（load_config 阶段填，调用方自行 drain 出来打 stderr）
_AUTO_FIX_WARNINGS: List[str] = []


def drain_auto_fix_warnings() -> List[str]:
    """取出并清空累积的 auto-fix 警告。"""
    global _AUTO_FIX_WARNINGS
    out = list(_AUTO_FIX_WARNINGS)
    _AUTO_FIX_WARNINGS = []
    return out


def _expand_env_str(value: str) -> str:
    """支持 ${ENV_VAR} 展开。约定：未定义的环境变量展开成空串。

    这样后续的"必填校验"环节就能稳定地捕获到"用户写了 ${VAR} 但 VAR 没定义"
    的情况，并给出明确错误（区分于"用户根本没写 api_key"）。
    """
    def repl(m: re.Match) -> str:
        return os.environ.get(m.group(1), "")
    return _ENV_RE.sub(repl, value)


# ─────────────────────────────────────────────────────────────
# 加载 + 校验
# ─────────────────────────────────────────────────────────────


def load_config(path: Path) -> Tuple[Optional[LLMConfig], List[ConfigError]]:
    """从 yaml 文件加载 + 校验。

    返回 (config, errors)：
    - config is None 且 errors 非空：校验失败
    - config 非 None：可用配置
    - 文件不存在 → 1 个 error（提示创建）
    """
    # 全局警告池：base_url 容错、context_window 异常大等情况往里写
    global _AUTO_FIX_WARNINGS
    errors: List[ConfigError] = []

    # 1. 文件存在
    if not path.is_file():
        errors.append(ConfigError(
            field="<file>",
            message=f"配置文件不存在：{path}\n"
                    f"      请创建该文件，内容示例：\n"
                    f"        provider: openai\n"
                    f"        api_key:  ${{YOUR_API_KEY_ENV}}\n"
                    f"        base_url: https://api.openai.com/v1\n"
                    f"        model:    gpt-4o",
        ))
        return None, errors

    # 2. YAML 可解析
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        errors.append(ConfigError(
            field="<file>",
            message=f"YAML 解析失败：{e}",
        ))
        return None, errors

    if raw is None:
        errors.append(ConfigError(
            field="<file>",
            message="配置文件为空",
        ))
        return None, errors

    if not isinstance(raw, dict):
        errors.append(ConfigError(
            field="<file>",
            message=f"配置根必须是 mapping（dict），实际是 {type(raw).__name__}",
        ))
        return None, errors

    # 3. 字段级校验
    data = dict(raw)  # 浅拷贝，避免污染

    # provider
    provider = data.get("provider")
    if provider is None or provider == "":
        errors.append(ConfigError(
            field="provider",
            message="未指定。必须是 'openai' 或 'anthropic' 之一",
        ))
        provider_type = ""
    elif provider not in _VALID_PROVIDERS:
        errors.append(ConfigError(
            field="provider",
            message=f"不支持的值 '{provider}'，必须是 {' / '.join(_VALID_PROVIDERS)} 之一",
        ))
        provider_type = ""
    else:
        provider_type = _PROVIDER_TYPE_MAP[provider]

    # api_key
    api_key_raw = data.get("api_key", "")
    if api_key_raw is None:
        api_key_raw = ""
    api_key = _expand_env_str(str(api_key_raw))
    if not api_key:
        # 判断是不是 ${ENV} 未定义
        if isinstance(api_key_raw, str) and _ENV_RE.search(api_key_raw):
            m = _ENV_RE.search(api_key_raw)
            errors.append(ConfigError(
                field="api_key",
                message=f"引用了未定义的环境变量 {m.group(0)}，请先在 shell 里 export",
            ))
        else:
            errors.append(ConfigError(
                field="api_key",
                message="未配置（api_key 必填）",
            ))

    # base_url
    base_url = data.get("base_url", "")
    if base_url is None:
        base_url = ""
    base_url = str(base_url).strip()
    if not base_url:
        errors.append(ConfigError(
            field="base_url",
            message="未配置（base_url 必填）",
        ))
    elif not (base_url.startswith("http://") or base_url.startswith("https://")):
        # 容错：用户经常忘记写协议头（直接填域名/IP/字符串）
        # 静默补成 https://；调用方负责在 stderr 警告一次
        # 这里**不**append ConfigError——否则会进 LLMConfig = None 路径，
        # 模型就建不起来。
        # 用 _WARNINGS 全局暂存，CLI 启动时打印
        _AUTO_FIX_WARNINGS.append(
            f"base_url 缺协议头，自动补成 https://{base_url}"
            f"（如不是你要的，请把 base_url 改成 'https://...' 形式）"
        )
        base_url = "https://" + base_url

    # model
    model = data.get("model", "")
    if model is None:
        model = ""
    model = str(model)
    if not model:
        errors.append(ConfigError(
            field="model",
            message="未配置（model 必填）",
        ))

    # extra
    extra = data.get("extra") or {}
    if not isinstance(extra, dict):
        errors.append(ConfigError(
            field="extra",
            message=f"必须是 mapping（dict），实际是 {type(extra).__name__}",
        ))
        extra = {}

    # context_window：可选，顶层字段或 extra.context_window
    # 0 / 未设置 → 未知，budget 用默认值（8000）
    # 支持 K/M 后缀："128K" / "1M" / 128000
    context_window = 0
    raw_cw = data.get("context_window")
    if raw_cw is None and isinstance(extra, dict):
        raw_cw = extra.get("context_window")
    if raw_cw is not None:
        parsed = _parse_size(raw_cw)
        if parsed is None:
            errors.append(ConfigError(
                field="context_window",
                message=f"必须是整数或带 K/M 后缀的数（如 128000 / 128K / 1M），实际是 {raw_cw!r}",
            ))
        else:
            if parsed < 0:
                errors.append(ConfigError(
                    field="context_window",
                    message=f"不能为负数，实际是 {parsed}",
                ))
            elif parsed > _SANE_UPPER_BOUND:
                # 不硬报错（未来模型可能更大），仅警告
                _AUTO_FIX_WARNINGS.append(
                    f"context_window={parsed} 异常大（>{_SANE_UPPER_BOUND}），"
                    f"请确认是不是单位错了（128K 应写 '128K'，不是 1280000）"
                )
            context_window = parsed

    if errors:
        return None, errors

    # 归一化 extra：
    # - 已知整数字段（max_tokens）若用户写字符串（"8K" / "1M"），自动展开成 int
    # - 这样模板里可以写 max_tokens: 8K 也合法
    extra = _normalize_extra_int_fields(extra)

    return LLMConfig(
        provider=str(provider),
        type=provider_type,
        api_key=api_key,
        base_url=base_url,
        model=model,
        extra=dict(extra),
        context_window=context_window,
    ), []


# ─────────────────────────────────────────────────────────────
# 错误渲染（CLI 启动用）
# ─────────────────────────────────────────────────────────────


def render_config_errors(path: Path, errors: List[ConfigError]) -> str:
    """把错误列表渲染成给用户看的多行文本。"""
    lines = [
        f"[minicode] 配置无效（{path}）：",
        "",
    ]
    for e in errors:
        lines.append(e.render(path))
    lines.append("")
    lines.append("修复后重新启动 minicode。")
    return "\n".join(lines)
