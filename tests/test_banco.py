"""Testes do cliente HTTP (banco.py) e dos índices Mango."""

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.models import Response
from requests.structures import CaseInsensitiveDict

from apoio import linha
from app import (
    consulta_catalogo,
    consulta_cliente_por_email,
    consulta_pedidos_do_cliente,
    consulta_pedidos_pendentes,
    finalizar_pedido,
)
from banco import BancoIndisponivel, Conflito, CouchDB


# ---------------------------------------------------------------------
# Credenciais
# ---------------------------------------------------------------------

def test_credenciais_saem_da_url_e_vao_para_a_sessao():
    cliente_http = CouchDB("https://usuario:s%40nha@exemplo.cloudant.com/", "torra_terra")

    assert cliente_http.url_servidor == "https://exemplo.cloudant.com"
    assert cliente_http.url_banco == "https://exemplo.cloudant.com/torra_terra"
    assert cliente_http.sessao.auth == ("usuario", "s@nha")


def test_erro_de_conexao_nao_vaza_a_senha():
    # Porta 9 (discard): nada escuta ali, a conexão é recusada na hora.
    cliente_http = CouchDB("http://usuario:senha-secreta@127.0.0.1:9", "torra_terra", timeout=(0.5, 0.5))

    with pytest.raises(BancoIndisponivel) as erro:
        cliente_http.info_banco()

    assert "senha-secreta" not in str(erro.value)
    assert "senha-secreta" not in repr(erro.value.__cause__)


# ---------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------

def test_id_com_caracteres_especiais_vai_e_volta(banco):
    doc = {
        "_id": "email:ana+café/teste@exemplo.com",
        "tipo": "email",
        "cliente_id": "cliente:1",
        "versao_esquema": 1,
    }
    banco.salvar(doc)

    assert banco.obter(doc["_id"])["cliente_id"] == "cliente:1"


def test_gravar_com_rev_velho_da_409(banco, catalogo):
    """O controle otimista em estado puro: duas cópias do mesmo documento."""
    primeira = banco.obter(catalogo["farto"]["_id"])
    segunda = banco.obter(catalogo["farto"]["_id"])

    primeira["estoque"] = 9
    banco.salvar(primeira)

    segunda["estoque"] = 8
    with pytest.raises(Conflito) as erro:
        banco.salvar(segunda)

    assert erro.value.status == 409
    assert banco.obter(catalogo["farto"]["_id"])["estoque"] == 9


def test_lote_devolve_um_resultado_por_documento(banco, catalogo):
    """_bulk_docs não é transação: um documento entra, o outro não."""
    piata = banco.obter(catalogo["farto"]["_id"])
    geisha_velho = dict(catalogo["escasso"], _rev="1-00000000000000000000000000000000")

    resultados = banco.gravar_lote([dict(piata, estoque=5), dict(geisha_velho, estoque=0)])

    assert "error" not in resultados[0]
    assert resultados[1]["error"] == "conflict"
    assert banco.obter(catalogo["farto"]["_id"])["estoque"] == 5
    assert banco.obter(catalogo["escasso"]["_id"])["estoque"] == 1


def test_atualizar_desiste_quando_o_conflito_nao_para(banco, catalogo, concorrente, monkeypatch):
    produto_id = catalogo["farto"]["_id"]
    salvar = banco.salvar

    def salvar_sempre_atrasado(doc):
        rival = concorrente.obter(produto_id)
        rival["estoque"] -= 1
        concorrente.salvar(rival)  # alguém sempre grava antes
        return salvar(doc)

    monkeypatch.setattr(banco, "salvar", salvar_sempre_atrasado)

    with pytest.raises(Conflito):
        banco.atualizar(produto_id, lambda doc: dict(doc, nome="Novo nome"), tentativas=3)


# ---------------------------------------------------------------------
# Limite de vazão
# ---------------------------------------------------------------------

class AdaptadorQueRecusa(BaseAdapter):
    """Responde 429 nas primeiras requisições, como o Cloudant Lite acima do limite."""

    def __init__(self, real: BaseAdapter, recusas: int):
        super().__init__()
        self.real = real
        self.recusas = recusas
        self.chamadas = 0

    def send(self, request, **kwargs):
        self.chamadas += 1
        if self.recusas:
            self.recusas -= 1
            resposta = Response()
            resposta.status_code = 429
            resposta._content = b'{"error":"too_many_requests","reason":"rate limit"}'
            resposta.headers = CaseInsensitiveDict({"Content-Type": "application/json", "Retry-After": "0"})
            resposta.url = request.url
            resposta.request = request
            return resposta
        return self.real.send(request, **kwargs)

    def close(self):
        self.real.close()


def test_limite_de_vazao_429_e_repetido(aplicacao, banco):
    """O 429 chega antes de gravar qualquer coisa, então repetir é seguro."""
    sessao_da_loja = aplicacao.config["COUCHDB_SESSAO"]
    adaptador = AdaptadorQueRecusa(sessao_da_loja.get_adapter(banco.url_banco), recusas=2)
    sessao = requests.Session()
    sessao.mount(banco.url_servidor, adaptador)
    cliente_http = CouchDB(aplicacao.config["COUCHDB_URL"], banco.banco, sessao=sessao)

    cliente_http.salvar({"_id": "email:limite@exemplo.com", "tipo": "email", "cliente_id": "cliente:1"})

    assert adaptador.chamadas == 3
    assert banco.obter("email:limite@exemplo.com")["cliente_id"] == "cliente:1"


# ---------------------------------------------------------------------
# Índices
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "consulta, indice",
    [
        (consulta_catalogo(), "idx_produtos_catalogo"),
        (consulta_catalogo("categoria:sul-de-minas"), "idx_produtos_categoria"),
        (consulta_cliente_por_email("ana@exemplo.com"), "idx_clientes_email"),
        (consulta_pedidos_do_cliente("cliente:1"), "idx_pedidos_cliente"),
        # A consulta de manutenção roda sem índice de propósito.
        (consulta_pedidos_pendentes(), "_all_docs"),
    ],
)
def test_indices_atendem_as_consultas(banco, consulta, indice):
    """Pergunta ao próprio CouchDB (_explain) qual índice ele usaria.

    Se alguém mudar uma consulta e ela perder o índice, este teste quebra —
    antes de a loja começar a varrer o banco inteiro em produção.
    """
    assert banco.explicar(consulta)["index"]["name"] == indice


def test_consultas_ordenadas_executam_de_verdade(banco, cliente, catalogo):
    """Ordenar sem índice é erro 400 no Mango. Aqui as consultas rodam."""
    finalizar_pedido(cliente["_id"], [linha(catalogo["farto"], 1)])

    nomes = [doc["nome"] for doc in banco.buscar(consulta_catalogo())]
    assert nomes == ["Chapada Geisha", "Piatã Altitude"]

    assert len(banco.buscar(consulta_catalogo("categoria:chapada-diamantina"))) == 2
    assert banco.buscar(consulta_cliente_por_email("ana@exemplo.com"))[0]["_id"] == cliente["_id"]
    assert len(banco.buscar(consulta_pedidos_do_cliente(cliente["_id"]))) == 1


def test_consulta_do_catalogo_traz_so_os_campos_do_cartao(banco, catalogo):
    """`fields` é o SELECT coluna do Mango: nada de descrição nem de reservas."""
    [primeiro, _] = banco.buscar(consulta_catalogo())

    assert "descricao" not in primeiro
    assert "reservas" not in primeiro
    assert primeiro["preco_centavos"] == 14800
