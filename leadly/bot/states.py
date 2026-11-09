from aiogram.fsm.state import State, StatesGroup


class SearchFlow(StatesGroup):
    custom_service = State()
    custom_niche = State()
    city = State()


class LeadInput(StatesGroup):
    note = State()
    deal_amount = State()
    edit_proposal = State()


class PromoInput(StatesGroup):
    code = State()


class ProfileInput(StatesGroup):
    field = State()


class AdminInput(StatesGroup):
    broadcast = State()


class TemplateInput(StatesGroup):
    title = State()
    text = State()


class SupportInput(StatesGroup):
    text = State()
