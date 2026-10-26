"""Точка входа: uvicorn app.main:app --reload

create_app() — «фабрика приложения»: собирает FastAPI из частей.
Зачем фабрика, а не просто app = FastAPI() в начале файла:
в тестах мы создаём отдельное приложение с временной базой и своим ключом,
не трогая настоящую базу и не перезагружая модули.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from .config import Settings, get_settings
from .db import Database
from .routers import stats, transactions
from .security import require_api_key


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Код до yield выполняется один раз при старте сервера, после yield — при остановке
        if settings.api_key == "change-me":
            logging.getLogger(__name__).warning(
                "API_KEY не задан — используется ключ по умолчанию. Задай свой в .env перед деплоем!")
        app.state.db = Database(settings.database_url)
        app.state.db.create_tables()
        yield
        app.state.db.engine.dispose()  # аккуратно закрываем соединения с базой

    app = FastAPI(
        title="Finance API",
        description="Учёт доходов и расходов: метрики по источникам дохода и прогноз денежного потока.",
        version="1.0.0",
        lifespan=lifespan,
    )
    # Подменяем настройки, чтобы require_api_key проверял ключ именно этого экземпляра приложения
    app.dependency_overrides[get_settings] = lambda: settings

    # dependencies=[...] на уровне роутера: ключ проверяется у ВСЕХ его адресов,
    # и нельзя случайно забыть защиту у нового эндпоинта
    protected = [Depends(require_api_key)]
    app.include_router(transactions.router, dependencies=protected)
    app.include_router(stats.router, dependencies=protected)

    @app.get("/health", tags=["Служебное"])
    def health():
        # Без ключа: по этому адресу Docker и хостинги проверяют, что сервис жив
        return {"status": "ok"}

    return app


app = create_app()
