"""Операции: создать, посмотреть список, получить одну, удалить, импортировать из CSV.

REST-соглашения, которым следуем (их ждут от любого API):
- POST   /transactions        → создать, ответ 201 Created
- GET    /transactions        → список с фильтрами и постраничной выдачей
- GET    /transactions/{id}   → одна операция или 404 Not Found
- PATCH  /transactions/{id}   → поменять отдельные поля, ответ — операция целиком
- DELETE /transactions/{id}   → удалить, ответ 204 No Content (тело пустое)
"""
import datetime as dt

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import Kind, Transaction
from ..schemas import ImportResult, TransactionIn, TransactionOut, TransactionUpdate, rub_to_kop
from ..services.importer import parse_csv

router = APIRouter(prefix="/transactions", tags=["Операции"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 МБ — защита от загрузки гигантского файла, который съест память
MAX_ERRORS_SHOWN = 20


def to_model(data: TransactionIn) -> Transaction:
    """Схема API → строка базы. Единственное место, где рубли превращаются в копейки."""
    return Transaction(date=data.date, kind=data.kind, amount_kop=rub_to_kop(data.amount),
                       category=data.category, source=data.source, hours=data.hours, note=data.note)


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(data: TransactionIn, db: Session = Depends(get_db)):
    # data уже проверена pydantic: если сюда дошли — поля корректны
    tx = to_model(data)
    db.add(tx)
    db.commit()  # без commit изменения не сохранятся — они живут только в сессии
    return TransactionOut.from_model(tx)


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    db: Session = Depends(get_db),
    # Query(...) описывает параметры адресной строки: /transactions?kind=expense&limit=20
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    kind: Kind | None = None,
    category: str | None = None,
    source: str | None = None,
    limit: int = Query(50, ge=1, le=500),  # ограничение сверху: нельзя выгрузить миллион строк разом
    offset: int = Query(0, ge=0),
):
    # Запрос собирается по шагам: добавляем условия только для переданных фильтров
    stmt = select(Transaction)
    if date_from:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.date <= date_to)
    if kind:
        stmt = stmt.where(Transaction.kind == kind)
    if category:
        stmt = stmt.where(Transaction.category == category.strip().lower())
    if source:
        stmt = stmt.where(Transaction.source == source.strip().lower())

    # Сортировка обязательна для постраничной выдачи: без неё база может вернуть строки
    # в разном порядке, и одна операция окажется на двух страницах, а другая — ни на одной.
    stmt = stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit).offset(offset)
    return [TransactionOut.from_model(t) for t in db.scalars(stmt)]


@router.get("/{tx_id}", response_model=TransactionOut)
def get_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)  # поиск по первичному ключу
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Операция {tx_id} не найдена")
    return TransactionOut.from_model(tx)


@router.delete("/{tx_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Операция {tx_id} не найдена")
    db.delete(tx)
    db.commit()


@router.post("/import", response_model=ImportResult)
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # async def — потому что чтение загруженного файла асинхронное (await file.read)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файл больше 2 МБ")
    try:
        items, errors = parse_csv(content)
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл должен быть в кодировке UTF-8") from None

    new, duplicates = drop_duplicates(db, items)

    # add_all + один commit: все строки сохраняются одной транзакцией базы —
    # либо все, либо ни одной (если база упадёт посередине, полуимпорта не будет)
    db.add_all(to_model(i) for i in new)
    db.commit()
    # Считаем все пропущенные строки, но показываем не больше 20 ошибок:
    # если файл целиком не того формата, тысяча одинаковых сообщений никому не поможет
    return ImportResult(imported=len(new), skipped=len(errors), duplicates=duplicates,
                        errors=errors[:MAX_ERRORS_SHOWN])


def drop_duplicates(db: Session, items: list[TransactionIn]) -> tuple[list[TransactionIn], int]:
    """Убирает операции, которые уже есть в базе. Возвращает (новые операции, число дублей).

    Дубль — совпадают дата, тип, сумма и категория. Это «ключ» операции: кортеж из четырёх полей.
    Ключи существующих операций кладём в set — проверка «ключ in set» мгновенная,
    сколько бы элементов там ни было (в отличие от поиска по списку).

    Компромисс: две одинаковые траты в один день (кофе за 200 ₽ утром и вечером)
    тоже сочтутся дублем. Чтобы их различать, в CSV нужен уникальный id операции от банка.
    """
    if not items:
        return [], 0

    # Всю таблицу не читаем — только операции в диапазоне дат из файла: остальные дублями быть не могут.
    # Выбираем 4 колонки, а не объекты целиком: нужны только поля ключа.
    dates = [i.date for i in items]
    rows = db.execute(
        select(Transaction.date, Transaction.kind, Transaction.amount_kop, Transaction.category)
        .where(Transaction.date.between(min(dates), max(dates)))
    )
    existing = {tuple(row) for row in rows}

    new, duplicates = [], 0
    for item in items:
        # Ключ должен выглядеть так же, как строка из базы:
        # - сумма в копейках (в TransactionIn рубли, в базе копейки);
        # - категория уже в нижнем регистре — её нормализовал валидатор TransactionIn;
        # - Kind наследует str, поэтому Kind из базы и Kind из файла дают одинаковый ключ.
        key = (item.date, item.kind, rub_to_kop(item.amount), item.category)
        if key in existing:
            duplicates += 1
            continue
        # Запоминаем и новые ключи — так отсекаются и повторы внутри самого файла
        existing.add(key)
        new.append(item)
    return new, duplicates


# Путь "/{tx_id}", а не "/transactions/{id}": префикс /transactions уже задан в APIRouter,
# а имя в фигурных скобках должно совпадать с именем параметра функции — так FastAPI их связывает.
@router.patch("/{tx_id}", response_model=TransactionOut)
def update_transaction(tx_id: int, data: TransactionUpdate, db: Session = Depends(get_db)):
    # 1. Только те поля, которые клиент прислал. Без exclude_unset получили бы все поля,
    #    и непереданные (None) затёрли бы данные в базе.
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пришлите хотя бы одно поле для изменения")

    # 2. Ищем операцию по первичному ключу — так же, как в get_transaction
    tx = db.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Операция {tx_id} не найдена")

    # 3. Проверка связи полей, которую схема одна сделать не может: ей не видно, что уже лежит в базе.
    #    Например, у дохода с часами меняют kind на expense — часы у расхода запрещены.
    #    .get(поле, текущее значение): новое значение, если прислали, иначе то, что в базе.
    new_kind = changes.get("kind", tx.kind)
    new_hours = changes.get("hours", tx.hours)
    if new_hours is not None and new_kind != Kind.income:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "hours можно указывать только для доходов")

    # 4. Рубли → копейки: в базе колонка amount_kop, поля amount там нет
    if "amount" in changes:
        changes["amount_kop"] = rub_to_kop(changes.pop("amount"))  # pop: достаёт значение и удаляет ключ

    # 5. setattr(obj, "category", "еда") — то же, что obj.category = "еда", но имя поля в переменной
    for field, value in changes.items():
        setattr(tx, field, value)
    db.commit()
    # refresh не нужен: expire_on_commit=False (db.py), объект после commit остаётся актуальным.
    # Отдаём через TransactionOut — иначе клиент получил бы копейки вместо рублей.
    return TransactionOut.from_model(tx)
