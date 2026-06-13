import motor.motor_asyncio
from datetime import datetime, timezone

class DatabaseClient:
    def __init__(self, uri: str, db_name: str = "field_assistant"):
        self.client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self.client[db_name]
        print(f"Initialized mongo instance")

    # --- Config / Offset ---
    async def get_offset(self):
        state = await self.db.config.find_one({"key": "last_update"})
        if state and state.get("last_update_id") is not None:
            print(f"Found last offset: {state}")
            return state["last_update_id"]
        else:
            print(f"Offset not found")
            return None


    async def update_offset(self, new_offset: int):
        try: 
            await self.db.config.update_one(
            {"key": "last_update"}, 
            {"$set": {"last_update_id": new_offset}}, 
            upsert=True
            )
            print(f"Updated offset to: {new_offset}")
        except Exception as e:
            print(f"An error occurred while updating the offset: {e}")
        
        

    # --- Users & State ---
    async def upsert_user(self, user_data: dict):
        try:
            await self.db.users.update_one(
            {"telegram_user_id": user_data["id"]},
            {"$set": {
                "first_name": user_data.get("first_name"),
                "username": user_data.get("username"),
                "language_code": user_data.get("language_code")
            }},
            upsert=True
        )
            print(f"User upserted: {user_data['id']}")
        except Exception as e:
            print(f"An error occurred while upserting user ({user_data.get('username')}): {e}")

    async def get_user_state(self, user_id: int):
        user = await self.db.users.find_one({"telegram_user_id": user_id})
        if not user:
            print(f"User state requested for unknown user_id: {user_id}")
            return "IDLE", {}
        print(f"Retrieved state for user_id {user_id}: {user.get('state', 'IDLE')}")
        return user.get("state", "IDLE"), user.get("state_data", {})

    async def set_user_state(self, user_id: int, state: str, data: dict = None):
        update = {"state": state}
        if data is not None: 
            update["state_data"] = data
        try:
            await self.db.users.update_one({"telegram_user_id": user_id}, {"$set": update})
            print(f"User state updated for user_id {user_id}: {state}")
        except Exception as e:
            print(f"An error occurred while updating user state for user_id {user_id}: {e}")

    # --- Trips ---
    async def get_active_trip(self, user_id: int):
        print(f"Checking for active trip for user_id: {user_id}")
        try:
            trip = await self.db.trips.find_one({"telegram_user_id": user_id, "status": "active"})
            if trip:
                print(f"Active trip found for user_id {user_id}: {trip}")
            else:
                print(f"No active trip found for user_id {user_id}")
            return trip
        except Exception as e:
            print(f"An error occurred while fetching active trip for user_id {user_id}: {e}")
            return None
        
    async def complete_active_trips(self, user_id: int):
        try:
            await self.db.trips.update_many(
            {"telegram_user_id": user_id, "status": "active"}, 
            {"$set": {"status": "completed", "end_date": datetime.now(timezone.utc)}}
        )
            print(f"Completed active trips for user_id {user_id}")
        except Exception as e:
            print(f"An error occurred while completing active trips for user_id {user_id}: {e}")
        

    async def start_trip(self, user_id: int, timestamp: datetime, metadata: dict):
        try: 
            await self.db.trips.insert_one({
            "telegram_user_id": user_id,
            "status": "active",
            "start_date": timestamp,
            "metadata": metadata
        })
            print(f"Started new trip for user_id {user_id} with metadata: {metadata}")
        except Exception as e:
            print(f"An error occurred while starting a trip for user_id {user_id}: {e}")
        

    # --- Messages & Media ---
    async def insert_message(self, message_doc: dict):
        try:
            await self.db.messages.insert_one(message_doc)
            print(f"Message inserted for user_id {message_doc.get('telegram_user_id')}")
        except Exception as e:
            print(f"An error occurred while inserting message: {e}")

    async def get_messages_since(self, user_id: int, since_timestamp: float, limit: int = 50):
        return await self.db.messages.find({
            "telegram_user_id": user_id, 
            "timestamp": {"$gte": since_timestamp}
        }).sort("timestamp", 1).to_list(length=limit)

    async def get_media_record(self, media_id: str):
        msg = await self.db.messages.find_one({"media.id": media_id})
        if not msg:
            return None
        return next((m for m in msg.get("media", []) if m["id"] == media_id), None)

    async def update_media_field(self, media_id: str, field: str, value: str):
        try:
            await self.db.messages.update_one(
                {"media.id": media_id},
                {"$set": {f"media.$.{field}": value}}
            )
            print(f"Media field updated for media_id {media_id}: {field}")
        except Exception as e:
            print(f"An error occurred while updating media field for media_id {media_id}: {e}")