#!/usr/bin/env bash
# guard-bash.sh — PreToolUse[Bash]: bloqueia comandos destrutivos/perigosos.
# Exit 2 = bloqueia a ferramenta e devolve stderr ao Claude. Exit 0 = permite.
set -euo pipefail

INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"
[ -z "$CMD" ] && exit 0

# Diretório onde o comando de fato roda. Numa sessão dentro de um worktree o cwd é
# o worktree, não CLAUDE_PROJECT_DIR (fixo na raiz do clone principal): guards que
# consultam git precisam do cwd, senão avaliam a árvore/branch errada (bug de squad).
CWD="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null || true)"
[ -z "$CWD" ] && CWD="${CLAUDE_PROJECT_DIR:-.}"

deny() { echo "BLOQUEADO pelo harness: $1" >&2; exit 2; }

# Prefixo de POSIÇÃO DE COMANDO: casa só no início de um comando — começo da linha
# (grep processa linha a linha, então cobre também comandos separados por \n) ou
# logo após um separador de shell ( ; & | ). Impede que uma STRING que apenas CITA
# um comando (mensagem de commit, padrão de grep) seja confundida com executá-lo.
# ponytail: heurística, não parser de shell — um separador DENTRO de aspas ainda
# pode enganar (ex.: -m "x; git push -f"). Aceitável: o CI é o gate final.
CP='(^|[;&|][[:space:]]*)'

# Destrutivos de filesystem
echo "$CMD" | grep -Eq 'rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)[[:space:]]+(/|~|\$HOME|\.\.)([[:space:]]|$|/)' \
  && deny "rm -rf em caminho raiz/home/parent"
echo "$CMD" | grep -Eq '(mkfs|dd[[:space:]]+if=.*of=/dev/|>[[:space:]]*/dev/sd)' \
  && deny "operação de disco destrutiva"

# Git perigoso (só quando git é o comando executado, não citado em string)
echo "$CMD" | grep -Eq "$CP"'git[[:space:]]+push[[:space:]]+.*(--force|-f)([[:space:]]|$).*\b(main|master)\b' \
  && deny "push --force em main/master"
echo "$CMD" | grep -Eq "$CP"'git[[:space:]]+push[[:space:]]+.*\b(main|master)\b.*(--force|-f)([[:space:]]|$)' \
  && deny "push --force em main/master"
echo "$CMD" | grep -Eq "$CP"'git[[:space:]]+(reset[[:space:]]+--hard[[:space:]]+origin|clean[[:space:]]+-[a-z]*x)' \
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

# CodeRabbit em tempo de commit — review é no PR (CLAUDE.md). Só bloqueia EXECUTAR
# o CLI (posição de comando); ler o status via `... | grep coderabbit` é legítimo.
echo "$CMD" | grep -Eq "$CP"'(coderabbit\b|cr[[:space:]]+review\b)' \
  && deny "CodeRabbit local/commit-time desabilitado por convenção — o review ocorre no PR"

# Instalação fora do gerenciador do projeto
echo "$CMD" | grep -Eq '\bpip[3]?[[:space:]]+install\b' \
  && deny "pip install direto — use 'uv add' (lockfile é invariante)"

# Branch fora da taxonomia (ADR-0004). Guard de conveniência — o gate real é o CI.
# ponytail: cobre só `git switch -c` e `git checkout -b` (formas de criação comuns);
# `git branch <nome>` não é parseado aqui — o CI barra no PR. Exige `git` em posição
# de comando, então uma mensagem que cita `checkout -b` não dispara.
NEWBRANCH="$(echo "$CMD" | grep -oE "$CP"'git[[:space:]]+(switch[[:space:]]+-[cC]|checkout[[:space:]]+-[bB])[[:space:]]+[^[:space:]]+' | grep -oE '[^[:space:]]+$' || true)"
if [ -n "$NEWBRANCH" ]; then
  # Valida TODAS as branches extraídas: com dois criadores na mesma linha
  # (`... && git checkout -b hack`), um nome válido não pode absolver os demais.
  # -vqE casa se ALGUMA linha ficar FORA da taxonomia (kebab-case).
  echo "$NEWBRANCH" | grep -vqE '^(feat|fix|chore|docs|test|refactor|ci)/[a-z0-9-]+$' \
    && deny "branch fora da taxonomia (feat|fix|chore|docs|test|refactor|ci)/slug kebab-case — ver ADR-0004: $NEWBRANCH"
fi

# Troca de branch com árvore suja (item 11 CLAUDE.md): carregar trabalho não-commitado
# pra outra branch foi o mecanismo do incidente (subagente commitou WIP do dono). Barra
# só a TROCA — criação (-b/-c) leva WIP legitimamente; restore de pathspec (--) e
# resolução de conflito (--ours/--theirs/--merge/-p) têm a árvore suja por natureza.
# `stash && switch` não escapa: o porcelain é lido antes do stash rodar (item 11 proíbe
# o contorno via stash). Árvore avaliada no CWD do comando, não em CLAUDE_PROJECT_DIR.
if echo "$CMD" | grep -Eq "$CP"'git[[:space:]]+(checkout|switch)([[:space:]]|$)' \
   && ! echo "$CMD" | grep -Eq '([[:space:]]|^)(-b|-B|-c|-C|--|--ours|--theirs|--merge|-p|--patch)([[:space:]]|$)'; then
  if [ -n "${GUARD_BASH_TEST_DIRTY+set}" ]; then DIRTY="$GUARD_BASH_TEST_DIRTY"
  else DIRTY="$([ -n "$(git -C "$CWD" status --porcelain 2>/dev/null)" ] && echo 1 || true)"; fi
  [ "$DIRTY" = 1 ] && deny "troca de branch com árvore suja — use 'git worktree add' para outra frente ou peça ao dono (item 11); não contorne via stash"
fi

# Commit direto em main (achado 0018b, fase4-roadmap.md): CLAUDE.md promete "duas camadas de
# enforce", esta cobria só criação de branch. GUARD_BASH_TEST_BRANCH é override só de teste;
# produção lê a branch real do CWD do comando (não CLAUDE_PROJECT_DIR — vide bug de worktree).
echo "$CMD" | grep -Eq "$CP"'git[[:space:]]+commit\b' && {
  CURBRANCH="${GUARD_BASH_TEST_BRANCH:-$(git -C "$CWD" rev-parse --abbrev-ref HEAD 2>/dev/null || true)}"
  case "$CURBRANCH" in
    main|master) deny "commit direto em $CURBRANCH — crie uma branch (feat|fix|chore|docs|test|refactor|ci)/slug primeiro" ;;
  esac
}

exit 0
