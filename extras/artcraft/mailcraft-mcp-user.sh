#!/bin/sh
# Una sessione MCP per un utente: il backend FamilAI manda come prima riga il token utente (coniato dal server con il token
# di servizio), poi il flusso MCP vero e proprio. `read` consuma solo la prima riga; il resto va a mailcraft-cli.
IFS= read -r tok || exit 1
case "$tok" in ""|*[!0-9a-fA-F]*) echo "token non valido" >&2; exit 1;; esac
exec mailcraft-cli mcp --server http://127.0.0.1:7878 --token "$tok"
