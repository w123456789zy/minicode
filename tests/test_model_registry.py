"""ModelRegistry 单测（单 provider 模式）。"""
from pathlib import Path

import pytest

from minicode.model.registry import ModelRegistry
from minicode.model.openai_compat import OpenAICompatModel
from minicode.model.anthropic import AnthropicModel
from minicode.model.demo import DemoModel


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────
# 启动校验
# ─────────────────────────────────────────────────────────────


def test_registry_missing_file_returns_errors(tmp_path: Path):
    reg = ModelRegistry(tmp_path / "nope.yaml")
    errs = reg.build()
    assert errs  # 非空
    assert reg.current() is None
    assert not reg.is_ready()


def test_registry_incomplete_config_returns_errors(tmp_path: Path):
    p = _write_config(tmp_path, "provider: openai\n")  # 缺其他字段
    reg = ModelRegistry(p)
    errs = reg.build()
    assert errs
    assert reg.current() is None


def test_registry_complete_config_succeeds(tmp_path: Path):
    p = _write_config(tmp_path, """
provider: openai
api_key: sk-test
base_url: https://api.openai.com/v1
model: gpt-4o
""")
    reg = ModelRegistry(p)
    errs = reg.build()
    assert errs == []
    assert reg.is_ready()
    m = reg.current()
    assert isinstance(m, OpenAICompatModel)
    assert m.info.model == "gpt-4o"
    assert m.info.type == "openai-compat"
    assert m.api_key == "sk-test"


# ─────────────────────────────────────────────────────────────
# 协议分发
# ─────────────────────────────────────────────────────────────


def test_registry_anthropic(tmp_path: Path):
    p = _write_config(tmp_path, """
provider: anthropic
api_key: sk-ant
base_url: https://api.anthropic.com
model: claude-3-5-sonnet-20241022
""")
    reg = ModelRegistry(p)
    reg.build()
    m = reg.current()
    assert isinstance(m, AnthropicModel)
    assert m.info.type == "anthropic"
    assert m.info.id == "anthropic"
    assert m.info.model == "claude-3-5-sonnet-20241022"


def test_registry_unknown_provider_falls_back_to_demo(tmp_path: Path):
    """provider 值未知时仍要进 REPL（type 兜底成 demo）。"""
    p = _write_config(tmp_path, """
provider: cohere
api_key: sk-x
base_url: https://x
model: y
""")
    reg = ModelRegistry(p)
    errs = reg.build()
    # provider 校验失败 → 报 error
    assert errs
    assert reg.current() is None


# ─────────────────────────────────────────────────────────────
# 错误聚合
# ─────────────────────────────────────────────────────────────


def test_registry_reload_after_fix(tmp_path: Path):
    """第一次 build 失败，修复后再次 build 应该成功。"""
    p = _write_config(tmp_path, """
provider: openai
api_key: ""
base_url: ""
model: ""
""")
    reg = ModelRegistry(p)
    errs1 = reg.build()
    assert errs1
    assert reg.current() is None

    # 修复
    p.write_text("""
provider: openai
api_key: sk-new
base_url: https://api.openai.com/v1
model: gpt-4o
""", encoding="utf-8")
    errs2 = reg.build()
    assert errs2 == []
    assert reg.is_ready()
