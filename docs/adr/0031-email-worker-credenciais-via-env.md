# ADR-0031 — Worker de e-mail: credenciais via env e manifest sem integração

> Status: **aceito** · Data: 2026-07-20 · Emenda o **ADR-0029 §2** (worker por canal e credenciais SMTP).

## Contexto

O ADR-0029 §2 previu `EmailDigestWorker` com `manifest.integrations=["smtp"]` e a falha de credencial SMTP se transformando em `SenderError` → `dispatch(error)` só no run de e-mail. Na prática, o schema genérico `Integration`/`IntegrationAuth` (`kubo/runtime/integrations.py`) foi desenhado para **um único segredo** (`secret_ref`) e um `base_url` opcional; SMTP exige host, porta, usuário, senha e endereço de remetente. Forçar o SMTP nesse schema exigiria estender o catálogo declarativo com campos específicos de protocolo ou condicionar a resolução a parsing de um DSN único.

## Decisão

1. **`EmailDigestWorker` declara `manifest.integrations=[]`.** As credenciais SMTP não passam por `ctx.integrations`; em vez disso, a `DEST_DISPATCH` factory de `kubo/scheduler/sweep.py` lê as variáveis de ambiente `KUBO_EMAIL_HOST`, `KUBO_EMAIL_PORT`, `KUBO_EMAIL_USER`, `KUBO_EMAIL_PASSWORD` e `KUBO_EMAIL_FROM` e monta um objeto `SmtpConfig` passado ao worker pelo **construtor**.
2. **`SmtpConfig` é um `dataclass` com `password` em `field(repr=False)`.** O segredo nunca aparece em `repr`/`str`/`traceback` (mesmo fechamento do `address` PII do destino).
3. **Env ausente ou incompleto vira `SenderError` dentro do worker,** capturado como `dispatch(error)` com `kind="email_send"`. Assim o run aparece na tela Execuções, não vira apenas um log do scheduler.
4. **Sem defaults de servidor.** `KUBO_EMAIL_HOST` ausente é falha; não se assume `smtp.gmail.com` nem nenhum outro host.
5. **STARTTLS obrigatório para portas não-465; SSL para 465.** Se a porta não for 465, o envio exige `STARTTLS`; sem ele, a senha viajaria em texto plano e a entrega falha.

## Consequências

- **Positivo:** mantém o catálogo `integrations` genérico e declarativo, sem campos de protocolo específicos.
- **Positivo:** a falha de configuração SMTP continua isolada por run de e-mail, preservando a entrega do Telegram no mesmo sweep.
- **Trade-off:** duas máquinas de credencial no mesmo sistema — Telegram por `ctx.integrations`, e-mail por env + construtor. O motivo é documentado aqui e reavaliado se `Integration` ganhar campos estruturados de endpoint.
- **Segurança:** o worker não lê `os.environ`; a senha vive só no `SmtpConfig` e nunca em log/payload/repr.

## Alternativas rejeitadas

- **Estender `Integration`/`IntegrationAuth` com `host`, `port`, `username`.** Rejeitada: scope creep no catálogo declarativo; afetaria todas as integrações para um único consumidor.
- **DSN único `smtp://user:pass@host:port` em `secret_ref`.** Rejeitada: parsing frágil com senhas contendo caracteres especiais (`@`, `:`, `/`) e mistura de config (host/porta) com segredo (senha).  <!-- pragma: allowlist secret -->
- **`ConfigError` na factory quando env falta.** Rejeitada: tornaria a falha invisível na tela Execuções; o padrão é `SenderError` → `dispatch(error)`.
