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

echo "---"
if [ "$fails" -eq 0 ]; then echo "todos os casos passaram"; else echo "$fails caso(s) falharam"; exit 1; fi
