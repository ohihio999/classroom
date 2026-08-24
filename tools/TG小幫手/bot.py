import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("TG小幫手已啟動，你可以直接傳訊息給我。")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text or ""
    logger.info("收到訊息: %s", user_text)
    await update.message.reply_text(f"收到：{user_text}")


def main() -> None:
    if not TOKEN:
        raise ValueError("找不到 TELEGRAM_BOT_TOKEN，請先建立 .env 並填入 Bot Token。")

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    logger.info("TG小幫手啟動中...")
    application.run_polling()


if __name__ == "__main__":
    main()
