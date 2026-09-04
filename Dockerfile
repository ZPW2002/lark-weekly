FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg tzdata \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @larksuite/cli@latest @earendil-works/pi-coding-agent \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY agent_home ./agent_home

RUN mkdir -p /data/state /root/.lark-cli /root/.local/share/lark-cli /root/.pi/agent

ENV STATE_DIR=/data/state \
    AGENT_HOME=/srv/app/agent_home

ENTRYPOINT ["python", "-m", "app.main"]
