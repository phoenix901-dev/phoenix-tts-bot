import asyncio
from sqlalchemy import Column, Integer, String, BigInteger, select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    text_voice = Column(String, default='ru-RU-DmitryNeural')
    book_voice = Column(String, default='ru-RU-DmitryNeural')
    rate = Column(String, default='+15%')

# Отключаем echo, чтобы не мусорить в системном журнале
engine = create_async_engine("sqlite+aiosqlite:///prometheus.db", echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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
    """Корректный асинхронный UPDATE для SQLAlchemy 2.0"""
    async with AsyncSessionLocal() as session:
        stmt = update(User).where(User.telegram_id == tg_id).values(**kwargs)
        await session.execute(stmt)
        await session.commit()