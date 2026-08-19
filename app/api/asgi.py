"""供 Uvicorn 加载的默认 ASGI 应用。"""

from app.api.app import create_app


app = create_app()
