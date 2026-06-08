# fhir-repair container image.
#
# Builds a slim image with the package and its anthropic extra installed.
# Mount your repair-config.yaml at /etc/fhir-repair/config.yaml; mount
# input and output volumes for the resources you want to fix.
#
# Example usage:
#   docker build -t fhir-repair -f docker/Dockerfile .
#   docker run --rm \
#       -e LLM_API_KEY=... \
#       -v $(pwd)/in:/in \
#       -v $(pwd)/out:/out \
#       fhir-repair fix /in/patient.json --out /out/patient.fixed.json

FROM python:3.11-slim AS builder

WORKDIR /build

# System packages needed for the wheel install. Kept small so the final
# image stays slim. `build-essential` covers the cffi / pydantic-core
# native bits.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY fhir_repair ./fhir_repair

RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels ".[anthropic,service]"


FROM python:3.11-slim

# Drop privileges. The image runs the CLI as `repair`; mount points must
# be writable by uid 1000 if the CLI is going to write output there.
RUN useradd --create-home --uid 1000 repair

COPY --from=builder /wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links=/tmp/wheels fhir-repair[anthropic,service] \
    && rm -rf /tmp/wheels

USER repair
WORKDIR /home/repair

# Default entrypoint is the CLI. To run the HTTP service instead, override
# the entrypoint (see the `api` service in docker-compose.yml):
#   uvicorn fhir_repair.service:app --host 0.0.0.0 --port 8000
ENTRYPOINT ["fhir-repair"]
CMD ["--help"]
