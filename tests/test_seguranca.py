"""Testes das proteções que o OWASP ZAP cobrou no projeto relacional.

Continuam valendo na versão NoSQL: sem teste, qualquer refatoração pode
remover um cabeçalho sem ninguém perceber até o próximo scan.
"""

import pytest

from apoio import extrair_token


@pytest.fixture
def navegador(aplicacao, banco):
    """Cliente HTTP de teste, com sessão própria."""
    return aplicacao.test_client()


# ---------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------

def test_post_sem_token_csrf_e_recusado(navegador):
    """O cenário do ataque: POST vindo de fora, sem o token."""
    resposta = navegador.post(
        "/cadastro",
        data={"nome": "Invasor", "email": "x@y.com", "senha": "12345678"},
    )
    assert resposta.status_code == 400


def test_post_com_token_errado_e_recusado(navegador):
    navegador.get("/cadastro")  # cria a sessão e o token
    resposta = navegador.post(
        "/cadastro",
        data={"nome": "Invasor", "email": "x@y.com", "senha": "12345678", "_csrf": "token-chutado"},
    )
    assert resposta.status_code == 400


def test_post_com_token_valido_passa(navegador):
    token = extrair_token(navegador.get("/cadastro").get_data(as_text=True))
    resposta = navegador.post(
        "/cadastro",
        data={
            "nome": "Cliente Legítimo",
            "email": "legitimo@exemplo.com",
            "senha": "senha-de-teste-123",
            "_csrf": token,
        },
    )
    assert resposta.status_code == 302  # cadastrou e redirecionou


def test_todo_formulario_tem_campo_csrf(navegador, catalogo):
    for caminho in ["/login", "/cadastro", "/produto/piata-altitude"]:
        html = navegador.get(caminho).get_data(as_text=True)
        assert 'name="_csrf"' in html, f"{caminho} tem formulário sem token"


def test_get_nao_exige_token(navegador):
    """Leitura nunca é barrada — GET não altera estado."""
    assert navegador.get("/").status_code == 200


# ---------------------------------------------------------------------
# Cabeçalhos de segurança
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "cabecalho, esperado",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ],
)
def test_cabecalhos_de_seguranca_presentes(navegador, cabecalho, esperado):
    assert navegador.get("/").headers.get(cabecalho) == esperado


def test_content_security_policy_bloqueia_script(navegador):
    csp = navegador.get("/").headers.get("Content-Security-Policy", "")

    assert "script-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    # O Google Fonts precisa continuar liberado, senão a tipografia quebra.
    assert "https://fonts.googleapis.com" in csp
    assert "https://fonts.gstatic.com" in csp


def test_hsts_so_aparece_sobre_https(navegador):
    assert "Strict-Transport-Security" not in navegador.get("/").headers

    # Simula o proxy do Vercel, que encaminha o protocolo original.
    com_tls = navegador.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=31536000" in com_tls.headers.get("Strict-Transport-Security", "")


# ---------------------------------------------------------------------
# Cookie de sessão
# ---------------------------------------------------------------------

def cadastrar(navegador, email):
    token = extrair_token(navegador.get("/cadastro").get_data(as_text=True))
    return navegador.post(
        "/cadastro",
        data={"nome": "Teste", "email": email, "senha": "senha-de-teste-123", "_csrf": token},
    )


def test_cookie_de_sessao_tem_httponly_e_samesite(navegador):
    resposta = cadastrar(navegador, "cookie@exemplo.com")

    cookie = next((c for c in resposta.headers.getlist("Set-Cookie") if "session=" in c), "")
    assert "HttpOnly" in cookie  # JavaScript não lê
    assert "SameSite=Lax" in cookie  # não viaja em POST de outro site


def test_pagina_de_cliente_logado_nao_vai_para_cache(navegador):
    cadastrar(navegador, "cache@exemplo.com")

    # Em computador compartilhado, o botão voltar não pode mostrar o
    # histórico de quem acabou de sair.
    assert navegador.get("/").headers.get("Cache-Control") == "no-store"


def test_producao_sem_segredos_responde_503_dizendo_o_que_falta(monkeypatch):
    """Sem SECRET_KEY a loja não atende — mas diz por quê, em vez de derrubar a função."""
    from app import criar_app

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("COUCHDB_URL", raising=False)

    navegador = criar_app().test_client()
    resposta = navegador.get("/produto/chapada-geisha")

    assert resposta.status_code == 503
    texto = resposta.get_data(as_text=True)
    assert "SECRET_KEY" in texto
    assert "COUCHDB_URL" in texto

    # A apresentação não depende de banco nem de sessão: continua no ar.
    assert navegador.get("/apresentacao").status_code == 200


def test_producao_com_couchdb_url_mal_colada_nao_mostra_o_valor(monkeypatch):
    from app import criar_app

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SECRET_KEY", "chave-de-teste-com-tamanho-suficiente")
    monkeypatch.setenv("COUCHDB_URL", "usuario:senha-secreta@servidor-sem-https")

    resposta = criar_app().test_client().get("/")

    assert resposta.status_code == 503
    assert "COUCHDB_URL" in resposta.get_data(as_text=True)
    assert "senha-secreta" not in resposta.get_data(as_text=True)


def test_redirecionamento_pos_login_so_aceita_destino_interno(navegador):
    cadastrar(navegador, "destino@exemplo.com")
    navegador.get("/sair")

    token = extrair_token(navegador.get("/login").get_data(as_text=True))
    resposta = navegador.post(
        "/login?proximo=//site-malicioso.com",
        data={"email": "destino@exemplo.com", "senha": "senha-de-teste-123", "_csrf": token},
    )

    assert resposta.headers["Location"] == "/"
