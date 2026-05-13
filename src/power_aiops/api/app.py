from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from power_aiops.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="power-aiops-agents",
        description="电力多智能体 AIOps 编排 API",
        version="0.1.0",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        with open("src/power_aiops/api/static/index.html", encoding="utf-8") as f:
            return f.read()

    @app.get("/visualization", response_class=HTMLResponse)
    def visualization() -> str:
        """GPT-Vis 可视化页面."""
        with open("src/power_aiops/api/static/visualization.html", encoding="utf-8") as f:
            return f.read()

    app.include_router(router, prefix="/incidents", tags=["incidents"])
    app.include_router(router, prefix="/api/v1", tags=["api"])
    return app


app = create_app()
