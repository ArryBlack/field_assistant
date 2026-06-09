import httpx

class TelegramBotClient:
    def __init__(self, token: str):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.file_url = f"https://api.telegram.org/file/bot{token}"
        self.client = httpx.AsyncClient(timeout=35.0)
        print(f"Initialized TelegramBotClient with token: {token[:5]}...")

    async def close(self):
        try:
            await self.client.aclose()
            print("HTTP client closed successfully.")
        except Exception as e:
            print(f"An error occurred while closing the HTTP client: {e}")

    async def get_updates(self, offset: int = None):
        params = {"timeout": 30}
        if offset:
            params["offset"] = offset
            print(f"Fetching updates with offset: {offset}")
        try:
            res = await self.client.get(f"{self.api_url}/getUpdates", params=params)
        except Exception as e:
            print(f"An error occurred while fetching updates: {e}")
            return []
        data = res.json()
        print(f"Received updates: {data.get('result', [])}")
        return data.get("result", []) if data.get("ok") else []

    async def send_message(self, chat_id: int, text: str):
        try:
            await self.client.post(f"{self.api_url}/sendMessage", json={"chat_id": chat_id, "text": text})
            print(f"Message sent to chat_id {chat_id}: {text}")
        except Exception as e:
            print(f"An error occurred while sending message: {e}")

    async def get_file_info(self, file_id: str):
        try:
            res = await self.client.get(f"{self.api_url}/getFile", params={"file_id": file_id})
            data = res.json()
            print(f"Received file info for file_id {file_id}: {data.get('result', {})}")
            return data["result"] if data.get("ok") else None
        except Exception as e:
            print(f"An error occurred while fetching file info: {e}")
            return None

    async def download_file_bytes(self, file_path: str) -> bytes:
        try:
            res = await self.client.get(f"{self.file_url}/{file_path}")
            res.raise_for_status()
            print(f"File downloaded successfully: {file_path} (size: {len(res.content)} bytes)")
            return res.content
        except Exception as e:
            print(f"An error occurred while downloading file: {e}")
            return b""