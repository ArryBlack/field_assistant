# 📚 Field Assistant

## Table of Contents

1. **System Architecture & Data Flow**
2. **Database Schema & State Management**
3. **The Ingestion Engine (`fetcher.py`)**
4. **The Backend API (`api.py`)**
5. **The Frontend Client (`App.tsx`)**
6. **AI Integration Layer (Gemini)**
7. **Infrastructure & Deployment**
8. **Common Developer Workflows**

---

## 1. System Architecture & Data Flow

Field Assistant is a containerized monorepo structured around three decoupled services.

### 1.1 Core Components

* **Database (PostgreSQL):** The source of truth for all relationships, metadata, and application state.
* **Blob Storage (S3-Compatible):** Stores raw binary data (photos, videos, audio, documents).
* **Fetcher (`fetcher` service):** A Python worker running an `asyncio` loop. It acts as the write-heavy ingest layer, pulling data from Telegram and pushing it to DB/S3.
* **API Server (`backend-api` service):** A read-heavy FastAPI application providing data to the frontend and proxying on-demand AI requests.
* **Frontend Client (`frontend` service):** A React/Vite SPA serving as the visualization and editing interface.

### 1.2 Data Flow: From Field to Screen

1. **Capture:** User sends a message/photo to the Telegram bot.
2. **Ingestion:** `fetcher.py` detects the update via long-polling (`bot.get_updates()`).
3. **Storage Routing:**
* Text/Location data -> Written directly to PostgreSQL `messages` and `media` tables.
* Binary Data -> Downloaded locally, uploaded to S3 asynchronously, and the resulting `s3_key` is written to PostgreSQL.


4. **Serving:** React client requests `/messages`. FastAPI executes a complex join to aggregate messages and media.
5. **Rendering:** React receives the JSON. For media elements, it requests temporary S3 presigned URLs via `/media-url` only when the item scrolls into view.

---

## 2. Database Schema & State Management

The `packages/backend/db.py` module establishes the schema. It relies heavily on foreign keys with `ON DELETE CASCADE` or `SET NULL` to maintain referential integrity.

### 2.1 The Core Hierarchy

1. **`users`**: Indexed primarily by `telegram_user_id`. Handled via `upsert_user()` to constantly update profile changes (username, language) without duplicating rows.
2. **`messages`**: Belongs to a user. Key columns:
* `raw_json (JSONB)`: Stores the immutable, exact payload from Telegram for audit/debugging.
* `survey_question (TEXT)`: If the message was an answer to an automated bot question, the question context is stored here to make the log readable later.


3. **`media`**: Belongs to a message (`message_id`). It is highly polymorphic, handling 7 different `media_type`s (photo, video, audio, voice, document, location, sticker). The `file_path` column stores the S3 Key, *not* a full URL.

### 2.2 Operational State Tables

* **`last_update`**: Contains exactly one row (`id=1`). It stores the `last_update_id` from Telegram. If the fetcher crashes and restarts, it reads this ID to resume polling exactly where it left off, preventing duplicate message processing.
* **`user_states`**: The backbone of the Finite State Machine (FSM).
* `current_state`: (e.g., `'survey_active'`).
* `current_step`: Integer pointer to the `QUESTION_BANK` array.
* `answers (JSONB)`: Accumulates responses until the survey is complete.



### 2.3 Custom Interceptors (`LoggingCursor`)

To ensure total observability, standard `psycopg2` cursors are overridden by `LoggingCursor` and `LoggingDictCursor`.

* **Function:** Wraps the `.execute()` method in a `try/except` block with `time.time()` measurements.
* **Value:** Every single SQL execution, its parameters, and its exact millisecond execution time are dumped to standard out, making slow queries trivial to identify in Docker logs.

---

## 3. The Ingestion Engine (`fetcher.py`)

This file is a robust, fault-tolerant background worker using `python-telegram-bot`.

### 3.1 The Main Loop

Runs continuously in `asyncio.run(main())`.

* Connects to the DB, fetches `last_update_id`.
* Calls `bot.get_updates(offset=..., timeout=30)`.
* Passes each update to `process_update()`, then commits the new `last_update_id` to the DB.

### 3.2 S3 Upload Concurrency

Handling large video/photo uploads in Python `asyncio` is dangerous if synchronous libraries (`boto3`) are used directly, as they block the event loop.

* **The Fix:** The `upload_telegram_file_to_s3` function wraps the `boto3.client.put_object` call inside `await asyncio.to_thread(...)`. This pushes the I/O-bound upload to a separate thread, keeping the bot responsive to new messages while uploads finish in the background.

### 3.3 Command Handling & State Logic

1. **`/start_trip`**:
* Queries `trips` to auto-close any "forgotten" trips by setting `end_date` to yesterday.
* Wipes and initializes `user_states` to trigger the FSM.


2. **`/end_trip`**:
* Updates `trips` with `NOW()`.
* Crucially, it executes a *force-clear* on `user_states` and deletes the user from the `active_conversations` memory dict to prevent the bot from getting permanently stuck in a bad state.



### 3.4 The `/converse` Subsystem

An advanced feature that proactively interviews the user based on their logs.

* **State Storage:** Uses a global Python dict `active_conversations = { user_id: { "queue": [...], "asking_continue": bool, "current_question": str } }`.
* **Execution Flow:** 1. Grabs all DB logs since trip start.
2. Sends to Gemini, requesting a JSON array of missing narrative questions.
3. Pops questions off the queue as the user replies.
4. By storing `current_question`, the script ensures that `insert_message` can save the AI's question into the `survey_question` DB column alongside the user's answer.

---

## 4. The Backend API (`api.py`)

A FastAPI implementation acting as the interface between the DB/S3 and the React client.

### 4.1 Deployment Readiness

At the bottom of `api.py`, `Mangum(app)` is implemented. This adapter allows the FastAPI application to be immediately deployed to AWS Lambda / API Gateway without code changes, enabling a serverless backend.

### 4.2 Query Optimization (`GET /messages`)

To display a timeline, the frontend needs User, Message, and Media data. A naive ORM approach would result in the "N+1 query problem" (fetching 1 message, then doing a separate query for its media, repeating 25 times per page).

* **The Solution:** The API executes a single, highly optimized Raw SQL query:
```sql
SELECT m.*, to_jsonb(u) as user,
COALESCE((SELECT jsonb_agg(med.* ORDER BY med.id) FROM media med WHERE med.message_id = m.id), '[]'::jsonb) as media
...

```


* **Why it matters:** This forces PostgreSQL to do the relational mapping internally, returning a perfectly structured JSON payload that directly matches the Pydantic `MessageWithRelations` model.

### 4.3 Data Export (`GET /messages/export`)

Instead of pagination, this endpoint accepts the same date/user filters but returns the *entire* dataset matching the criteria.

* It formats the data into a simplified, flat dictionary (`ExportMessage` model).
* **Side-effect:** It automatically writes a backup copy to `packages/backend/exports/_test_export.json` locally on the server for debugging purposes.

---

## 5. The Frontend Client (`App.tsx`)

A React 19 application utilizing Tailwind CSS and TypeScript.

### 5.1 Performance Optimization: `LazyMediaItem`

Loading 25 messages, each potentially containing multiple high-res photos, would crash the browser and incur heavy S3 presigned-URL generation costs.

* **Implementation:** The `LazyMediaItem` uses the browser's native `IntersectionObserver` API.
* **Logic:** It renders a lightweight 100px placeholder div. When the user scrolls and the div comes within `200px` of the viewport, it flips `isVisible` to true, unobserves the element, and mounts the actual `MediaItem` component. Only *then* does it request the S3 URL from the backend.

### 5.2 Optimistic UI & State Mutation

The `EditableField` component handles updating AI descriptions/transcriptions.

* When a user clicks "Save" or "Generate", it sends a request to the API.
* Upon receiving the updated `Media` object, it calls `handleMediaUpdated(updatedMedia)` on the parent `App` component.
* **The Deep Map:** To avoid re-fetching all messages and losing scroll position, `App.tsx` uses a deeply nested mapping function to immutably replace *only* the specific media object inside the specific message within the React state array.

### 5.3 JSON Export Functionality

The `handleSummarize` function fetches data from `/messages/export`. It takes the raw JSON array and formats it cleanly: `const jsonString = JSON.stringify(exportData, null, 2);`. This formatted string is then injected into a highly stylised `<pre>` block for easy copying.

---

## 6. AI Integration Layer (Google Gemini)

The system heavily leverages `gemini-2.5-flash` via the `google-generativeai` SDK across both the Fetcher and the API.

### 6.1 Trip Context Extraction (`fetcher.py`)

After the FSM completes, the raw Q&A string is passed to Gemini with a strict schema enforcement prompt:

```text
Mandatory response format: 
{ "destination": "<name>", "guide": "<name>", "target_species": "<name>", "context_summary": "<name>" }

```

The response is parsed via `json.loads()` and saved to the `trips` table.

### 6.2 Image Analysis Context Injection (`api.py`)

When generating an image description via `/media/{id}/generate-description`, the API doesn't just send the image.

* **The Context Query:** It dynamically looks up the `trip_context` associated with the user at the specific `timestamp` the image was uploaded.
* **Prompt Architecture:** It constructs a dynamic prompt: `Describe this image... Below is the context... {trip_context_str} Original caption... {caption_context}`. This ensures the AI knows *where* the user is, drastically improving description accuracy.
* **Direct Byte Passing:** Instead of creating a file in Google's cloud, the raw S3 byte stream is passed directly in the prompt array: `{"mime_type": mime_type, "data": image_bytes}`.

### 6.3 Audio File Management (`api.py`)

Audio *must* be uploaded via the Gemini File API.

* **Lifecycle Management:** The code uploads the file via `genai.upload_file()`, awaits the transcription, and then executes a `finally` block containing `genai.files.delete_file(audio_file.name)` to guarantee no orphaned data is left in the Google AI Studio project.

---

## 7. Infrastructure & Deployment

The system is configured for seamless deployment via `docker-compose.yml`.

### 7.1 Container Config

* **`backend-api`**: Maps host `5002` to container `8000`.
* **`fetcher`**: Shares the backend Docker image but executes `start.sh fetcher` to run the background script instead of the web server.
* **`frontend`**: Built via Vite. Requires `VITE_API_URL` to be injected at build time via Docker `args` so the React bundle knows where to point its API calls. Mapped to host port `5003`.

### 7.2 Secrets Management (`.env`)

The entire monorepo is governed by a single `.env` file at the root. Both the React app (via build args) and the Python backend parse this file.

* Python specifically navigates up two directories to load it: `load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))`.

---

## 8. Common Developer Workflows

### How to Modify the Database Schema

1. Edit the raw SQL strings in `packages/backend/db.py`.
2. Delete the existing PostgreSQL tables or database container.
3. Rerun `python db.py` to recreate the schema.
4. Update the corresponding `OrmBaseModel` in `packages/backend/models.py`.
5. Update the corresponding TypeScript `interface` in `packages/frontend/src/types.ts`.

### How to Add a New AI Bot Command

1. Open `packages/backend/fetcher.py`.
2. Locate the Command Interception block: `if text.strip() == "/...":`.
3. Add your new `elif text.strip() == "/your_command":` block.
4. Implement state handling (if multi-step) in `user_states` or simple API responses via `bot.send_message()`.
5. Ensure you call `return` to prevent the command from falling through into the standard archiving block.
