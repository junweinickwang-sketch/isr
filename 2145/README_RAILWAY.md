# Deploy to Railway

## 1) Environment Variables
- `GEMINI_API_KEY` = your Gemini API key
- `ADMIN_PASSWORD` = password for /admin pages (default: gour)
- (optional) `LOGS_DIR` = /app/logs  (use a Railway Volume for persistence)

## 2) Persistent Logs (Recommended)
In Railway → Storage/Volumes → Create a volume and mount to `/app/logs`.  
Set `LOGS_DIR=/app/logs` in Variables.

## 3) Start Command
Procfile already included:
```
web: gunicorn -w 2 -k gthread -b 0.0.0.0:$PORT app:app
```

## 4) Local Test
```
pip install -r requirements.txt
export GEMINI_API_KEY=...
python app.py
```
Then open http://localhost:8000
