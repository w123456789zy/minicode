"""config 加载单测（单 provider + 强校验模式）。"""
from pathlib import Path

from minicode.config import (
    load_config,
    render_config_errors,
    drain_auto_fix_warnings,
    ConfigError,
    LLMConfig,
    _parse_size,
)


# ─────────────────────────────────────────────────────────────
# 文件级
# ─────────────────────────────────────────────────────────────


def test_load_missing_file(tmp_path: Path):
    cfg, errs = load_config(tmp_path / "nope.yaml")
    assert cfg is None
    assert len(errs) == 1
    assert errs[0].field == "<file>"


def test_load_empty_file(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("", encoding="utf-8")
    cfg, errs = load_config(p)
    assert cfg is None
    assert any("空" in e.message for e in errs)


def test_load_invalid_yaml(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("a: b\n  c: d\n : e", encoding="utf-8")  # 缩进错乱
    cfg, errs = load_config(p)
    assert cfg is None
    assert len(errs) >= 1


def test_load_root_not_mapping(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("- just a list\n", encoding="utf-8")
    cfg, errs = load_config(p)
    assert cfg is None
    assert any("mapping" in e.message for e in errs)


# ─────────────────────────────────────────────────────────────
# 字段级
# ─────────────────────────────────────────────────────────────


def _write_minimal(tmp_path: Path, **overrides) -> Path:
    """写一个最小可用的 openai 配置，可覆盖任意字段。"""
    p = tmp_path / "config.yaml"
    data = {
        "provider": "openai",
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "extra": {"temperature": 0.5, "max_tokens": 2048},
    }
    data.update(overrides)
    # 手写 yaml 避免引入 pyyaml
    lines = []
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for kk, vv in v.items():
                lines.append(f"  {kk}: {_yaml_value(vv)}")
        else:
            lines.append(f"{k}: {_yaml_value(v)}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _yaml_value(v) -> str:
    if isinstance(v, str):
        # 含特殊字符 → 引号
        if any(c in v for c in [":", "#", "$", "{", "}", "\n", '"']):
            return '"' + v.replace('"', '\\"') + '"'
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    return str(v)


def test_load_minimal_openai(tmp_path: Path):
    p = _write_minimal(tmp_path)
    cfg, errs = load_config(p)
    assert errs == []
    assert isinstance(cfg, LLMConfig)
    assert cfg.provider == "openai"
    assert cfg.type == "openai-compat"
    assert cfg.api_key == "sk-test"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "gpt-4o"
    assert cfg.extra == {"temperature": 0.5, "max_tokens": 2048}


def test_load_anthropic_provider(tmp_path: Path):
    p = _write_minimal(
        tmp_path,
        provider="anthropic",
        model="claude-3-5-sonnet-20241022",
        base_url="https://api.anthropic.com",
    )
    cfg, errs = load_config(p)
    assert errs == []
    assert cfg.provider == "anthropic"
    assert cfg.type == "anthropic"


def test_load_missing_provider(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("api_key: x\nbase_url: y\nmodel: z\n", encoding="utf-8")
    cfg, errs = load_config(p)
    assert cfg is None
    assert any(e.field == "provider" for e in errs)


def test_load_invalid_provider(tmp_path: Path):
    p = _write_minimal(tmp_path, provider="cohere")
    cfg, errs = load_config(p)
    assert cfg is None
    assert any(e.field == "provider" and "cohere" in e.message for e in errs)


def test_load_missing_api_key(tmp_path: Path):
    p = _write_minimal(tmp_path, api_key="")
    cfg, errs = load_config(p)
    assert cfg is None
    assert any(e.field == "api_key" for e in errs)


def test_load_missing_base_url(tmp_path: Path):
    p = _write_minimal(tmp_path, base_url="")
    cfg, errs = load_config(p)
    assert cfg is None
    assert any(e.field == "base_url" for e in errs)


def test_load_missing_model(tmp_path: Path):
    p = _write_minimal(tmp_path, model="")
    cfg, errs = load_config(p)
    assert cfg is None
    assert any(e.field == "model" for e in errs)


def test_load_aggregates_all_errors(tmp_path: Path):
    """多个字段缺失 → 一次报全。"""
    p = tmp_path / "config.yaml"
    p.write_text("extra: {}\n", encoding="utf-8")
    cfg, errs = load_config(p)
    assert cfg is None
    fields = {e.field for e in errs}
    assert {"provider", "api_key", "base_url", "model"} <= fields


# ─────────────────────────────────────────────────────────────
# 环境变量
# ─────────────────────────────────────────────────────────────


def test_env_expansion(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-from-env")
    p = _write_minimal(tmp_path, api_key="${MY_KEY}")
    cfg, errs = load_config(p)
    assert errs == []
    assert cfg.api_key == "sk-from-env"


def test_undefined_env_var_preserved_then_caught(tmp_path: Path, monkeypatch):
    """${NOT_SET} 在 shell 里没定义 → 保留原样 → 校验报"引用了未定义的环境变量"。"""
    monkeypatch.delenv("NOT_SET", raising=False)
    p = _write_minimal(tmp_path, api_key="${NOT_SET}")
    cfg, errs = load_config(p)
    assert cfg is None
    assert any(e.field == "api_key" and "NOT_SET" in e.message for e in errs)


def test_partial_env_expansion(tmp_path: Path, monkeypatch):
    """'prefix-${KEY}-suffix' 形式。"""
    monkeypatch.setenv("KEY", "value")
    p = _write_minimal(tmp_path, api_key="prefix-${KEY}-suffix")
    cfg, errs = load_config(p)
    assert errs == []
    assert cfg.api_key == "prefix-value-suffix"


# ─────────────────────────────────────────────────────────────
# 渲染
# ─────────────────────────────────────────────────────────────


def test_render_config_errors_format(tmp_path: Path):
    p = tmp_path / "config.yaml"
    errs = [
        ConfigError(field="provider", message="未指定"),
        ConfigError(field="api_key", message="未配置"),
    ]
    out = render_config_errors(p, errs)
    assert ".minicode" in out or "config.yaml" in out
    assert "provider" in out
    assert "api_key" in out
    assert "未指定" in out
    assert "修复后" in out


# ─────────────────────────────────────────────────────────────
# _parse_size 解析（K / M 后缀）
# ─────────────────────────────────────────────────────────────


def test_parse_size_pure_int():
    assert _parse_size(128000) == 128000
    assert _parse_size(0) == 0
    assert _parse_size(100) == 100


def test_parse_size_K_suffix():
    assert _parse_size("128K") == 128000
    assert _parse_size("128k") == 128000  # 大小写不敏感
    assert _parse_size("256K") == 256000
    assert _parse_size("1K") == 1000


def test_parse_size_M_suffix():
    assert _parse_size("1M") == 1_000_000
    assert _parse_size("2M") == 2_000_000
    assert _parse_size("1.5M") == 1_500_000


def test_parse_size_with_spaces():
    assert _parse_size("128 K") == 128000
    assert _parse_size("  1M  ") == 1_000_000


def test_parse_size_invalid():
    assert _parse_size("abc") is None
    assert _parse_size("128G") is None  # 不支持 G
    assert _parse_size("") is None
    assert _parse_size(None) is None
    assert _parse_size([128]) is None


# ─────────────────────────────────────────────────────────────
# context_window 字段（顶层 / extra / 单位 / 警告）
# ─────────────────────────────────────────────────────────────


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _valid_yaml_extra(context_window=None) -> str:
    """生成一个最小可用的 yaml，extra 里可指定 context_window。"""
    lines = [
        "provider: openai",
        "api_key: test",
        "base_url: https://example.com",
        "model: gpt-4o",
    ]
    if context_window is not None:
        lines.append("extra:")
        lines.append(f"  context_window: {context_window}")
    return "\n".join(lines) + "\n"


def test_context_window_K_suffix(tmp_path: Path):
    """128K → 128000。"""
    drain_auto_fix_warnings()  # 清空历史警告
    p = _write_config(tmp_path, _valid_yaml_extra(context_window='"128K"'))
    cfg, errs = load_config(p)
    assert cfg is not None
    assert cfg.context_window == 128000
    assert drain_auto_fix_warnings() == []  # 正常值不警告


def test_context_window_pure_int(tmp_path: Path):
    """128000 → 128000（兼容裸整数）。"""
    drain_auto_fix_warnings()
    p = _write_config(tmp_path, _valid_yaml_extra(context_window="128000"))
    cfg, errs = load_config(p)
    assert cfg is not None
    assert cfg.context_window == 128000
    assert drain_auto_fix_warnings() == []


def test_context_window_top_level(tmp_path: Path):
    """顶层 context_window 字段也生效。"""
    body = (
        "provider: openai\n"
        "api_key: test\n"
        "base_url: https://example.com\n"
        "model: gpt-4o\n"
        "context_window: 256K\n"
    )
    p = _write_config(tmp_path, body)
    cfg, _ = load_config(p)
    assert cfg is not None
    assert cfg.context_window == 256000


def test_context_window_huge_value_warns(tmp_path: Path):
    """异常大的值（> 8M）打 stderr 警告，不报错。"""
    drain_auto_fix_warnings()
    body = _valid_yaml_extra(context_window='"10000M"')  # 10B 绝对异常
    p = _write_config(tmp_path, body)
    cfg, errs = load_config(p)
    assert cfg is not None  # 不报错
    assert cfg.context_window == 10_000_000_000  # 仍生效
    warnings = drain_auto_fix_warnings()
    assert any("context_window" in w for w in warnings)


def test_context_window_invalid_value_errors(tmp_path: Path):
    """无效值（不是数字也不是 K/M 格式）报错。"""
    body = _valid_yaml_extra(context_window='"abc"')
    p = _write_config(tmp_path, body)
    cfg, errs = load_config(p)
    assert cfg is None
    assert any(e.field == "context_window" for e in errs)


def test_context_window_omitted_defaults_to_zero(tmp_path: Path):
    """没写 context_window → 0（budget 用默认 8000）。"""
    p = _write_config(tmp_path, _valid_yaml_extra())
    cfg, _ = load_config(p)
    assert cfg is not None
    assert cfg.context_window == 0
