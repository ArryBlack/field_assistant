import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from datetime import date, datetime
import json
import psycopg2
import psycopg2.extras
import os
import boto3
import mimetypes 
import math
import io
import google.generativeai as genai


# Import shared database and models
import db
from models import (
    MessageWithRelations, User, Media, PaginatedMessages,
    GenerateRequest, UpdateDescriptionRequest, UpdateTranscriptionRequest,
    SummarizeRequest, ExportMessage  
)
# --- Pydantic models for API request bodies ---
from pydantic import BaseModel

class GenerateRequest(BaseModel):
    prompt: Optional[str] = None

class UpdateDescriptionRequest(BaseModel):
    description: str = ""

class UpdateTranscriptionRequest(BaseModel):
    transcription: str = ""

# --- S3 Configuration ---
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')
S3_ENDPOINT_URL = os.environ.get('S3_ENDPOINT_URL')
S3_ACCESS_KEY_ID = os.environ.get('S3_ACCESS_KEY_ID')
S3_SECRET_ACCESS_KEY = os.environ.get('S3_SECRET_ACCESS_KEY')

s3_client = boto3.client(
    's3',
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=S3_ACCESS_KEY_ID,
    aws_secret_access_key=S3_SECRET_ACCESS_KEY
)

# --- Gemini Configuration ---
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
else:
    print("Warning: GOOGLE_API_KEY not set. AI features will be disabled.")

vision_model = genai.GenerativeModel("gemini-2.5-flash")

# --- App Initialization ---
app = FastAPI(
    title="Field Assistant API",
    description="API for the Telegram bot archiver",
    version="1.0.0"
)

# --- CORS Configuration ---
origins = ["*", "http://localhost:9000", "https://localhost:9000"] # Allow all origins for simplicity
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*", "GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*", "Content-Type", "Authorization"],
)

# --- Database Dependency ---
def get_db_connection():
    conn = db.get_conn()
    try:
        yield conn
    finally:
        conn.close()

# --- Helper function to get a single media item ---
def get_media_item(media_id: int, conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM media WHERE id = %s", (media_id,))
        media = cur.fetchone()
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        return media

# --- API Endpoints ---

@app.get("/")
def read_root():
    return {"message": "Welcome to the Field Assistant API!"}

@app.get("/media-url")
def get_media_url(key: str = Query(..., min_length=1)):
    if not S3_BUCKET_NAME:
        raise HTTPException(status_code=500, detail="S3 bucket not configured")
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET_NAME, 'Key': key},
            ExpiresIn=3600
        )
        return {"url": url}
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        raise HTTPException(status_code=500, detail="Could not generate media URL")

@app.get("/users", response_model=List[User])
def get_all_users(conn: psycopg2.extensions.connection = Depends(get_db_connection)):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM users ORDER BY first_name;")
        return cur.fetchall()

@app.get("/messages", response_model=PaginatedMessages)
def get_all_messages(
    conn: psycopg2.extensions.connection = Depends(get_db_connection),
    telegram_user_id: Optional[int] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100)
):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        where_clauses = []
        filter_params = []
        join_clause = "LEFT JOIN users u ON m.user_id = u.id" 
        
        if telegram_user_id:
            where_clauses.append("u.telegram_user_id = %s")
            filter_params.append(telegram_user_id)
        if start_date:
            where_clauses.append("m.timestamp >= %s")
            filter_params.append(start_date)
        if end_date:
            where_clauses.append("m.timestamp <= %s")
            filter_params.append(end_date)
        
        where_sql = ""
        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)
            
        count_query = f"SELECT COUNT(m.id) FROM messages m {join_clause} {where_sql};"
        cur.execute(count_query, tuple(filter_params))
        total_count = cur.fetchone()['count']
        
        if total_count == 0:
            return {"messages": [], "total_count": 0, "total_pages": 0, "current_page": 1}

        total_pages = math.ceil(total_count / limit)
        offset = (page - 1) * limit
        
        main_query = f"""
            SELECT 
                m.id, m.telegram_message_id, m.update_id, m.user_id, 
                m.chat_id, m.text, m.survey_question, m.timestamp, m.raw_json,
                to_jsonb(u) as user,
                COALESCE(
                    (SELECT jsonb_agg(med.* ORDER BY med.id) FROM media med WHERE med.message_id = m.id), 
                    '[]'::jsonb
                ) as media
            FROM messages m
            {join_clause}
            {where_sql}
            ORDER BY m.timestamp DESC
            LIMIT %s OFFSET %s;
        """
        
        final_params = tuple(filter_params) + (limit, offset)
        cur.execute(main_query, final_params)
        messages = cur.fetchall()
        
        return {
            "messages": messages,
            "total_count": total_count,
            "total_pages": total_pages,
            "current_page": page
        }

@app.put("/media/{media_id}/description", response_model=Media)
async def update_description(
    media_id: int, 
    payload: UpdateDescriptionRequest,
    conn: psycopg2.extensions.connection = Depends(get_db_connection)
):
    description = payload.description
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE media SET description = %s WHERE id = %s RETURNING *", 
            (description, media_id)
        )
        updated_media = cur.fetchone()
        conn.commit()
        if not updated_media:
            raise HTTPException(status_code=404, detail="Media not found")
        return updated_media

@app.put("/media/{media_id}/transcription", response_model=Media)
async def update_transcription(
    media_id: int, 
    payload: UpdateTranscriptionRequest,
    conn: psycopg2.extensions.connection = Depends(get_db_connection)
):
    transcription = payload.transcription
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE media SET transcription = %s WHERE id = %s RETURNING *", 
            (transcription, media_id)
        )
        updated_media = cur.fetchone()
        conn.commit()
        if not updated_media:
            raise HTTPException(status_code=404, detail="Media not found")
        return updated_media

@app.post("/media/{media_id}/generate-description", response_model=Media)
async def generate_description(
    media_id: int,
    request: GenerateRequest,
    conn: psycopg2.extensions.connection = Depends(get_db_connection)
):
    """Generates a description for an image using Gemini."""
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=501, detail="Gemini API key not configured")

    media = get_media_item(media_id, conn)
    if not media["file_path"]:
        raise HTTPException(status_code=400, detail="Media has no file path")

    try:
        caption_context = ""
        if media.get("message_id"):
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT text FROM messages WHERE id = %s", (media["message_id"],))
                msg_row = cur.fetchone()
                if msg_row and msg_row["text"]:
                    caption_context = msg_row["text"]
        # --- THIS IS THE FIX ---
        
        # 1. Download image bytes from S3 (e.g., 5MB)
        obj = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=media["file_path"])
        image_bytes = obj['Body'].read()
        
        # 2. Get the mime type (e.g., 'image/jpeg')
        mime_type = media["mime_type"] or mimetypes.guess_type(media["file_name"])[0]
        
        # 3. Create a 'Part' object for Gemini using the raw bytes
        # We are SKIPPING the memory-intensive Pillow (Image.open) step
        image_part = {
            "mime_type": mime_type,
            "data": image_bytes
        }
        
        # 4. Send to Gemini
        prompt = f"""
            Describe this image, be concise, objective and relevant to the caption.
            Below is the context for the image. It might or might not be relevant, but it could help you understand the image better: 
            {request.prompt}
            If a location is mentioned, it will always be relevant. 
            Original caption or message text (if any): {caption_context}
            """
        prompt_text = prompt.strip()
        # Pass the prompt and the image part (raw bytes)
        response = await vision_model.generate_content_async([prompt_text, image_part])
        
        description = response.text
        
        # 5. Save to database
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE media SET description = %s WHERE id = %s RETURNING *",
                (description, media_id)
            )
            updated_media = cur.fetchone()
            conn.commit()
            return updated_media
            
    except Exception as e:
        print(f"Error generating description: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/media/{media_id}/generate-transcription", response_model=Media)
async def generate_transcription(
    media_id: int, 
    request: GenerateRequest,
    conn: psycopg2.extensions.connection = Depends(get_db_connection)
):
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=501, detail="Gemini API key not configured")
    media = get_media_item(media_id, conn)
    if not media["file_path"]:
        raise HTTPException(status_code=400, detail="Media has no file path")
    
    audio_file = None
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=media["file_path"])
        audio_bytes = obj['Body'].read()
        mime_type = media["mime_type"] or mimetypes.guess_type(media["file_name"])[0]
        
        audio_file = genai.upload_file(
            path=io.BytesIO(audio_bytes),
            display_name=media["file_name"],
            mime_type=mime_type
        )
        
        prompt_text = request.prompt or "Transcribe this audio. Only return the transcribed text."
        response = await vision_model.generate_content_async([prompt_text, audio_file])
        transcription = response.text
        
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE media SET transcription = %s WHERE id = %s RETURNING *",
                (transcription, media_id)
            )
            updated_media = cur.fetchone()
            conn.commit()
            return updated_media
            
    except Exception as e:
        print(f"Error generating transcription: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if audio_file:
            try:
                genai.files.delete_file(audio_file.name)
                print(f"Cleaned up Gemini file: {audio_file.name}")
            except Exception as e:
                print(f"Warning: Failed to delete Gemini file {audio_file.name}: {e}")


# ---  ENDPOINT FOR SUMMARIZATION (EXPORT) ---
@app.get("/messages/export", response_model=List[ExportMessage]) 
def get_all_messages_for_export(
    conn: psycopg2.extensions.connection = Depends(get_db_connection),
    # It accepts the exact same filters as /messages
    telegram_user_id: Optional[int] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None)
):
    """
    Fetches ALL messages matching a filter, without pagination,
    and formats them into a structured JSON list for summarization.
    Saves a local copy for testing.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        
        # 1. Build the dynamic query (same as /messages)
        query = """
            SELECT 
                m.text, m.timestamp,
                to_jsonb(u) as user,
                COALESCE(
                    (SELECT jsonb_agg(med.* ORDER BY med.id) FROM media med WHERE med.message_id = m.id), 
                    '[]'::jsonb
                ) as media
            FROM 
                messages m
            LEFT JOIN 
                users u ON m.user_id = u.id
        """
        where_clauses = []
        params = []
        
        if telegram_user_id:
            where_clauses.append("u.telegram_user_id = %s")
            params.append(telegram_user_id)
        if start_date:
            where_clauses.append("m.timestamp >= %s")
            params.append(start_date)
        if end_date:
            where_clauses.append("m.timestamp <= %s")
            params.append(end_date)
        
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
            
        query += " ORDER BY m.timestamp ASC;"
        
        cur.execute(query, tuple(params))
        messages = cur.fetchall()

        # 3. --- UPDATED: Create a list of structured objects ---
        export_data = []
        for msg in messages:
            user_name = msg['user']['first_name'] if msg['user'] and msg['user']['first_name'] else 'Unknown'
            timestamp_str = msg['timestamp'].isoformat()
            
            message_entry = {
                "timestamp": timestamp_str,
                "user": user_name,
                "text": msg['text'] or None
            }
            
            # Add media data if it exists
            for media_item in msg['media']:
                if media_item['media_type'] == 'photo' and media_item['description']:
                    message_entry["image_description"] = media_item['description']
                if media_item['media_type'] in ('audio', 'voice') and media_item['transcription']:
                    message_entry["audio_transcription"] = media_item['transcription']
                if media_item['media_type'] == 'location':
                    message_entry["location"] = f"({media_item['latitude']}, {media_item['longitude']})"
            
            export_data.append(message_entry)

        # 4. --- UPDATED: Save the data as a JSON file ---
        export_filename = "_test_export.json"
        try:
            export_dir = os.path.join(os.path.dirname(__file__), "exports")
            os.makedirs(export_dir, exist_ok=True)
            export_path = os.path.join(export_dir, export_filename)

            # Write the file as a JSON list
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2)
                
            print(f"Export saved to {export_path}")
        except Exception as e:
            print(f"Warning: failed to save export locally: {e}")

        # 5. Return the structured list to the frontend
        return export_data

# --- NEW ENDPOINT FOR SUMMARIZATION (AI) ---
@app.post("/summarize", response_model=dict)
async def generate_summary(request: SummarizeRequest):
    """
    Takes a large block of text and generates a summary using Gemini.
    """
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=501, detail="Gemini API key not configured")

    try:
        final_prompt = request.prompt or "Summarize the following field notes, messages, descriptions, and transcriptions into a concise report. Group observations by theme or location if possible:"
        
        full_content = final_prompt + "\n\n--- DATA START ---\n" + request.full_text + "\n--- DATA END ---"
        
        # Send to Gemini
        response = await vision_model.generate_content_async(full_content)
        
        return {"summary": response.text}
        
    except Exception as e:
        print(f"Error generating summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Run the App ---
if __name__ == "__main__":
    print("Starting FastAPI server on http://127.0.0.1:8000")
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)

# --- DEPLOYMENT HANDLER FOR AWS LAMBDA ---
from mangum import Mangum
handler = Mangum(app)