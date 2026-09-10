FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY app.py .
# should exist a config .streamlit/secrets.toml with
# GEMINI_API_KEY = "XXXXX"
COPY .streamlit/ .streamlit/

# Streamlit default port
EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]