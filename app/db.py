"""Подключение к базе данных.

Три сущности SQLAlchemy, которые нужно понимать:
- engine   — «двигатель»: знает, как подключаться к базе (одно на всё приложение);
- Session  — «сеанс работы»: в нём делаются запросы и копятся изменения до commit();
- Base     — родительский класс для моделей (таблиц), см. models.py.
"""
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        # SQLite по умолчанию запрещает пользоваться соединением из другого потока,
        # а FastAPI обрабатывает запросы в пуле потоков — снимаем это ограничение.
        connect_args["check_same_thread"] = False
        # Создаём папку под файл базы, если её нет (sqlite:///./data/finance.db → ./data)
        # make_url сам разбирает URL: для "sqlite://" (база в памяти) database будет None
        path = make_url(url).database
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, connect_args=connect_args)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _):
            # В SQLite проверка внешних ключей выключена по умолчанию — включаем на каждом соединении
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


class Database:
    """Держит engine и фабрику сессий. Один объект на приложение (создаётся в main.py)."""

    def __init__(self, url: str):
        self.engine = make_engine(url)
        # expire_on_commit=False: после commit() объекты остаются читаемыми,
        # иначе при возврате ответа SQLAlchemy полезла бы в базу ещё раз
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_tables(self) -> None:
        # Создаёт таблицы, которых ещё нет. Для проекта такого размера этого достаточно;
        # в больших проектах изменения схемы ведут миграциями (Alembic)
        from . import models  # noqa: F401 — импорт регистрирует модели в Base.metadata
        Base.metadata.create_all(self.engine)

    def session(self) -> Iterator[Session]:
        """Зависимость FastAPI: на каждый запрос — своя сессия, которая гарантированно закрывается.

        yield вместо return: код до yield выполняется перед обработкой запроса,
        код после (в finally) — после ответа, даже если внутри произошла ошибка.
        """
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()
