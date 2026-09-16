"""
Torra & Terra — e-commerce de café especial, versão NoSQL.

FACAMP · Tratamento e Armazenamento da Informação
Professor: Nivaldo T. Marcusso

É a mesma loja do projeto relacional (tag v1-relacional no Git), agora sobre
o Apache CouchDB — ou o IBM Cloudant, que fala a mesma API. O que mudou de
lugar na troca:

    tabelas + FOREIGN KEY     ->  documentos JSON com `tipo`, embed ou referência
    CHECK / NOT NULL          ->  validate_doc_update (couchdb/validacao.js)
    UNIQUE (email)            ->  o _id de um documento-chave "email:<endereço>"
    SELECT ... FOR UPDATE     ->  controle otimista por _rev, nova tentativa no 409
    COMMIT / ROLLBACK         ->  saga: reserva, confirmação e compensação

Este arquivo tem a configuração, as regras de negócio, as rotas e os
comandos de linha de comando. A conversa HTTP com o banco fica em banco.py.
"""

import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from banco import BancoIndisponivel, Conflito, CouchDB, ErroBanco, NaoEncontrado

RAIZ = Path(__file__).resolve().parent
PASTA_COUCHDB = RAIZ / "couchdb"

# Caminho explícito: sem ele, o python-dotenv sobe pelas pastas-pai até achar
# um .env — e esta pasta mora dentro do projeto relacional, que tem o seu.
load_dotenv(RAIZ / ".env")

# Vai em todo documento gravado. Se o formato de um documento mudar no
# futuro, o código sabe distinguir o novo do antigo sem migrar tudo de uma vez.
VERSAO_ESQUEMA = 1

log = logging.getLogger("torra_terra")


# =====================================================================
# Configuração
# =====================================================================

def em_producao() -> bool:
    """O Vercel define VERCEL=1 em todo deploy, de produção e de preview."""
    return os.getenv("VERCEL") == "1" or os.getenv("FLASK_ENV") == "production"


def criar_app(config_teste: dict | None = None) -> Flask:
    """Fábrica da aplicação.

    Recebe `config_teste` para o pytest apontar a loja para um banco
    descartável, sem tocar no banco de desenvolvimento nem no de produção.
    """
    # Os estáticos moram em public/static porque é a pasta que o Vercel
    # entrega direto do CDN, sem acordar a função Python. Localmente o Flask
    # serve a mesma pasta no mesmo endereço /static.
    app = Flask(__name__, static_folder="public/static", static_url_path="/static")

    producao = em_producao()
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY"),
        COUCHDB_URL=os.getenv("COUCHDB_URL"),
        COUCHDB_DATABASE=os.getenv("COUCHDB_DATABASE", "torra_terra"),
        COUCHDB_IAM_APIKEY=os.getenv("COUCHDB_IAM_APIKEY"),
        COUCHDB_SESSAO=None,
        # HttpOnly: JavaScript não lê o cookie, então um XSS não rouba a sessão.
        SESSION_COOKIE_HTTPONLY=True,
        # SameSite=Lax: o navegador não manda o cookie em POST vindo de outro
        # site. É a primeira barreira contra CSRF, antes mesmo do token.
        SESSION_COOKIE_SAMESITE="Lax",
        # Secure só em produção: localmente o Flask roda em http e um cookie
        # Secure não seria enviado, quebrando o login.
        SESSION_COOKIE_SECURE=producao,
    )
    if config_teste:
        app.config.update(config_teste)

    # Em produção, faltar segredo é erro de deploy, não motivo para cair num
    # valor padrão: sessão assinada com chave conhecida é sessão forjável.
    if producao:
        faltando = [nome for nome in ("SECRET_KEY", "COUCHDB_URL") if not app.config[nome]]
        if faltando:
            raise RuntimeError(
                "Variáveis de ambiente obrigatórias ausentes: " + ", ".join(faltando)
            )
    app.config["SECRET_KEY"] = app.config["SECRET_KEY"] or "dev-inseguro-trocar"
    app.config["COUCHDB_URL"] = app.config["COUCHDB_URL"] or "http://admin:admin@127.0.0.1:5984"

    app.extensions["couchdb"] = CouchDB(
        app.config["COUCHDB_URL"],
        app.config["COUCHDB_DATABASE"],
        apikey_iam=app.config["COUCHDB_IAM_APIKEY"],
        sessao=app.config["COUCHDB_SESSAO"],
    )

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    registrar_comandos(app)
    registrar_rotas(app)
    return app


def banco() -> CouchDB:
    return current_app.extensions["couchdb"]


# =====================================================================
# Regras dos documentos, do lado da aplicação
#
# São as mesmas da validate_doc_update (couchdb/validacao.js). Conferir aqui
# antes de gravar dá mensagem clara e poupa uma ida ao banco; a função do
# banco continua sendo a última palavra, para quem não passa pela loja.
# =====================================================================

TORRAS = ("CLARA", "MEDIA", "ESCURA")
MOAGENS = ("GRAO", "MEDIA", "FINA")
STATUS_PEDIDO = ("PENDENTE", "CRIADO", "PAGO", "ENVIADO", "CANCELADO")


class ErroValidacao(ValueError):
    """Documento que o banco recusaria."""


def _inteiro(valor) -> bool:
    # bool é subclasse de int em Python: sem a segunda checagem, True
    # passaria como estoque 1.
    return isinstance(valor, int) and not isinstance(valor, bool)


def _exigir(condicao: bool, mensagem: str) -> None:
    if not condicao:
        raise ErroValidacao(mensagem)


def validar_produto(doc: dict) -> None:
    _exigir(
        doc.get("tipo") == "produto" and str(doc.get("_id", "")).startswith("produto:"),
        "produto com _id ou tipo inválido",
    )
    _exigir(bool(str(doc.get("nome") or "").strip()), "produto sem nome")
    _exigir(
        _inteiro(doc.get("preco_centavos")) and doc["preco_centavos"] >= 0,
        "preco_centavos precisa ser inteiro e maior ou igual a zero",
    )
    _exigir(
        _inteiro(doc.get("estoque")) and doc["estoque"] >= 0,
        "estoque precisa ser inteiro e maior ou igual a zero",
    )
    _exigir(doc.get("torra") in TORRAS, "torra precisa ser CLARA, MEDIA ou ESCURA")
    sca = doc.get("pontuacao_sca")
    _exigir(
        sca is None
        or (isinstance(sca, (int, float)) and not isinstance(sca, bool) and 80 <= sca <= 100),
        "pontuacao_sca precisa estar entre 80 e 100",
    )
    _exigir(
        _inteiro(doc.get("peso_g")) and doc["peso_g"] > 0,
        "peso_g precisa ser inteiro e maior que zero",
    )
    _exigir(isinstance(doc.get("ativo"), bool), "ativo precisa ser true ou false")
    _exigir(
        str(doc.get("categoria_id", "")).startswith("categoria:"),
        "categoria_id precisa referenciar uma categoria",
    )


def validar_pedido(doc: dict) -> None:
    _exigir(
        doc.get("tipo") == "pedido" and str(doc.get("_id", "")).startswith("pedido:"),
        "pedido com _id ou tipo inválido",
    )
    _exigir(str(doc.get("cliente_id", "")).startswith("cliente:"), "pedido sem cliente")
    _exigir(doc.get("status") in STATUS_PEDIDO, f"status inválido: {doc.get('status')}")

    itens = doc.get("itens")
    _exigir(isinstance(itens, list) and len(itens) > 0, "pedido sem itens")

    vistos, soma = set(), 0
    for item in itens:
        _exigir(str(item.get("produto_id", "")).startswith("produto:"), "item sem produto")
        _exigir(
            _inteiro(item.get("quantidade")) and item["quantidade"] > 0,
            "quantidade precisa ser inteira e maior que zero",
        )
        _exigir(item.get("moagem") in MOAGENS, "moagem precisa ser GRAO, MEDIA ou FINA")
        _exigir(
            _inteiro(item.get("preco_unitario_centavos")) and item["preco_unitario_centavos"] >= 0,
            "preco_unitario_centavos precisa ser inteiro e maior ou igual a zero",
        )
        chave = (item["produto_id"], item["moagem"])
        _exigir(chave not in vistos, f"item repetido: {item['produto_id']} em moagem {item['moagem']}")
        vistos.add(chave)
        soma += item["quantidade"] * item["preco_unitario_centavos"]

    _exigir(doc.get("total_centavos") == soma, "total_centavos não bate com a soma dos itens")


# =====================================================================
# Datas e formatação
# =====================================================================

# O Brasil não tem horário de verão desde 2019: Brasília é UTC-3 o ano todo.
# Um fuso fixo evita depender do pacote tzdata, que o Windows não traz.
FUSO_BRASILIA = timezone(timedelta(hours=-3), "BRT")


def agora_iso() -> str:
    """Instante em UTC, ISO 8601 com milissegundos.

    Todas as datas no mesmo fuso e no mesmo formato ordenam corretamente como
    texto — é isso que permite o índice de pedidos ordenar por criado_em.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def formatar_brl(centavos) -> str:
    """R$ 1.234,56 a partir de centavos inteiros.

    A troca dupla de separadores existe porque o Python formata no padrão
    americano e não há locale pt-BR garantido no ambiente do deploy.
    """
    reais = Decimal(int(centavos or 0)) / 100
    texto = f"{reais:,.2f}"
    return "R$ " + texto.replace(",", "_").replace(".", ",").replace("_", ".")


def slug(doc_id: str) -> str:
    """'produto:chapada-geisha' -> 'chapada-geisha', o pedaço que vai na URL."""
    return doc_id.split(":", 1)[1]


def numero_pedido(pedido_id: str) -> str:
    """Oito primeiros caracteres da chave do pedido, para exibir e falar ao telefone."""
    return slug(pedido_id)[:8].upper()


def data_brasilia(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(FUSO_BRASILIA).strftime("%d/%m/%Y às %H:%M")


ROTULOS_STATUS = {
    "PENDENTE": "processando",
    "CRIADO": "criado",
    "PAGO": "pago",
    "ENVIADO": "enviado",
    "CANCELADO": "cancelado",
}


# =====================================================================
# Catálogo de consultas
#
# Em NoSQL a consulta vem antes do modelo: primeiro se lista o que a loja
# precisa perguntar, depois se desenha o documento e o índice que respondem.
# Toda consulta Mango da loja está aqui, cada uma apontando o índice que a
# atende (couchdb/indices.json). O teste test_indices_atendem_as_consultas
# pergunta ao próprio CouchDB, via _explain, qual índice ele escolheu.
# =====================================================================

CAMPOS_DO_CARTAO = [
    "_id", "nome", "preco_centavos", "estoque", "torra",
    "nota_sensorial", "pontuacao_sca", "peso_g", "categoria",
]


def consulta_catalogo(categoria_id: str | None = None) -> dict:
    # `fields` é o SELECT coluna1, coluna2 do Mango: o cartão do catálogo não
    # precisa da descrição longa nem das marcas de reserva do checkout.
    if categoria_id is None:
        return {
            "selector": {"tipo": "produto", "ativo": True},
            "fields": CAMPOS_DO_CARTAO,
            # O Mango só ordena por campos do índice, e na ordem do índice.
            "sort": [{"tipo": "asc"}, {"ativo": "asc"}, {"nome": "asc"}],
            "use_index": ["_design/idx_produtos_catalogo", "idx_produtos_catalogo"],
            "limit": 100,
        }
    return {
        "selector": {"tipo": "produto", "categoria_id": categoria_id, "ativo": True},
        "fields": CAMPOS_DO_CARTAO,
        "sort": [{"tipo": "asc"}, {"categoria_id": "asc"}, {"nome": "asc"}],
        "use_index": ["_design/idx_produtos_categoria", "idx_produtos_categoria"],
        "limit": 100,
    }


def consulta_cliente_por_email(email: str) -> dict:
    return {
        "selector": {"tipo": "cliente", "email": email},
        "use_index": ["_design/idx_clientes_email", "idx_clientes_email"],
        "limit": 1,
    }


def consulta_pedidos_do_cliente(cliente_id: str) -> dict:
    # O filtro por cliente_id nunca sai daqui: sem ele, um cliente veria o
    # histórico de todos os outros.
    return {
        "selector": {"tipo": "pedido", "cliente_id": cliente_id},
        "sort": [{"tipo": "desc"}, {"cliente_id": "desc"}, {"criado_em": "desc"}],
        "use_index": ["_design/idx_pedidos_cliente", "idx_pedidos_cliente"],
        "limit": 50,
    }


def consulta_pedidos_pendentes() -> dict:
    # Consulta de manutenção, usada só pela reconciliação. Roda SEM índice
    # de propósito: um índice custa escrita em todo pedido gravado, e esta
    # consulta roda raramente, sobre poucos documentos.
    return {"selector": {"tipo": "pedido", "status": "PENDENTE"}, "limit": 500}


# =====================================================================
# Proteção contra CSRF
#
# Sem isto, um site malicioso aberto enquanto o cliente está logado poderia
# disparar um POST para /checkout usando o cookie de sessão dele. A defesa é
# um segredo que só o nosso HTML conhece: o token vive na sessão e volta num
# campo escondido do formulário.
# =====================================================================

METODOS_QUE_ESCREVEM = ("POST", "PUT", "PATCH", "DELETE")


def token_csrf() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def renovar_token_csrf() -> None:
    """Troca o token. Chamado no login, contra fixação de sessão."""
    session["_csrf"] = secrets.token_urlsafe(32)


def login_obrigatorio(rota):
    """Protege rotas de cliente logado e devolve cada um aonde estava."""

    @wraps(rota)
    def envelope(*args, **kwargs):
        if not session.get("cliente_id"):
            flash("Entre na sua conta para continuar.", "erro")
            return redirect(url_for("login", proximo=request.path))
        return rota(*args, **kwargs)

    return envelope


# =====================================================================
# Carrinho
#
# Mora na sessão, não no banco: o carrinho é processo, não entidade. Só
# vira documento — o pedido — no checkout. Formato na sessão:
#
#     [{"produto_id": "produto:chapada-geisha", "quantidade": 2, "moagem": "FINA"}]
#
# Nunca guarda o preço. O preço é lido do banco a cada exibição e só é
# congelado dentro do pedido; confiar no cookie deixaria o cliente mudá-lo.
# =====================================================================

def ler_carrinho() -> list[dict]:
    return session.get("carrinho", [])


def gravar_carrinho(linhas: list[dict]) -> None:
    session["carrinho"] = linhas
    session.modified = True


def carrinho_detalhado() -> tuple[list[dict], int]:
    """Junta as linhas da sessão com os produtos do banco. Total em centavos."""
    linhas_sessao = ler_carrinho()
    if not linhas_sessao:
        return [], 0

    # Uma ida ao banco para o carrinho inteiro (_all_docs com keys), em vez
    # de um GET por linha.
    produtos = banco().obter_varios(sorted({linha["produto_id"] for linha in linhas_sessao}))

    linhas, total = [], 0
    for linha in linhas_sessao:
        produto = produtos.get(linha["produto_id"])
        if produto is None or not produto.get("ativo"):
            continue  # o checkout trata esse caso com mensagem explícita
        subtotal = produto["preco_centavos"] * linha["quantidade"]
        total += subtotal
        linhas.append(
            {
                "produto": produto,
                "quantidade": linha["quantidade"],
                "moagem": linha["moagem"],
                "subtotal": subtotal,
            }
        )
    return linhas, total


# =====================================================================
# Clientes
# =====================================================================

class EmailJaCadastrado(Exception):
    pass


def cadastrar_cliente(nome: str, email: str, senha: str) -> dict:
    """Cria o cliente garantindo e-mail único.

    O CouchDB não tem UNIQUE. A única unicidade que ele garante é a do _id:
    dois PUT no mesmo _id sem _rev, um grava e o outro recebe 409. Por isso a
    unicidade do e-mail mora num documento cujo _id É o e-mail. Consultar o
    índice antes de gravar não bastaria — entre a consulta e a gravação,
    outro cadastro com o mesmo e-mail pode entrar.
    """
    b = banco()
    agora = agora_iso()
    cliente = {
        "_id": f"cliente:{uuid.uuid4()}",
        "tipo": "cliente",
        "nome": nome,
        "email": email,
        # A senha em texto puro morre aqui: só o hash segue para o banco.
        "senha_hash": generate_password_hash(senha),
        "criado_em": agora,
        "versao_esquema": VERSAO_ESQUEMA,
    }
    chave_email = {
        "_id": f"email:{email}",
        "tipo": "email",
        "cliente_id": cliente["_id"],
        "criado_em": agora,
        "versao_esquema": VERSAO_ESQUEMA,
    }

    try:
        b.salvar(chave_email)
    except Conflito:
        raise EmailJaCadastrado(email) from None

    try:
        b.salvar(cliente)
    except ErroBanco:
        # Compensação: sem isto, o e-mail ficaria preso a um cliente que
        # nunca foi gravado, e ninguém mais conseguiria usá-lo.
        try:
            b.apagar(chave_email["_id"], chave_email["_rev"])
        except ErroBanco:
            log.exception("%s ficou reservado; a reconciliação libera", chave_email["_id"])
        raise

    return cliente


def buscar_cliente_por_email(email: str) -> dict | None:
    encontrados = banco().buscar(consulta_cliente_por_email(email))
    return encontrados[0] if encontrados else None


# =====================================================================
# Checkout — a saga
#
# No PostgreSQL o checkout era uma transação: o banco garantia o
# tudo-ou-nada. No CouchDB a unidade de consistência é UM documento, e o
# pedido mexe em vários (o pedido e cada café). O tudo-ou-nada passa a ser
# responsabilidade da aplicação:
#
#   1. ler e validar       nada é gravado se faltar estoque ou café
#   2. registrar intenção  o pedido nasce PENDENTE — é o diário da saga
#   3. reservar estoque    um _bulk_docs; cada café baixa o saldo e ganha
#                          uma marca {pedido_id: quantidade}; 409 -> relê
#   4. confirmar           o pedido vira CRIADO: o ponto sem volta
#   5. limpar marcas       melhor esforço; sobra só lixo inofensivo
#
#   falha em 3 ou 4  ->  compensação: pedido CANCELADO, depois o estoque
#                        de cada marca volta para o café
#
# A marca de reserva é o que torna a compensação exata. Um timeout no meio
# do _bulk_docs não diz o que foi gravado — a marca diz. Devolver só onde há
# marca faz a compensação poder rodar de novo sem devolver em dobro.
# =====================================================================

MENSAGEM_INDISPONIVEL = (
    "Um dos cafés do seu carrinho não está mais disponível. "
    "Revise o carrinho e tente de novo."
)
MENSAGEM_INTERROMPIDO = (
    "Não conseguimos concluir o pedido agora. Nenhum pedido foi gerado — "
    "tente de novo em instantes."
)


class ErroCheckout(Exception):
    """Falha que impede o fechamento do pedido."""


class CarrinhoVazio(ErroCheckout):
    pass


class ProdutoInexistente(ErroCheckout):
    pass


class EstoqueInsuficiente(ErroCheckout):
    def __init__(self, nome: str, disponivel: int, pedido: int):
        self.nome = nome
        self.disponivel = disponivel
        self.pedido = pedido
        super().__init__(
            f"Estoque insuficiente de {nome}: você pediu {pedido} "
            f"e temos {disponivel} em estoque."
        )


class PedidoDuplicado(ErroCheckout):
    """A mesma compra chegou duas vezes (duplo clique, F5 no POST)."""

    def __init__(self, pedido: dict):
        self.pedido = pedido
        super().__init__("Este pedido já tinha sido registrado.")


class CheckoutInterrompido(ErroCheckout):
    """O banco falhou no meio da saga."""


def _montar_pedido(pedido_id: str, cliente_id: str, linhas: list[dict], produtos: dict) -> dict:
    itens = []
    for linha in linhas:
        produto = produtos[linha["produto_id"]]
        itens.append(
            {
                # Referência: o café tem vida própria fora do pedido.
                "produto_id": produto["_id"],
                # Snapshot: nome e preço copiados para dentro do pedido. Se o
                # café mudar de nome ou de preço amanhã, o pedido de hoje não
                # muda — é o preco_unitario congelado do projeto relacional,
                # agora resolvido pelo formato do próprio documento.
                "nome": produto["nome"],
                "moagem": linha["moagem"],
                "quantidade": linha["quantidade"],
                "preco_unitario_centavos": produto["preco_centavos"],
            }
        )

    agora = agora_iso()
    return {
        "_id": pedido_id,
        "tipo": "pedido",
        "cliente_id": cliente_id,
        "status": "PENDENTE",
        # Embed: os itens são lidos junto com o pedido, nunca sozinhos, e
        # são limitados pelo carrinho — não crescem sem fim.
        "itens": itens,
        "total_centavos": sum(i["quantidade"] * i["preco_unitario_centavos"] for i in itens),
        "criado_em": agora,
        "historico": [{"status": "PENDENTE", "em": agora}],
        "versao_esquema": VERSAO_ESQUEMA,
    }


def _mudar_status(pedido: dict, status: str, motivo: str | None = None) -> None:
    pedido["status"] = status
    evento = {"status": status, "em": agora_iso()}
    if motivo:
        evento["motivo"] = motivo
    pedido["historico"].append(evento)


def _reservar(produto: dict, pedido_id: str, quantidade: int) -> dict | None:
    reservas = produto.setdefault("reservas", {})
    if pedido_id in reservas:
        return None  # já reservado nesta saga: repetir não baixa de novo
    if not produto.get("ativo"):
        raise ProdutoInexistente(MENSAGEM_INDISPONIVEL)
    if produto["estoque"] < quantidade:
        raise EstoqueInsuficiente(produto["nome"], produto["estoque"], quantidade)
    produto["estoque"] -= quantidade
    reservas[pedido_id] = quantidade
    return produto


def _devolver_reserva(produto: dict, pedido_id: str) -> dict | None:
    quantidade = produto.get("reservas", {}).pop(pedido_id, None)
    if quantidade is None:
        return None  # a reserva nunca entrou, ou já foi devolvida
    produto["estoque"] += quantidade
    return produto


def _liberar_marca(produto: dict, pedido_id: str) -> dict | None:
    if produto.get("reservas", {}).pop(pedido_id, None) is None:
        return None
    return produto


def _limpar_marcas(pedido: dict) -> None:
    """Fase 5: tira as marcas de reserva de um pedido confirmado."""
    produtos = sorted({item["produto_id"] for item in pedido["itens"]})
    try:
        banco().atualizar_varios(
            produtos,
            lambda doc: _liberar_marca(doc, pedido["_id"]),
            ignorar_ausentes=True,
        )
    except ErroBanco:
        # O pedido já está confirmado e o estoque, baixado. A marca que sobrar
        # não muda saldo nenhum; a reconciliação limpa depois.
        log.warning("marcas de reserva do %s ficaram para a reconciliação", pedido["_id"], exc_info=True)


def cancelar_e_devolver(pedido_id: str, motivo: str) -> dict:
    """A compensação. Devolve o pedido como ele ficou no banco.

    A ordem importa. Primeiro o pedido vira CANCELADO — é esse registro que
    decide o destino da compra. Só depois o estoque volta. Se o pedido já
    estava CRIADO (a confirmação foi gravada, só a resposta se perdeu), nada
    é desfeito: a compra valeu.

    Sempre relê o pedido do banco. Depois de uma gravação que falhou, a cópia
    em memória não é confiável — ela pode dizer CRIADO sem que o banco saiba.
    """
    b = banco()

    def cancelar(doc: dict) -> dict | None:
        if doc["status"] != "PENDENTE":
            return None  # já cancelado, ou já confirmado: não mexe
        _mudar_status(doc, "CANCELADO", motivo)
        return doc

    pedido = b.atualizar(pedido_id, cancelar)
    if pedido["status"] != "CANCELADO":
        return pedido

    b.atualizar_varios(
        sorted({item["produto_id"] for item in pedido["itens"]}),
        lambda doc: _devolver_reserva(doc, pedido_id),
        ignorar_ausentes=True,
    )
    return pedido


def finalizar_pedido(cliente_id: str, linhas_carrinho: list[dict], chave: str | None = None) -> dict:
    """Transforma o carrinho em pedido confirmado, ou não deixa nada valendo.

    `chave` é a chave de idempotência da compra e vira o _id do pedido. A
    mesma compra enviada duas vezes esbarra no mesmo _id e não baixa o
    estoque duas vezes.
    """
    if not linhas_carrinho:
        raise CarrinhoVazio("Seu carrinho está vazio.")

    b = banco()
    pedido_id = f"pedido:{chave or uuid.uuid4().hex}"

    existente = b.obter_ou_none(pedido_id)
    if existente is not None:
        raise PedidoDuplicado(existente)

    # Quantidade total por café, somando as moagens: 2 em grão e 1 moído fino
    # do mesmo café saem do mesmo estoque.
    por_produto: dict[str, int] = {}
    for linha in linhas_carrinho:
        por_produto[linha["produto_id"]] = por_produto.get(linha["produto_id"], 0) + linha["quantidade"]
    ids = sorted(por_produto)

    # --- Fase 1: ler e validar tudo, sem gravar nada ---------------------
    produtos = b.obter_varios(ids)
    for produto_id in ids:
        produto = produtos[produto_id]
        if produto is None or not produto.get("ativo"):
            raise ProdutoInexistente(MENSAGEM_INDISPONIVEL)
        if produto["estoque"] < por_produto[produto_id]:
            raise EstoqueInsuficiente(produto["nome"], produto["estoque"], por_produto[produto_id])

    pedido = _montar_pedido(pedido_id, cliente_id, linhas_carrinho, produtos)
    validar_pedido(pedido)

    # --- Fase 2: registrar a intenção ------------------------------------
    try:
        b.salvar(pedido)
    except Conflito:
        # Outra requisição com a mesma chave gravou entre a checagem e aqui.
        raise PedidoDuplicado(b.obter(pedido_id)) from None

    # --- Fases 3 e 4: reservar e confirmar -------------------------------
    try:
        try:
            b.atualizar_varios(
                ids,
                lambda doc: _reservar(doc, pedido_id, por_produto[doc["_id"]]),
                docs=produtos,
            )
        except NaoEncontrado:
            raise ProdutoInexistente(MENSAGEM_INDISPONIVEL) from None

        _mudar_status(pedido, "CRIADO")
        b.salvar(pedido)

    except (ErroCheckout, ErroBanco) as falha:
        motivo = (
            str(falha)
            if isinstance(falha, ErroCheckout)
            else "Falha de comunicação com o banco durante o checkout."
        )
        try:
            situacao = cancelar_e_devolver(pedido_id, motivo)
        except ErroBanco:
            # Nem a compensação conseguiu falar com o banco. O pedido fica
            # PENDENTE com as marcas de reserva, e `flask reconciliar` termina.
            log.exception("compensação do %s não concluiu", pedido_id)
            raise CheckoutInterrompido(MENSAGEM_INTERROMPIDO) from falha

        if situacao["status"] != "CANCELADO":
            # A confirmação tinha sido gravada; só a resposta se perdeu.
            _limpar_marcas(situacao)
            return situacao
        if isinstance(falha, ErroCheckout):
            raise
        raise CheckoutInterrompido(MENSAGEM_INTERROMPIDO) from falha

    # --- Fase 5: limpar as marcas ----------------------------------------
    _limpar_marcas(pedido)
    return pedido


# =====================================================================
# Reconciliação
# =====================================================================

def reconciliar(minutos: int = 10) -> dict[str, list[str]]:
    """Termina o que uma saga interrompida deixou pela metade.

    Uma função serverless pode morrer entre duas gravações. O PostgreSQL
    resolveria isso sozinho no restart, pelo log de transações; aqui quem
    resolve é esta rotina, lendo o estado que a saga deixou nos documentos.
    Tudo nela é idempotente: pode rodar a qualquer hora, quantas vezes for.
    """
    b = banco()
    limite = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat(timespec="milliseconds")
    relatorio: dict[str, list[str]] = {
        "pedidos_cancelados": [],
        "reservas_devolvidas": [],
        "marcas_limpas": [],
        "emails_liberados": [],
    }

    # 1. Pedidos PENDENTE antigos: a saga morreu antes de confirmar.
    for pedido in b.buscar(consulta_pedidos_pendentes()):
        if pedido["criado_em"] < limite:
            situacao = cancelar_e_devolver(
                pedido["_id"], "Checkout interrompido; cancelado pela reconciliação."
            )
            if situacao["status"] == "CANCELADO":
                relatorio["pedidos_cancelados"].append(pedido["_id"])

    # 2. Marcas de reserva que sobraram nos cafés.
    for produto in b.listar_por_prefixo("produto:"):
        for pedido_id in list(produto.get("reservas", {})):
            pedido = b.obter_ou_none(pedido_id)
            if pedido is not None and pedido["status"] == "PENDENTE":
                continue  # checkout em andamento, ou recente demais para julgar
            if pedido is None or pedido["status"] == "CANCELADO":
                b.atualizar(produto["_id"], lambda doc, p=pedido_id: _devolver_reserva(doc, p))
                relatorio["reservas_devolvidas"].append(f"{produto['_id']} <- {pedido_id}")
            else:
                b.atualizar(produto["_id"], lambda doc, p=pedido_id: _liberar_marca(doc, p))
                relatorio["marcas_limpas"].append(f"{produto['_id']} <- {pedido_id}")

    # 3. E-mails reservados por um cadastro que caiu antes de gravar o cliente.
    for chave_email in b.listar_por_prefixo("email:"):
        if chave_email.get("criado_em", "") < limite and b.obter_ou_none(chave_email["cliente_id"]) is None:
            b.apagar(chave_email["_id"], chave_email["_rev"])
            relatorio["emails_liberados"].append(chave_email["_id"])

    return relatorio


# =====================================================================
# Preparação do banco
# =====================================================================

def preparar_banco() -> list[str]:
    """Cria o banco, a validate_doc_update e os índices Mango. Idempotente."""
    b = banco()
    passos = ["banco criado" if b.criar_banco() else "banco já existia"]

    regras = {
        "_id": "_design/regras",
        "language": "javascript",
        "validate_doc_update": (PASTA_COUCHDB / "validacao.js").read_text(encoding="utf-8"),
    }
    atual = b.obter_ou_none("_design/regras")
    if atual and atual.get("validate_doc_update") == regras["validate_doc_update"]:
        passos.append("validate_doc_update já estava atualizada")
    else:
        if atual:
            regras["_rev"] = atual["_rev"]
        b.salvar(regras)
        passos.append("validate_doc_update gravada em _design/regras")

    indices = json.loads((PASTA_COUCHDB / "indices.json").read_text(encoding="utf-8"))
    for indice in indices:
        resultado = b.criar_indice(indice["definicao"])
        passos.append(f"índice {indice['definicao']['name']}: {resultado.get('result')}")

    return passos


def carregar_catalogo() -> int:
    """Grava as 4 regiões e os 12 cafés de couchdb/seed.json.

    Um único _bulk_docs para os 16 documentos. Rodar de novo atualiza os
    existentes no lugar, com o _rev atual de cada um — e recoloca o estoque
    da carga inicial. Clientes e pedidos não são tocados.
    """
    b = banco()
    docs = json.loads((PASTA_COUCHDB / "seed.json").read_text(encoding="utf-8"))
    for doc in docs:
        if doc["tipo"] == "produto":
            validar_produto(doc)

    existentes = b.obter_varios([doc["_id"] for doc in docs])
    for doc in docs:
        if existentes.get(doc["_id"]):
            doc["_rev"] = existentes[doc["_id"]]["_rev"]

    recusados = [r for r in b.gravar_lote(docs) if "error" in r]
    if recusados:
        raise click.ClickException(f"{len(recusados)} documento(s) recusado(s): {recusados}")
    return len(docs)


def registrar_comandos(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db() -> None:
        """Cria o banco, a validate_doc_update e os índices Mango."""
        for passo in preparar_banco():
            click.echo(passo)

    @app.cli.command("seed-db")
    def seed_db() -> None:
        """Carrega as 4 regiões e os 12 cafés."""
        total = carregar_catalogo()
        click.echo(f"Carga concluída: {total} documentos gravados")

    @app.cli.command("reset-db")
    @click.confirmation_option(prompt="Isto apaga o banco inteiro, com clientes e pedidos. Continuar?")
    def reset_db() -> None:
        """Apaga e recria tudo. Só para desenvolvimento."""
        banco().apagar_banco()
        for passo in preparar_banco():
            click.echo(passo)
        click.echo(f"Banco recriado do zero: {carregar_catalogo()} documentos")

    @app.cli.command("reconciliar")
    @click.option("--minutos", default=10, show_default=True, help="Idade para considerar um checkout abandonado.")
    def reconciliar_cmd(minutos: int) -> None:
        """Termina sagas de checkout interrompidas."""
        for categoria, itens in reconciliar(minutos).items():
            click.echo(f"{categoria}: {len(itens)}")
            for item in itens:
                click.echo(f"  {item}")


# =====================================================================
# Rotas
# =====================================================================

def registrar_rotas(app: Flask) -> None:
    app.jinja_env.filters["brl"] = formatar_brl
    app.jinja_env.filters["slug"] = slug
    app.jinja_env.filters["numero_pedido"] = numero_pedido
    app.jinja_env.filters["data_brasilia"] = data_brasilia
    app.jinja_env.filters["sca"] = lambda valor: f"{valor:.2f}"
    app.jinja_env.filters["status"] = lambda valor: ROTULOS_STATUS.get(valor, valor.lower())
    app.jinja_env.globals["token_csrf"] = token_csrf

    @app.before_request
    def exigir_token_csrf():
        """Barra qualquer escrita sem token válido, antes de tocar no banco."""
        if request.method not in METODOS_QUE_ESCREVEM:
            return
        enviado = request.form.get("_csrf", "")
        esperado = session.get("_csrf", "")
        # compare_digest: tempo constante, para não vazar o token pelo tempo
        # de resposta.
        if not esperado or not secrets.compare_digest(enviado, esperado):
            abort(400, description="Sessão expirada. Recarregue a página e tente de novo.")

    @app.after_request
    def cabecalhos_de_seguranca(resposta):
        """Os cabeçalhos que o OWASP ZAP cobrou no projeto relacional."""
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["X-Frame-Options"] = "DENY"
        resposta.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # A loja não usa JavaScript nenhum, então script-src 'none' é possível
        # — o que torna XSS praticamente inviável. As exceções são o Google
        # Fonts: o CSS vem de googleapis e os arquivos de fonte, de gstatic.
        resposta.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "script-src 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        )

        # HSTS só faz sentido sobre HTTPS. Atrás do proxy do Vercel o
        # request.is_secure é falso, então olhamos o protocolo encaminhado.
        encaminhado = request.headers.get("X-Forwarded-Proto", "")
        if request.is_secure or encaminhado == "https":
            resposta.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Página de cliente logado não fica em cache: em computador
        # compartilhado, o botão voltar mostraria o pedido de quem saiu.
        if session.get("cliente_id"):
            resposta.headers["Cache-Control"] = "no-store"

        return resposta

    @app.context_processor
    def injetar_contexto():
        # O nome vem da sessão, gravado no login. No relacional esta função
        # buscava o cliente no banco a cada página; aqui isso seria uma ida
        # ao Cloudant por requisição só para escrever "Sair" no menu.
        return {
            "cliente_logado": session.get("cliente_nome") if session.get("cliente_id") else None,
            "itens_no_carrinho": sum(linha["quantidade"] for linha in ler_carrinho()),
        }

    @app.errorhandler(BancoIndisponivel)
    def banco_fora_do_ar(erro):
        log.error("banco indisponível: %s", erro)
        return render_template("indisponivel.html"), 503

    # -----------------------------------------------------------------
    # Saúde — para monitoramento externo
    # -----------------------------------------------------------------
    @app.route("/saude")
    def saude():
        inicio = time.perf_counter()
        try:
            banco().info_banco()
            estado, codigo = "ok", 200
        except ErroBanco:
            estado, codigo = "indisponivel", 503
        latencia = round((time.perf_counter() - inicio) * 1000)
        return jsonify(aplicacao="ok", couchdb=estado, latencia_ms=latencia), codigo

    # -----------------------------------------------------------------
    # RF01 — Catálogo
    # -----------------------------------------------------------------
    @app.route("/")
    def catalogo():
        b = banco()

        # As regiões vêm do índice primário, pela faixa de _id
        # "categoria:" — sem consulta Mango e sem índice secundário.
        categorias = b.listar_por_prefixo("categoria:")

        regiao = request.args.get("regiao", "")
        produtos = b.buscar(consulta_catalogo(f"categoria:{regiao}" if regiao else None))

        return render_template(
            "catalogo.html", produtos=produtos, categorias=categorias, regiao_ativa=regiao
        )

    # -----------------------------------------------------------------
    # RF02 — Detalhe do produto
    # -----------------------------------------------------------------
    @app.route("/produto/<slug_produto>")
    def produto(slug_produto: str):
        b = banco()
        doc = b.obter_ou_none(f"produto:{slug_produto}")
        if doc is None or not doc.get("ativo"):
            abort(404, description="Café não encontrado.")

        # A região vem embutida no produto (nome e estado, o que o cartão
        # mostra), mas a descrição longa mora só no documento da categoria:
        # referência, lida aqui pelo _id.
        categoria = b.obter_ou_none(doc["categoria_id"])
        return render_template("produto.html", produto=doc, categoria=categoria)

    # -----------------------------------------------------------------
    # RF04 — Cadastro, login e sessão
    # -----------------------------------------------------------------
    @app.route("/cadastro", methods=["GET", "POST"])
    def cadastro():
        if request.method == "GET":
            return render_template("cadastro.html")

        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""

        if not nome or not email or not senha:
            flash("Preencha nome, e-mail e senha.", "erro")
            return render_template("cadastro.html", nome=nome, email=email)

        if "@" not in email[1:]:
            flash("Informe um e-mail válido.", "erro")
            return render_template("cadastro.html", nome=nome, email=email)

        if len(senha) < 8:
            flash("A senha precisa ter pelo menos 8 caracteres.", "erro")
            return render_template("cadastro.html", nome=nome, email=email)

        try:
            cliente = cadastrar_cliente(nome, email, senha)
        except EmailJaCadastrado:
            flash("Este e-mail já tem cadastro. Tente entrar.", "erro")
            return render_template("cadastro.html", nome=nome, email=email)

        session["cliente_id"] = cliente["_id"]
        session["cliente_nome"] = cliente["nome"]
        # Token novo depois de autenticar: se alguém tivesse plantado uma
        # sessão no navegador da vítima, ela deixa de valer agora.
        renovar_token_csrf()
        flash(f"Bem-vindo, {cliente['nome']}.", "sucesso")
        return redirect(url_for("catalogo"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")

        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""
        cliente = buscar_cliente_por_email(email)

        # Mensagem única para e-mail inexistente e senha errada: dizer qual
        # dos dois falhou entregaria a um atacante a lista de quem tem conta.
        if not cliente or not check_password_hash(cliente["senha_hash"], senha):
            flash("E-mail ou senha inválidos.", "erro")
            return render_template("login.html", email=email)

        session["cliente_id"] = cliente["_id"]
        session["cliente_nome"] = cliente["nome"]
        renovar_token_csrf()
        flash(f"Bem-vindo de volta, {cliente['nome']}.", "sucesso")

        # Só aceita destino interno: `proximo` vem da URL e poderia mandar o
        # cliente para fora do site (open redirect).
        proximo = request.args.get("proximo", "")
        if proximo.startswith("/") and not proximo.startswith("//"):
            return redirect(proximo)
        return redirect(url_for("catalogo"))

    @app.route("/sair")
    def sair():
        session.clear()
        flash("Você saiu da sua conta.", "sucesso")
        return redirect(url_for("catalogo"))

    # -----------------------------------------------------------------
    # RF03 — Carrinho
    # -----------------------------------------------------------------
    @app.route("/carrinho")
    def carrinho():
        linhas, total = carrinho_detalhado()
        return render_template("carrinho.html", linhas=linhas, total=total)

    @app.route("/carrinho/adicionar/<slug_produto>", methods=["POST"])
    def adicionar_ao_carrinho(slug_produto: str):
        doc = banco().obter_ou_none(f"produto:{slug_produto}")
        if doc is None or not doc.get("ativo"):
            flash("Este café não está mais disponível.", "erro")
            return redirect(url_for("catalogo"))

        moagem = request.form.get("moagem", "")
        if moagem not in MOAGENS:
            flash("Escolha uma moagem válida.", "erro")
            return redirect(url_for("produto", slug_produto=slug_produto))

        try:
            quantidade = int(request.form.get("quantidade", 1))
        except ValueError:
            quantidade = 0

        if quantidade < 1:
            flash("A quantidade precisa ser pelo menos 1.", "erro")
            return redirect(url_for("produto", slug_produto=slug_produto))

        linhas = ler_carrinho()

        # A chave é o par (produto, moagem): meio quilo em grão e meio quilo
        # moído fino são duas linhas do mesmo café. É a mesma regra que a
        # validate_doc_update aplica aos itens do pedido.
        for linha in linhas:
            if linha["produto_id"] == doc["_id"] and linha["moagem"] == moagem:
                linha["quantidade"] += quantidade
                break
        else:
            linhas.append({"produto_id": doc["_id"], "quantidade": quantidade, "moagem": moagem})

        gravar_carrinho(linhas)
        flash(f"{doc['nome']} adicionado ao carrinho.", "sucesso")
        return redirect(url_for("carrinho"))

    @app.route("/carrinho/remover", methods=["POST"])
    def remover_do_carrinho():
        produto_id = request.form.get("produto_id", "")
        moagem = request.form.get("moagem", "")
        linhas = [
            linha
            for linha in ler_carrinho()
            if not (linha["produto_id"] == produto_id and linha["moagem"] == moagem)
        ]
        gravar_carrinho(linhas)
        flash("Item removido do carrinho.", "sucesso")
        return redirect(url_for("carrinho"))

    @app.route("/carrinho/limpar", methods=["POST"])
    def limpar_carrinho():
        gravar_carrinho([])
        flash("Carrinho esvaziado.", "sucesso")
        return redirect(url_for("carrinho"))

    # -----------------------------------------------------------------
    # RF05 — Checkout
    # -----------------------------------------------------------------
    @app.route("/checkout", methods=["GET", "POST"])
    @login_obrigatorio
    def checkout():
        linhas, total = carrinho_detalhado()
        if not linhas:
            flash("Seu carrinho está vazio.", "erro")
            return redirect(url_for("catalogo"))

        # Chave de idempotência da compra. Nasce quando o cliente abre a tela
        # de confirmação e vira o _id do pedido: um segundo clique em
        # "Confirmar" tenta gravar o mesmo _id e não gera pedido em dobro.
        chave = session.setdefault("checkout_chave", uuid.uuid4().hex)

        if request.method == "GET":
            return render_template("checkout.html", linhas=linhas, total=total)

        try:
            pedido = finalizar_pedido(session["cliente_id"], ler_carrinho(), chave=chave)
        except PedidoDuplicado as duplicado:
            session.pop("checkout_chave", None)
            if duplicado.pedido["status"] == "CANCELADO":
                flash("Aquela tentativa de compra foi cancelada. Confira o carrinho e tente de novo.", "erro")
                return redirect(url_for("carrinho"))
            gravar_carrinho([])
            session["ultimo_pedido"] = duplicado.pedido["_id"]
            flash(f"O pedido #{numero_pedido(duplicado.pedido['_id'])} já tinha sido registrado.", "sucesso")
            return redirect(url_for("meus_pedidos"))
        except ErroCheckout as erro:
            # O carrinho continua intacto de propósito: o cliente corrige a
            # quantidade e tenta de novo sem remontar a compra.
            session.pop("checkout_chave", None)
            flash(str(erro), "erro")
            return redirect(url_for("carrinho"))

        session.pop("checkout_chave", None)
        gravar_carrinho([])
        session["ultimo_pedido"] = pedido["_id"]
        flash(f"Pedido #{numero_pedido(pedido['_id'])} confirmado.", "sucesso")
        return redirect(url_for("meus_pedidos"))

    # -----------------------------------------------------------------
    # RF06 — Meus pedidos
    # -----------------------------------------------------------------
    @app.route("/meus-pedidos")
    @login_obrigatorio
    def meus_pedidos():
        b = banco()
        pedidos = b.buscar(consulta_pedidos_do_cliente(session["cliente_id"]))

        # Ler a própria escrita. O Cloudant é um cluster eventualmente
        # consistente: a consulta ao índice pode ser respondida por uma cópia
        # que ainda não recebeu a gravação, e o pedido recém-feito sumiria da
        # tela por um instante. O GET pelo _id lê por quórum e vai direto ao
        # documento, então o último pedido entra garantido por fora do índice.
        ultimo = session.get("ultimo_pedido")
        if ultimo and all(p["_id"] != ultimo for p in pedidos):
            doc = b.obter_ou_none(ultimo)
            if doc and doc["cliente_id"] == session["cliente_id"]:
                pedidos.insert(0, doc)

        return render_template("meus_pedidos.html", pedidos=pedidos)


app = criar_app()


if __name__ == "__main__":
    app.run(debug=True)
