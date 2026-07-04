#!/usr/bin/env bash
# guard-bash.sh — PreToolUse[Bash]: bloqueia comandos destrutivos/perigosos.
# Exit 2 = bloqueia a ferramenta e devolve stderr ao Claude. Exit 0 = permite.
set -euo pipefail

INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"
[ -z "$CMD" ] && exit 0

deny() { echo "BLOQUEADO pelo harness: $1" >&2; exit 2; }

# Destrutivos de filesystem
echo "$CMD" | grep -Eq 'rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)[[:space:]]+(/|~|\$HOME|\.\.)([[:space:]]|$|/)' \
  && deny "rm -rf em caminho raiz/home/parent"
echo "$CMD" | grep -Eq '(mkfs|dd[[:space:]]+if=.*of=/dev/|>[[:space:]]*/dev/sd)' \
  && deny "operação de disco destrutiva"

# Git perigoso
echo "$CMD" | grep -Eq 'git[[:space:]]+push[[:space:]]+.*(--force|-f)([[:space:]]|$).*\b(main|master)\b' \
  && deny "push --force em main/master"
echo "$CMD" | grep -Eq 'git[[:space:]]+push[[:space:]]+.*\b(main|master)\b.*(--force|-f)([[:space:]]|$)' \
  && deny "push --force em main/master"
echo "$CMD" | grep -Eq 'git[[:space:]]+(reset[[:space:]]+--hard[[:space:]]+origin|clean[[:space:]]+-[a-z]*x)' \
  && deny "git reset --hard origin / clean -x (perda de trabalho não commitado)"

# Segredos e exfiltração
echo "$CMD" | grep -Eq '(cat|less|head|tail|grep)[[:space:]]+[^|;]*\.env([[:space:]]|$|\.)' \
  && grep -vq '\.env\.example' <<<"$CMD" \
  && deny "leitura direta de .env (use variáveis de ambiente)"
echo "$CMD" | grep -Eq 'curl[[:space:]]+.*(-d|--data|--upload-file)[[:space:]]+.*\.(env|pem|key)' \
  && deny "upload de arquivo de segredo"

# CodeRabbit em tempo de commit — review é no PR (CLAUDE.md)
echo "$CMD" | grep -Eq '\bcoderabbit\b|\bcr[[:space:]]+review\b' \
  && deny "CodeRabbit local/commit-time desabilitado por convenção — o review ocorre no PR"

# Instalação fora do gerenciador do projeto
echo "$CMD" | grep -Eq '\bpip[3]?[[:space:]]+install\b' \
  && deny "pip install direto — use 'uv add' (lockfile é invariante)"

exit 0
