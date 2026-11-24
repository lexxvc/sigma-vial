@echo off
REM SIGMA-Vial (Windows). Requiere haber corrido: pip install -r requirements.txt
REM La clave de Gemini se lee de .env (copia .env.example a .env).
start "" http://localhost:8000
python -m uvicorn api.main:app --port 8000
pause
