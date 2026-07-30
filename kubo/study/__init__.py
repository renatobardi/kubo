"""Módulo Estudos (ADR-0043): ingestão de Material, Tema, Plano e Lição.

Este pacote guarda a lógica PURA do domínio de estudos — parsing de material
(epub/PDF) sem I/O de banco nem de rede. A persistência vive em `kubo/store/study.py`
e a UI em `kubo/api/routes/study.py`.
"""

from __future__ import annotations
