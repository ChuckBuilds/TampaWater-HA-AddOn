# Tampa Water add-on image.
#
# The base image is pinned here on purpose, and this Dockerfile deliberately
# declares no build argument for it. Supervisor substitutes its own base image
# into such an argument when one exists -- ghcr.io/home-assistant/base, which is
# Alpine and has no pip -- and it did so even with a build.yaml present, failing
# the build with "pip: not found". With no argument to substitute, the build-arg
# Supervisor passes is simply unused and this FROM always wins.
#
# Debian (glibc) rather than Alpine on purpose: every dependency here has a
# manylinux wheel, so nothing has to be compiled at install time.
FROM python:3.14-slim-bookworm

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
