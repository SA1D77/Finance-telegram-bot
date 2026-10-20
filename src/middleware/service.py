from database import async_sessioin_maker


async def _execute_insert_update_delete_command(statemant):
    async with async_sessioin_maker() as session:
        async with session.begin():
            result = await session.execute(statemant)

    return result


async def _execute_select_command(statemant):
    async with async_sessioin_maker() as session:
        async with session.begin():

            result = await session.execute(statemant)

    return result.scalars().all()


async def _execute_select_rows(statemant):
    """Like _execute_select_command, but keeps every selected column."""
    async with async_sessioin_maker() as session:
        async with session.begin():
            result = await session.execute(statemant)

    return result.all()


async def _execute_scalar(statemant):
    async with async_sessioin_maker() as session:
        async with session.begin():
            result = await session.execute(statemant)

    return result.scalar()
