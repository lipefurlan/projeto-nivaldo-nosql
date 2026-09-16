"""Regras de integridade — o CHECK, o NOT NULL e o UNIQUE do projeto relacional.

Em duas camadas, testadas separadamente:

- a aplicação (validar_produto, validar_pedido), que roda com qualquer banco;
- o próprio CouchDB (validate_doc_update em couchdb/validacao.js), gravando
  direto pela API, sem passar pela loja. Só um CouchDB de verdade executa o
  JavaScript, então esses testes são marcados `couchdb_real`.
"""

import copy
import json

import pytest
from werkzeug.security import check_password_hash

from apoio import linha, pedido_de_teste, produto_de_teste
from app import (
    EmailJaCadastrado,
    ErroValidacao,
    cadastrar_cliente,
    finalizar_pedido,
    validar_pedido,
    validar_produto,
)
from banco import BancoIndisponivel, Recusado

# (campo, valor inválido) — cada linha é um CHECK do schema.sql relacional.
PRODUTOS_INVALIDOS = [
    ("preco_centavos", -1000),
    ("preco_centavos", 89.9),  # dinheiro nunca em ponto flutuante
    ("estoque", -1),
    ("pontuacao_sca", 72.0),  # café especial pontua 80 ou mais
    ("torra", "QUEIMADA"),
    ("peso_g", 0),
]


# ---------------------------------------------------------------------
# Camada da aplicação
# ---------------------------------------------------------------------

def test_aplicacao_aceita_produto_valido():
    validar_produto(produto_de_teste("x", "Café X", preco_centavos=1000, estoque=1))


@pytest.mark.parametrize("campo, valor", PRODUTOS_INVALIDOS + [("ativo", "sim")])
def test_aplicacao_recusa_produto_invalido(campo, valor):
    produto = produto_de_teste("x", "Café X", preco_centavos=1000, estoque=1)
    produto[campo] = valor

    with pytest.raises(ErroValidacao, match=campo):
        validar_produto(produto)


def pedido_valido() -> dict:
    produto = produto_de_teste("x", "Café X", preco_centavos=1000, estoque=5)
    return pedido_de_teste("pedido:1", "cliente:1", produto, 2)


def test_aplicacao_aceita_pedido_valido():
    validar_pedido(pedido_valido())


def test_aplicacao_recusa_moagem_invalida():
    pedido = pedido_valido()
    pedido["itens"][0]["moagem"] = "MOIDA"
    with pytest.raises(ErroValidacao, match="moagem"):
        validar_pedido(pedido)


def test_aplicacao_recusa_quantidade_zero():
    pedido = pedido_valido()
    pedido["itens"][0]["quantidade"] = 0
    pedido["total_centavos"] = 0
    with pytest.raises(ErroValidacao, match="quantidade"):
        validar_pedido(pedido)


def test_aplicacao_recusa_total_que_nao_bate_com_os_itens():
    pedido = pedido_valido()
    pedido["total_centavos"] += 1
    with pytest.raises(ErroValidacao, match="total_centavos"):
        validar_pedido(pedido)


def test_aplicacao_recusa_mesmo_cafe_na_mesma_moagem_em_duas_linhas():
    pedido = pedido_valido()
    pedido["itens"].append(copy.deepcopy(pedido["itens"][0]))
    pedido["total_centavos"] *= 2
    with pytest.raises(ErroValidacao, match="repetido"):
        validar_pedido(pedido)


def test_aplicacao_recusa_pedido_sem_data():
    pedido = pedido_valido()
    del pedido["criado_em"]
    with pytest.raises(ErroValidacao, match="criado_em"):
        validar_pedido(pedido)


def test_aplicacao_recusa_status_fora_do_dominio():
    pedido = pedido_valido()
    pedido["status"] = "EXTRAVIADO"
    with pytest.raises(ErroValidacao, match="status"):
        validar_pedido(pedido)


# ---------------------------------------------------------------------
# Camada do banco — validate_doc_update
# ---------------------------------------------------------------------

@pytest.mark.couchdb_real
@pytest.mark.parametrize("campo, valor", PRODUTOS_INVALIDOS)
def test_banco_recusa_produto_invalido_gravado_direto(banco, catalogo, campo, valor):
    """Gravando pela API, sem passar pela aplicação: o próprio banco barra."""
    produto = banco.obter(catalogo["farto"]["_id"])
    produto[campo] = valor

    with pytest.raises(Recusado) as erro:
        banco.salvar(produto)

    assert erro.value.status == 403
    assert campo in erro.value.motivo
    assert banco.obter(produto["_id"])[campo] == catalogo["farto"][campo]  # nada mudou


@pytest.mark.couchdb_real
def test_banco_recusa_pedido_com_moagem_invalida(banco, cliente, catalogo):
    pedido = pedido_de_teste("pedido:moagem", cliente["_id"], catalogo["farto"], 1)
    pedido["itens"][0]["moagem"] = "MOIDA"

    with pytest.raises(Recusado, match="moagem"):
        banco.salvar(pedido)


@pytest.mark.couchdb_real
def test_banco_nao_deixa_mudar_o_preco_de_um_pedido_gravado(banco, cliente, catalogo):
    """O preço congelado garantido pelo banco, não só pela aplicação."""
    pedido = finalizar_pedido(cliente["_id"], [linha(catalogo["farto"], 2)])

    adulterado = banco.obter(pedido["_id"])
    adulterado["itens"][0]["preco_unitario_centavos"] = 1
    adulterado["total_centavos"] = 2

    with pytest.raises(Recusado, match="itens"):
        banco.salvar(adulterado)


@pytest.mark.couchdb_real
def test_banco_nao_deixa_mudar_o_nome_do_cafe_no_pedido_gravado(banco, cliente, catalogo):
    pedido = finalizar_pedido(cliente["_id"], [linha(catalogo["farto"], 1)])

    adulterado = banco.obter(pedido["_id"])
    adulterado["itens"][0]["nome"] = "Outro café"

    with pytest.raises(Recusado, match="itens"):
        banco.salvar(adulterado)


@pytest.mark.couchdb_real
def test_banco_nao_deixa_pedido_confirmado_voltar_a_pendente(banco, cliente, catalogo):
    pedido = finalizar_pedido(cliente["_id"], [linha(catalogo["farto"], 1)])

    gravado = banco.obter(pedido["_id"])
    gravado["status"] = "PENDENTE"

    with pytest.raises(Recusado, match="PENDENTE"):
        banco.salvar(gravado)


@pytest.mark.couchdb_real
def test_banco_recusa_pedido_sem_data(banco, cliente, catalogo):
    pedido = pedido_de_teste("pedido:sem-data", cliente["_id"], catalogo["farto"], 1)
    del pedido["criado_em"]

    with pytest.raises(Recusado, match="criado_em"):
        banco.salvar(pedido)


@pytest.mark.couchdb_real
def test_banco_nao_deixa_pedido_cancelado_voltar_a_valer(banco, cliente, catalogo):
    pedido = pedido_de_teste("pedido:cancelado", cliente["_id"], catalogo["farto"], 1, status="CANCELADO")
    banco.salvar(pedido)

    pedido["status"] = "CRIADO"
    with pytest.raises(Recusado, match="cancelado"):
        banco.salvar(pedido)


@pytest.mark.couchdb_real
def test_banco_recusa_senha_em_texto_puro(banco):
    with pytest.raises(Recusado, match="senha"):
        banco.salvar(
            {
                "_id": "cliente:descuidado",
                "tipo": "cliente",
                "nome": "Descuidado",
                "email": "descuidado@exemplo.com",
                "senha": "123456",
                "senha_hash": "scrypt:qualquer",
            }
        )


@pytest.mark.couchdb_real
def test_banco_recusa_documento_sem_tipo_conhecido(banco):
    with pytest.raises(Recusado, match="tipo"):
        banco.salvar({"_id": "cupom:natal", "tipo": "cupom", "desconto": 10})


@pytest.mark.couchdb_real
def test_banco_recusa_id_que_nao_comeca_pelo_tipo(banco, catalogo):
    produto = copy.deepcopy(catalogo["farto"])
    del produto["_rev"]
    produto["_id"] = "piata-sem-prefixo"

    with pytest.raises(Recusado, match="_id"):
        banco.salvar(produto)


# ---------------------------------------------------------------------
# Senha e e-mail
# ---------------------------------------------------------------------

def test_senha_nunca_e_gravada_em_texto_puro(banco, cliente):
    gravado = banco.obter(cliente["_id"])

    assert "senha" not in gravado
    assert "senha-de-teste-123" not in json.dumps(gravado)
    assert gravado["senha_hash"].startswith("scrypt:")
    assert check_password_hash(gravado["senha_hash"], "senha-de-teste-123")


def test_email_repetido_e_recusado(banco, cliente):
    with pytest.raises(EmailJaCadastrado):
        cadastrar_cliente("Outra Ana", "ana@exemplo.com", "outra-senha-123")

    assert len(banco.listar_por_prefixo("cliente:")) == 1


def test_email_unico_mesmo_com_dois_cadastros_ao_mesmo_tempo(banco, concorrente, monkeypatch):
    """O _id decide a corrida, não uma consulta prévia.

    O concorrente grava o documento do mesmo e-mail um instante antes do
    nosso. Nenhuma consulta feita antes teria visto — e mesmo assim só um
    cadastro vale.
    """
    salvar = banco.salvar

    def salvar_com_corrida(doc):
        if doc["_id"] == "email:bia@exemplo.com":
            concorrente.salvar(
                {
                    "_id": "email:bia@exemplo.com",
                    "tipo": "email",
                    "cliente_id": "cliente:do-concorrente",
                    "criado_em": "2026-09-16T12:00:00.000+00:00",
                    "versao_esquema": 1,
                }
            )
        return salvar(doc)

    monkeypatch.setattr(banco, "salvar", salvar_com_corrida)

    with pytest.raises(EmailJaCadastrado):
        cadastrar_cliente("Bia", "bia@exemplo.com", "senha-da-bia-123")

    assert banco.listar_por_prefixo("cliente:") == []


def test_cadastro_com_resposta_perdida_depois_de_gravar_o_cliente_vale(banco, monkeypatch):
    """O cliente foi gravado, mas a resposta não chegou.

    Liberar o e-mail aqui deixaria um segundo cadastro com o mesmo endereço
    passar. O cadastro confere se o cliente existe antes de desfazer algo.
    """
    salvar = banco.salvar

    def salvar_sem_resposta(doc):
        if doc["tipo"] == "cliente":
            salvar(doc)
            raise BancoIndisponivel(0, "ReadTimeout", "a resposta se perdeu")
        return salvar(doc)

    monkeypatch.setattr(banco, "salvar", salvar_sem_resposta)
    cliente = cadastrar_cliente("Bia", "bia@exemplo.com", "senha-da-bia-123")
    monkeypatch.undo()

    assert cliente["email"] == "bia@exemplo.com"
    assert banco.obter("email:bia@exemplo.com")["cliente_id"] == cliente["_id"]
    with pytest.raises(EmailJaCadastrado):
        cadastrar_cliente("Bia de novo", "bia@exemplo.com", "outra-senha-123")
    assert len(banco.listar_por_prefixo("cliente:")) == 1


def test_cadastro_que_falha_depois_de_reservar_o_email_libera_o_email(banco, monkeypatch):
    """Compensação no cadastro: se o cliente não grava, o e-mail volta a ficar livre."""
    salvar = banco.salvar

    def salvar_que_falha_no_cliente(doc):
        if doc["tipo"] == "cliente":
            raise BancoIndisponivel(0, "ReadTimeout", "sem resposta")
        return salvar(doc)

    monkeypatch.setattr(banco, "salvar", salvar_que_falha_no_cliente)
    with pytest.raises(BancoIndisponivel):
        cadastrar_cliente("Caio", "caio@exemplo.com", "senha-do-caio-123")
    monkeypatch.undo()

    assert banco.obter_ou_none("email:caio@exemplo.com") is None
    assert cadastrar_cliente("Caio", "caio@exemplo.com", "senha-do-caio-123")["email"] == "caio@exemplo.com"
