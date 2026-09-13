"""Coordinator Mode 的双锁与 Lead 工具收窄。"""

from __future__ import annotations

import os
from yucode.config import TeamConfig


COORDINATOR_ENV = "YUCODE_COORDINATOR"
COORDINATOR_NOTICE = """<coordinator-mode>
你是 Team Lead。按四阶段工作：Research（调研）、Synthesis（形成任务与决策）、Implementation（委派成员实施）、Verification（验证并收敛）。不要直接修改项目文件；通过成员、消息和 Git 收敛推进工作。
</coordinator-mode>"""


def active(config: TeamConfig, env: dict[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return config.coordinator_enabled and values.get(COORDINATOR_ENV) == "1"


def allowed_names(names: set[str], enabled: bool) -> set[str]:
    return names - {"write_file", "edit_file"} if enabled else names
