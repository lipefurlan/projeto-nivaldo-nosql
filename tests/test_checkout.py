"""Testes do checkout — a saga que substitui a transação do projeto relacional.

Os casos do briefing continuam valendo:

1. compra normal          -> pedido CRIADO, itens embutidos, estoque baixado
2. estoque insuficiente   -> nada gravado, estoque intacto, mensagem com o café
3. produto inexistente    -> pedido não é finalizado

E entram os que só existem porque o CouchDB não tem transação entre
documentos:

4. 409 no meio do checkout     -> relê e tenta de novo, sem perder baixa
5. café acaba no meio da saga  -> a compensação devolve o que foi reservado
6. resposta perdida            -> as marcas de reserva dizem o que desfazer
7. clique duplo                -> o _id do pedido barra a segunda compra
8. função morreu no meio       -> a reconciliação termina o serviço
"""

import pytest

from apoio import linha, pedido_de_teste
from app import (
    CarrinhoVazio,
    CheckoutInterrompido,
    EstoqueInsuficiente,
    PedidoDuplicado,
    ProdutoInexistente,
    agora_iso,
    finalizar_pedido,
    reconciliar,
)
from banco import BancoIndisponivel


def pedidos_gravados(banco) -> list[dict]:
    return banco.listar_por_prefixo("pedido:")


# ---------------------------------------------------------------------
# 1. Compra normal
# ---------------------------------------------------------------------

def test_compra_normal_grava_pedido_e_baixa_estoque(banco, cliente, catalogo):
    farto = catalogo["farto"]

    pedido = finalizar_pedido(cliente["_id"], [linha(farto, 3, "FINA")])

    gravado = banco.obter(pedido["_id"])
    assert gravado["status"] == "CRIADO"
    assert gravado["cliente_id"] == cliente["_id"]
    assert gravado["total_centavos"] == 3 * 8900
    assert gravado["itens"] == [
        {
            "produto_id": "produto:piata-altitude",
            "nome": "Piatã Altitude",
            "moagem": "FINA",
            "quantidade": 3,
            "preco_unitario_centavos": 8900,
        }
    ]
    assert [evento["status"] for evento in gravado["historico"]] == ["PENDENTE", "CRIADO"]

    produto = banco.obter(farto["_id"])
    assert produto["estoque"] == 10 - 3
    # A marca de reserva sai depois da confirmação: nada fica pendurado.
    assert produto["reservas"] == {}


# ---------------------------------------------------------------------
# 2. Estoque insuficiente
# ---------------------------------------------------------------------

def test_estoque_insuficiente_nao_grava_nada(banco, cliente, catalogo):
    farto, escasso = catalogo["farto"], catalogo["escasso"]

    # O item que falha vem por último de propósito: o primeiro passaria
    # sozinho, e mesmo assim nada pode ser gravado.
    with pytest.raises(EstoqueInsuficiente) as erro:
        finalizar_pedido(cliente["_id"], [linha(farto, 2, "GRAO"), linha(escasso, 5)])

    # A mensagem diz QUAL café faltou — critério de aceite do RF05.
    assert "Chapada Geisha" in str(erro.value)
    assert erro.value.disponivel == 1
    assert erro.value.pedido == 5

    assert pedidos_gravados(banco) == []
    assert banco.obter(farto["_id"])["estoque"] == 10
    assert banco.obter(escasso["_id"])["estoque"] == 1


def test_moagens_diferentes_do_mesmo_cafe_somam_no_estoque(banco, cliente, catalogo):
    escasso = catalogo["escasso"]  # estoque 1

    # Cada linha sozinha caberia no estoque; juntas, não.
    with pytest.raises(EstoqueInsuficiente) as erro:
        finalizar_pedido(cliente["_id"], [linha(escasso, 1, "GRAO"), linha(escasso, 1, "FINA")])

    assert erro.value.pedido == 2
    assert pedidos_gravados(banco) == []


# ---------------------------------------------------------------------
# 3. Produto inexistente
# ---------------------------------------------------------------------

def test_produto_inexistente_nao_finaliza_o_pedido(banco, cliente, catalogo):
    fantasma = {"produto_id": "produto:nao-existe", "quantidade": 1, "moagem": "FINA"}

    with pytest.raises(ProdutoInexistente):
        finalizar_pedido(cliente["_id"], [linha(catalogo["farto"], 1), fantasma])

    assert pedidos_gravados(banco) == []
    assert banco.obter(catalogo["farto"]["_id"])["estoque"] == 10


def test_produto_desativado_nao_finaliza_o_pedido(banco, cliente, catalogo):
    geisha = banco.obter(catalogo["escasso"]["_id"])
    geisha["ativo"] = False
    banco.salvar(geisha)

    with pytest.raises(ProdutoInexistente):
        finalizar_pedido(cliente["_id"], [linha(geisha, 1)])

    assert pedidos_gravados(banco) == []


# ---------------------------------------------------------------------
# Decorrências do modelo
# ---------------------------------------------------------------------

def test_mesmo_cafe_em_moagens_diferentes_vira_dois_itens(banco, cliente, catalogo):
    """É o que faz o item ter identidade própria dentro do pedido."""
    farto = catalogo["farto"]

    pedido = finalizar_pedido(cliente["_id"], [linha(farto, 2, "GRAO"), linha(farto, 1, "FINA")])

    assert {(item["moagem"], item["quantidade"]) for item in pedido["itens"]} == {("GRAO", 2), ("FINA", 1)}
    assert banco.obter(farto["_id"])["estoque"] == 10 - 3


def test_preco_fica_congelado_no_pedido_depois_do_reajuste(banco, cliente, catalogo):
    farto = catalogo["farto"]
    pedido = finalizar_pedido(cliente["_id"], [linha(farto, 1)])

    # O café sobe de preço depois da compra.
    produto = banco.obter(farto["_id"])
    produto["preco_centavos"] = 12900
    banco.salvar(produto)

    gravado = banco.obter(pedido["_id"])
    assert gravado["itens"][0]["preco_unitario_centavos"] == 8900
    assert gravado["total_centavos"] == 8900


def test_carrinho_vazio_nao_gera_pedido(banco, cliente):
    with pytest.raises(CarrinhoVazio):
        finalizar_pedido(cliente["_id"], [])

    assert pedidos_gravados(banco) == []


# ---------------------------------------------------------------------
# 4. Conflito 409
# ---------------------------------------------------------------------

def test_conflito_409_e_resolvido_relendo_o_documento(banco, cliente, catalogo, concorrente, monkeypatch):
    """Outra compra baixa o mesmo café entre a nossa leitura e a nossa gravação.

    O _rev que a saga leu fica velho, o _bulk_docs devolve conflito para o
    café, e a saga relê e tenta de novo — contando com a baixa da outra.
    """
    farto = catalogo["farto"]
    gravar_lote = banco.gravar_lote
    interferiu = []

    def lote_com_concorrente(docs):
        if not interferiu:
            interferiu.append(True)
            atual = concorrente.obter(farto["_id"])
            atual["estoque"] -= 4  # a outra compra levou 4
            concorrente.salvar(atual)
        return gravar_lote(docs)

    monkeypatch.setattr(banco, "gravar_lote", lote_com_concorrente)

    pedido = finalizar_pedido(cliente["_id"], [linha(farto, 3)])

    assert interferiu
    assert banco.obter(pedido["_id"])["status"] == "CRIADO"
    # 10 - 4 da outra compra - 3 desta: nenhuma baixa se perdeu.
    assert banco.obter(farto["_id"])["estoque"] == 3


# ---------------------------------------------------------------------
# 5. Compensação
# ---------------------------------------------------------------------

def test_cafe_que_acaba_no_meio_da_saga_e_compensado(banco, cliente, catalogo, concorrente, monkeypatch):
    """O cenário que o _bulk_docs do projeto-base não tratava.

    Os dois cafés passam na validação. Antes da reserva, outra compra leva a
    última unidade do Geisha: no lote, o Piatã entra e o Geisha volta com
    conflito. Relido, o Geisha está zerado — e o Piatã, que já tinha baixado,
    precisa voltar para o estoque.
    """
    farto, escasso = catalogo["farto"], catalogo["escasso"]
    gravar_lote = banco.gravar_lote
    interferiu = []

    def lote_com_concorrente(docs):
        if not interferiu:
            interferiu.append(True)
            geisha = concorrente.obter(escasso["_id"])
            geisha["estoque"] = 0
            concorrente.salvar(geisha)
        return gravar_lote(docs)

    monkeypatch.setattr(banco, "gravar_lote", lote_com_concorrente)

    with pytest.raises(EstoqueInsuficiente) as erro:
        finalizar_pedido(cliente["_id"], [linha(farto, 2), linha(escasso, 1)])

    assert "Chapada Geisha" in str(erro.value)

    [pedido] = pedidos_gravados(banco)
    assert pedido["status"] == "CANCELADO"
    assert [evento["status"] for evento in pedido["historico"]] == ["PENDENTE", "CANCELADO"]
    assert "Chapada Geisha" in pedido["historico"][-1]["motivo"]

    piata = banco.obter(farto["_id"])
    assert piata["estoque"] == 10  # a baixa do Piatã foi desfeita
    assert piata["reservas"] == {}
    assert banco.obter(escasso["_id"])["estoque"] == 0  # a venda da outra compra fica


# ---------------------------------------------------------------------
# 6. Resposta perdida
# ---------------------------------------------------------------------

def test_timeout_depois_de_gravar_a_reserva_e_desfeito_pelas_marcas(banco, cliente, catalogo, monkeypatch):
    """O banco grava o lote de reservas, mas a resposta nunca chega.

    Do lado da loja, é impossível saber o que entrou. A compensação não
    precisa saber: ela devolve estoque só onde encontra a marca do pedido.
    """
    farto, escasso = catalogo["farto"], catalogo["escasso"]
    gravar_lote = banco.gravar_lote
    perdeu = []

    def lote_sem_resposta(docs):
        if not perdeu:
            perdeu.append(True)
            gravar_lote(docs)  # o banco gravou...
            raise BancoIndisponivel(0, "ReadTimeout", "a resposta se perdeu")  # ...e a loja não soube
        return gravar_lote(docs)

    monkeypatch.setattr(banco, "gravar_lote", lote_sem_resposta)

    with pytest.raises(CheckoutInterrompido):
        finalizar_pedido(cliente["_id"], [linha(farto, 2), linha(escasso, 1)])

    [pedido] = pedidos_gravados(banco)
    assert pedido["status"] == "CANCELADO"
    for produto, estoque_original in ((farto, 10), (escasso, 1)):
        atual = banco.obter(produto["_id"])
        assert atual["estoque"] == estoque_original
        assert atual["reservas"] == {}


def test_confirmacao_gravada_sem_resposta_nao_e_desfeita(banco, cliente, catalogo, monkeypatch):
    """O inverso: o pedido virou CRIADO no banco, mas a resposta se perdeu.

    A compensação relê o pedido antes de desfazer qualquer coisa. Encontra
    CRIADO e para — a compra valeu, e o estoque continua baixado.
    """
    farto = catalogo["farto"]
    salvar = banco.salvar
    perdeu = []

    def salvar_sem_resposta(doc):
        if doc.get("status") == "CRIADO" and not perdeu:
            perdeu.append(True)
            salvar(doc)
            raise BancoIndisponivel(0, "ReadTimeout", "a resposta se perdeu")
        return salvar(doc)

    monkeypatch.setattr(banco, "salvar", salvar_sem_resposta)

    pedido = finalizar_pedido(cliente["_id"], [linha(farto, 2)])

    assert pedido["status"] == "CRIADO"
    assert banco.obter(pedido["_id"])["status"] == "CRIADO"
    produto = banco.obter(farto["_id"])
    assert produto["estoque"] == 8
    assert produto["reservas"] == {}


# ---------------------------------------------------------------------
# 7. Idempotência
# ---------------------------------------------------------------------

def test_mesma_compra_enviada_duas_vezes_nao_baixa_em_dobro(banco, cliente, catalogo):
    farto = catalogo["farto"]
    carrinho = [linha(farto, 3)]

    primeiro = finalizar_pedido(cliente["_id"], carrinho, chave="clique-duplo")

    with pytest.raises(PedidoDuplicado) as erro:
        finalizar_pedido(cliente["_id"], carrinho, chave="clique-duplo")

    assert erro.value.pedido["_id"] == primeiro["_id"]
    assert erro.value.pedido["status"] == "CRIADO"
    assert len(pedidos_gravados(banco)) == 1
    assert banco.obter(farto["_id"])["estoque"] == 7


def test_quem_barra_a_duplicidade_e_o_id_nao_a_checagem_previa(banco, cliente, catalogo, monkeypatch):
    """Mesmo que as duas requisições passem juntas pela checagem inicial, só
    uma grava: o segundo PUT no mesmo _id recebe 409 do banco."""
    farto = catalogo["farto"]
    finalizar_pedido(cliente["_id"], [linha(farto, 3)], chave="corrida")

    # A segunda requisição chegou antes da primeira gravar: a checagem não vê nada.
    monkeypatch.setattr(banco, "obter_ou_none", lambda doc_id: None)

    with pytest.raises(PedidoDuplicado):
        finalizar_pedido(cliente["_id"], [linha(farto, 3)], chave="corrida")

    assert len(pedidos_gravados(banco)) == 1
    assert banco.obter(farto["_id"])["estoque"] == 7


# ---------------------------------------------------------------------
# 8. Reconciliação
# ---------------------------------------------------------------------

def test_reconciliacao_termina_saga_que_morreu_no_meio(banco, cliente, catalogo):
    """A função serverless morreu depois de reservar e antes de confirmar."""
    farto = catalogo["farto"]
    pedido_id = "pedido:saga-interrompida"

    # O estado que a saga deixou: um pedido PENDENTE antigo e a reserva no café.
    banco.salvar(pedido_de_teste(pedido_id, cliente["_id"], farto, 4))
    produto = banco.obter(farto["_id"])
    produto["estoque"] = 6
    produto["reservas"] = {pedido_id: 4}
    banco.salvar(produto)

    relatorio = reconciliar(minutos=10)

    assert relatorio["pedidos_cancelados"] == [pedido_id]
    assert banco.obter(pedido_id)["status"] == "CANCELADO"
    produto = banco.obter(farto["_id"])
    assert produto["estoque"] == 10
    assert produto["reservas"] == {}

    # Idempotente: rodar de novo não devolve estoque em dobro.
    reconciliar(minutos=10)
    assert banco.obter(farto["_id"])["estoque"] == 10


def test_reconciliacao_limpa_marca_de_pedido_confirmado_sem_mexer_no_estoque(banco, cliente, catalogo):
    farto = catalogo["farto"]
    pedido = finalizar_pedido(cliente["_id"], [linha(farto, 2)])

    # Simula a fase 5 que não chegou a rodar.
    produto = banco.obter(farto["_id"])
    produto["reservas"] = {pedido["_id"]: 2}
    banco.salvar(produto)

    relatorio = reconciliar(minutos=10)

    assert relatorio["marcas_limpas"] == [f"{farto['_id']} <- {pedido['_id']}"]
    produto = banco.obter(farto["_id"])
    assert produto["estoque"] == 8
    assert produto["reservas"] == {}


def test_reconciliacao_nao_mexe_em_checkout_em_andamento(banco, cliente, catalogo):
    """Um PENDENTE de agora pode ser uma compra acontecendo neste instante."""
    farto = catalogo["farto"]
    pedido_id = "pedido:em-andamento"
    agora = agora_iso()
    banco.salvar(
        pedido_de_teste(
            pedido_id, cliente["_id"], farto, 1,
            criado_em=agora, historico=[{"status": "PENDENTE", "em": agora}],
        )
    )
    produto = banco.obter(farto["_id"])
    produto["estoque"] = 9
    produto["reservas"] = {pedido_id: 1}
    banco.salvar(produto)

    relatorio = reconciliar(minutos=10)

    assert all(not itens for itens in relatorio.values())
    assert banco.obter(pedido_id)["status"] == "PENDENTE"
    assert banco.obter(farto["_id"])["reservas"] == {pedido_id: 1}


def test_reconciliacao_libera_email_de_cadastro_que_caiu_no_meio(banco):
    banco.salvar(
        {
            "_id": "email:orfao@exemplo.com",
            "tipo": "email",
            "cliente_id": "cliente:que-nunca-foi-gravado",
            "criado_em": "2026-01-01T12:00:00.000+00:00",
            "versao_esquema": 1,
        }
    )

    relatorio = reconciliar(minutos=10)

    assert relatorio["emails_liberados"] == ["email:orfao@exemplo.com"]
    assert banco.obter_ou_none("email:orfao@exemplo.com") is None
