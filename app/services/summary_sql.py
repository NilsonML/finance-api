"""Сводка за период, посчитанная самой базой данных через GROUP BY.

Чем отличается от analytics.summarize:
- там Python получает ВСЕ операции за период и складывает их в циклах;
- здесь база возвращает уже готовые суммы: по строке на тип операции, категорию или источник.
На тысячах операций разницы почти нет, а на миллионах это быстрее и экономнее по памяти:
по сети едут не все строки, а только итоги.

Отдельный модуль, а не analytics.py: тот модуль намеренно ничего не знает о базе.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from ..models import Kind, Transaction
from ..schemas import SourceStats, Summary, kop_to_rub
from .analytics import budget_split


def in_period(stmt: Select, date_from: dt.date | None, date_to: dt.date | None) -> Select:
    """Добавляет к запросу фильтр по датам. Каждый .where() дописывает условие через AND."""
    if date_from:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.date <= date_to)
    return stmt


def summarize_sql(db: Session, date_from: dt.date | None = None, date_to: dt.date | None = None) -> Summary:
    # --- доход и расход: SELECT kind, SUM(amount_kop) FROM transactions GROUP BY kind
    # Результат — до двух строк вида (Kind.income, 20500), (Kind.expense, 6100).
    # dict(...) превращает список пар в словарь {тип: сумма}.
    totals = dict(db.execute(in_period(
        select(Transaction.kind, func.sum(Transaction.amount_kop)).group_by(Transaction.kind),
        date_from, date_to,
    )).all())
    # Если операций такого типа за период нет, строки для него в ответе просто не будет → 0
    income = totals.get(Kind.income, 0)
    expense = totals.get(Kind.expense, 0)

    # --- расходы по категориям: только expense, сумма по каждой категории, от больших к меньшим.
    # Сумму пишем один раз в переменную, чтобы использовать и в SELECT, и в ORDER BY.
    cat_total = func.sum(Transaction.amount_kop)
    by_category = db.execute(in_period(
        select(Transaction.category, cat_total)
        .where(Transaction.kind == Kind.expense)
        .group_by(Transaction.category)
        .order_by(cat_total.desc()),  # сортирует база, в Python sorted() больше не нужен
        date_from, date_to,
    )).all()

    # --- доход и часы по источникам: та же логика, что в analytics.summarize, но одним запросом.
    # coalesce(a, b) — «a, а если a пустое (NULL), то b»: доходы без источника собираем в одну группу.
    source = func.coalesce(Transaction.source, "без источника")
    # SUM по колонке пропускает NULL, поэтому часы складываются только там, где они указаны.
    # Если часов нет ни у одной операции источника, SUM вернёт NULL → в Python это None.
    hours = func.sum(Transaction.hours)
    # case(...) — это if внутри SQL: берём сумму операции, только если у неё указаны часы, иначе 0.
    # Так ставка «рублей в час» считается по тем же операциям, что и часы (как в старой функции).
    paid_with_hours = func.sum(case((Transaction.hours.is_not(None), Transaction.amount_kop), else_=0))
    source_rows = db.execute(in_period(
        select(source, func.sum(Transaction.amount_kop), hours, paid_with_hours)
        .where(Transaction.kind == Kind.income)
        .group_by(source),
        date_from, date_to,
    )).all()

    sources = [
        SourceStats(
            source=name,
            income=kop_to_rub(total),
            hours=round(h, 2) if h else None,
            per_hour=kop_to_rub(round(paid / h)) if h else None,
        )
        for name, total, h, paid in source_rows
    ]
    sources.sort(key=lambda s: s.income, reverse=True)

    return Summary(
        date_from=date_from, date_to=date_to,
        income=kop_to_rub(income), expense=kop_to_rub(expense), net=kop_to_rub(income - expense),
        savings_rate=round((income - expense) / income, 3) if income else None,
        # Строки уже отсортированы базой, а словарь в Python сохраняет порядок вставки
        expense_by_category={category: kop_to_rub(total) for category, total in by_category},
        income_by_source=sources,
        # Та же функция, что в старой сводке: ей нужны копейки по категориям — строки запроса их и содержат
        budget_split=budget_split(income, dict(by_category)),
    )
