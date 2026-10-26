"""Общие заготовки: каждый тест получает своё приложение с чистой временной базой."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_KEY = "test-key"


@pytest.fixture
def client(tmp_path):
    # tmp_path — временная папка, которую pytest создаёт для каждого теста и потом удаляет
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/test.db", api_key=API_KEY))
    # with — чтобы выполнился lifespan (создание таблиц) при входе и закрытие базы при выходе
    with TestClient(app, headers={"X-API-Key": API_KEY}) as c:
        yield c


@pytest.fixture
def add(client):
    """Короткий способ создать операцию в тесте: add("2026-09-01", "income", 1000, "фриланс")."""
    def _add(date, kind, amount, category="прочее", **extra):
        r = client.post("/transactions", json={"date": date, "kind": kind, "amount": str(amount),
                                               "category": category, **extra})
        assert r.status_code == 201, r.text
        return r.json()
    return _add
