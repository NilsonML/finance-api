"""Аналитика: сводка за период, динамика по месяцам, прогноз.

Роутер только достаёт данные из базы и передаёт их в чистые функции services/analytics.py.
Сам он ничего не считает — так логика расчётов не перемешана с HTTP и SQL.
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import Kind, Transaction
from ..schemas import Forecast, MonthStats, Summary, TransactionOut
from ..services import analytics
from ..services.analytics import Tx
from ..services.summary_sql import summarize_sql

router = APIRouter(prefix="/stats", tags=["Аналитика"])


def load(db: Session, date_from: dt.date | None = None, date_to: dt.date | None = None) -> list[Tx]:
    """Операции за период в виде лёгких Tx (см. analytics.py).

    Выбираем только нужные колонки, а не объекты целиком — меньше памяти и работы для базы.
    Для помесячной динамики и прогноза (тысячи строк) считать в Python нормально.
    Сводка /summary уже считается в самой базе через GROUP BY — см. services/summary_sql.py.
    """
    stmt = select(Transaction.date, Transaction.kind, Transaction.amount_kop,
                  Transaction.category, Transaction.source, Transaction.hours)
    if date_from:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.date <= date_to)
    return [Tx(*row) for row in db.execute(stmt)]


def check_period(date_from: dt.date | None, date_to: dt.date | None) -> None:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from позже date_to")


@router.get("/summary", response_model=Summary)
def summary(date_from: dt.date | None = None, date_to: dt.date | None = None, db: Session = Depends(get_db)):
    """Доходы, расходы, норма сбережений, расходы по категориям, доход и ставка по источникам."""
    check_period(date_from, date_to)
    # Суммы считает сама база (GROUP BY), а не Python по списку всех операций
    return summarize_sql(db, date_from, date_to)


@router.get("/top-expenses", response_model=list[TransactionOut])
def top_expenses(date_from: dt.date | None = None, date_to: dt.date | None = None,
                 limit: int = Query(5, ge=1, le=50, description="Сколько трат вернуть"),
                 db: Session = Depends(get_db)):
    """Самые крупные расходы за период — от большего к меньшему."""
    check_period(date_from, date_to)
    # Сортировку и обрезку делает база: ORDER BY amount_kop DESC LIMIT 5.
    # Python получает только нужные 5 строк, а не все траты за период.
    stmt = select(Transaction).where(Transaction.kind == Kind.expense)
    if date_from:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.date <= date_to)
    # Дата и id — «тай-брейк»: при одинаковых суммах порядок всё равно однозначный
    # (свежие выше), иначе база могла бы возвращать равные траты в разном порядке.
    stmt = stmt.order_by(Transaction.amount_kop.desc(), Transaction.date.desc(),
                         Transaction.id.desc()).limit(limit)
    # db.scalars — когда в select одна модель целиком: возвращает объекты Transaction, а не кортежи
    return [TransactionOut.from_model(t) for t in db.scalars(stmt)]


@router.get("/monthly", response_model=list[MonthStats])
def monthly(date_from: dt.date | None = None, date_to: dt.date | None = None, db: Session = Depends(get_db)):
    check_period(date_from, date_to)
    return analytics.monthly(load(db, date_from, date_to))


@router.get("/forecast", response_model=Forecast)
def forecast(months: int = Query(3, ge=1, le=12, description="На сколько месяцев вперёд"),
             db: Session = Depends(get_db)):
    try:
        return analytics.forecast(analytics.monthly(load(db)), horizon=months)
    except ValueError as e:
        # Мало данных — это не сбой сервера (500), а ограничение запроса (422) с понятным объяснением
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
