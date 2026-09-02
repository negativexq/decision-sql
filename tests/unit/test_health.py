from sqlalchemy import create_engine, text

from app.api.routes import health


class FakeSession:
    def __init__(self, engine: object) -> None:
        self.engine = engine

    def get_bind(self) -> object:
        return self.engine


def test_health_reports_reader_connectivity(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    monkeypatch.setattr("app.api.routes.check_reader_connection", lambda _: True)
    response = health(FakeSession(engine))

    assert response.status == "ok"
    assert response.database == "ok"
