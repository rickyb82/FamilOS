# Public runtime/tools only. The user's APK-derived .so is NEVER copied here.
FROM python:3.12-slim-bookworm@sha256:82836454a795d9d75549a9e157fd08dbac6c383292cf89e99f4248a97ce6b2ff AS build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY bionic_shim.c .
RUN mkdir /compat && gcc -shared -fPIC -O2 bionic_shim.c \
    -Wl,-soname,libx.so -Wl,--no-as-needed -lm -ldl -o /compat/libx.so

FROM python:3.12-slim-bookworm@sha256:82836454a795d9d75549a9e157fd08dbac6c383292cf89e99f4248a97ce6b2ff
COPY --from=build /compat/libx.so /compat/libx.so
WORKDIR /app
COPY worker.py sandbox.py service.py ./
ENV PYTHONDONTWRITEBYTECODE=1 \
    LD_LIBRARY_PATH=/compat \
    HOME_PULSE_NATIVE_LIBRARY=/native/libappecore-compat.so \
    HOME_PULSE_NATIVE_SHIM=/compat/libx.so
USER 10001:10001
EXPOSE 8081
CMD ["python", "-B", "/app/service.py"]
