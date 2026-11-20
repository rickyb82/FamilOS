# MailCraft server for a framework: multi-user `mailcraft-cli serve` plus the web app.
#
#   docker build -t mailcraft .
#   docker run -d -p 7878:7878 -v mailcraft-data:/data -e MAILCRAFT_TOKEN=change-me mailcraft
#
# Data (one directory per user: config.json, secrets.json 0600, cache/) lives in /data/users.

FROM rust:1.97-bookworm AS build
WORKDIR /src
RUN rustup target add wasm32-unknown-unknown \
 && curl -sSL https://github.com/trunk-rs/trunk/releases/download/v0.21.14/trunk-x86_64-unknown-linux-gnu.tar.gz | tar -xz -C /usr/local/bin trunk
COPY . .
# OAuth clients compiled in (see docs/oauth.md): --build-arg MAILCRAFT_BUILTIN_GOOGLE_CLIENT_ID=… etc.
ARG MAILCRAFT_BUILTIN_GOOGLE_CLIENT_ID=""
ARG MAILCRAFT_BUILTIN_GOOGLE_CLIENT_SECRET=""
ARG MAILCRAFT_BUILTIN_MICROSOFT_CLIENT_ID=""
ARG MAILCRAFT_BUILTIN_MICROSOFT_CLIENT_SECRET=""
RUN cargo build --release -p mailcraft-cli \
 && cd apps/mailcraft-web && trunk build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* \
 && useradd --system --create-home --home-dir /data mailcraft
COPY --from=build /src/target/release/mailcraft-cli /usr/local/bin/mailcraft-cli
COPY --from=build /src/dist/web /app/web
USER mailcraft
VOLUME ["/data"]
EXPOSE 7878
ENV MAILCRAFT_TOKEN=""
# The framework talks to the API with the service token; users get per-user tokens.
CMD ["sh", "-c", "exec mailcraft-cli serve --bind 0.0.0.0:7878 --static /app/web --users-dir /data/users --token \"$MAILCRAFT_TOKEN\" ${MAILCRAFT_LANG:+--lang $MAILCRAFT_LANG}"]
