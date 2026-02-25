import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables from the .env file in the *root* directory
# We go up two levels: packages/backend -> packages/ -> field-assistant/
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

# --- Database Configuration ---
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = int(os.environ.get('DB_PORT', 5432))
DB_NAME = os.environ.get('DB_NAME', 'telegram_bot_db')
DB_USER = os.environ.get('DB_USER', 'postgres')
DB_PASS = os.environ.get('DB_PASS', '')

# SQL schema (This is your original schema)
CREATE_TABLE_SQL = r"""
-- users
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  telegram_user_id BIGINT UNIQUE NOT NULL,
  username TEXT,
  first_name TEXT,
  last_name TEXT,
  language_code TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- messages
CREATE TABLE IF NOT EXISTS messages (
  id SERIAL PRIMARY KEY,
  telegram_message_id BIGINT,
  update_id BIGINT,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  chat_id BIGINT,
  text TEXT,
  survey_question TEXT,
  timestamp TIMESTAMP WITH TIME ZONE,
  raw_json JSONB
);

-- trips
CREATE TABLE IF NOT EXISTS trips (
  trip_id SERIAL PRIMARY KEY,
  telegram_user_id BIGINT REFERENCES users(telegram_user_id) ON DELETE CASCADE,
  start_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  destination TEXT,
  target_species TEXT,
  guide TEXT,
  context TEXT,
  end_date TIMESTAMP WITH TIME ZONE
);

-- media
CREATE TABLE IF NOT EXISTS media (
  id SERIAL PRIMARY KEY,
  message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
  media_type TEXT NOT NULL,
  file_id TEXT,
  file_path TEXT, -- This stores the S3 Key
  file_name TEXT,
  mime_type TEXT,
  file_size INTEGER,
  transcription TEXT DEFAULT '',
  description TEXT DEFAULT '',
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION
);

-- last_update tracker
CREATE TABLE IF NOT EXISTS last_update (
  id SMALLINT PRIMARY KEY DEFAULT 1,
  last_update_id BIGINT
);

-- FSM Memory Table
CREATE TABLE IF NOT EXISTS user_states (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  current_state TEXT,
  current_step INTEGER DEFAULT 0,
  answers JSONB DEFAULT '[]'::jsonb
);

"""

# --- Database Helper Functions ---

def get_conn():
    """Establishes and returns a new database connection."""
    return psycopg2.connect(
        host=DB_HOST, 
        port=DB_PORT, 
        dbname=DB_NAME, 
        user=DB_USER, 
        password=DB_PASS
    )

def init_db():
    """Initializes the database by creating tables if they don't exist."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            # ensure tracker row exists
            cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS survey_question TEXT;")
            cur.execute("INSERT INTO last_update (id, last_update_id) VALUES (1, NULL) ON CONFLICT (id) DO NOTHING;")
        conn.commit()
        print("Database tables initialized successfully.")
    except Exception as e:
        print(f"Error initializing database: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    # You can run this file directly to set up your database
    # python packages/backend/db.py
    print("Initializing database...")
    init_db()