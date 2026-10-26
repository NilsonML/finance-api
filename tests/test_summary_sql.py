"""SQL-сводка (GROUP BY в базе) должна давать ровно то же, что старая сводка на Python."""
import datetime as dt

import pytest

from app.db import Database
from app.models import Kind, Transaction
from app.routers.stats import load
from app.services.analytics import summarize
from app.services.summary_sql import summarize_sql


@pytest.fixture
def db(tmp_path):
    # Своя временная база без HTTP: тестируем сами функции, а не эндпоинт
    database = Database(f"sqlite:///{tmp_path}/test.db")
    database.create_tables()
    with database.SessionLocal() as session:
        yield session
    database.engine.dispose()  # закрываем соединения, иначе Windows не даст удалить файл базы


def add(db, date, kind, rub, category="прочее", source=None, hours=None):
    db.add(Transaction(date=dt.date.fromisoformat(date), kind=kind, amount_kop=rub * 100,
                       category=category, source=source, hours=hours))


@pytest.fixture
def filled(db):
    # Разные случаи сразу: часы есть и нет, доход без источника, несколько трат в одной категории,
    # операции в разных месяцах (для проверки периода). Суммы разные, чтобы порядок сортировки был однозначным.
    add(db, "2026-08-20", Kind.income, 3000, "фриланс", source="kwork", hours=2)
    add(db, "2026-09-01", Kind.income, 10000, "фриланс", source="kwork", hours=5)
    add(db, "2026-09-05", Kind.income, 6000, "фриланс", source="kwork")
    add(db, "2026-09-10", Kind.income, 4500, "стипендия", source="вуз")
    add(db, "2026-09-11", Kind.income, 700, "подарок")                      # без источника
    add(db, "2026-08-25", Kind.expense, 900, "такси")
    add(db, "2026-09-12", Kind.expense, 4100, "еда")
    add(db, "2026-09-13", Kind.expense, 1500, "еда")
    add(db, "2026-09-15", Kind.expense, 2000, "спорт")
    db.commit()
    return db


@pytest.mark.parametrize("date_from, date_to", [
    (None, None),                                    # всё время
    (dt.date(2026, 9, 1), None),                     # только с сентября: августовские операции не в счёт
    (None, dt.date(2026, 8, 31)),                    # только август
    (dt.date(2026, 9, 1), dt.date(2026, 9, 10)),     # кусок месяца: трат в нём нет вовсе
])
def test_sql_summary_matches_python(filled, date_from, date_to):
    old = summarize(load(filled, date_from, date_to), date_from, date_to)
    new = summarize_sql(filled, date_from, date_to)
    # Pydantic-модели сравниваются по всем полям сразу
    assert new == old
    # Словари при == не смотрят на порядок ключей, а нам важна сортировка категорий — проверяем отдельно
    assert list(new.expense_by_category) == list(old.expense_by_category)


def test_sql_summary_empty_db(db):
    # Без операций SUM вернул бы NULL — проверяем, что получаются нули, а не ошибка
    assert summarize_sql(db) == summarize([])
