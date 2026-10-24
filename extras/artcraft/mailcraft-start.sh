#!/bin/sh
# Avvia il server MailCraft multi-utente (API + web app su :7878) e il ponte MCP per utente (socat su :$MCP_PORT).
set -eu
[ -n "${MAILCRAFT_TOKEN:-}" ] || { echo "!! MAILCRAFT_TOKEN vuoto: ./familai.sh mailcraft up lo genera nel .env"; exit 1; }
echo ">> mailcraft-cli mcp per utente in ascolto su :${MCP_PORT:-7889}"
socat -d "TCP-LISTEN:${MCP_PORT:-7889},fork,reuseaddr" "EXEC:/usr/local/bin/mailcraft-mcp-user" &
echo ">> mailcraft-cli serve su :7878 (web app + API, utenti in /mail/users)"
exec mailcraft-cli serve --bind 0.0.0.0:7878 --static /app/web --users-dir /mail/users --token "$MAILCRAFT_TOKEN" --lang "${MAILCRAFT_LANG:-it}"
