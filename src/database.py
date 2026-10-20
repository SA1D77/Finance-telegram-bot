from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from config import DB_URL


# echo stays off: SQL parameters would put amounts and emails into the logs.
engine = create_async_engine(DB_URL, echo=False)
async_sessioin_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
