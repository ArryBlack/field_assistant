import os
import time
import math
import mimetypes
from datetime import datetime
from typing import Optional

import aiofiles
from fastapi import FastAPI, Request, Query, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Import our modular services
from packages.backend.services.database import DatabaseClient
from packages.backend.services.llm import GeminiAssistant

# --- FORCE MIME TYPES FOR BROWSERS ---
mimetypes.add_type('audio/ogg', '.oga')
mimetypes.add_type('audio/ogg', '.ogg')
mimetypes.add_type('audio/mpeg', '.mp3')
mimetypes.add_type('video/mp4', '.mp4')


class FieldNotesAPI:
    def __init__(self):
        """Initializes the FastAPI application, services, and route bindings."""
        self.app = FastAPI(title="FieldNotes API", description="Backend API for documentary field notes.")
        
        # Initialize Core Services
        self.db = DatabaseClient(uri=os.environ.get("MONGO_URI", "mongodb://mongodb:27017"))
        self.ai = GeminiAssistant(api_key=os.environ.get("GOOGLE_API_KEY"))
        
        # Setup Storage
        self.local_media_path = os.environ.get("LOCAL_MEDIA_PATH", "/app/media")
        os.makedirs(self.local_media_path, exist_ok=True)
        
        # Wire up the application
        self._setup_middleware()
        self._setup_routes()

    def _setup_middleware(self):
        """Configures CORS and request logging middleware."""
        origins = [os.environ.get("VITE_UI_URL", "http://localhost:5003")]
        self.app.add_middleware(
            CORSMiddleware, 
            allow_origins=["*"] if "*" in origins else origins, 
            allow_methods=["*"], 
            allow_headers=["*"]
        )

        @self.app.middleware("http")
        async def log_requests(request: Request, call_next):
            start_time = time.time()
            try:
                response = await call_next(request)
                process_time = time.time() - start_time
                print(f"API Request: {request.method} {request.url} | Time: {process_time:.4f}s | Status: {response.status_code}")
                return response
            except Exception as e:
                process_time = time.time() - start_time
                print(f"API Error: {request.method} {request.url} | Time: {process_time:.4f}s | Exception: {str(e)}")
                raise

    def _setup_routes(self):
        """Mounts static files and binds class methods explicitly to API endpoints."""
        self.app.mount("/static-media", StaticFiles(directory=self.local_media_path, html=True), name="static_media")
        
        # Data Read Endpoints
        self.app.add_api_route("/users", self.get_all_users, methods=["GET"])
        self.app.add_api_route("/trips", self.get_trips, methods=["GET"])
        self.app.add_api_route("/messages", self.get_messages, methods=["GET"])
        self.app.add_api_route("/messages/export", self.export_messages, methods=["GET"])
        
        # Media & AI Endpoints
        self.app.add_api_route("/media-url", self.get_media_url, methods=["GET"])
        self.app.add_api_route("/media/{media_id}/{field_name}", self.update_media_field, methods=["PUT"])
        self.app.add_api_route("/media/{media_id}/generate-description", self.generate_media_description, methods=["POST"])
        self.app.add_api_route("/media/{media_id}/generate-transcription", self.generate_media_transcription, methods=["POST"])

    # ---------------------------------------------------------
    # ROUTE HANDLER METHODS
    # ---------------------------------------------------------
    
    async def get_all_users(self):
        return await self.db.db.users.find({}, {"_id": 0}).sort("first_name", 1).to_list(length=1000)

    async def get_trips(self):
        return await self.db.db.trips.find({}, {"_id": 0}).sort("start_date", -1).to_list(length=1000)

    async def get_messages(
        self,
        telegram_user_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page: int = Query(1, ge=1),
        limit: int = Query(25, ge=1, le=100)
    ):
        query = {}
        if telegram_user_id: query["telegram_user_id"] = telegram_user_id
            
        date_query = {}
        if start_date: date_query["$gte"] = start_date
        if end_date: date_query["$lte"] = end_date
        if date_query: query["timestamp"] = date_query

        total_count = await self.db.db.messages.count_documents(query)
        total_pages = math.ceil(total_count / limit) if total_count > 0 else 0
        skip = (page - 1) * limit

        messages = await self.db.db.messages.find(query, {"_id": 0}).sort("timestamp", -1).skip(skip).limit(limit).to_list(length=limit)

        for msg in messages:
            msg["id"] = msg.get("message_id") # React Key Polyfill
                
            if "media" not in msg: msg["media"] = []
            if "user" not in msg:
                user_doc = await self.db.db.users.find_one({"telegram_user_id": msg.get("telegram_user_id")}, {"_id": 0})
                msg["user"] = user_doc if user_doc else {"first_name": "Unknown User"}
                
        return {"messages": messages, "total_count": total_count, "total_pages": total_pages, "current_page": page}

    async def export_messages(
        self, 
        telegram_user_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ):
        query = {}
        if telegram_user_id: 
            query["telegram_user_id"] = telegram_user_id
            
        # Add date filtering logic
        date_query = {}
        if start_date: date_query["$gte"] = start_date
        if end_date: date_query["$lte"] = end_date
        if date_query: query["timestamp"] = date_query

        messages = await self.db.db.messages.find(query, {"_id": 0}).sort("timestamp", 1).to_list(length=10000)
        
        for msg in messages:
            msg["id"] = msg.get("message_id")
            
            if msg.get("timestamp"):
                # FIX 1: Safely convert the native Datetime object into a millisecond timestamp
                msg["timestamp"] = int(msg["timestamp"].timestamp() * 1000)
                
            if "user" not in msg:
                user_doc = await self.db.db.users.find_one({"telegram_user_id": msg.get("telegram_user_id")}, {"_id": 0})
                msg["user"] = user_doc if user_doc else {"first_name": "Unknown User"}
                
        return messages

    async def get_media_url(self, key: str, request: Request):
        env_base = os.environ.get("VITE_API_URL")
        base_url = env_base.rstrip("/") if env_base else str(request.base_url).rstrip("/")
        return {"url": f"{base_url}/static-media/{key}"}

    async def update_media_field(self, media_id: str, field_name: str, payload: dict = Body(...)):
        if field_name not in ["description", "transcription"]:
            raise HTTPException(status_code=400, detail="Invalid field name")
            
        await self.db.update_media_field(media_id, field_name, payload.get(field_name, ""))
        return await self.db.get_media_record(media_id)

    async def generate_media_description(self, media_id: str):
        media_item = await self.db.get_media_record(media_id)
        if not media_item: raise HTTPException(status_code=404, detail="Media not found")
            
        absolute_path = os.path.join(self.local_media_path, media_item.get("file_path"))
        
        # FIX 2: Dynamically detect MIME type with a generic image fallback
        mime_type, _ = mimetypes.guess_type(absolute_path)
        mime_type = mime_type or "image/jpeg"
        
        try:
            async with aiofiles.open(absolute_path, 'rb') as f:
                file_bytes = await f.read()
                
            generated_text = await self.ai.generate_description(file_bytes, mime_type)
            await self.db.update_media_field(media_id, "description", generated_text)
            
            media_item["description"] = generated_text
            return media_item
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def generate_media_transcription(self, media_id: str):
        media_item = await self.db.get_media_record(media_id)
        if not media_item: raise HTTPException(status_code=404, detail="Media not found")
            
        absolute_path = os.path.join(self.local_media_path, media_item.get("file_path"))
        
        # FIX 2: Dynamically detect MIME type with a generic audio fallback
        mime_type, _ = mimetypes.guess_type(absolute_path)
        mime_type = mime_type or "audio/ogg"
        
        try:
            async with aiofiles.open(absolute_path, 'rb') as f:
                file_bytes = await f.read()
                
            generated_text = await self.ai.generate_transcription(file_bytes, mime_type)
            await self.db.update_media_field(media_id, "transcription", generated_text)
            
            media_item["transcription"] = generated_text
            return media_item
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# --- APP EXPORT ---
# Uvicorn looks for a global 'app' object in the file. 
# We instantiate our class and expose the embedded FastAPI app instance.
api_instance = FieldNotesAPI()
app = api_instance.app