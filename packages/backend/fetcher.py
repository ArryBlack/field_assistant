#!/usr/bin/env python3

import os
import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from telegram import Bot, Message
import boto3
import google.generativeai as genai

# --- Import from our new db.py module ---
from db import get_conn, init_db

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

import time

# --- Custom Database Cursors for Logging ---
class LoggingCursor(psycopg2.extensions.cursor):
    """Logs standard SQL queries and execution times."""
    def execute(self, query, vars=None):
        start_time = time.time()
        clean_query = " ".join(query.split())
        logger.info(f"DB Query: {clean_query} | Params: {vars}")
        try:
            result = super().execute(query, vars)
            exec_time = time.time() - start_time
            logger.info(f"DB Query Success | Time: {exec_time:.4f}s")
            return result
        except Exception as e:
            exec_time = time.time() - start_time
            logger.error(f"DB Query Failed | Time: {exec_time:.4f}s | Error: {e}")
            raise

class LoggingDictCursor(psycopg2.extras.DictCursor):
    """Logs dictionary-based SQL queries and execution times."""
    def execute(self, query, vars=None):
        start_time = time.time()
        clean_query = " ".join(query.split())
        logger.info(f"DB Query (Dict): {clean_query} | Params: {vars}")
        try:
            result = super().execute(query, vars)
            exec_time = time.time() - start_time
            logger.info(f"DB Query Success | Time: {exec_time:.4f}s")
            return result
        except Exception as e:
            exec_time = time.time() - start_time
            logger.error(f"DB Query Failed | Time: {exec_time:.4f}s | Error: {e}")
            raise
# --- Load Environment Variables ---
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

# Telegram
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

# S3-Compatible Storage
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')
S3_ENDPOINT_URL = os.environ.get('S3_ENDPOINT_URL')
S3_ACCESS_KEY_ID = os.environ.get('S3_ACCESS_KEY_ID')
S3_SECRET_ACCESS_KEY = os.environ.get('S3_SECRET_ACCESS_KEY')

# --- Gemini Configuration ---
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    text_model = genai.GenerativeModel("gemini-2.5-flash") 
    logger.info("Gemini AI configured successfully with gemini-2.5-flash.")
else:
    text_model = None
    logger.warning("GOOGLE_API_KEY not set. Trip summarization and conversation will be disabled.")

# --- Validations ---
if not BOT_TOKEN:
    raise RuntimeError("Please set TELEGRAM_BOT_TOKEN in your .env file.")
if not S3_BUCKET_NAME or not S3_ACCESS_KEY_ID or not S3_SECRET_ACCESS_KEY:
    raise RuntimeError("Please set S3_BUCKET_NAME, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY in your .env")

# --- Global Clients ---
s3_client = boto3.client(
    's3',
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=S3_ACCESS_KEY_ID,
    aws_secret_access_key=S3_SECRET_ACCESS_KEY
)

# --- Question Bank for FSM ---
QUESTION_BANK = [
    "Where are you going?", 
    "Who is the local guide?", 
    "What's the target species?"
]

# --- In-Memory State for /converse ---
active_conversations = {}

# ---------------- DB helpers (specific to fetcher) ----------------

def get_last_update_id(conn):
    with conn.cursor(cursor_factory=LoggingCursor) as cur:
        cur.execute('SELECT last_update_id FROM last_update WHERE id=1')
        row = cur.fetchone()
        return row[0] if row else None

def set_last_update_id(conn, update_id):
    with conn.cursor(cursor_factory=LoggingCursor) as cur:
        cur.execute('UPDATE last_update SET last_update_id=%s WHERE id=1', (update_id,))
    conn.commit()

def upsert_user(conn, user_obj):
    with conn.cursor(cursor_factory=LoggingCursor) as cur:
        cur.execute("""
            INSERT INTO users (telegram_user_id, username, first_name, last_name, language_code)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (telegram_user_id) DO UPDATE SET
              username = EXCLUDED.username,
              first_name = EXCLUDED.first_name,
              last_name = EXCLUDED.last_name,
              language_code = EXCLUDED.language_code
            RETURNING id
        """, (
            user_obj.id,
            getattr(user_obj, 'username', None),
            getattr(user_obj, 'first_name', None),
            getattr(user_obj, 'last_name', None),
            getattr(user_obj, 'language_code', None)
        ))
        uid = cur.fetchone()[0]
    conn.commit()
    return uid

def insert_message(conn, update_id, msg: Message, user_id, survey_question=None):
    with conn.cursor(cursor_factory=LoggingCursor) as cur:
        ts = msg.date if msg.date else datetime.now(timezone.utc)
        cur.execute(
            "INSERT INTO messages (telegram_message_id, update_id, user_id, chat_id, text, survey_question, timestamp, raw_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                msg.message_id,
                update_id,
                user_id,
                msg.chat.id if msg.chat else None,
                msg.text or msg.caption,
                survey_question,
                ts,
                psycopg2.extras.Json(msg.to_dict())
            )
        )
        mid = cur.fetchone()[0]
    conn.commit()
    return mid

def insert_media(conn, message_id, media_record):
    with conn.cursor(cursor_factory=LoggingCursor) as cur:
        cur.execute(
            "INSERT INTO media (message_id, media_type, file_id, file_path, file_name, mime_type, file_size, transcription, description, latitude, longitude) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                message_id,
                media_record.get('media_type'),
                media_record.get('file_id'),
                media_record.get('file_path'),
                media_record.get('file_name'),
                media_record.get('mime_type'),
                media_record.get('file_size'),
                media_record.get('transcription', ''),
                media_record.get('description', ''),
                media_record.get('latitude'),
                media_record.get('longitude')
            )
        )
        mid = cur.fetchone()[0]
    conn.commit()
    return mid

def insert_trip(conn, telegram_user_id, destination, guide, target_species, context):
    with conn.cursor(cursor_factory=LoggingCursor) as cur:
        cur.execute("""
            INSERT INTO trips (telegram_user_id, destination, target_species, guide, context)
            VALUES (%s, %s, %s, %s, %s)
        """, (telegram_user_id, destination, target_species, guide, context))
    conn.commit()

def end_active_trip(conn, telegram_user_id):
    with conn.cursor(cursor_factory=LoggingCursor) as cur:
        cur.execute("""
            UPDATE trips SET end_date = NOW()
            WHERE telegram_user_id = %s AND end_date IS NULL
        """, (telegram_user_id,))
        updated_rows = cur.rowcount
    conn.commit()
    return updated_rows

# ---------------- /converse Helpers ----------------

def get_latest_trip_and_logs(conn, telegram_user_id):
    """Fetches the latest trip and all subsequent logs if the trip has started."""
    with conn.cursor(cursor_factory=LoggingDictCursor) as cur:
        cur.execute("""
            SELECT * FROM trips 
            WHERE telegram_user_id = %s AND end_date IS NULL
            ORDER BY start_date DESC LIMIT 1
        """, (telegram_user_id,))
        trip = cur.fetchone()
        
        if not trip or not trip['start_date']:
            return None, None
            
        if datetime.now(timezone.utc) < trip['start_date']:
            return trip, None
            
        cur.execute("""
            SELECT text FROM messages 
            WHERE user_id = (SELECT id FROM users WHERE telegram_user_id = %s)
            AND timestamp >= %s
            AND text IS NOT NULL AND text != ''
            ORDER BY timestamp ASC
        """, (telegram_user_id, trip['start_date']))
        
        logs = [row['text'] for row in cur.fetchall()]
        return trip, logs

async def generate_converse_queue(logs):
    """Passes logs to Gemini and returns a parsed JSON array of questions/suggestions."""
    logs_text = "\n".join(logs) if logs else "No logs yet."
    
    prompt = f"""Read the travel logs given below and then do the tasks:
LOGS:
{logs_text}

TASK:
The logs would be used to write blog posts, and make documentary reels on social media. Your job is to be a curious reader, and come up with questions that will make a more compelling blog post and/or a better documentary reel. Come up with a maximum of 5 questions and 3 suggestions, and based on your intelligence score them on the importance of their answers on a scale of 0 to 3. 
If some key information is missing and is essential for you to write the blog post or make the documentary, then label it as 3. If something is really not important and you are just conversing, then label it 0. So, 3 is when it is a deal breaker and you can not work without that input, 2 is when it is very important, 1 is when it is important and would be used to make reading the blog post more interesting, adding finer details, etc. 0 when it is not important. 

Your suggestions should entail what kind of videos the traveller can take, or experience they can have. You can even ask. Eg: You can ask, "Did you take a video while travelling from the airport to the jungle?" or "You should take videos of the surroundings, travelling..".

Know the scope of the blog post and of the documentary:
The blog post and the documentary would cover the travel, the accommodation, the local guide, the arranging company, the species observed including the target species, their quirks, experience of the exploration of the jungle, finances, to do's, precautions, etc. 

MANDATORY Response Format: 
[
    {{
        "message_type": "question",
        "importance": 3,
        "text": "What kind of amenities are you getting in the homestay?"
    }}
]"""

    logger.info("Generating /converse queue via Gemini API.")
    logger.info(f"--- GEMINI PROMPT START ---\n{prompt}\n--- GEMINI PROMPT END ---")
    
    try:
        response = await text_model.generate_content_async(prompt)
        logger.info(f"--- GEMINI RESPONSE START ---\n{response.text}\n--- GEMINI RESPONSE END ---")
        
        raw_json = response.text.strip()
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:-3]
        elif raw_json.startswith("```"):
            raw_json = raw_json[3:-3]
            
        return json.loads(raw_json)
    except Exception as e:
        logger.error(f"Error generating converse queue: {e}")
        return []

# ---------------- S3/Telegram helpers ----------------

async def upload_telegram_file_to_s3(file_obj, s3_key, mime_type):
    try:
        start_time = time.time()
        logger.info(f"Starting S3 upload for {s3_key}...")
        byte_data = await file_obj.download_as_bytearray()
        await asyncio.to_thread(
            s3_client.put_object,
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=byte_data,
            ContentType=mime_type or 'application/octet-stream'
        )
        exec_time = time.time() - start_time
        logger.info(f"S3 Upload Success | File: {s3_key} | Size: {len(byte_data)} bytes | Time: {exec_time:.4f}s")
        return len(byte_data)
    except Exception as e:
        logger.error(f"Error uploading {s3_key} to S3: {e}")
        return 0

# ---------------- Main Processor ----------------

async def process_update(conn, update_obj, bot: Bot):
    msg = update_obj.message or update_obj.edited_message
    if not msg: return
    update_id = update_obj.update_id
    from_user = msg.from_user
    if not from_user: return

    user_id_db = upsert_user(conn, from_user)
    
    # 1. Check FSM State BEFORE saving the message
    with conn.cursor(cursor_factory=LoggingCursor) as cur:
        cur.execute("SELECT current_state, current_step, answers FROM user_states WHERE user_id = %s", (user_id_db,))
        state_row = cur.fetchone()
        
    current_question_context = None
    text = msg.text or msg.caption or ""

    # 2. Command Interception
    if text.strip() == "/start_trip":
        logger.info(f"User {from_user.id} initiated /start_trip")
        
        # --- NEW LOGIC: Auto-close unended trips with yesterday's date ---
        with conn.cursor(cursor_factory=LoggingCursor) as cur:
            cur.execute("""
                UPDATE trips 
                SET end_date = NOW() - INTERVAL '1 day'
                WHERE telegram_user_id = %s AND end_date IS NULL
            """, (from_user.id,))
            closed_count = cur.rowcount
            conn.commit()
            
        if closed_count > 0:
            await bot.send_message(
                chat_id=msg.chat.id, 
                text="Notice: You had an ongoing trip that wasn't ended. I have automatically closed it using yesterday's date before starting this new one."
            )
            
        # --- Initialize the new FSM ---
        with conn.cursor(cursor_factory=LoggingCursor) as cur:
            cur.execute("""
                INSERT INTO user_states (user_id, current_state, current_step, answers) 
                VALUES (%s, 'survey_active', 0, '[]'::jsonb)
                ON CONFLICT (user_id) DO UPDATE SET current_state = 'survey_active', current_step = 0, answers = '[]'::jsonb
            """, (user_id_db,))
            conn.commit()
            
        await bot.send_message(chat_id=msg.chat.id, text=f"Question 1: {QUESTION_BANK[0]}")
        insert_message(conn, update_id, msg, user_id_db, survey_question=None)
        return

    if text.strip() == "/converse":
        logger.info(f"User {from_user.id} initiated /converse")
        trip, logs = get_latest_trip_and_logs(conn, from_user.id)
        
        if not trip or logs is None:
            await bot.send_message(chat_id=msg.chat.id, text="There is no ongoing trip. Please use /start_trip to begin one before conversing.")
            return

        await bot.send_message(chat_id=msg.chat.id, text="Analyzing your logs so far... give me a moment to come up with some questions!")
        
        queue = await generate_converse_queue(logs)
        if not queue:
            await bot.send_message(chat_id=msg.chat.id, text="I couldn't generate questions right now. Try again later.")
            return
            
        first_q = queue.pop(0)
        q_text = first_q.get('text', '') # Removed importance score labels
        
        # --- LOCAL VARIABLE: Save queue and the question we are about to ask ---
        active_conversations[user_id_db] = {
            "queue": queue, 
            "asking_continue": False,
            "current_question": q_text 
        }
            
        await bot.send_message(chat_id=msg.chat.id, text=q_text)
        insert_message(conn, update_id, msg, user_id_db, survey_question=None)
        return

    # 3. Answer Processing
    if state_row and state_row[0] == 'survey_active':
        current_step = state_row[1]
        answers = state_row[2] or []
        
        current_question_context = QUESTION_BANK[current_step]
        answers.append(text)
        next_step = current_step + 1

        with conn.cursor(cursor_factory=LoggingCursor) as cur:
            if next_step < len(QUESTION_BANK):
                cur.execute("UPDATE user_states SET current_step = %s, answers = %s::jsonb WHERE user_id = %s", 
                            (next_step, psycopg2.extras.Json(answers), user_id_db))
                conn.commit()
                await bot.send_message(chat_id=msg.chat.id, text=f"Question {next_step + 1}: {QUESTION_BANK[next_step]}")
            else:
                cur.execute("UPDATE user_states SET current_state = NULL, current_step = 0 WHERE user_id = %s", (user_id_db,))
                conn.commit()
                
                await bot.send_message(chat_id=msg.chat.id, text="Survey Complete! Processing your trip details via AI...")
                
                summary_text = "\n".join([f"Q: {q}\nA: {a}" for q, a in zip(QUESTION_BANK, answers)])
                
                prompt = f"""From the given text, extract the name of the place the user is travelling to, the local guide, and the target species. 
Response: json
Mandatory response format: 
{{
  "destination": "<name of place>",
  "guide": "<name of local guide>",
  "target_species": "<target species>",
  "context_summary": "Place: <name>, local guide: <name>, target species: <name>"
}}

Text:
{summary_text}"""
                
                logger.info("Sending /start_trip summary to Gemini for extraction.")
                logger.info(f"--- GEMINI PROMPT START ---\n{prompt}\n--- GEMINI PROMPT END ---")
                
                destination, guide, target_species, context_summary = "Unknown", "Unknown", "Unknown", summary_text
                
                try:
                    if text_model:
                        response = await text_model.generate_content_async(prompt)
                        logger.info(f"--- GEMINI RESPONSE START ---\n{response.text}\n--- GEMINI RESPONSE END ---")
                        
                        raw_json = response.text.strip()
                        if raw_json.startswith("```json"):
                            raw_json = raw_json[7:-3]
                        elif raw_json.startswith("```"):
                            raw_json = raw_json[3:-3]
                            
                        extracted = json.loads(raw_json)
                        destination = extracted.get("destination", "Unknown")
                        guide = extracted.get("guide", "Unknown")
                        target_species = extracted.get("target_species", "Unknown")
                        context_summary = extracted.get("context_summary", summary_text)
                        
                    insert_trip(conn, from_user.id, destination, guide, target_species, context_summary)
                    success_msg = f"New trip logged!\n📍 Destination: {destination}\n👤 Guide: {guide}\n🐾 Target: {target_species}"
                    await bot.send_message(chat_id=msg.chat.id, text=success_msg)
                    
                except Exception as e:
                    logger.error(f"Error parsing Gemini response: {e}")
                    insert_trip(conn, from_user.id, "Error Parsing", "Error Parsing", "Error Parsing", summary_text)
                    await bot.send_message(chat_id=msg.chat.id, text="Trip started, but I had trouble parsing the details automatically.")

    # 4. Converse Active Processing 
    elif user_id_db in active_conversations:
        local_state = active_conversations[user_id_db]
        
        # 1. Grab the question they are currently answering for the DB log!
        current_question_context = local_state.get("current_question")
        
        # 2. Process the state
        if local_state.get("asking_continue"):
            if text.strip().lower() in ['yes', 'y', 'yeah']:
                await bot.send_message(chat_id=msg.chat.id, text="Great! Let me read the new logs and generate more questions...")
                trip, logs = get_latest_trip_and_logs(conn, from_user.id)
                new_queue = await generate_converse_queue(logs)
                
                if new_queue:
                    first_q = new_queue.pop(0)
                    q_text = first_q.get('text', '') # Removed importance score labels
                    
                    # Update local variable with new queue and new question
                    active_conversations[user_id_db] = {
                        "queue": new_queue, 
                        "asking_continue": False,
                        "current_question": q_text
                    }
                    await bot.send_message(chat_id=msg.chat.id, text=q_text)
                else:
                    del active_conversations[user_id_db] # Wipe memory
                    await bot.send_message(chat_id=msg.chat.id, text="I ran out of ideas! Let's chat later.")
            else:
                del active_conversations[user_id_db] # Wipe memory
                await bot.send_message(chat_id=msg.chat.id, text="Alright, I'll stop asking questions. Enjoy your trip!")
        else:
            queue = local_state.get("queue", [])
            if queue:
                next_q = queue.pop(0)
                q_text = next_q.get('text', '') # Removed importance score labels
                
                # Update the state so the NEXT reply logs this question
                local_state["current_question"] = q_text
                await bot.send_message(chat_id=msg.chat.id, text=q_text)
            else:
                # Setup the continue prompt so their yes/no reply links to this string
                local_state["asking_continue"] = True
                local_state["current_question"] = "I've run out of questions for now! Do you want to converse further? (Yes/No)"
                await bot.send_message(chat_id=msg.chat.id, text=local_state["current_question"])
    # 5. Standard Archiving (With Context)
    message_db_id = insert_message(conn, update_id, msg, user_id_db, survey_question=current_question_context)

    # Location
    if msg.location:
        loc = msg.location
        media_record = {'media_type': 'location', 'latitude': loc.latitude, 'longitude': loc.longitude}
        insert_media(conn, message_db_id, media_record)

    # Photo
    if msg.photo:
        best = msg.photo[-1]
        file_obj = await bot.get_file(best.file_id)
        ext = Path(file_obj.file_path).suffix or '.jpg'
        s3_key_name = f'photo_{best.file_id}{ext}'
        s3_key_path = f"{from_user.id}/{msg.message_id}/{s3_key_name}"
        size = await upload_telegram_file_to_s3(file_obj, s3_key_path, 'image/jpeg')
        media_record = {'media_type': 'photo', 'file_id': best.file_id, 'file_path': s3_key_path, 'file_name': s3_key_name, 'mime_type': 'image/jpeg', 'file_size': size}
        insert_media(conn, message_db_id, media_record)

    # Audio
    if msg.audio:
        audio = msg.audio
        file_obj = await bot.get_file(audio.file_id)
        ext = Path(file_obj.file_path).suffix or '.mp3'
        s3_key_name = audio.file_name or f'audio_{audio.file_id}{ext}'
        s3_key_path = f"{from_user.id}/{msg.message_id}/{s3_key_name}"
        size = await upload_telegram_file_to_s3(file_obj, s3_key_path, audio.mime_type)
        media_record = {'media_type': 'audio', 'file_id': audio.file_id, 'file_path': s3_key_path, 'file_name': s3_key_name, 'mime_type': audio.mime_type, 'file_size': size}
        insert_media(conn, message_db_id, media_record)

    # Voice
    if msg.voice:
        voice = msg.voice
        file_obj = await bot.get_file(voice.file_id)
        ext = Path(file_obj.file_path).suffix or '.ogg'
        s3_key_name = f'voice_{voice.file_id}{ext}'
        s3_key_path = f"{from_user.id}/{msg.message_id}/{s3_key_name}"
        size = await upload_telegram_file_to_s3(file_obj, s3_key_path, voice.mime_type)
        media_record = {'media_type': 'voice', 'file_id': voice.file_id, 'file_path': s3_key_path, 'file_name': s3_key_name, 'mime_type': voice.mime_type, 'file_size': size}
        insert_media(conn, message_db_id, media_record)

    # Video
    if msg.video:
        video = msg.video
        file_obj = await bot.get_file(video.file_id)
        ext = Path(file_obj.file_path).suffix or '.mp4'
        s3_key_name = f'video_{video.file_id}{ext}'
        s3_key_path = f"{from_user.id}/{msg.message_id}/{s3_key_name}"
        size = await upload_telegram_file_to_s3(file_obj, s3_key_path, video.mime_type)
        media_record = {'media_type': 'video', 'file_id': video.file_id, 'file_path': s3_key_path, 'file_name': s3_key_name, 'mime_type': video.mime_type, 'file_size': size}
        insert_media(conn, message_db_id, media_record)

    # Document
    if msg.document:
        doc = msg.document
        file_obj = await bot.get_file(doc.file_id)
        ext = Path(file_obj.file_path).suffix or '.bin'
        s3_key_name = doc.file_name or f'doc_{doc.file_id}{ext}'
        s3_key_path = f"{from_user.id}/{msg.message_id}/{s3_key_name}"
        size = await upload_telegram_file_to_s3(file_obj, s3_key_path, doc.mime_type)
        media_record = {'media_type': 'document', 'file_id': doc.file_id, 'file_path': s3_key_path, 'file_name': s3_key_name, 'mime_type': doc.mime_type, 'file_size': size}
        insert_media(conn, message_db_id, media_record)

    # Sticker
    if msg.sticker:
        st = msg.sticker
        file_obj = await bot.get_file(st.file_id)
        ext = Path(file_obj.file_path).suffix or '.webp'
        s3_key_name = f'sticker_{st.file_id}{ext}'
        s3_key_path = f"{from_user.id}/{msg.message_id}/{s3_key_name}"
        size = await upload_telegram_file_to_s3(file_obj, s3_key_path, st.mime_type or 'image/webp')
        media_record = {'media_type': 'sticker', 'file_id': st.file_id, 'file_path': s3_key_path, 'file_name': s3_key_name, 'mime_type': st.mime_type or 'image/webp', 'file_size': size}
        insert_media(conn, message_db_id, media_record)

# ---------------- Main Execution ----------------

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Please set TELEGRAM_BOT_TOKEN in your .env file.")
        
    bot = Bot(token=BOT_TOKEN)
    logger.info("Starting persistent Telegram fetcher...")

    while True:
        conn = None
        try:
            conn = get_conn()
            
            with conn.cursor(cursor_factory=LoggingCursor) as cur:
                cur.execute("SELECT last_update_id FROM last_update LIMIT 1")
                row = cur.fetchone()
                offset = row[0] + 1 if row else None

            updates = await bot.get_updates(offset=offset, timeout=30)
            
            for update in updates:
                logger.info(f"Processing update {update.update_id}")
                await process_update(conn, update, bot)
                
                with conn.cursor(cursor_factory=LoggingCursor) as cur:
                    if offset is None:
                        cur.execute("INSERT INTO last_update (last_update_id) VALUES (%s)", (update.update_id,))
                    else:
                        cur.execute("UPDATE last_update SET last_update_id = %s", (update.update_id,))
                conn.commit()

        except Exception as e:
            logger.error(f"Error in fetch loop: {e}", exc_info=True)
            await asyncio.sleep(5) 
            
        finally:
            if conn:
                conn.close()

if __name__ == '__main__':
    asyncio.run(main())