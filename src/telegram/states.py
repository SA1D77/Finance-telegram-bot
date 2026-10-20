from aiogram.fsm.state import State, StatesGroup


class IncomeState(StatesGroup):
    income_input = State()
    category_input = State()
    new_category = State()


class ExpenditureState(StatesGroup):
    expenditure_input = State()
    category_input = State()
    new_category = State()


class AddCategoryState(StatesGroup):
    add_categories_input = State()
    kind = State()


class DeleteCategoryState(StatesGroup):
    delete_categories_input = State()


class ReportState(StatesGroup):
    send_place = State()
    time_interval = State()
    email = State()
    remember_email = State()
    custom_range = State()


class EmailState(StatesGroup):
    reuse_email = State()


class LanguageState(StatesGroup):
    language = State()
