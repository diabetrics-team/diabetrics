"""База: SQLite + SQLAlchemy 2.0.

Владелец: Dev 1.

Всё время в БД — UTC. SQLite не хранит часовой пояс, поэтому колонки времени
имеют тип UTCDateTime: на запись принимают только aware-datetime, на чтение
отдают aware UTC. Часовой пояс пользователя — в users.tz (нужен для ночного режима).

Функции доступа (get_mode, set_mode, save_alert, open_alerts, mark_ack,
recent_alerts) — день 3, в этом же файле.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.models import Mode

UTC = UTC


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN201
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("В БД пишем только datetime с tzinfo (UTC)")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN201
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(100))
    ns_url: Mapped[str | None] = mapped_column(String(300))
    ns_token: Mapped[str | None] = mapped_column(String(200))  # только на чтение; в логи не пишем
    tz: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    escalation_minutes: Mapped[int] = mapped_column(Integer, default=10)
    night_start: Mapped[str] = mapped_column(String(5), default="23:00")  # локальное время, HH:MM
    night_end: Mapped[str] = mapped_column(String(5), default="07:00")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # False — заблокировал бота
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ReadingRow(Base):
    __tablename__ = "readings"
    __table_args__ = (
        # одно показание не сохраняется дважды
        UniqueConstraint("user_id", "ts", name="uq_readings_user_ts"),
        Index("ix_readings_user_ts", "user_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    mmol: Mapped[float] = mapped_column(Float)
    direction: Mapped[str | None] = mapped_column(String(32))


class AlertRow(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(16))   # Level.value
    kind: Mapped[str] = mapped_column(String(32))    # AlertKind.value
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    value: Mapped[float | None] = mapped_column(Float)
    needs_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    acked_by: Mapped[int | None] = mapped_column(BigInteger)  # telegram id нажавшего, как в Alert
    escalated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # для повтора раз в 5 мин
    message_id: Mapped[int | None] = mapped_column(BigInteger)          # чтобы погасить кнопку


class ModeRow(Base):
    """Текущий режим: одна строка на пользователя."""

    __tablename__ = "modes"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(16), default=Mode.NORMAL.value)
    until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)


class TrustedContact(Base):
    __tablename__ = "trusted_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    invite_code: Mapped[str] = mapped_column(String(64), unique=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)  # None — приглашение ещё не принято
    name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


def make_engine(url: str = "sqlite:///data/diabetrics.db", *, echo: bool = False) -> Engine:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, echo=echo)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001, ANN202
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def init_db(engine: Engine) -> None:
    """Создать таблицы, если их нет. Миграций в MVP нет: схему поменяли — удалить .db."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
