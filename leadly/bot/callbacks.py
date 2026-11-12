"""Compact callback_data factories (Telegram limit: 64 bytes)."""

from aiogram.filters.callback_data import CallbackData


class Menu(CallbackData, prefix="m"):
    to: str  # home | find | leads | ai | prop | plans | stats | ref | help | cancel


class Flow(CallbackData, prefix="f"):
    to: str  # service | niche | sub | geo | filters  (back navigation)


class Svc(CallbackData, prefix="sv"):
    code: str


class Nch(CallbackData, prefix="ni"):
    code: str


class Sub(CallbackData, prefix="sb"):
    code: str  # subcategory code or "all"


class Geo(CallbackData, prefix="g"):
    act: str  # my | city | other | multi | toggle | done
    val: int = -1


class Flt(CallbackData, prefix="ft"):
    act: str  # site | contacts | go
    val: str = ""


class Res(CallbackData, prefix="rs"):
    sid: int
    page: int = 0


class Alt(CallbackData, prefix="al"):
    act: str  # allcat | near | niche — widen a search that found too little
    sid: int


class Co(CallbackData, prefix="co"):
    k: str  # r = search result, s = saved lead
    ref: int  # search id or lead id
    i: int = 0  # result index
    a: str = "open"
    x: str = ""


class LList(CallbackData, prefix="ll"):
    st: str = "home"  # home | all | <status>
    page: int = 0


class Pick(CallbackData, prefix="pk"):
    purpose: str  # ai | prop


class Pay(CallbackData, prefix="pay"):
    act: str  # buy | sub | trial | promo | cancel | export
    plan: str = ""


class Adm(CallbackData, prefix="adm"):
    act: str  # home | sources | errors | reload | bc_ok | bc_no


class Prof(CallbackData, prefix="pf"):
    act: str  # edit | clear
    field: str = ""


class Watch(CallbackData, prefix="w"):
    act: str  # add | list | del | open
    id: int = 0


class Tpl(CallbackData, prefix="tp"):
    act: str  # list | new | del
    id: int = 0


class Notif(CallbackData, prefix="nt"):
    what: str  # watches | followups
