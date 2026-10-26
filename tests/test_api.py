"""Тесты через HTTP: проверяем API так, как им будет пользоваться клиент."""
from pathlib import Path

from conftest import API_KEY

EXAMPLE_CSV = Path(__file__).parent.parent / "examples" / "transactions.csv"


# ---------- безопасность ----------
def test_requires_api_key(client):
    assert client.get("/transactions", headers={"X-API-Key": ""}).status_code == 401
    assert client.get("/transactions", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/transactions", headers={"X-API-Key": API_KEY}).status_code == 200


def test_health_is_public(client):
    assert client.get("/health", headers={"X-API-Key": ""}).json() == {"status": "ok"}


# ---------- создание и проверка данных ----------
def test_money_has_no_float_errors(client, add):
    # 0.1 + 0.2 во float = 0.30000000000000004; в копейках — ровно 0.3
    add("2026-09-01", "income", "0.10")
    add("2026-09-02", "income", "0.20")
    assert client.get("/stats/summary").json()["income"] == 0.3


def test_validation_errors(client):
    bad = [
        {"date": "2026-09-01", "kind": "income", "amount": "-5", "category": "x"},          # минус
        {"date": "2026-09-01", "kind": "gift", "amount": "5", "category": "x"},             # нет такого kind
        {"date": "вчера", "kind": "income", "amount": "5", "category": "x"},                # не дата
        {"date": "2026-09-01", "kind": "expense", "amount": "5", "category": "x", "hours": 2},  # часы у расхода
        {"date": "2026-09-01", "kind": "income", "amount": "5.555", "category": "x"},       # 3 знака после запятой
    ]
    for body in bad:
        assert client.post("/transactions", json=body).status_code == 422, body


def test_category_and_source_are_normalized(add):
    tx = add("2026-09-01", "income", 100, "  Фриланс ", source="KWORK")
    assert (tx["category"], tx["source"]) == ("фриланс", "kwork")


# ---------- список, фильтры, удаление ----------
def test_list_filters_and_pagination(client, add):
    for day in range(1, 6):
        add(f"2026-09-0{day}", "expense", 100 * day, "еда")
    add("2026-09-10", "income", 5000, "фриланс")

    assert len(client.get("/transactions", params={"kind": "expense"}).json()) == 5
    page = client.get("/transactions", params={"kind": "expense", "limit": 2, "offset": 0}).json()
    assert [t["date"] for t in page] == ["2026-09-05", "2026-09-04"]  # новые сверху
    ranged = client.get("/transactions", params={"date_from": "2026-09-02", "date_to": "2026-09-03"}).json()
    assert len(ranged) == 2


def test_patch_changes_only_sent_fields(client, add):
    tx = add("2026-09-01", "expense", 300, "еда", note="обед")
    r = client.patch(f"/transactions/{tx['id']}", json={"amount": "450.50", "category": " Кафе "})
    assert r.status_code == 200
    body = r.json()
    assert (body["amount"], body["category"]) == (450.5, "кафе")      # поменялось, рубли, нормализовано
    assert (body["date"], body["note"]) == ("2026-09-01", "обед")      # не присланное — не тронуто
    assert client.get(f"/transactions/{tx['id']}").json() == body       # и правда сохранилось в базе


def test_patch_errors(client, add):
    expense = add("2026-09-01", "expense", 300, "еда")
    income = add("2026-09-02", "income", 5000, "фриланс", hours=2)
    url = f"/transactions/{expense['id']}"
    assert client.patch("/transactions/999", json={"note": "x"}).status_code == 404
    assert client.patch(url, json={}).status_code == 400                     # нечего менять
    assert client.patch(url, json={"amount": "-5"}).status_code == 422       # те же проверки, что в POST
    assert client.patch(url, json={"category": None}).status_code == 422     # обязательное поле не стереть
    assert client.patch(url, json={"hours": 3}).status_code == 422           # часы у расхода
    # Доход с часами нельзя превратить в расход: проверка учитывает то, что уже лежит в базе
    assert client.patch(f"/transactions/{income['id']}", json={"kind": "expense"}).status_code == 422
    assert client.patch(url, json={"note": None}).status_code == 200         # необязательное — можно очистить


def test_get_and_delete(client, add):
    tx = add("2026-09-01", "expense", 300, "еда")
    assert client.get(f"/transactions/{tx['id']}").json()["amount"] == 300
    assert client.delete(f"/transactions/{tx['id']}").status_code == 204
    assert client.get(f"/transactions/{tx['id']}").status_code == 404
    assert client.delete(f"/transactions/{tx['id']}").status_code == 404


# ---------- импорт ----------
def test_import_example_csv(client):
    with EXAMPLE_CSV.open("rb") as f:
        r = client.post("/transactions/import", files={"file": ("tx.csv", f, "text/csv")})
    assert r.json() == {"imported": 21, "skipped": 0, "duplicates": 0, "errors": []}


def test_import_twice_does_not_duplicate(client):
    def upload():
        with EXAMPLE_CSV.open("rb") as f:
            return client.post("/transactions/import", files={"file": ("tx.csv", f, "text/csv")}).json()

    assert upload()["imported"] == 21
    again = upload()
    assert (again["imported"], again["duplicates"]) == (0, 21)  # всё уже есть — ничего не добавили
    assert len(client.get("/transactions", params={"limit": 500}).json()) == 21


def test_import_skips_existing_and_repeats_in_file(client, add):
    add("2026-09-01", "expense", 350, "еда")              # уже в базе
    csv = ("date,kind,amount,category\n"
           "2026-09-01,expense,350,Еда\n"                  # дубль записи из базы (регистр не важен)
           "2026-09-01,expense,350,такси\n"                # другая категория — не дубль
           "2026-09-02,expense,100,кафе\n"
           "2026-09-02,expense,100,кафе\n")                # повтор внутри файла
    r = client.post("/transactions/import", files={"file": ("d.csv", csv.encode(), "text/csv")}).json()
    assert (r["imported"], r["duplicates"]) == (2, 2)


def test_import_signed_amounts_and_bad_rows(client):
    csv = ("date,amount,category\n"
           "2026-09-01,-350.50,еда\n"        # минус → расход
           "2026-09-02,\"1 500,00\",подарок\n"  # пробел в тысячах и запятая
           "31.02.2026,100,ошибка\n"          # такой даты нет
           "2026-09-03,abc,ошибка\n")         # не число
    r = client.post("/transactions/import", files={"file": ("b.csv", csv.encode(), "text/csv")}).json()
    assert r["imported"] == 2 and r["skipped"] == 2
    assert r["errors"][0].startswith("строка 4")
    kinds = {t["category"]: (t["kind"], t["amount"]) for t in client.get("/transactions").json()}
    assert kinds == {"еда": ("expense", 350.5), "подарок": ("income", 1500.0)}


def test_import_rejects_wrong_columns(client):
    r = client.post("/transactions/import", files={"file": ("x.csv", b"foo;bar\n1;2\n", "text/csv")}).json()
    assert r["imported"] == 0 and "нужны колонки" in r["errors"][0]


# ---------- аналитика ----------
def test_summary_per_hour_and_savings(client, add):
    add("2026-09-01", "income", 10000, "фриланс", source="kwork", hours=5)
    add("2026-09-05", "income", 6000, "фриланс", source="kwork")          # без часов — не влияет на ставку
    add("2026-09-10", "income", 4500, "стипендия", source="вуз")
    add("2026-09-12", "expense", 4100, "еда")
    add("2026-09-15", "expense", 2000, "спорт")
    s = client.get("/stats/summary").json()

    assert (s["income"], s["expense"], s["net"]) == (20500, 6100, 14400)
    assert s["savings_rate"] == round(14400 / 20500, 3)
    assert list(s["expense_by_category"]) == ["еда", "спорт"]           # от больших к меньшим
    kwork = next(x for x in s["income_by_source"] if x["source"] == "kwork")
    assert (kwork["income"], kwork["hours"], kwork["per_hour"]) == (16000, 5, 2000)  # 10000 / 5 ч


def test_monthly_fills_empty_months(client, add):
    add("2026-06-10", "income", 1000)
    add("2026-08-10", "expense", 300)   # июля нет вовсе
    months = client.get("/stats/monthly").json()
    assert [m["month"] for m in months] == ["2026-06", "2026-07", "2026-08"]
    assert months[1] == {"month": "2026-07", "income": 0, "expense": 0, "net": 0}


def test_top_expenses(client, add):
    add("2026-08-30", "expense", 9000, "техника")          # самая крупная, но вне периода
    add("2026-09-01", "expense", 300, "еда")
    add("2026-09-02", "expense", 5000, "жильё")
    add("2026-09-03", "expense", 1200, "кафе")
    add("2026-09-04", "income", 20000, "фриланс")         # доход в топ трат попасть не должен
    top = client.get("/stats/top-expenses", params={"date_from": "2026-09-01", "limit": 2}).json()
    assert [(t["category"], t["amount"]) for t in top] == [("жильё", 5000), ("кафе", 1200)]

    assert client.get("/stats/top-expenses", params={"limit": 0}).status_code == 422  # limit ≥ 1


def test_period_validation(client):
    r = client.get("/stats/summary", params={"date_from": "2026-09-10", "date_to": "2026-09-01"})
    assert r.status_code == 422


def test_forecast_needs_history(client, add):
    add("2026-09-01", "income", 1000)
    r = client.get("/stats/forecast")
    assert r.status_code == 422 and "минимум 3" in r.json()["detail"]
