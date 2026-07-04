#!/usr/bin/env bash
# stop-tests.sh — Stop: o turno não termina com suite unit quebrada.
# Exceção deliberada: durante RED do TDD, o Claude deve declarar o estado
# (teste novo falhando pelo motivo certo) — este hook só valida testes pré-existentes.
set -uo pipefail

# Evita loop infinito de Stop hook
INPUT="$(cat)"
ACTIVE="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active",False))' 2>/dev/null || echo False)"
[ "$ACTIVE" = "True" ] && exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -d tests ] || exit 0

# Runner: neste projeto o pytest vive no venv do uv (não é global). Preferir `uv run`.
if command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  RUN=(uv run pytest)
elif command -v pytest >/dev/null 2>&1; then
  RUN=(pytest)
else
  exit 0   # sem runner disponível — não bloqueia
fi

# UMA execução, capturando a saída. Integração roda no CI/sob demanda, não aqui.
OUT="$("${RUN[@]}" -q -m 'not integration' 2>&1)"
CODE=$?

# 0 = verde; 5 = nenhum teste coletado (repo pode não ter testes ainda) — não bloqueia.
if [ "$CODE" -eq 0 ] || [ "$CODE" -eq 5 ]; then
  exit 0
fi

printf "Suite unit falhando ao encerrar o turno:\n%s\n\nSe isto é o RED intencional do TDD, siga para o GREEN antes de encerrar — ou declare explicitamente o estado ao dono.\n" "$OUT" >&2
exit 2
