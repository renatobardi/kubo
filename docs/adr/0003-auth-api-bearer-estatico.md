# ADR-0003 — Auth da API — bearer token estático + security list OCI

> Status: **aceito** · Data: 2026-07-04

## Contexto

A API (FastAPI) roda na VPC da OCI do dono. Mesmo sendo uso pessoal/solo, a superfície de rede exige auth (CLAUDE.md §Segurança: "API com auth mesmo sendo pessoal"). Fadiga de complexidade é premissa do projeto.

## Decisão

**Bearer token estático** (segredo por referência — env/secret manager, nunca em código/YAML/commit) como auth da API, combinado com **security list/NSG da OCI** liberando apenas as portas explicitamente necessárias. SurrealDB nunca exposto fora da VCN.

## Consequências

Simples de operar por um mantenedor solo; rotação manual do token; sem provider de identidade para manter. Suficiente para superfície pessoal atrás da security list.

**Pré-condição:** o token só trafega sobre TLS/HTTPS em qualquer porta exposta fora da VCN — bearer estático em texto claro numa porta exposta é auth de teatro (qualquer observador da rede captura o token). Tráfego interno à VCN pode ser plaintext a critério do dono. Nota de implementação: comparar o token com `secrets.compare_digest` (timing-safe) quando a rota de auth for escrita.

## Alternativas rejeitadas

(a) OAuth/OIDC ou provider de identidade — rejeitada: overkill para mantenedor solo, contraria a premissa de fadiga de complexidade.

(b) Sem auth (confiar só na rede) — rejeitada: viola a postura de segurança (defense-in-depth; auth mesmo atrás da security list).
