"""Construtores de documentos e utilidades compartilhadas pelos testes."""

import re


def produto_de_teste(
    slug: str,
    nome: str,
    *,
    preco_centavos: int,
    estoque: int,
    categoria: str = "chapada-diamantina",
    ativo: bool = True,
) -> dict:
    return {
        "_id": f"produto:{slug}",
        "tipo": "produto",
        "nome": nome,
        "descricao": "Café de teste.",
        "preco_centavos": preco_centavos,
        "estoque": estoque,
        "torra": "CLARA",
        "nota_sensorial": "Mel, jasmim",
        "pontuacao_sca": 89.25,
        "peso_g": 250,
        "ativo": ativo,
        "categoria_id": f"categoria:{categoria}",
        "categoria": {"nome": "Chapada Diamantina", "regiao": "Bahia"},
        "reservas": {},
        "versao_esquema": 1,
    }


def pedido_de_teste(pedido_id: str, cliente_id: str, produto: dict, quantidade: int, **extra) -> dict:
    """Um pedido montado à mão, no formato que a saga grava."""
    pedido = {
        "_id": pedido_id,
        "tipo": "pedido",
        "cliente_id": cliente_id,
        "status": "PENDENTE",
        "itens": [
            {
                "produto_id": produto["_id"],
                "nome": produto["nome"],
                "moagem": "MEDIA",
                "quantidade": quantidade,
                "preco_unitario_centavos": produto["preco_centavos"],
            }
        ],
        "total_centavos": quantidade * produto["preco_centavos"],
        "criado_em": "2026-01-01T12:00:00.000+00:00",
        "historico": [{"status": "PENDENTE", "em": "2026-01-01T12:00:00.000+00:00"}],
        "versao_esquema": 1,
    }
    pedido.update(extra)
    return pedido


def linha(produto: dict, quantidade: int, moagem: str = "MEDIA") -> dict:
    """Uma linha de carrinho, no formato da sessão."""
    return {"produto_id": produto["_id"], "quantidade": quantidade, "moagem": moagem}


def extrair_token(html: str) -> str:
    achado = re.search(r'name="_csrf" value="([^"]+)"', html)
    assert achado, "o formulário deveria trazer o campo _csrf"
    return achado.group(1)
