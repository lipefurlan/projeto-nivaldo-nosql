"""A loja pelo navegador: telas, sessão e a compra de ponta a ponta."""

import pytest

from apoio import extrair_token, pedido_de_teste, produto_de_teste
from banco import BancoIndisponivel


@pytest.fixture
def navegador(aplicacao, banco):
    return aplicacao.test_client()


def entrar(navegador, email="ana@exemplo.com", senha="senha-de-teste-123"):
    token = extrair_token(navegador.get("/login").get_data(as_text=True))
    return navegador.post("/login", data={"email": email, "senha": senha, "_csrf": token})


def adicionar(navegador, slug, quantidade=1, moagem="MEDIA"):
    token = extrair_token(navegador.get(f"/produto/{slug}").get_data(as_text=True))
    return navegador.post(
        f"/carrinho/adicionar/{slug}",
        data={"moagem": moagem, "quantidade": str(quantidade), "_csrf": token},
    )


# ---------------------------------------------------------------------
# RF01 e RF02 — Catálogo e detalhe
# ---------------------------------------------------------------------

def test_catalogo_lista_os_cafes_em_ordem_alfabetica(navegador, catalogo):
    html = navegador.get("/").get_data(as_text=True)

    assert html.index("Chapada Geisha") < html.index("Piatã Altitude")
    assert "R$ 148,00" in html
    assert "R$ 89,00" in html
    assert "SCA 89.25" in html


def test_filtro_por_regiao_mostra_so_os_cafes_dela(navegador, banco, catalogo):
    banco.salvar(
        produto_de_teste(
            "varginha-tradicional", "Varginha Tradicional",
            preco_centavos=4400, estoque=75, categoria="sul-de-minas",
        )
    )

    html = navegador.get("/?regiao=sul-de-minas").get_data(as_text=True)

    assert "Varginha Tradicional" in html
    assert "Piatã Altitude" not in html


def test_cafe_desativado_sai_do_catalogo(navegador, banco, catalogo):
    geisha = banco.obter(catalogo["escasso"]["_id"])
    geisha["ativo"] = False
    banco.salvar(geisha)

    assert "Chapada Geisha" not in navegador.get("/").get_data(as_text=True)
    assert navegador.get("/produto/chapada-geisha").status_code == 404


def test_produto_inexistente_devolve_404(navegador, catalogo):
    assert navegador.get("/produto/nao-existe").status_code == 404


def test_detalhe_le_a_descricao_da_regiao_por_referencia(navegador, catalogo):
    html = navegador.get("/produto/piata-altitude").get_data(as_text=True)

    assert "Piatã Altitude" in html
    assert "semiárido baiano" in html  # vem do documento categoria, não do produto


# ---------------------------------------------------------------------
# RF04 — Cadastro e login
# ---------------------------------------------------------------------

def test_cadastro_login_e_saida(navegador, banco):
    token = extrair_token(navegador.get("/cadastro").get_data(as_text=True))
    resposta = navegador.post(
        "/cadastro",
        data={"nome": "Bia", "email": " Bia@Exemplo.com ", "senha": "senha-da-bia-123", "_csrf": token},
    )
    assert resposta.status_code == 302

    [cliente] = banco.listar_por_prefixo("cliente:")
    assert cliente["email"] == "bia@exemplo.com"  # normalizado

    navegador.get("/sair")
    assert entrar(navegador, "bia@exemplo.com", "senha-da-bia-123").status_code == 302
    assert "Sair" in navegador.get("/").get_data(as_text=True)


def test_email_repetido_no_formulario_da_mensagem_clara(navegador, cliente):
    token = extrair_token(navegador.get("/cadastro").get_data(as_text=True))
    resposta = navegador.post(
        "/cadastro",
        data={"nome": "Outra", "email": "ana@exemplo.com", "senha": "outra-senha-123", "_csrf": token},
    )

    assert resposta.status_code == 200
    assert "já tem cadastro" in resposta.get_data(as_text=True)


def test_login_errado_nao_diz_o_que_errou(navegador, cliente):
    for email, senha in [("ana@exemplo.com", "senha-errada-123"), ("ninguem@exemplo.com", "senha-de-teste-123")]:
        html = entrar(navegador, email, senha).get_data(as_text=True)
        assert "E-mail ou senha inválidos." in html


# ---------------------------------------------------------------------
# RF03, RF05 e RF06 — Carrinho, checkout e pedidos
# ---------------------------------------------------------------------

def test_checkout_exige_login_e_devolve_o_cliente_ao_checkout(navegador, catalogo):
    adicionar(navegador, "piata-altitude")

    resposta = navegador.get("/checkout")

    assert resposta.status_code == 302
    assert "/login?proximo=/checkout" in resposta.headers["Location"]


def test_compra_de_ponta_a_ponta(navegador, banco, cliente, catalogo):
    entrar(navegador)
    adicionar(navegador, "piata-altitude", 2, "FINA")

    token = extrair_token(navegador.get("/checkout").get_data(as_text=True))
    resposta = navegador.post("/checkout", data={"_csrf": token})

    assert resposta.status_code == 302
    assert resposta.headers["Location"].endswith("/meus-pedidos")

    html = navegador.get("/meus-pedidos").get_data(as_text=True)
    assert "2 × Piatã Altitude" in html
    assert "moagem fina" in html
    assert "R$ 178,00" in html
    assert banco.obter("produto:piata-altitude")["estoque"] == 8


def test_clique_duplo_no_confirmar_nao_gera_dois_pedidos(navegador, banco, cliente, catalogo):
    """As duas requisições saem do navegador com o MESMO cookie de sessão."""
    entrar(navegador)
    adicionar(navegador, "piata-altitude", 3)
    token = extrair_token(navegador.get("/checkout").get_data(as_text=True))
    cookie_antes = navegador.get_cookie("session").value

    navegador.post("/checkout", data={"_csrf": token})

    navegador.set_cookie("session", cookie_antes)  # o segundo clique leva o cookie antigo
    resposta = navegador.post("/checkout", data={"_csrf": token}, follow_redirects=True)

    assert "já tinha sido registrado" in resposta.get_data(as_text=True)
    assert len(banco.listar_por_prefixo("pedido:")) == 1
    assert banco.obter("produto:piata-altitude")["estoque"] == 7


def test_estoque_insuficiente_volta_ao_carrinho_dizendo_qual_cafe(navegador, banco, cliente, catalogo):
    entrar(navegador)
    adicionar(navegador, "chapada-geisha", 3)
    token = extrair_token(navegador.get("/checkout").get_data(as_text=True))

    resposta = navegador.post("/checkout", data={"_csrf": token}, follow_redirects=True)

    html = resposta.get_data(as_text=True)
    assert "Estoque insuficiente de Chapada Geisha" in html
    assert "Seu carrinho" in html  # voltou ao carrinho, com os itens
    assert banco.listar_por_prefixo("pedido:") == []


def test_pedido_cancelado_pela_compensacao_aparece_com_o_motivo(navegador, banco, cliente, catalogo):
    motivo = "Estoque insuficiente de Chapada Geisha: você pediu 1 e temos 0 em estoque."
    banco.salvar(
        pedido_de_teste(
            "pedido:3f9a2c1b-cancelado", cliente["_id"], catalogo["escasso"], 1,
            status="CANCELADO",
            historico=[
                {"status": "PENDENTE", "em": "2026-09-16T15:00:00.000+00:00"},
                {"status": "CANCELADO", "em": "2026-09-16T15:00:01.000+00:00", "motivo": motivo},
            ],
        )
    )
    entrar(navegador)

    html = navegador.get("/meus-pedidos").get_data(as_text=True)

    assert "Pedido #3F9A2C1B" in html
    assert "selo-cancelado" in html
    assert motivo in html
    assert "01/01/2026 às 09:00" in html  # 12:00 UTC é 09:00 em Brasília


def test_meus_pedidos_nao_mostra_pedido_de_outro_cliente(navegador, banco, cliente, catalogo):
    entrar(navegador)
    adicionar(navegador, "piata-altitude", 1)
    token = extrair_token(navegador.get("/checkout").get_data(as_text=True))
    navegador.post("/checkout", data={"_csrf": token})
    navegador.get("/sair")

    outro = navegador.application.test_client()
    token = extrair_token(outro.get("/cadastro").get_data(as_text=True))
    outro.post("/cadastro", data={"nome": "Caio", "email": "caio@exemplo.com", "senha": "senha-do-caio-123", "_csrf": token})

    assert "Você ainda não fez nenhum pedido." in outro.get("/meus-pedidos").get_data(as_text=True)


# ---------------------------------------------------------------------
# Operação
# ---------------------------------------------------------------------

def test_saude_informa_o_estado_do_banco(navegador):
    resposta = navegador.get("/saude")

    assert resposta.status_code == 200
    assert resposta.get_json()["couchdb"] == "ok"


def test_banco_fora_do_ar_mostra_pagina_503(navegador, banco, monkeypatch):
    def fora_do_ar(*args, **kwargs):
        raise BancoIndisponivel(0, "ConnectionError", "sem resposta do CouchDB")

    monkeypatch.setattr(banco, "buscar", fora_do_ar)
    monkeypatch.setattr(banco, "info_banco", fora_do_ar)

    resposta = navegador.get("/")
    assert resposta.status_code == 503
    assert "Voltamos em instantes" in resposta.get_data(as_text=True)
    assert navegador.get("/saude").status_code == 503
