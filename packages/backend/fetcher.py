import os
import asyncio
import uuid
from datetime import datetime, timezone

# Import our modular services
from packages.backend.services.database import DatabaseClient
from packages.backend.services.storage import LocalFileManager
from packages.backend.services.llm import GeminiAssistant
from packages.backend.services.telegram import TelegramBotClient


class FieldNotesFetcher:
    def __init__(self):
        """Initializes core services and binds them to the application instance."""
        self.db = DatabaseClient(uri=os.environ.get("MONGO_URI", "mongodb://mongodb:27017"))
        self.storage = LocalFileManager(base_dir=os.environ.get("LOCAL_MEDIA_PATH", "/app/media"))
        self.ai = GeminiAssistant(api_key=os.environ.get("GOOGLE_API_KEY"))
        self.bot = TelegramBotClient(token=os.environ.get("TELEGRAM_BOT_TOKEN"))

    async def _orchestrate_media_download(self, file_id: str, media_type: str, user_id: int, message_id: int, default_ext: str):
        """Combines the Telegram and Storage services to download and save a file."""
        file_info = await self.bot.get_file_info(file_id)
        if not file_info: return None
        
        file_bytes = await self.bot.download_file_bytes(file_info["file_path"])
        
        saved_meta = await self.storage.save_file(
            file_bytes=file_bytes, 
            user_id=user_id, 
            message_id=message_id, 
            media_type=media_type, 
            file_id=file_id, 
            default_ext=default_ext,
            original_path=file_info["file_path"]
        )
        
        saved_meta["id"] = str(uuid.uuid4())
        saved_meta["message_id"] = message_id
        saved_meta["media_type"] = media_type
        saved_meta["file_id"] = file_id
        return saved_meta

    async def _handle_command(self, user_id: int, chat_id: int, text: str):
        """Routes and executes top-level system commands."""
        if text == "/start_trip":
            await self.db.complete_active_trips(user_id)
            await self.db.set_user_state(user_id, "TRIP_Q1", {})
            await self.bot.send_message(chat_id, "Let's start a new trip! Where are you going?")
        
        elif text == "/end_trip":
            await self.db.complete_active_trips(user_id)
            await self.db.set_user_state(user_id, "IDLE", {})
            await self.bot.send_message(chat_id, "Trip ended. Your logs are safely archived.")
            
        elif text == "/converse":
            active_trip = await self.db.get_active_trip(user_id)
            if not active_trip:
                await self.bot.send_message(chat_id, "No active trip. Use /start_trip first.")
                return

            await self.bot.send_message(chat_id, "Reviewing your notes and preparing questions...")
            
            # Safely handle both legacy integer dates and new ISODates
            start_date = active_trip.get("start_date")
            
            msgs = await self.db.get_messages_since(user_id, start_date)
            
            # Inject Trip Details into the AI Context
            meta = active_trip.get('metadata', {})
            dest = meta.get('destination', 'Unknown')
            guide = meta.get('guide_name', 'Unknown')
            species = meta.get('target_species', 'Unknown')
                                        
            trip_context = f"TRIP CONTEXT: Travelling to {dest}. Local guide is {guide}. Main target species: {species}."
            
            # Prepend the context to the message logs
            logs = [trip_context] 
            for m in msgs:
                            # 1. Add manual text logs or media captions
                            if m.get("text"):
                                logs.append(f"User Note: {m.get('text')}")
                                
                            # 2. Dig into the media array for AI-generated metadata
                            for media_item in m.get("media", []):
                                if media_item.get("description"):
                                    logs.append(f"[Photo/Video Description]: {media_item.get('description')}")
                                
                                if media_item.get("transcription"):
                                    logs.append(f"[Audio Transcription]: {media_item.get('transcription')}")
            
            print(f"Compiled {len(logs)-1} messages for AI context (plus trip details).")
            
            # Fetch the array of objects from Gemini
            queue = await self.ai.generate_converse_queue(logs)
            
            if queue:
                await self.db.set_user_state(user_id, "CONVERSE_Q", {"queue": queue, "index": 0})
                first_item = queue[0]
                prefix = "🎥 SUGGESTION:" if first_item.get("message_type") == "suggestion" else f"❓ QUESTION (Priority {first_item.get('importance', 0)}):"
                
                await self.bot.send_message(chat_id, f"{prefix}\n{first_item.get('text', '')}")
            else:
                await self.bot.send_message(chat_id, "Not enough context yet! Log some more notes first.")
                
        elif text == "/end_converse":
            await self.db.set_user_state(user_id, "IDLE", {})
            await self.bot.send_message(chat_id, "Interview ended early. Back to passive logging.")

    async def _handle_fsm_state(self, user_id: int, chat_id: int, text: str, msg: dict, state: str, state_data: dict) -> str:
        """Processes responses to active state machine questions. Returns the question context if answered."""
        answered_question = None

        if state == "TRIP_Q1":
            answered_question = "Where are you going?"
            state_data["destination"] = text
            await self.db.set_user_state(user_id, "TRIP_Q2", state_data)
            await self.bot.send_message(chat_id, "Got it. Who is the local guide?")
            
        elif state == "TRIP_Q2":
            answered_question = "Who is the local guide?"
            state_data["guide_name"] = text
            await self.db.set_user_state(user_id, "TRIP_Q3", state_data)
            await self.bot.send_message(chat_id, "Great. What is the main target species?")
            
        elif state == "TRIP_Q3":
            answered_question = "What is the main target species?"
            state_data["target_species"] = text
            
            # Convert Telegram's integer date to a native UTC ISODate
            trip_start_date = datetime.fromtimestamp(msg.get("date"), timezone.utc)
            
            await self.db.start_trip(user_id, trip_start_date, state_data)
            await self.db.set_user_state(user_id, "IDLE", {})
            await self.bot.send_message(chat_id, "Trip started successfully! Send me updates.")

        elif state == "CONVERSE_Q":
            idx = state_data.get("index", 0)
            queue = state_data.get("queue", [])
            
            if idx < len(queue):
                answered_question = queue[idx].get("text")
            
            next_idx = idx + 1
            if next_idx < len(queue):
                state_data["index"] = next_idx
                await self.db.set_user_state(user_id, "CONVERSE_Q", state_data)
                
                next_item = queue[next_idx]
                prefix = "🎥 SUGGESTION:" if next_item.get("message_type") == "suggestion" else f"❓ QUESTION :"
                
                await self.bot.send_message(chat_id, f"{prefix}\n{next_item.get('text', '')}")
            else:
                await self.db.set_user_state(user_id, "IDLE", {})
                await self.bot.send_message(chat_id, "Interview complete! Great field notes. Back to passive logging.")
        
        return answered_question

    async def _process_passive_media(self, user_id: int, msg_id: int, text: str, msg: dict, answered_question: str):
        """Extracts media payloads and archives the formatted document into MongoDB."""
        media_array = []
        
        if "location" in msg:
            loc = msg["location"]
            media_array.append({
                "id": str(uuid.uuid4()), "message_id": msg_id, "media_type": "location",
                "latitude": loc["latitude"], "longitude": loc["longitude"]
            })
        if "photo" in msg:
            record = await self._orchestrate_media_download(msg["photo"][-1]["file_id"], "photo", user_id, msg_id, ".jpg")
            if record: media_array.append(record)
        if "video" in msg:
            record = await self._orchestrate_media_download(msg["video"]["file_id"], "video", user_id, msg_id, ".mp4")
            if record: media_array.append(record)
        if "voice" in msg:
            record = await self._orchestrate_media_download(msg["voice"]["file_id"], "voice", user_id, msg_id, ".ogg")
            if record: media_array.append(record)
        elif "audio" in msg:
            record = await self._orchestrate_media_download(msg["audio"]["file_id"], "audio", user_id, msg_id, ".mp3")
            if record: media_array.append(record)

        message_doc = {
            "message_id": msg_id,
            "telegram_user_id": user_id,
            "text": text,
            "timestamp": datetime.fromtimestamp(msg.get("date"), timezone.utc),
            "media": media_array
        }
        
        if answered_question:
            message_doc["question"] = answered_question
            
        await self.db.insert_message(message_doc)

    async def start_polling(self):
        """Main execution loop tracking the update offset and routing incoming data."""
        print("Polling...")
        offset = await self.db.get_offset()
        if offset: offset += 1

        while True:
            try:
                updates = await self.bot.get_updates(offset)
                
                for update in updates:
                    update_id = update["update_id"]
                    
                    if "message" in update:
                        msg = update["message"]
                        user = msg.get("from")
                        chat_id = msg["chat"]["id"]
                        msg_id = msg["message_id"]
                        text = msg.get("text") or msg.get("caption") or ""
                        
                        if user: await self.db.upsert_user(user)
                        user_id = user["id"]

                        state, state_data = await self.db.get_user_state(user_id)
                        is_command = text.startswith("/")
                        answered_question = None

                        # Core routing logic
                        if is_command:
                            await self._handle_command(user_id, chat_id, text)
                        else:
                            answered_question = await self._handle_fsm_state(user_id, chat_id, text, msg, state, state_data)

                        await self._process_passive_media(user_id, msg_id, text, msg, answered_question)

                    offset = update_id + 1
                    await self.db.update_offset(update_id)

            except Exception as e:
                print(f"Loop Error: {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    app = FieldNotesFetcher()
    asyncio.run(app.start_polling())
