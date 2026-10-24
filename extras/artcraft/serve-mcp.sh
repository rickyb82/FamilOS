#!/bin/sh
# MODE=mcp  (default): espone il server MCP stdio dell'app ($APP-cli mcp ...) sulla porta TCP $MCP_PORT, una sessione per connessione.
# MODE=http: per le app senza MCP (IdeaCraft): piccolo servizio HTTP che esegue la CLI (cli-server.py) su $HTTP_PORT.
set -eu
if [ "${MODE:-mcp}" = http ]; then
  exec python3 /usr/local/bin/cli-server.py
fi
# Niente spazio finale se MCP_ARGS e' vuoto: socat lo passerebbe come argomento "" e la CLI proverebbe ad aprire un file senza nome.
cmd="${APP}-cli mcp"
[ -n "${MCP_ARGS:-}" ] && cmd="$cmd ${MCP_ARGS}"
echo ">> $cmd in ascolto su :${MCP_PORT}"
exec socat -d "TCP-LISTEN:${MCP_PORT},fork,reuseaddr" "EXEC:$cmd"
