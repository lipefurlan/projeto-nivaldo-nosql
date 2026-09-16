"""Fixtures dos testes.

A mesma suíte roda de dois jeitos:

- contra um CouchDB DE VERDADE, quando a variável TEST_COUCHDB_URL está
  definida. É assim que ela roda no GitHub Actions, com o Apache CouchDB 3
  num container, e é assim que se testa contra qualquer outro CouchDB;
- contra o dublê em memória (tests/couchdb_falso.py), quando não está. É o
  que permite rodar `pytest` numa máquina sem Docker.

Os testes que dependem da validate_doc_update — JavaScript executado pelo
CouchDB — são marcados `couchdb_real` e pulados com o dublê.

Cada rodada cria um banco com nome aleatório e o apaga no fim. Os testes
nunca encostam no banco de desenvolvimento nem no de produção.
"""

import os
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

from apoio import produto_de_teste
from app import cadastrar_cliente, criar_app, preparar_banco
from banco import CouchDB
from couchdb_falso import CouchDBFalso

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

URL_REAL = os.getenv("TEST_COUCHDB_URL")
URL_FALSA = "http://couchdb-falso"


def pytest_report_header(config):
    if URL_REAL:
        return "banco dos testes: CouchDB de verdade (TEST_COUCHDB_URL)"
    return "banco dos testes: dublê em memória — defina TEST_COUCHDB_URL para usar um CouchDB de verdade"


def pytest_collection_modifyitems(config, items):
    if URL_REAL:
        return
    pular = pytest.mark.skip(reason="precisa de CouchDB de verdade: defina TEST_COUCHDB_URL")
    for item in items:
        if "couchdb_real" in item.keywords:
            item.add_marker(pular)


@pytest.fixture(scope="session")
def aplicacao():
    sessao = requests.Session()
    if URL_REAL:
        url = URL_REAL
    else:
        url = URL_FALSA
        sessao.mount(URL_FALSA, CouchDBFalso())

    app = criar_app(
        {
            "TESTING": True,
            "SECRET_KEY": "chave-de-teste",
            "COUCHDB_URL": url,
            "COUCHDB_DATABASE": f"torra_terra_teste_{uuid.uuid4().hex[:8]}",
            "COUCHDB_IAM_APIKEY": None,
            "COUCHDB_SESSAO": sessao,
        }
    )

    with app.app_context():
        # A estrutura sai do mesmo código do `flask init-db`: se a
        # validate_doc_update ou um índice quebrar, os testes quebram junto.
        preparar_banco()
        yield app
        app.extensions["couchdb"].apagar_banco()


@pytest.fixture
def banco(aplicacao) -> CouchDB:
    """O cliente do banco da loja. Cada teste começa com o banco vazio."""
    cliente_http = aplicacao.extensions["couchdb"]
    restos = [
        doc
        for doc in cliente_http.listar_por_prefixo("", com_documentos=False)
        if not doc["_id"].startswith("_design/")
    ]
    if restos:
        cliente_http.gravar_lote([{**doc, "_deleted": True} for doc in restos])
    return cliente_http


@pytest.fixture
def concorrente(aplicacao, banco) -> CouchDB:
    """Outro cliente do mesmo banco: a outra compra acontecendo ao mesmo tempo.

    É outra instância de propósito. Os testes trocam métodos da instância da
    loja para simular falhas, e o concorrente precisa continuar falando com
    o banco normalmente.
    """
    return CouchDB(aplicacao.config["COUCHDB_URL"], banco.banco, sessao=aplicacao.config["COUCHDB_SESSAO"])


@pytest.fixture
def catalogo(banco) -> dict[str, dict]:
    """Uma região e dois cafés: um com estoque folgado, outro com estoque 1."""
    categoria = {
        "_id": "categoria:chapada-diamantina",
        "tipo": "categoria",
        "nome": "Chapada Diamantina",
        "regiao": "Bahia",
        "descricao": "Altitudes acima de 1.000 m no semiárido baiano.",
        "versao_esquema": 1,
    }
    farto = produto_de_teste("piata-altitude", "Piatã Altitude", preco_centavos=8900, estoque=10)
    escasso = produto_de_teste("chapada-geisha", "Chapada Geisha", preco_centavos=14800, estoque=1)

    for doc in (categoria, farto, escasso):
        banco.salvar(doc)

    return {"categoria": categoria, "farto": farto, "escasso": escasso}


@pytest.fixture
def cliente(banco) -> dict:
    return cadastrar_cliente("Ana Teste", "ana@exemplo.com", "senha-de-teste-123")
