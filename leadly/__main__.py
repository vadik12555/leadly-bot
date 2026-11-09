"""python -m leadly — start the Telegram bot (long polling)."""

import asyncio
import sys

from leadly.bot.app import run_polling, setup_logging
from leadly.config import load_settings


def main() -> None:
    try:
        settings = load_settings()
    except Exception as e:  # broken plans.json or .env value
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        sys.exit(2)
    setup_logging(settings)
    try:
        code = asyncio.run(run_polling(settings))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
