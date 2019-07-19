FROM python:3.7 as base

FROM base as builder
RUN mkdir /install
WORKDIR /install
COPY requirements.txt /requirements.txt
RUN pip install --install-option="--prefix=/install" -r /requirements.txt

FROM base

ARG BUILD_DATE
ARG COMMIT_SHA1
ARG VERSION

LABEL \
    maintainer="Rahul Powar <rahul@redsift.io>" \
    org.label-schema.build-date=${BUILD_DATE}} \
    org.label-schema.name="s3-writer" \
    org.label-schema.description="AWS S3 Writer" \
    org.label-schema.vcs-ref=${COMMIT_SHA1}} \
    org.label-schema.vcs-url="https://github.com/redsift/s3-writer" \
    org.label-schema.vendor="Redsift Limited" \
    org.label-schema.version=${VERSION}} \
    org.label-schema.schema-version="1.0"

COPY --from=builder /install /usr/local
COPY src /app
WORKDIR /app
ENTRYPOINT [ "/app/app.py" ]