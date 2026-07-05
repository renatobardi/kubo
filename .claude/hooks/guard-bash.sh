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

# Segredos e exfiltração. Bypass corrigido (CodeRabbit PR#1): a exclusão de
# .env.example era feita contra o comando inteiro, então `cat .env .env.example`
# passava. Agora a decisão é por TOKEN — bloqueia .env que não seja .env.example.
if echo "$CMD" | grep -Eq '(cat|less|head|tail|grep)[[:space:]]'; then
  set -f  # sem globbing ao iterar tokens
  for tok in $CMD; do
    case "$tok" in
      *.env.example) : ;;
      *.env|*.env.*) deny "leitura direta de .env (use variáveis de ambiente)" ;;
    esac
  done
  set +f
fi
echo "$CMD" | grep -Eq 'curl[[:space:]]+.*(-d|--data|--upload-file)[[:space:]]+.*\.(env|pem|key)' \
  && deny "upload de arquivo de segredo"

# CodeRabbit em tempo de commit — review é no PR (CLAUDE.md)
echo "$CMD" | grep -Eq '\bcoderabbit\b|\bcr[[:space:]]+review\b' \
  && deny "CodeRabbit local/commit-time desabilitado por convenção — o review ocorre no PR"

# Instalação fora do gerenciador do projeto
echo "$CMD" | grep -Eq '\bpip[3]?[[:space:]]+install\b' \
  && deny "pip install direto — use 'uv add' (lockfile é invariante)"

# Branch fora da taxonomia (ADR-0004). Guard de conveniência — o gate real é o CI.
# ponytail: cobre só `switch -c` e `checkout -b` (formas de criação comuns);
# `git branch <nome>` não é parseado aqui — o CI barra no PR.
NEWBRANCH="$(echo "$CMD" | grep -oE '(switch[[:space:]]+-[cC]|checkout[[:space:]]+-[bB])[[:space:]]+[^[:space:]]+' | grep -oE '[^[:space:]]+$' || true)"
if [ -n "$NEWBRANCH" ]; then
  echo "$NEWBRANCH" | grep -Eq '^(feat|fix|chore|docs|test|refactor|ci)/[a-z0-9._-]+$' \
    || deny "branch '$NEWBRANCH' fora da taxonomia (feat|fix|chore|docs|test|refactor|ci)/slug — ver ADR-0004"
fi

exit 0
