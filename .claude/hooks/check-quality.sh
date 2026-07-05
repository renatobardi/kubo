#!/usr/bin/env bash
# check-quality.sh — PostToolUse[Edit|Write|MultiEdit]: feedback imediato de qualidade.
# Exit 2 devolve os problemas ao Claude para correção no mesmo turno.
set -uo pipefail

INPUT="$(cat)"
FILE="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("tool_input",{}); print(d.get("file_path") or d.get("path") or "")' 2>/dev/null || true)"

# Só arquivos Python do pacote/testes
case "$FILE" in
  *.py) : ;;
  *) exit 0 ;;
esac
[ -f "$FILE" ] || exit 0

PROJ="$(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || echo "${CLAUDE_PROJECT_DIR:-.}")"
# Canoniza o caminho (resolve `..` e symlinks) antes de comparar — senão um path
# relativo/`../` escaparia do teste de contenção abaixo.
FILE="$(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$FILE" 2>/dev/null || echo "$FILE")"

# Só arquivos DENTRO do projeto: gate de qualidade não se aplica a scratchpad/tmp
# ou qualquer caminho fora do repo (probes descartáveis não são código do projeto).
case "$FILE" in
  "$PROJ"/*) : ;;
  *) exit 0 ;;
esac

# Runner: neste projeto ruff/pyright vivem no venv do uv, não no PATH global.
if command -v uv >/dev/null 2>&1 && [ -f "$PROJ/pyproject.toml" ]; then
  USE_UV=1
elif command -v ruff >/dev/null 2>&1; then
  USE_UV=
else
  exit 0   # sem ferramentas disponíveis — não bloqueia
fi
run() { if [ -n "${USE_UV:-}" ]; then uv run --project "$PROJ" "$@"; else "$@"; fi; }

OUT=""; FAIL=0

R="$(run ruff check "$FILE" 2>&1)" || { FAIL=1; OUT+="── ruff check ──\n$R\n"; }
run ruff format --check "$FILE" >/dev/null 2>&1 || { run ruff format "$FILE" >/dev/null 2>&1; OUT+="── ruff format: auto-formatado ──\n"; }

P="$(run pyright "$FILE" 2>&1 | tail -20)"
echo "$P" | grep -q "0 errors" || { FAIL=1; OUT+="── pyright ──\n$P\n"; }

if [ "$FAIL" -eq 1 ]; then
  printf "Qualidade falhou em %s — corrija antes de prosseguir:\n%b" "$FILE" "$OUT" >&2
  exit 2
fi
exit 0
