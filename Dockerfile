FROM python:3.11-slim
WORKDIR /app
COPY requirements.lock requirements-neural.txt ./
RUN pip install --no-cache-dir -r requirements.lock
ARG INSTALL_NEURAL=true
RUN if [ "$INSTALL_NEURAL" = "true" ]; then pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements-neural.txt; fi
COPY . .
RUN useradd --create-home agent && mkdir -p /app/runtime && chown -R agent:agent /app
USER agent
EXPOSE 8000
CMD ["python","-m","uvicorn","data_agent.api:app","--host","0.0.0.0","--port","8000"]
