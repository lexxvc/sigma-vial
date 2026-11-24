# SIGMA-Vial · imagen para Hugging Face Spaces (Docker) o cualquier nube con contenedores
FROM python:3.12-slim

# librerías del sistema que necesita el binario de SUMO
RUN apt-get update && apt-get install -y --no-install-recommends \
        libatomic1 libexpat1 tzdata libgl1 libx11-6 libxext6 libxrender1 libxcb1 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
WORKDIR /app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .
USER user

ENV PORT=7860 TZ=America/Mexico_City
EXPOSE 7860
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
