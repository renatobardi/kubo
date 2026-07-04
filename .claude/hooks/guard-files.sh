#!/usr/bin/env bash
# guard-files.sh — PreToolUse[Edit|Write|MultiEdit]: protege arquivos sensíveis.
set -euo pipefail

INPUT="$(cat)"
FILE="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("tool_input",{}); print(d.get("file_path") or d.get("path") or "")' 2>/dev/null || true)"
[ -z "$FILE" ] && exit 0

deny() { echo "BLOQUEADO pelo harness: $1 ($FILE)" >&2; exit 2; }

case "$FILE" in
  *.env|*.env.*)      [[ "$FILE" == *.env.example ]] || deny "edição de arquivo de segredos" ;;
  *.pem|*.key|*id_rsa*) deny "edição de chave/certificado" ;;
  */.git/*)           deny "edição direta de internals do git" ;;
esac

# Escrita fora do repositório
if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
  case "$FILE" in
    "$CLAUDE_PROJECT_DIR"/*|/tmp/*) : ;;
    /*) deny "escrita fora do diretório do projeto" ;;
  esac
fi

# Hooks do harness só mudam por PR consciente — avisa mas não bloqueia
case "$FILE" in
  */.claude/hooks/*|*/.claude/settings.json)
    echo "AVISO: editando o próprio harness — mudança deve ir em PR dedicado (CLAUDE.md)." >&2 ;;
esac

exit 0
