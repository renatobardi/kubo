"""Teste de sanidade: o pacote kubo importa e expõe versão."""

import kubo


def test_kubo_importa_e_tem_versao():
    assert kubo.__version__ == "0.1.0"
