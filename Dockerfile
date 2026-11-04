# SIGMA-Vial · imagen para Hugging Face Spaces (Docker) o cualquier nube con contenedores
FROM python:3.12-slim

# librerías del sistema que necesita el binario de SUMO
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libx11-6 libxext6 libxrender1 libxcb1 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
WORKDIR /app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .
USER user

ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "python app/servidor.py --puerto ${PORT} --no-abrir"]
