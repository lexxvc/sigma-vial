@echo off
REM SIGMA-Vial en vivo (Windows). Requiere: pip install -r requirements.txt
REM Para usar el Crew en vivo:  set GEMINI_API_KEY=tu_clave
python app\servidor.py %*
pause
