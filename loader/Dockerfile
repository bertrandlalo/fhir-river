FROM python:3.7-slim

RUN apt-get update \
    && apt-get -y install build-essential

ARG LOADER_HOME=/app
ENV PYTHONPATH=${LOADER_HOME}

WORKDIR ${LOADER_HOME}

COPY loader/requirements.txt ${LOADER_HOME}/requirements.txt

RUN pip install -r ${LOADER_HOME}/requirements.txt

COPY loader ${LOADER_HOME}/loader
COPY analyzer ${LOADER_HOME}/analyzer

COPY loader/entrypoint.sh /entrypoint.sh

CMD ["sh", "/entrypoint.sh"]
