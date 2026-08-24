import click
import config


@click.command()
@click.option(
    "--mode",
    type=click.Choice(["bot", "flush"]),
    required=True,
    help="bot=啟動 Webhook 伺服器；flush=將累積訊息輸出為 Markdown",
)
def main(mode: str):
    if mode == "bot":
        from core.webhook_server import run
        run(port=config.PORT)
    elif mode == "flush":
        from formatters.md_formatter import flush_to_markdown
        files = flush_to_markdown()
        if files:
            print(f"\n✅ 共輸出 {len(files)} 個 Markdown 檔案")


if __name__ == "__main__":
    main()
