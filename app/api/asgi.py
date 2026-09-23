"""供 Uvicorn 加载的默认 ASGI 应用。"""

import os

from app.api.app import create_app


# The standard server entry point is configurable so production runs and
# validation instances use the same project root as the control plane.
projects_root = os.environ.get("PROJECTOS_PROJECTS_ROOT", "./projects")
app = create_app(projects_root=projects_root)
