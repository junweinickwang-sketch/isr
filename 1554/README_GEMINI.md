# Enable real AI Overview with Gemini

## Install
```
pip install -r requirements.txt
```

## Configure API key (recommended: environment variable)
**Mac/Linux**
```
export GEMINI_API_KEY=YOUR_KEY_HERE
```

**Windows (PowerShell)**
```
setx GEMINI_API_KEY "YOUR_KEY_HERE"
# then open a new terminal
```

> Alternatively, you may set GOOGLE_API_KEY.

## Run
```
python app.py
```

## Toggle
- Default: uses Gemini. If it fails or key missing, falls back to local heuristic overview.
- Force local (no AI): append `?ai=0` to the results URL.
