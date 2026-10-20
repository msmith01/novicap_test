"""Persistence for applications and the append-only score history.

Postgres in deployment (DATABASE_URL=postgresql+psycopg://...); SQLite locally.
"""

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Application(Base):
    """One validated submission. source_application_id is not unique: the export repeats IDs."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_application_id: Mapped[str] = mapped_column(String, index=True)
    employees: Mapped[int] = mapped_column(Integer)
    annual_revenue: Mapped[float] = mapped_column(Float)
    requested_amount: Mapped[float] = mapped_column(Float)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ScoreRecord(Base):
    """Append-only: rows are inserted, never updated or deleted by the service."""

    __tablename__ = "score_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    decision: Mapped[str] = mapped_column(String)
    risk_factors: Mapped[list] = mapped_column(JSON)
    ruleset_version: Mapped[str] = mapped_column(String)
    image_version: Mapped[str] = mapped_column(String)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


def make_session_factory(url: str | None = None) -> sessionmaker[Session]:
    url = url or os.environ.get("DATABASE_URL", "sqlite:///./scores.db")
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    if url in ("sqlite://", "sqlite:///:memory:"):
        from sqlalchemy.pool import StaticPool

        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)  # TODO: replace with migrations (Alembic) before production
    return sessionmaker(engine, expire_on_commit=False)


def save_score(
    session: Session,
    *,
    source_application_id: str,
    employees: int,
    annual_revenue: float,
    requested_amount: float,
    request_id: str,
    decision: str,
    risk_factors: list[str],
    ruleset_version: str,
    image_version: str,
) -> ScoreRecord:
    application = Application(
        id=str(uuid.uuid4()),
        source_application_id=source_application_id,
        employees=employees,
        annual_revenue=annual_revenue,
        requested_amount=requested_amount,
    )
    record = ScoreRecord(
        application_id=application.id,
        request_id=request_id,
        decision=decision,
        risk_factors=risk_factors,
        ruleset_version=ruleset_version,
        image_version=image_version,
    )
    session.add_all([application, record])
    session.commit()
    return record
