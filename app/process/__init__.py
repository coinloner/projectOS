"""流程定义层。

流程定义只描述软件交付阶段、合法转换和系统上限，不描述某个项目的模块数量
或文件布局。项目专属执行图仍由后续动态计划编译器生成。
"""

from app.process.definition import (
    ProcessDefinition,
    ProcessLimits,
    ProcessRegistry,
    StageDefinition,
    TransitionRule,
    default_process_registry,
    software_delivery_process,
)

__all__ = [
    "ProcessDefinition",
    "ProcessLimits",
    "ProcessRegistry",
    "StageDefinition",
    "TransitionRule",
    "default_process_registry",
    "software_delivery_process",
]
