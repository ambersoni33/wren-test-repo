# Fieldnote — Voice Recorder Native Mobile App & Backend

A native cross-platform voice recording, continuous chunk streaming, and memo management system built with **Expo SDK 58 (React Native 0.88)**, **`expo-audio`**, **Whisper AI**, **MongoDB**, and a high-performance **FastAPI (Python)** backend.

---

## Architecture Overview

```
voiceRecorderApp/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── db.py            # SQLite schema for audio files & recordings metadata
│   │   ├── mongo.py         # MongoDB connection & transcript chunk storage
│   │   ├── transcriber.py   # Whisper AI speech-to-text pipeline
│   │   └── main.py          # FastAPI endpoints (transcription, SSE stream, WS, CRUD)
│   ├── requirements.txt
│   └── data/                # SQLite DB and stored audio files
└── mobile/
    ├── src/
    │   ├── components/
    │   │   ├── AudioPlayerBar.tsx      # Docked playback bar with scrub & speed controls
    │   │   ├── RenameModal.tsx        # In-place memo title editor
    │   │   ├── SaveModal.tsx          # Post-recording title & favorite modal
    │   │   ├── SettingsModal.tsx      # Server & MongoDB URL config, test ping & sync
    │   │   ├── TranscriptModal.tsx    # AI transcript viewer loaded from MongoDB
    │   │   └── WaveformVisualizer.tsx # Live decibel reactive wave & static scrub wave
    │   ├── screens/
    │   │   ├── RecordScreen.tsx       # Live continuous recorder & live streaming transcript
    │   │   └── LibraryScreen.tsx      # Memo list with search, filter chips, player & transcript
    │   ├── services/
    │   │   ├── api.ts                 # Backend REST client (chunks, transcripts, CRUD)
    │   │   └── storage.ts             # Offline-first AsyncStorage & permanent audio files
    │   ├── types/
    │   │   └── recording.ts           # Shared TypeScript interfaces
    │   └── utils/
    │       └── formatters.ts          # Duration, date, file size & client ID helpers
    ├── App.tsx                        # Tab navigator, global state & active player bar
    ├── app.json                       # Native permissions & expo-audio plugin config
    └── package.json
```

---

## Key Features

1. **Record, then Whisper**:
   - Tap the record button to capture audio. Tap it again to stop.
   - The finished recording is sent once to the backend.
   - The backend transcribes it with OpenAI Whisper (`whisper-1` when `OPENAI_API_KEY` is set, otherwise the official `openai-whisper` model on this machine).

2. **MongoDB Transcript Storage**:
   - The finished transcript is saved in MongoDB. The database name comes from `MONGODB_URI` (this project uses `voiceRecorder`).
   - Configurable MongoDB connection string:
     - Set via environment variable `MONGODB_URI` (e.g. `mongodb://localhost:27017` or MongoDB Atlas `mongodb+srv://...`).
     - Or configure live from the mobile app's **Settings Modal** ("Test MongoDB" button).
     - Built-in fallback to local storage so the server stays operational even before MongoDB is connected.

3. **Real-Time Streaming from Saved Side**:
   - Server-Sent Events (`GET /api/transcribe/{session_id}/stream`) and WebSockets (`/ws/transcribe/{session_id}`).
   - Streams newly transcribed and saved text chunks to the frontend as they are committed to MongoDB.
   - Any recording in the **Library** can be opened to view its full saved transcript breakdown with the "AI Transcript" button.

4. **Native Audio Engine & Visualizer**:
   - Stereo 44.1 kHz AAC/M4A recording using `expo-audio`.
   - Real-time reactive waveform bars responding to microphone decibels (`metering`).
   - Pause, resume, and discard capabilities during active recording.

5. **Playback Bar & Scrubber**:
   - In-app playback with scrubbable waveform visualizer.
   - ±10-second skip buttons and 1.0x / 1.25x / 1.5x / 2.0x playback speed toggling.

6. **Offline-First Resilience**:
   - Local storage in app document directory; automatically uploads to the cloud when online.

---

## Getting Started

### 1. MongoDB Configuration
You can pass your MongoDB connection string in one of two ways:
- In `.env` or environment:
  ```bash
  export MONGODB_URI="mongodb+srv://<username>:<password>@cluster0.mongodb.net/fieldnote_db"
  ```
- Or open the mobile app, tap the **Settings** icon, paste your MongoDB URI, and tap **"Test MongoDB"**.

### 2. Start the Backend Server (FastAPI)

```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- API Docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/api/health`
- MongoDB status: `http://localhost:8000/api/mongo/status`

### 3. Start the Mobile App (Expo)

```bash
cd mobile
npx expo start
```
- Press `a` for Android Emulator or `i` for iOS Simulator.
- Or scan with **Expo Go** on a physical phone.
