"""Domain 模块安装清单。

这里是唯一的运行时模块组合位置；main.py、FastAPI 和测试不再逐个注册 Agent/Tool。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer

from app.domain.architecture.module import install as install_architecture
from app.domain.bootstrap.module import install as install_bootstrap
from app.domain.code.module import install as install_code
from app.domain.requirement.module import install as install_requirement
from app.domain.review.module import install as install_review
from app.domain.task.module import install as install_task
from app.domain.test.module import install as install_test
from app.workflow.templates import (
    architecture_compact_template,
    architecture_parallel_template,
    project_delivery_minimal_template,
)


MODULES: tuple[Callable[["ProjectOSContainer"], None], ...] = (
    install_requirement,
    install_architecture,
    install_task,
    install_bootstrap,
    install_code,
    install_test,
    install_review,
)


def install_modules(container: "ProjectOSContainer") -> None:
    for install in MODULES:
        install(container)
    container.templates.register(architecture_compact_template())
    container.templates.register(architecture_parallel_template())
    container.templates.register(project_delivery_minimal_template())
