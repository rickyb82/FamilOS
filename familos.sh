#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
case "${1:-help}" in
  domain)
    if [ "${2:-}" != apply ]; then echo "Uso: ./familos.sh domain apply" >&2; exit 2; fi
    exec ./familai.sh domain-apply
    ;;
  productivity)
    action="${2:-up}"
    if [ "$action" = status ]; then exec ./familai.sh status; fi
    case "$action" in up|down|status|upgrade) ;; *) echo 'Uso: ./familos.sh productivity up|down|status|upgrade' >&2; exit 2;; esac
    for app in wordcraft gridcraft pdfcraft deckcraft ideacraft mailcraft; do
      ./familai.sh "$app" "$action"
    done
    ;;
  dev|prod|update|status|stop|backup|restore|logs)
    exec ./familai.sh "$@"
    ;;
  *)
    echo 'FamilOS — sistema operativo web della famiglia'
    echo '  ./familos.sh dev|prod|update|status|stop'
    echo '  ./familos.sh domain apply         applica dominio e certificati salvati nel pannello'
    echo '  ./familos.sh productivity up       attiva i cinque editor e MailCraft'
    echo '  ./familos.sh productivity down     ferma la suite'
    echo '  ./familos.sh productivity upgrade  aggiorna la suite'
    echo 'Le prime build delle app Rust possono richiedere molto tempo.'
    ;;
esac
