"""База: SQLite + SQLAlchemy 2.0.

Владелец: Dev 1.

Всё время в БД — UTC. SQLite не хранит часовой пояс, поэтому колонки времени
имеют тип UTCDateTime: на запись принимают только aware-datetime, на чтение
отдают aware UTC. Часовой пояс пользователя — в users.tz (нужен для ночного режима).

Использование:
    from app import db
    db.configure("sqlite:///data/diabetrics.db")   # один раз при старте (main.py)
    db.get_mode(user_id)

Функции доступа синхронные: SQLite локальный, запросы занимают миллисекунды,
отдельный поток ради них не нужен. user_id в функциях — внутренний users.id;
по telegram id пользователя находит get_user_by_chat().
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
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
    delete,
    event,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.alerts.antispam import EPISODE_KINDS
from app.models import Alert, AlertKind, Level, Mode, ModeState, Reading

UTC = timezone.utc


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
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)  # = telegram id
    name: Mapped[str | None] = mapped_column(String(100))
    ns_url: Mapped[str | None] = mapped_column(String(300))
    ns_token: Mapped[str | None] = mapped_column(String(200))  # только на чтение; в логи не пишем
    tz: Mapped[str | None] = mapped_column(String(64))         # None — ещё не выбран в онбординге
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


# ------------------------------------------------------------------ подключение ---

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


_engine: Engine | None = None
_Session: sessionmaker | None = None


def configure(url: str) -> None:
    """Подключиться к БД и создать таблицы. Повторный вызов переподключает (тесты)."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url)
    init_db(_engine)
    _Session = make_session_factory(_engine)


def is_configured() -> bool:
    return _Session is not None


def _session() -> Session:
    if _Session is None:
        raise RuntimeError("БД не подключена: вызовите db.configure(url) при старте")
    return _Session()


# ------------------------------------------------------------------ пользователи ---

def get_user(user_id: int) -> User | None:
    with _session() as s:
        return s.get(User, user_id)


def get_user_by_chat(chat_id: int) -> User | None:
    with _session() as s:
        return s.scalar(select(User).where(User.chat_id == chat_id))


def upsert_user(chat_id: int, now: datetime, **fields: object) -> User:
    """Создать или обновить пользователя по telegram id. fields — колонки User."""
    unknown = set(fields) - set(User.__table__.columns.keys())
    if unknown:
        raise ValueError(f"Нет таких колонок в users: {sorted(unknown)}")
    with _session() as s, s.begin():
        user = s.scalar(select(User).where(User.chat_id == chat_id))
        if user is None:
            user = User(chat_id=chat_id, created_at=now)
            s.add(user)
        for key, value in fields.items():
            setattr(user, key, value)
    return user


def active_users() -> list[User]:
    """Кого опрашивает pipeline: активные и с подключённым Nightscout."""
    with _session() as s:
        return list(s.scalars(
            select(User).where(User.is_active.is_(True), User.ns_url.is_not(None))
        ))


def set_active(user_id: int, active: bool) -> None:
    """False — пользователь заблокировал бота: перестаём опрашивать и слать."""
    with _session() as s, s.begin():
        s.execute(update(User).where(User.id == user_id).values(is_active=active))


# ------------------------------------------------------------------ показания ---

def last_reading_ts(user_id: int) -> datetime | None:
    with _session() as s:
        return s.scalar(
            select(ReadingRow.ts).where(ReadingRow.user_id == user_id)
            .order_by(ReadingRow.ts.desc()).limit(1)
        )


def save_readings(user_id: int, readings: Iterable[Reading]) -> list[Reading]:
    """Сохранить новые показания. Возвращает только реально новые (уже сохранённые пропускает)."""
    readings = list(readings)
    if not readings:
        return []
    with _session() as s, s.begin():
        existing = set(s.scalars(
            select(ReadingRow.ts).where(
                ReadingRow.user_id == user_id,
                ReadingRow.ts.in_([r.ts for r in readings]),
            )
        ))
        new = [r for r in readings if r.ts not in existing]
        s.add_all(
            ReadingRow(user_id=user_id, ts=r.ts, mmol=r.mmol, direction=r.direction) for r in new
        )
    return new


def readings_since(user_id: int, since: datetime) -> list[Reading]:
    """История для predict/evaluate: от старых к новым."""
    with _session() as s:
        rows = s.scalars(
            select(ReadingRow).where(ReadingRow.user_id == user_id, ReadingRow.ts >= since)
            .order_by(ReadingRow.ts)
        )
        return [Reading(ts=r.ts, mmol=r.mmol, direction=r.direction) for r in rows]


# ------------------------------------------------------------------ режимы ---

def get_mode(user_id: int) -> ModeState:
    with _session() as s:
        row = s.get(ModeRow, user_id)
        if row is None:
            return ModeState()
        return ModeState(kind=Mode(row.kind), until=row.until)


def set_mode(user_id: int, state: ModeState, now: datetime) -> None:
    with _session() as s, s.begin():
        row = s.get(ModeRow, user_id)
        if row is None:
            row = ModeRow(user_id=user_id)
            s.add(row)
        row.kind = Mode(state.kind).value
        row.until = state.until
        row.started_at = now


# ------------------------------------------------------------------ уведомления ---

def _to_alert(row: AlertRow) -> Alert:
    return Alert(
        level=Level(row.level),
        kind=AlertKind(row.kind),
        created_at=row.created_at,
        value=row.value,
        needs_ack=row.needs_ack,
        id=row.id,
        user_id=row.user_id,
        acked_at=row.acked_at,
        acked_by=row.acked_by,
        escalated_at=row.escalated_at,
    )


def save_alert(alert: Alert, sent_at: datetime | None = None) -> Alert:
    """Сохранить новое уведомление. Проставляет alert.id. alert.user_id обязателен."""
    if alert.user_id is None:
        raise ValueError("save_alert: у Alert не задан user_id")
    with _session() as s, s.begin():
        row = AlertRow(
            user_id=alert.user_id,
            level=Level(alert.level).value,
            kind=AlertKind(alert.kind).value,
            created_at=alert.created_at,
            value=alert.value,
            needs_ack=alert.needs_ack,
            acked_at=alert.acked_at,
            acked_by=alert.acked_by,
            escalated_at=alert.escalated_at,
            last_sent_at=sent_at,
        )
        s.add(row)
        s.flush()
        alert.id = row.id
    return alert


def get_alert(alert_id: int) -> Alert | None:
    with _session() as s:
        row = s.get(AlertRow, alert_id)
        return _to_alert(row) if row is not None else None


def open_alerts(user_id: int | None = None) -> list[Alert]:
    """Критические без «Я в порядке». Читаются из БД — эскалация переживает перезапуск."""
    with _session() as s:
        q = select(AlertRow).where(AlertRow.needs_ack.is_(True), AlertRow.acked_at.is_(None),
                                   AlertRow.escalated_at.is_(None))
        if user_id is not None:
            q = q.where(AlertRow.user_id == user_id)
        return [_to_alert(r) for r in s.scalars(q.order_by(AlertRow.created_at))]


def recent_alerts(user_id: int, since: datetime) -> list[Alert]:
    """Уведомления пользователя с момента since (для антиспама в evaluate)."""
    with _session() as s:
        rows = s.scalars(
            select(AlertRow).where(AlertRow.user_id == user_id, AlertRow.created_at >= since)
            .order_by(AlertRow.created_at)
        )
        return [_to_alert(r) for r in rows]


def latest_episode_alerts(user_id: int, now: datetime) -> list[Alert]:
    """Keep hysteresis and the last outage state past the recent-alert window."""
    ranked = (
        select(AlertRow.id, func.row_number().over(
            partition_by=(AlertRow.kind, AlertRow.level),
            order_by=(AlertRow.created_at.desc(), AlertRow.id.desc()),
        ).label("position"))
        .where(AlertRow.user_id == user_id, AlertRow.created_at <= now,
               AlertRow.kind.in_([kind.value for kind in EPISODE_KINDS]))
        .subquery()
    )
    with _session() as s:
        rows = s.scalars(select(AlertRow).join(ranked, ranked.c.id == AlertRow.id).where(ranked.c.position == 1))
        return [_to_alert(row) for row in rows]


def mark_ack(alert_id: int, who: int, at: datetime) -> bool:
    """«Я в порядке». who — telegram id нажавшего. False — уже было подтверждено раньше."""
    owner = select(User.id).where(User.id == AlertRow.user_id, User.chat_id == who).exists()
    trusted = select(TrustedContact.id).where(
        TrustedContact.user_id == AlertRow.user_id,
        TrustedContact.chat_id == who,
        TrustedContact.confirmed_at.is_not(None),
    ).exists()
    with _session() as s, s.begin():
        result = s.execute(
            update(AlertRow)
            .where(AlertRow.id == alert_id, AlertRow.needs_ack.is_(True), AlertRow.acked_at.is_(None),
                   or_(owner, trusted))
            .values(acked_at=at, acked_by=who)
        )
        return result.rowcount == 1


def mark_sent(alert_id: int, at: datetime, message_id: int | None = None) -> None:
    """Уведомление (или его повтор) ушло пользователю."""
    values: dict[str, object] = {"last_sent_at": at}
    if message_id is not None:
        values["message_id"] = message_id
    with _session() as s, s.begin():
        s.execute(update(AlertRow).where(AlertRow.id == alert_id).values(**values))


def last_sent_at(alert_id: int) -> datetime | None:
    with _session() as s:
        return s.scalar(select(AlertRow.last_sent_at).where(AlertRow.id == alert_id))


def mark_escalated(alert_id: int, at: datetime) -> bool:
    """Ушло доверенному лицу. False — уже эскалировано раньше (повторно не шлём)."""
    with _session() as s, s.begin():
        result = s.execute(
            update(AlertRow)
            .where(AlertRow.id == alert_id, AlertRow.acked_at.is_(None), AlertRow.escalated_at.is_(None))
            .values(escalated_at=at)
        )
        return result.rowcount == 1


# ------------------------------------------------------------------ доверенные лица ---

def create_invite(user_id: int, code: str, now: datetime) -> None:
    with _session() as s, s.begin():
        s.add(TrustedContact(user_id=user_id, invite_code=code, created_at=now))


def accept_invite(code: str, chat_id: int, name: str | None, now: datetime) -> User | None:
    """Доверенное лицо перешло по ссылке. Возвращает пользователя, который пригласил."""
    with _session() as s, s.begin():
        tc = s.scalar(select(TrustedContact).where(
            TrustedContact.invite_code == code, TrustedContact.confirmed_at.is_(None)
        ))
        if tc is None:
            return None
        tc.chat_id, tc.name, tc.confirmed_at = chat_id, name, now
        return s.get(User, tc.user_id)


def trusted_chat_ids(user_id: int) -> list[int]:
    with _session() as s:
        return list(s.scalars(select(TrustedContact.chat_id).where(
            TrustedContact.user_id == user_id, TrustedContact.confirmed_at.is_not(None)
        )))


def remove_trusted(user_id: int, chat_id: int | None = None) -> int:
    """Отвязать доверенных лиц пользователя (всех или одного). Возвращает, сколько удалено."""
    with _session() as s, s.begin():
        q = delete(TrustedContact).where(TrustedContact.user_id == user_id)
        if chat_id is not None:
            q = q.where(TrustedContact.chat_id == chat_id)
        return s.execute(q).rowcount


# ------------------------------------------- для бота (Dev 3): доверенные лица, кнопки ---

def invite_owner(code: str) -> User | None:
    """Кто пригласил по коду, если приглашение ещё не принято.

    Боту нужно знать это ДО accept_invite: иначе, если человек открыл собственную ссылку,
    приглашение уже израсходовано и настоящему доверенному лицу не достанется.
    """
    with _session() as s:
        return s.scalar(
            select(User).join(TrustedContact, TrustedContact.user_id == User.id)
            .where(TrustedContact.invite_code == code, TrustedContact.confirmed_at.is_(None))
        )


def drop_pending_invites(user_id: int) -> None:
    """Старые непринятые ссылки перестают работать: действует только последняя выданная."""
    with _session() as s, s.begin():
        s.execute(delete(TrustedContact).where(
            TrustedContact.user_id == user_id, TrustedContact.confirmed_at.is_(None)
        ))


def trusted_contacts(user_id: int) -> list[TrustedContact]:
    """Подтверждённые доверенные лица пользователя — с именами (trusted_chat_ids — без)."""
    with _session() as s:
        return list(s.scalars(
            select(TrustedContact)
            .where(TrustedContact.user_id == user_id, TrustedContact.confirmed_at.is_not(None))
            .order_by(TrustedContact.confirmed_at)
        ))


def guarded_users(chat_id: int) -> list[User]:
    """Для кого этот telegram id — подтверждённое доверенное лицо."""
    with _session() as s:
        return list(s.scalars(
            select(User).join(TrustedContact, TrustedContact.user_id == User.id)
            .where(TrustedContact.chat_id == chat_id, TrustedContact.confirmed_at.is_not(None))
            .order_by(User.id)
        ))


def alert_message_id(alert_id: int) -> int | None:
    """Сообщение с уведомлением (из mark_sent) — чтобы погасить у него кнопку «Я в порядке»."""
    with _session() as s:
        return s.scalar(select(AlertRow.message_id).where(AlertRow.id == alert_id))
