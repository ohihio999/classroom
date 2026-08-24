import os

VAULT_ROOT = r"C:\Users\admin\Desktop\本機MD檔"
ATTACHMENT_DIR = os.path.join(VAULT_ROOT, "附件")
INBOX_DIR = os.path.join(VAULT_ROOT, "60_文章庫", "_inbox")

# Chrome user data dir for persistent FB session
CHROME_USER_DATA = r"C:\Users\admin\AppData\Local\Google\Chrome\User Data"
CHROME_PROFILE = "Default"

# Playwright timeout (ms)
PAGE_TIMEOUT = 30000
