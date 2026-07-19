#!/usr/bin/env bash
# Teste do guard-bash.sh. Roda: bash .claude/hooks/guard-bash.test.sh
# Pina que comandos perigosos em POSIÇÃO DE COMANDO bloqueiam e que strings que
# apenas CITAM um comando (mensagem de commit, padrão de grep) não bloqueiam.
# Exit 1 se qualquer caso falhar — pronto para virar step de CI se necessário.
set -u
cd "$(dirname "$0")/../.." || exit 1
HOOK=".claude/hooks/guard-bash.sh"
export CLAUDE_PROJECT_DIR="$PWD"
fails=0

run() {
  printf '{"tool_input":{"command":%s}}' \
    "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$1")" \
    | bash "$HOOK" >/dev/null 2>&1
  echo $?
}

expect() {  # expect BLOCK|ALLOW "<cmd>"
  local want="$1" cmd="$2" code got
  code="$(run "$cmd")"
  if [ "$want" = BLOCK ]; then got=$([ "$code" = 2 ] && echo BLOCK || echo ALLOW)
  else got=$([ "$code" = 0 ] && echo ALLOW || echo BLOCK); fi
  if [ "$got" = "$want" ]; then
    printf 'ok    %-5s : %s\n' "$want" "$cmd"
  else
    printf 'FAIL  want=%-5s got=%-5s (exit %s) : %s\n' "$want" "$got" "$code" "$cmd"
    fails=$((fails+1))
  fi
}

# Perigosos em posição de comando -> bloqueiam
expect BLOCK 'git push --force main'
expect BLOCK 'git push origin main --force'
expect BLOCK 'git switch -c wip'
expect BLOCK 'git checkout -B FooBar'
expect BLOCK 'git reset --hard origin/main'
expect BLOCK 'coderabbit review'
expect BLOCK 'cr review .'
expect BLOCK 'deploy && git push -f main'
expect BLOCK 'cat .env'
expect BLOCK 'git checkout -b feat/ok && git checkout -b hack'   # 1 válida não absolve a inválida

# Commit direto em main (fase4-roadmap.md, achado 0018b) — override só pro teste,
# produção lê a branch real via git rev-parse.
GUARD_BASH_TEST_BRANCH=main expect BLOCK 'git commit -m "oops"'
GUARD_BASH_TEST_BRANCH=main expect BLOCK 'git commit --amend'
GUARD_BASH_TEST_BRANCH=feat/ok expect ALLOW 'git commit -m "fine"'

# Strings que citam comandos, ou leitura legítima -> não bloqueiam
expect ALLOW 'git commit -m "guard blocks checkout -b outside taxonomy"'
expect ALLOW 'git commit -m "explain git push --force to main danger"'
expect ALLOW 'gh pr checks 2 | grep -i coderabbit'
expect ALLOW 'echo "run coderabbit review in the PR"'
expect ALLOW 'git switch -c ci/0002-git-flow'
expect ALLOW 'git checkout -b feat/spike origin/main'
expect ALLOW 'git checkout -b feat/ok && git switch -c ci/also-ok'   # todas na taxonomia
expect ALLOW 'git status'
expect ALLOW 'cat .env.example'

# Troca de branch com árvore suja (item 11 CLAUDE.md global; incidente do subagente
# que carregou WIP alheio pra outra branch). Sujeira simulada via GUARD_BASH_TEST_DIRTY
# ("1"=suja, "0"=limpa); produção lê `git status --porcelain` do cwd do comando.
GUARD_BASH_TEST_DIRTY=1 expect BLOCK 'git switch main'
GUARD_BASH_TEST_DIRTY=1 expect BLOCK 'git checkout develop'
GUARD_BASH_TEST_DIRTY=1 expect BLOCK 'git checkout -f main'          # -f é pior: destrói WIP
GUARD_BASH_TEST_DIRTY=1 expect BLOCK 'git switch -'                  # branch anterior também é troca
GUARD_BASH_TEST_DIRTY=1 expect BLOCK 'git stash && git switch main'  # stash não absolve: item 11
# Árvore suja, mas operações legítimas que NÃO trocam branch -> permite
GUARD_BASH_TEST_DIRTY=1 expect ALLOW 'git checkout -- src/file.py'         # restore de pathspec
GUARD_BASH_TEST_DIRTY=1 expect ALLOW 'git checkout main -- src/file.py'    # restore de outra ref
GUARD_BASH_TEST_DIRTY=1 expect ALLOW 'git checkout --theirs src/file.py'   # resolução de conflito
GUARD_BASH_TEST_DIRTY=1 expect ALLOW 'git switch -c feat/new'              # criar carrega WIP: ok
# Árvore limpa -> troca de branch é livre (dono voltando pra main após merge)
GUARD_BASH_TEST_DIRTY=0 expect ALLOW 'git switch main'
GUARD_BASH_TEST_DIRTY=0 expect ALLOW 'git checkout develop'

# Guard de commit-em-main lê a branch do CWD do comando, não de CLAUDE_PROJECT_DIR
# (bug de worktree: uma sessão em ../wt commitava avaliando a branch do clone principal).
# Sem override — exercita a leitura real. CLAUDE_PROJECT_DIR aponta pro repo Kubo
# (branch feat/*), o cwd injetado aponta pra um repo em main: só o fix bloqueia.
tmp="$(mktemp -d)"
git -C "$tmp" init -q -b main
git -C "$tmp" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
cwd_input="$(printf '{"cwd":%s,"tool_input":{"command":"git commit -m x"}}' \
  "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$tmp")")"
cwd_code="$(printf '%s' "$cwd_input" | bash "$HOOK" >/dev/null 2>&1; echo $?)"
if [ "$cwd_code" = 2 ]; then printf 'ok    %-5s : %s\n' BLOCK 'commit em main lido do cwd do worktree'
else printf 'FAIL  want=BLOCK got=ALLOW (exit %s) : commit-em-main via cwd\n' "$cwd_code"; fails=$((fails+1)); fi
rm -rf "$tmp"

echo "---"
if [ "$fails" -eq 0 ]; then echo "todos os casos passaram"; else echo "$fails caso(s) falharam"; exit 1; fi
