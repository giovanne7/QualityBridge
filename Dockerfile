FROM python:3.12-slim
WORKDIR /opt/qualitybridge
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[dev]'
WORKDIR /project
CMD ["qualitybridge", "--help"]
