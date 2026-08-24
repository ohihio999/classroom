import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
PORT = int(os.getenv("PORT", "5000"))

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
IMAGES_DIR = OUTPUT_DIR / "images"
MARKDOWN_DIR = OUTPUT_DIR / "markdown"
DATA_DIR = BASE_DIR / "data"
PENDING_FILE = DATA_DIR / "pending_messages.json"
