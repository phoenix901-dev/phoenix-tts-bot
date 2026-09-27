import os

from sqlalchemy import BigInteger, Column, Integer, String, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    text_voice = Column(String, default='ru-RU-DmitryNeural')
    book_voice = Column(String, default='ru-RU-DmitryNeural')
    rate = Column(String, default='+15%')


DB_PATH = os.getenv("TTS_DB_PATH", "tts_bot.db")

# Отключаем echo, чтобы не мусорить в системном журнале
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """PRAGMA на каждое соединение.

    Раньше journal_mode=WAL выставлялся внутри engine.begin(), то есть внутри
    транзакции — SQLite в этом случае может отказать с "cannot change into wal
    mode from within a transaction". Здесь же настройка применяется к каждому
    соединению из пула, а busy_timeout гасит "database is locked" при
    параллельных get_user/update_user из разных хендлеров.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_user(tg_id: int):
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.telegram_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalars().first()

        if not user:
            user = User(telegram_id=tg_id)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


async def update_user(tg_id: int, **kwargs):
    """Сохраняет настройки пользователя, создавая запись при необходимости."""
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.telegram_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalars().first()

        if user is None:
            # Раньше здесь был голый SQL UPDATE: если строки не было (например,
            # после сброса БД), настройка молча не сохранялась, и бот продолжал
            # показывать старый голос.
            session.add(User(telegram_id=tg_id, **kwargs))
        else:
            for key, value in kwargs.items():
                setattr(user, key, value)

        await session.commit()
