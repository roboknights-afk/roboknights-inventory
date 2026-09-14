# Builds the Discord bot for Hugging Face Spaces (Docker SDK). Not used
# by the Oracle/Pi path, which just runs `python bot.py` directly - this
# only matters wherever HF Spaces is the chosen host (see DEPLOY.md).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# HF Spaces routes external traffic (including the uptime-ping pattern
# that keeps a Space from sleeping after 48h idle) to this port by
# default - see _start_keepalive_server() in bot.py.
EXPOSE 7860

CMD ["python", "bot.py"]
