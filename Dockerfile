FROM python:3.11-slim

# Imposta la directory di lavoro
WORKDIR /app

# Copia i requisiti
COPY requirements.txt .

# Installa le dipendenze
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice sorgente
COPY main.py .

# Comando per avviare il bot
CMD ["python", "main.py"]
