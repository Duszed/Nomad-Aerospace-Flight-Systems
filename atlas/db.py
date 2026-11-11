"""Database models. SQLite on the laptop; the same models run on Postgres.

Rule: a signed record (`RecordVersion`) is never edited or deleted. A change
creates a new version that points at the hash of the one it supersedes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine, event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """Always UTC, always timezone-aware in Python (SQLite drops tzinfo)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: Atlas stores timezone-aware UTC only")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class Farmer(Base):
    __tablename__ = "farmer"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    language: Mapped[str] = mapped_column(String(2), default="uz")
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    registered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Field(Base):
    __tablename__ = "field"
    __table_args__ = (UniqueConstraint("farmer_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farmer.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    boundary_geojson: Mapped[str] = mapped_column(Text)
    area_ha: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(Base):
    """One spray job. Its facts live in the signed record versions."""

    __tablename__ = "job"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farmer.id"), index=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("field.id"), index=True)
    sample_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    farmer: Mapped[Farmer] = relationship()
    field: Mapped[Field] = relationship()
    versions: Mapped[list["RecordVersion"]] = relationship(
        back_populates="job", order_by="RecordVersion.version")


class RecordVersion(Base):
    """A signed, immutable version of a job's record."""

    __tablename__ = "record_version"
    __table_args__ = (UniqueConstraint("job_id", "version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("job.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)            # canonical JSON, as signed
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    signature: Mapped[str] = mapped_column(Text)          # base64 Ed25519
    key_id: Mapped[str] = mapped_column(String(16))
    prev_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="versions")


class SigningKey(Base):
    """Public keys this installation trusts to sign records."""

    __tablename__ = "signing_key"

    key_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    public_key: Mapped[str] = mapped_column(Text)         # base64 raw Ed25519
    label: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Share(Base):
    """An explicit, expiring, revocable grant to view one record.

    recipient_type "owner" is the farmer's own link (the QR on their PDF).
    """

    __tablename__ = "share"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("record_version.id"), index=True)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farmer.id"), index=True)
    recipient_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    record: Mapped[RecordVersion] = relationship()
    events: Mapped[list["ShareEvent"]] = relationship(
        back_populates="share", order_by="ShareEvent.id")


class ShareEvent(Base):
    """Audit log: every share creation, opening and revocation."""

    __tablename__ = "share_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    share_id: Mapped[str] = mapped_column(ForeignKey("share.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(20))   # created | opened | revoked | refused
    detail: Mapped[str] = mapped_column(String(300), default="")

    share: Mapped[Share] = relationship(back_populates="events")


class Confirmation(Base):
    """The farmer tapped Confirm on a record. Separate from the signed payload."""

    __tablename__ = "confirmation"

    record_id: Mapped[str] = mapped_column(ForeignKey("record_version.id"), primary_key=True)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farmer.id"))
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Outbox(Base):
    """Work that needs the internet: Telegram deliveries and server sync.

    Rows wait here while offline and are retried until they succeed.
    """

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # telegram_report | sync_record
    record_id: Mapped[str] = mapped_column(ForeignKey("record_version.id"), index=True)
    farmer_id: Mapped[str | None] = mapped_column(ForeignKey("farmer.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ImmutableRecordError(RuntimeError):
    """Raised when code tries to edit or delete a signed record."""


@event.listens_for(RecordVersion, "before_update")
def _no_update(mapper, connection, target):
    raise ImmutableRecordError(
        "signed records are never edited - create a new version instead")


@event.listens_for(RecordVersion, "before_delete")
def _no_delete(mapper, connection, target):
    raise ImmutableRecordError("signed records are never deleted")


def make_engine(url: str) -> Engine:
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return engine


def init_db(url: str) -> sessionmaker:
    """Create tables if needed and return a session factory."""
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
