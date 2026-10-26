"""Зависимости (dependencies) — то, что FastAPI готовит для обработчика перед его вызовом.

Обработчик пишет в параметрах db: Session = Depends(get_db), и FastAPI сам:
открывает сессию → вызывает обработчик → закрывает сессию.
В тестах зависимость легко подменить (app.dependency_overrides), не трогая код обработчиков.
"""
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    # Объект Database создаётся один раз при старте (main.py) и лежит в app.state
    yield from request.app.state.db.session()
