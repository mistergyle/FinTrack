# Home-Assistant-Add-on. BUILD_FROM wird vom Supervisor passend zur Architektur gesetzt.
ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 LANG=C.UTF-8

RUN apk add --no-cache python3 py3-pip

WORKDIR /opt/fintrack
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt
ENV PATH="/opt/venv/bin:${PATH}"

COPY app ./app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
