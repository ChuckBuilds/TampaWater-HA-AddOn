# Tampa Water add-on image.
# No browser: the City of Tampa portal is a plain Spring form login with no
# captcha, so requests + BeautifulSoup is enough. That keeps this image small
# (tens of MB rather than the ~1GB a Chromium-based scraper needs).
ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./
COPY run.sh ./
RUN chmod +x run.sh

EXPOSE 8099
CMD [ "./run.sh" ]
