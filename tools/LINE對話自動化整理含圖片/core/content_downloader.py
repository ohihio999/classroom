import config
from linebot.v3.messaging import Configuration, ApiClient, MessagingApiBlob


def download_image(message_id: str) -> bytes:
    """從 LINE Content API 下載圖片，回傳 bytes。"""
    configuration = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        blob_api = MessagingApiBlob(api_client)
        response = blob_api.get_message_content(message_id)
        # SDK v3 回傳 bytes 或 file-like，統一處理
        if isinstance(response, (bytes, bytearray)):
            return bytes(response)
        return response.read()
