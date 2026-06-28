"""
ModelRegistry：单 provider 模式（启动时校验配置 + 实例化 Model）。

启动流程：
1. `load_config(config_yaml)` → LLMConfig 或 ConfigError 列表
2. 用 LLMConfig.type 选具体 Model 实现（openai-compat / anthropic / demo）
3. `current()` 返回唯一的 Model

CLI 启动时如果 load_config 返回 errors，**先**打印错误退出，不进 REPL。
所以这里的 `build()` 假定配置已经校验过；如果传了非法配置，会 raise。
"""

from __future__ import annotations

from typing import List, Optional

from minicode.config import LLMConfig, load_config
from minicode.model.base import Model, ModelInfo
from minicode.model.openai_compat import OpenAICompatModel
from minicode.model.anthropic import AnthropicModel
from minicode.model.demo import DemoModel


class ModelRegistry:
    """单 provider registry。"""

    def __init__(self, config_path):
        from pathlib import Path
        self._config_path: Path = config_path
        self._config: Optional[LLMConfig] = None
        self._model: Optional[Model] = None

    # ─────────────────────────────────────────
    # 加载
    # ─────────────────────────────────────────

    def build(self) -> List[str]:
        """加载 + 校验 config.yaml。

        返回 errors 列表：
        - 空列表：成功，self._model 已就绪
        - 非空：失败，self._model 仍为 None
        """
        cfg, errors = load_config(self._config_path)
        if errors:
            self._config = None
            self._model = None
            return [e.render(self._config_path) for e in errors]

        self._config = cfg
        self._model = self._build_one(cfg)
        return []

    def _build_one(self, cfg: LLMConfig) -> Model:
        info = ModelInfo(
            id=cfg.provider,
            type=cfg.type,
            base_url=cfg.base_url,
            model=cfg.model,
        )
        if cfg.type == "openai-compat":
            return OpenAICompatModel(info, cfg.api_key, cfg.extra)
        if cfg.type == "anthropic":
            return AnthropicModel(info, cfg.api_key, cfg.extra)
        # 兜底：未知 type → demo（让 CLI 不至于完全不能跑）
        return DemoModel(info, cfg.api_key, cfg.extra)

    # ─────────────────────────────────────────
    # 查询
    # ─────────────────────────────────────────

    def current(self) -> Optional[Model]:
        return self._model

    def config(self) -> Optional[LLMConfig]:
        return self._config

    def context_window(self) -> int:
        """返回配置的上下文窗口大小；未配置返回 0。"""
        if self._config is None:
            return 0
        return self._config.context_window

    def is_ready(self) -> bool:
        return self._model is not None
