"""Stats, plans & payments, referrals, admin screens."""

from __future__ import annotations

from urllib.parse import quote

from leadly.bot.callbacks import Adm, Menu, Pay
from leadly.bot.views import KIND_LABELS, Screen, b, h, home_btn, kb, quota_line, url_btn  # noqa: F401 (kb/b reused by admin)
from leadly.config import SAVED_LEADS, USAGE_KINDS, WATCHES, Plan, ProductConfig
from leadly.services.usage import Quota
from leadly.utils import fmt_date, fmt_money, plural


def stats_screen(st: dict[str, int], plan: Plan, plan_until: str | None, summary: dict[str, Quota],
                 niches: list[dict] | None = None, goal: tuple[int, int, int] | None = None) -> Screen:
    lines = ["📊 <b>Моя статистика</b>", ""]
    if goal:
        done, target, streak = goal
        bar = "🟩" * min(done, target) + "⬜" * max(0, target - done)
        lines += [f"🎯 Цель на сегодня: написать {target} компаниям", f"{bar} {done}/{target}"
                  + (f" · серия {streak} дн. 🔥" if streak > 1 else ""), ""]
    if not st["searches"] and not st["saved"]:
        lines += ["Пока статистики нет. Сделай первый поиск 🚀", ""]
    else:
        lines += [
            f"Поисков: {st['searches']}",
            f"Просмотрено компаний: {st['viewed']}",
            f"Сохранено лидов: {st['saved']}",
            f"Подготовлено предложений: {st['proposals']}",
            f"Связался: {st['contacted']}",
            f"Ответили: {st['replied']}",
            f"Есть интерес: {st['interested']}",
            f"Сделок: {st['deals']}" + (f" · {fmt_money(st['revenue'])}" if st["revenue"] else ""),
            "",
        ]
    lines += niche_stats_lines(niches or [])
    if niches:
        lines.append("")
    lines.append(f"<b>Тариф: {h(plan.title)}</b>" + (f" до {fmt_date(plan_until)}" if plan_until else ""))
    lines += [f"• {KIND_LABELS[k]}: {quota_line(summary[k])}" for k in USAGE_KINDS if k in summary]
    goals = [b(("• " if goal and goal[1] == g else "") + f"Цель {g}/день", Menu(to=f"goal{g}")) for g in (3, 5, 10)]
    return "\n".join(lines), kb(goals, [b("📋 Мои лиды", Menu(to="leads")), b("⭐ Тарифы", Menu(to="plans"))],
                                [home_btn()])


def _limit_text(plan: Plan, kind: str) -> str:
    lim = plan.limit(kind)
    if lim.amount is None:
        return "без ограничений"
    return f"{lim.amount}" + (" в день" if lim.period == "day" else " всего")


def plans_screen(product: ProductConfig, current: Plan, until: str | None, payments_enabled: bool,
                 bonus: int, trial_available: bool = False, auto_renew: dict | None = None,
                 ai: bool = False, promo: bool = False, trial_left: int | None = None) -> Screen:
    no_access = not current.is_paid and current.limit("searches").amount == 0
    if trial_left is not None and not current.is_paid:
        status = (f"🎁 Бесплатных поисков осталось: {trial_left}" if trial_left else
                  "🎁 Бесплатные поиски закончились — выбери тариф, чтобы искать дальше.")
    elif no_access:
        status = "Доступ пока не оплачен."
    else:
        status = f"Твой тариф: <b>{h(current.title)}</b>" + (f" до {fmt_date(until)}" if until else "")
    lines = ["⭐ <b>Тарифы Leadly</b>", status]
    if auto_renew:
        lines.append(f"🔁 Автопродление включено, следующее списание {fmt_date(auto_renew['ends_at'])}")
    if bonus:
        lines.append(f"Бонусных поисков: {bonus}")
    for plan in product.plans.values():
        if not plan.is_paid:
            continue  # the free trial isn't something to choose: it's what a user has before paying
        price = "бесплатно" if not plan.is_paid else (
            f"{plan.price_stars} ⭐ / {plan.period_days} дней"
            + (f" (≈ {plan.price_rub_hint} ₽)" if plan.price_rub_hint else "")
            + (f" или {plan.year_price_stars} ⭐ за год" if plan.year_price_stars else ""))
        watches = plan.limit(WATCHES).amount
        block = [f"\n<b>{h(plan.title)}</b> — {price}",
                 f"• Поиски: {_limit_text(plan, 'searches')}",
                 f"• Компаний в выдаче: до {plan.results_per_search} за поиск, {_limit_text(plan, 'results')}",
                 f"• Проверки сайтов: {_limit_text(plan, 'site_checks')}",
                 f"• ИИ-анализ: {_limit_text(plan, 'ai_analyses')}",
                 f"• Предложения: {_limit_text(plan, 'proposals')}",
                 f"• Сохранённые лиды: {_limit_text(plan, SAVED_LEADS).replace(' всего', '')}",
                 f"• Автопоиск: {'без ограничений' if watches is None else watches} "
                 f"{plural(watches or 0, 'ниша', 'ниши', 'ниш') if watches is not None else ''}, "
                 "новые компании " + ("каждый день" if plan.watch_interval_days == 1 else
                                      f"раз в {plan.watch_interval_days} "
                                      f"{plural(plan.watch_interval_days, 'день', 'дня', 'дней')}")]
        block += [f"• {h(p)}" for p in plan.perks if not p.startswith("Автопоиск")]
        lines += [line for line in block if ai or "ИИ-анализ" not in line]  # don't sell what isn't connected
    pro = product.plans.get("pro")
    if pro and pro.price_rub_hint:
        lines += ["", "<b>Почему это окупается</b>",
                  f"Средний заказ на сайт или рекламу — от 20–30 тысяч рублей. Один клиент, найденный через Leadly, "
                  f"окупает PRO (≈ {pro.price_rub_hint} ₽/мес) на годы вперёд. Бот экономит часы ручного поиска: "
                  "компании, контакты, проверка сайта и готовый текст — за минуту."]
    lines += ["", "<i>Оплата — Telegram Stars прямо в Telegram. Подписку с автопродлением можно отключить "
                  "в любой момент: доступ сохранится до конца оплаченного периода.</i>"]
    rows = []
    if trial_available and product.trial_plan:
        rows.append([b(f"🎁 {product.plan(product.trial_plan).title} бесплатно на {product.trial_days} "
                       f"{plural(product.trial_days, 'день', 'дня', 'дней')}", Pay(act="trial"))])
    if payments_enabled:
        for plan in product.plans.values():
            if plan.is_paid:
                rows.append([b(f"⭐ {plan.title} · {plan.price_stars} ⭐/мес · автопродление",
                               Pay(act="sub", plan=plan.code))])
                row = [b(f"{plan.title} на {plan.period_days} дней разово", Pay(act="buy", plan=plan.code))]
                if plan.year_price_stars:
                    discount = round(100 - plan.year_price_stars * 100 / (plan.price_stars * 12))
                    row.append(b(f"На год · {plan.year_price_stars} ⭐ (−{discount}%)", Pay(act="year", plan=plan.code)))
                rows.append(row)
    else:
        lines.append("<i>Оплата временно недоступна.</i>")
    if promo:
        rows.append([b("🎟 Промокод или ключ", Pay(act="promo"))])
    if auto_renew:
        rows.append([b("Отключить автопродление", Pay(act="cancel"))])
    rows.append([home_btn()])
    return "\n".join(lines), kb(*rows)


def subscription_link(plan: Plan, link: str) -> Screen:
    return (f"⭐ <b>Подписка {h(plan.title)}</b>\n\n{plan.price_stars} ⭐ каждые 30 дней, списание автоматически "
            "через Telegram Stars. Отключить можно в любой момент в «⭐ Тарифы» или в настройках Telegram.",
            kb([url_btn(f"Оплатить {plan.price_stars} ⭐", link)], [b("← Тарифы", Menu(to="plans"))]))


def trial_started(plan: Plan, ends_at: str) -> Screen:
    return (f"🎁 <b>{h(plan.title)} активирован до {fmt_date(ends_at)}</b>\n\nПопробуй автопоиск, проверку сайтов "
            "и предложения. Когда пробный период закончится, доступ отключится автоматически — "
            "ничего не спишется.", kb([b("🔎 Найти клиентов", Menu(to="find"))], [home_btn()]))


def ask_promo() -> Screen:
    return ("🎟 Пришли промокод или ключ активации одним сообщением (например <code>LDY-ABCD-EFGH</code>).",
            kb([b("✖️ Отмена", Menu(to="cancel"))]))


PROMO_ERRORS = {
    "unknown": "Такого промокода или ключа нет, либо он отключён.",
    "used_up": "Этот код уже использован.",
    "already": "Ты уже активировал этот код.",
}


def promo_ok(plan: Plan, ends_at: str) -> Screen:
    return (f"🎟 Промокод активирован: <b>{h(plan.title)}</b> до {fmt_date(ends_at)}.",
            kb([b("🔎 Найти клиентов", Menu(to="find"))], [home_btn()]))


def invoice_texts(plan: Plan, recurring: bool = False, yearly: bool = False) -> tuple[str, str]:
    title = f"Leadly {plan.title} · " + ("год" if yearly else "подписка" if recurring else f"{plan.period_days} дней")
    renew = ("Продлевается автоматически каждые 30 дней, отключить можно в любой момент."
             if recurring else "Без автопродления.")
    desc = (f"Доступ к тарифу {plan.title}: больше поисков, компаний, автопоиск, "
            f"проверки сайтов и предложения. {renew}")
    return title, desc[:255]


def payment_ok(plan: Plan, ends_at: str, renewed: bool = False) -> Screen:
    head = "🔁 <b>Подписка продлена.</b>" if renewed else "✅ <b>Оплата получена.</b>"
    return (f"{head} Тариф {h(plan.title)} активен до {fmt_date(ends_at)}.\n\n"
            "Лимиты уже обновлены. Спасибо!", kb([b("🔎 Найти клиентов", Menu(to="find"))], [home_btn()]))


def payment_problem(support: str) -> str:
    return ("⚠️ Платёж получен, но не удалось автоматически сопоставить его со счётом. "
            "Мы видим его в системе и разберёмся вручную."
            + (f" Напиши в поддержку: {h(support)}" if support else " Напиши /paysupport."))


def paysupport(support: str) -> str:
    return ("💬 <b>Поддержка по оплатам</b>\n\n"
            "Если оплата прошла, а тариф не активировался, или нужен возврат — напиши "
            + (h(support) if support else "администратору бота")
            + " и приложи ID платежа из чека Telegram (раздел «Звёзды» в настройках Telegram).")


def terms(support: str) -> str:
    return ("📄 <b>Условия использования Leadly</b>\n\n"
            "• Leadly помогает находить компании в открытых источниках и готовить черновики сообщений. "
            "Бот не отправляет сообщения компаниям.\n"
            "• Данные берутся из сторонних источников и могут быть неполными или устаревшими.\n"
            "• Пользователь сам отвечает за то, кому и как он пишет. Запрещены спам и массовые рассылки.\n"
            "• Платный тариф — цифровой доступ к повышенным лимитам на указанный срок, оплата в Telegram Stars, "
            "подписка продлевается автоматически, если ты выбрал вариант с автопродлением; отключить можно "
            "в «⭐ Тарифы». Вопросы по оплате и возвратам — /paysupport.\n"
            "• Обработка персональных данных — /privacy, удаление данных — /delete_my_data.\n"
            + (f"• Контакт: {h(support)}" if support else ""))


def subscription_expiring(plan_title: str, ends_at: str) -> Screen:
    return (f"⏰ Тариф {h(plan_title)} закончится {fmt_date(ends_at)}. Продлить можно в «⭐ Тарифы».",
            kb([b("⭐ Продлить", Menu(to="plans"))]))


def subscription_expired(plan_title: str) -> Screen:
    return (f"Тариф {h(plan_title)} закончился. Чтобы снова получать клиентов, продли доступ.",
            kb([b("⭐ Продлить", Menu(to="plans"))], [home_btn()]))


def referral_screen(link: str, invited: int, bonus: int, product: ProductConfig) -> Screen:
    share = ("https://t.me/share/url?url=" + quote(link) + "&text="
             + quote("Leadly — бот для поиска клиентов фрилансерам"))
    r, i = product.referral_bonus_referrer, product.referral_bonus_invited
    text = ("🎁 <b>Пригласить друга</b>\n\n"
            "Условия: когда друг впервые запускает бота по твоей ссылке, ты получаешь "
            f"+{r} {plural(r, 'бонусный поиск', 'бонусных поиска', 'бонусных поисков')}, друг — +{i}. "
            "Бонус начисляется один раз за каждого нового пользователя; себя пригласить нельзя. "
            "Бонусные поиски тратятся, когда закончился лимит тарифа.\n\n"
            f"Твоя ссылка:\n<code>{h(link)}</code>\n\n"
            f"Приглашено: {invited}\nНачислено бонусных поисков: {bonus}")
    return text, kb([url_btn("📤 Поделиться", share)], [home_btn()])


# --- admin --------------------------------------------------------------------------------


def admin_home(ov: dict, product: ProductConfig) -> Screen:
    paid = ", ".join(f"{product.plan(k).title}: {v}" for k, v in ov["paid_by_plan"].items()) or "нет"
    text = ("🛠 <b>Админка Leadly</b>\n\n"
            f"Пользователей: {ov['users']} (активны за 24 ч: {ov['active_1d']}, за 7 дн: {ov['active_7d']})\n"
            f"Платные подписки: {paid}\n"
            f"Поисков: {ov['searches']} (за 24 ч: {ov['searches_24h']}, ошибок: {ov['searches_failed']})\n"
            f"Лидов сохранено: {ov['leads']}\n"
            f"Оплачено Stars: {ov['stars_paid']}\n\n"
            "<b>Воронка (за всё время / 7 дней)</b>\n"
            f"Зашли: {ov['f_users']} / {ov['f_users_7d']}\n"
            f"Искали: {ov['f_searched']} / {ov['f_searched_7d']}\n"
            f"Сохранили лида: {ov['f_saved']} / {ov['f_saved_7d']}\n"
            f"Заплатили: {ov['f_paid']} / {ov['f_paid_7d']}\n"
            f"Автопоисков активно: {ov['watches']}\n\n"
            "Команды: /user ID · /grant ID plan дни · /refund CHARGE_ID · /promo КОД plan дни N · "
            "/keys plan дни N · /broadcast текст\n"
            "Партнёры: /partner_add @username · /partners · /partner_paid КОД ЗВЁЗД")
    return text, kb([b("🔌 Источники и ИИ", Adm(act="sources")), b("🧯 Ошибки", Adm(act="errors"))],
                    [b("🔁 Перечитать plans.json", Adm(act="reload"))], [home_btn()])


def admin_sources(rows: list[tuple[str, bool, str]], last_error: dict | None) -> Screen:
    lines = ["🔌 <b>Источники и ИИ</b>", ""]
    lines += [f"{'✅' if ok else '⚠️'} {h(name)}: {h(status)}" for name, ok, status in rows]
    if last_error:
        lines += ["", f"Последняя ошибка поиска ({fmt_date(last_error['created_at'])}):",
                  f"<i>{h(last_error['error'] or '')}</i>"]
    return "\n".join(lines), kb([b("← Админка", Adm(act="home"))])


def admin_errors(lines_: list[str]) -> Screen:
    body = "\n".join(f"<code>{h(x[:300])}</code>" for x in lines_) if lines_ else "Ошибок в логе нет 🎉"
    return f"🧯 <b>Последние ошибки</b>\n\n{body}", kb([b("← Админка", Adm(act="home"))])


def admin_user(card: dict, product: ProductConfig) -> str:
    u = card["user"]
    lines = [f"👤 <b>{u.id}</b> @{h(u.username or '—')} {h(u.first_name or '')}",
             f"С нами с {fmt_date(u.created_at)}, был {fmt_date(u.last_seen_at)}",
             f"Бонусных поисков: {u.bonus_searches} · пригласил его: {u.referred_by or '—'}", "", "<b>Подписки</b>"]
    lines += [f"• {product.plan(s['plan']).title} {fmt_date(s['starts_at'])}–{fmt_date(s['ends_at'])} "
              f"[{s['status']}, {s['source']}]" for s in card["subscriptions"]] or ["нет"]
    lines += ["", "<b>Платежи</b>"]
    lines += [f"• {p['plan']} {p['amount']} {p['currency']} [{p['status']}] {p['telegram_charge_id'] or ''}"
              for p in card["payments"]] or ["нет"]
    st = card["stats"]
    lines += ["", f"Поисков: {st['searches']} · лидов: {st['saved']} · сделок: {st['deals']}"]
    return "\n".join(lines)


# --- executor profile ---------------------------------------------------------------


def profile_screen(profile, fields: dict) -> Screen:
    from leadly.bot.callbacks import Prof
    lines = ["👤 <b>Мой профиль исполнителя</b>",
             "Эти данные подставляются в предложения вместо «[Ваше имя]»: имя, чем занимаешься, портфолио, "
             "цены и контакт. Так тексты сразу готовы к отправке.", ""]
    for key, (label, _limit, example) in fields.items():
        value = getattr(profile, key)
        lines.append(f"<b>{label}:</b> {h(value) if value else '<i>не заполнено</i>'}")
    lines += ["", f"Заполнено {profile.filled} из {len(fields)}.",
              "<i>Если подключён ИИ, профиль передаётся ему только для написания твоих предложений.</i>"]
    buttons = [b(("✏️ " if getattr(profile, key) else "➕ ") + label, Prof(act="edit", field=key))
               for key, (label, _l, _e) in fields.items()]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([b("📄 Мои шаблоны", Menu(to="templates")), b("🔔 Уведомления", Menu(to="notify"))])
    rows.append([home_btn()])
    return "\n".join(lines), kb(*rows)


def ask_profile_field(label: str, example: str, current: str | None) -> Screen:
    text = f"✏️ <b>{h(label)}</b>\n\nНапиши одним сообщением. Например: <i>{h(example)}</i>"
    if current:
        text += f"\n\nСейчас: {h(current)}\nЧтобы очистить поле, отправь «-»."
    return text, kb([b("✖️ Отмена", Menu(to="profile"))])


# --- auto-search & follow-ups -------------------------------------------------------------


def watches_screen(rows: list[dict], limit: int | None, interval_days: int) -> Screen:
    from leadly.bot.callbacks import Watch
    every = "каждый день" if interval_days == 1 else f"раз в {interval_days} {plural(interval_days, 'день', 'дня', 'дней')}"
    lines = ["🔔 <b>Автопоиск</b>",
             f"Leadly сам проверяет выбранные ниши {every} и присылает только <b>новые</b> компании — "
             "которых ты ещё не видел.", ""]
    if not rows:
        lines += ["Пока ничего не отслеживается.",
                  "Сделай поиск и нажми «🔔 Следить за новыми» под результатами."]
        return "\n".join(lines), kb([b("🔎 Найти клиентов", Menu(to="find"))], [home_btn()])
    lim = "без ограничений" if limit is None else f"{len(rows)} из {limit}"
    lines.append(f"Отслеживается: {lim}")
    for i, w in enumerate(rows, 1):
        lines.append(f"{i}. {h(w['title'])} · проверено {fmt_date(w['last_run_at'])}")
    buttons = [[b(f"🗑 {i}. {w['title'][:40]}", Watch(act="del", id=w["id"]))] for i, w in enumerate(rows, 1)]
    return "\n".join(lines), kb(*buttons, [b("⭐ Чаще и больше ниш — в тарифах", Menu(to="plans"))], [home_btn()])


def watch_found(title: str, count: int, search_id: int) -> Screen:
    from leadly.bot.callbacks import Res
    return (f"🔔 <b>Автопоиск: {count} {plural(count, 'новая компания', 'новые компании', 'новых компаний')}</b>\n"
            f"{h(title)}\n\nЭтих компаний ещё не было в твоих результатах.",
            kb([b("Посмотреть", Res(sid=search_id))], [b("🔔 Настроить автопоиск", Menu(to="watches"))]))


def followup_reminder(lead_id: int, name: str, contacted_at: str) -> Screen:
    from leadly.bot.callbacks import Co
    return (f"⏰ <b>Пора напомнить о себе</b>\n{h(name)}\n\nТы написал(а) {fmt_date(contacted_at)}, ответа пока нет. "
            "Компании часто отвечают после второго короткого касания.",
            kb([b("✍️ Текст напоминания", Co(k="s", ref=lead_id, a="fup"))],
               [b("👀 Ответили", Co(k="s", ref=lead_id, a="setst", x="replied")),
                b("❌ Отказ", Co(k="s", ref=lead_id, a="setst", x="refused"))],
               [b("Открыть лида", Co(k="s", ref=lead_id))]))


def privacy(operator: str, support: str) -> str:
    who = h(operator) if operator else "владелец бота (данные оператора указываются в настройках бота)"
    contact = h(support) if support else "через администратора бота"
    return (
        "🔒 <b>Политика конфиденциальности Leadly</b>\n\n"
        f"<b>Оператор:</b> {who}. Связь: {contact}.\n\n"
        "<b>Какие данные храним:</b> Telegram ID, имя и username из Telegram; город, который ты указал; "
        "профиль исполнителя (если заполнил); твои поиски, сохранённые лиды, заметки и тексты предложений; "
        "данные об оплатах (ID платежа Telegram, сумма, тариф).\n\n"
        "<b>Зачем:</b> чтобы бот работал — показывал твои лиды и статистику, учитывал лимиты и тариф, "
        "присылал автопоиск и напоминания.\n\n"
        "<b>Кому передаём:</b> никому не продаём. Данные хранятся на сервере бота. Запросы к источникам "
        "компаний (OpenStreetMap, 2ГИС) не содержат твоих данных. Если включён ИИ, ему передаются публичные "
        "данные компании и твой профиль исполнителя — только чтобы написать предложение. Платежи проводит "
        "Telegram.\n\n"
        "<b>Сведения о компаниях</b> берутся из открытых источников и с публичных сайтов самих компаний.\n\n"
        "<b>Удаление:</b> команда /delete_my_data стирает твои лиды, поиски, профиль, заметки и предложения. "
        "Записи об оплатах сохраняются как финансовые документы.\n\n"
        "Используя бота, ты соглашаешься с этой политикой и /terms."
    )


def confirm_delete_data() -> Screen:
    from leadly.bot.callbacks import Menu as M
    return ("⚠️ <b>Удалить все мои данные?</b>\n\nБудут стёрты лиды, заметки, поиски, предложения, профиль и "
            "автопоиски. Это нельзя отменить. Тариф и записи об оплатах сохранятся.",
            kb([b("🗑 Да, удалить всё", M(to="erase_ok"))], [b("Отмена", M(to="home"))]))


# --- templates -----------------------------------------------------------------------------


def templates_screen(rows: list[dict], variables: dict[str, str], max_templates: int) -> Screen:
    from leadly.bot.callbacks import Tpl
    lines = ["📄 <b>Мои шаблоны</b>",
             "Напиши текст один раз — Leadly подставит данные каждой компании. Переменные:",
             " ".join(f"<code>{{{k}}}</code>" for k in variables), ""]
    if rows:
        lines += [f"{i}. <b>{h(r['title'])}</b>: <i>{h(r['text'][:90])}{'…' if len(r['text']) > 90 else ''}</i>"
                  for i, r in enumerate(rows, 1)]
    else:
        lines.append("Шаблонов пока нет. Создай свой или сохрани удачный текст из «✍️ Создать предложение» "
                     "кнопкой «💾 В шаблоны».")
    buttons = [[b(f"🗑 {i}. {r['title'][:36]}", Tpl(act="del", id=r["id"]))] for i, r in enumerate(rows, 1)]
    if len(rows) < max_templates:
        buttons.insert(0, [b("➕ Новый шаблон", Tpl(act="new"))])
    return "\n".join(lines), kb(*buttons, [b("👤 Профиль", Menu(to="profile")), home_btn()])


def ask_template_title() -> Screen:
    return ("📄 Как назвать шаблон? Например: <i>Короткий для салонов</i>",
            kb([b("✖️ Отмена", Menu(to="templates"))]))


def ask_template_text(variables: dict[str, str]) -> Screen:
    example = ("Здравствуйте! Меня зовут {имя}, я {услуга} для компаний в городе {город}. "
               "Посмотрел {сайт} — {наблюдение}. Примеры работ: {портфолио}")
    return ("✏️ Пришли текст шаблона. Можно использовать переменные:\n"
            + "\n".join(f"<code>{{{k}}}</code> — {h(v)}" for k, v in variables.items())
            + f"\n\nПример:\n<code>{h(example)}</code>", kb([b("✖️ Отмена", Menu(to="templates"))]))


def notify_screen(user) -> Screen:
    from leadly.bot.callbacks import Notif

    def mark(on: int) -> str:
        return "🔔 вкл" if on else "🔕 выкл"
    text = ("🔔 <b>Уведомления</b>\n\n"
            f"Автопоиск — новые компании по отслеживаемым нишам: {mark(user.notify_watches)}\n"
            f"Напоминания по лидам — «пора напомнить о себе»: {mark(user.notify_followups)}")
    return text, kb([b(f"Автопоиск: {mark(user.notify_watches)}", Notif(what="watches"))],
                    [b(f"Напоминания: {mark(user.notify_followups)}", Notif(what="followups"))],
                    [b("👤 Профиль", Menu(to="profile")), home_btn()])


def niche_stats_lines(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    out = ["", "<b>По нишам</b>"]
    for r in rows[:6]:
        rate = f" ({round(r['replied'] * 100 / r['contacted'])}% ответов)" if r["contacted"] else ""
        out.append(f"{h(r['label'])}: {r['saved']} сохр. · {r['contacted']} написал · {r['replied']} ответили"
                   f"{rate} · {r['deals']} сделок")
    return out


def first_search_tip() -> str:
    return ("💡 <b>Как получить первого клиента</b>\n"
            "1. Открой компанию с 🔥 или 📞 вверху списка.\n"
            "2. «✍️ Создать предложение» → скопируй текст и отправь компании сам.\n"
            "3. Сохрани лида и поставь статус «💬 Связался» — через 3 дня бот напомнит написать ещё раз.\n"
            "4. Нажми «🔔 Следить за новыми» — бот сам пришлёт новые компании в этой нише.")


def final_touch_reminder(lead_id: int, name: str) -> Screen:
    from leadly.bot.callbacks import Co
    return (f"⏳ <b>Последнее касание</b>\n{h(name)}\n\nПрошла неделя без ответа. Вежливо закрой диалог — "
            "так иногда отвечают даже те, кто молчал.",
            kb([b("✍️ Текст последнего сообщения", Co(k="s", ref=lead_id, a="fup2"))],
               [b("👀 Ответили", Co(k="s", ref=lead_id, a="setst", x="replied")),
                b("❌ Отказ", Co(k="s", ref=lead_id, a="setst", x="refused"))]))


def weekly_digest(d: dict) -> Screen:
    lines = ["📬 <b>Твоя неделя в Leadly</b>", "",
             f"Поисков: {d['searches']} · новых компаний: {d['found']}",
             f"Сохранено лидов: {d['saved']}",
             f"Написал: {d['contacted']} · ответили: {d['replied']} · сделок: {d['deals']}"]
    if d["waiting"]:
        lines += ["", f"💡 {d['waiting']} {plural(d['waiting'], 'лид ждёт', 'лида ждут', 'лидов ждут')} "
                      "первого сообщения — открой «📋 Мои лиды» → «🆕 Новый»."]
    elif not d["contacted"]:
        lines += ["", "💡 Цель на неделю: написать 5 компаниям. Начни с тех, где 🔥 — у них подтверждённая проблема."]
    return "\n".join(lines), kb([b("📋 Мои лиды", Menu(to="leads")), b("🔎 Найти клиентов", Menu(to="find"))])


def share_success(link: str) -> Screen:
    share = ("https://t.me/share/url?url=" + quote(link) + "&text="
             + quote("Нашёл клиента через Leadly — бот сам находит компании и пишет первое сообщение 🎯"))
    return ("💰 <b>Поздравляю со сделкой!</b>\n\nПоделись с коллегами-фрилансерами — за каждого, кто придёт по "
            "твоей ссылке, получишь бонусные поиски.",
            kb([url_btn("📣 Поделиться успехом", share)], [home_btn()]))
