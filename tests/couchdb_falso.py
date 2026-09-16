"""
Dublê do CouchDB em memória, para os testes rodarem sem Docker.

Implementa o pedaço da API HTTP que a loja usa — documentos com _rev e 409,
_all_docs, _bulk_docs, _find, _explain e _index — com a semântica do
CouchDB 3. Ele entra no lugar da rede: é um adaptador do `requests`, então o
banco.py inteiro roda de verdade por cima dele, da montagem das URLs à
tradução dos erros.

O que ele NÃO faz: executar a validate_doc_update, que é JavaScript. Os
testes das regras do banco são marcados `couchdb_real` e rodam no GitHub
Actions contra um Apache CouchDB de verdade — junto com a suíte inteira.
"""

import copy
import hashlib
import json
import unicodedata
import uuid
from urllib.parse import parse_qsl, unquote, urlsplit

from requests.adapters import BaseAdapter
from requests.models import Response
from requests.structures import CaseInsensitiveDict

CONFLITO = {"error": "conflict", "reason": "Document update conflict."}


class CouchDBFalso(BaseAdapter):
    def __init__(self):
        super().__init__()
        self.bancos: dict[str, dict] = {}

    # --- transporte ------------------------------------------------------

    def send(self, request, **kwargs):
        partes = urlsplit(request.url)
        segmentos = [unquote(parte) for parte in partes.path.split("/") if parte]
        parametros = dict(parse_qsl(partes.query))
        corpo = json.loads(request.body) if request.body else None
        status, dados = self._rotear(request.method, segmentos, parametros, corpo)

        resposta = Response()
        resposta.status_code = status
        resposta._content = json.dumps(dados).encode("utf-8")
        resposta.headers = CaseInsensitiveDict({"Content-Type": "application/json"})
        resposta.encoding = "utf-8"
        resposta.url = request.url
        resposta.request = request
        return resposta

    def close(self):
        pass

    def _rotear(self, metodo, segmentos, parametros, corpo):
        if not segmentos:
            return 200, {"couchdb": "Welcome", "version": "3-falso"}

        nome = segmentos[0]
        if len(segmentos) == 1:
            return self._banco(metodo, nome)
        if nome not in self.bancos:
            return 404, {"error": "not_found", "reason": "Database does not exist."}

        banco = self.bancos[nome]
        recurso = segmentos[1]
        if recurso == "_design":
            return self._documento(banco, metodo, "_design/" + "/".join(segmentos[2:]), parametros, corpo)
        if recurso == "_all_docs":
            return self._all_docs(banco, parametros, corpo)
        if recurso == "_bulk_docs":
            return self._bulk_docs(banco, corpo)
        if recurso == "_find":
            return self._find(banco, corpo, explicar=False)
        if recurso == "_explain":
            return self._find(banco, corpo, explicar=True)
        if recurso == "_index":
            return self._index(banco, corpo)
        return self._documento(banco, metodo, "/".join(segmentos[1:]), parametros, corpo)

    # --- banco -------------------------------------------------------------

    def _banco(self, metodo, nome):
        if metodo == "PUT":
            if nome in self.bancos:
                return 412, {"error": "file_exists", "reason": "The database could not be created."}
            self.bancos[nome] = {"docs": {}, "indices": []}
            return 201, {"ok": True}
        if nome not in self.bancos:
            return 404, {"error": "not_found", "reason": "Database does not exist."}
        if metodo == "DELETE":
            del self.bancos[nome]
            return 200, {"ok": True}
        vivos = sum(1 for registro in self.bancos[nome]["docs"].values() if not registro["apagado"])
        return 200, {"db_name": nome, "doc_count": vivos}

    # --- documentos -------------------------------------------------------

    @staticmethod
    def _gravar(banco, doc):
        """PUT de um documento com a regra do _rev. Devolve (status, resultado)."""
        doc = copy.deepcopy(doc)
        doc_id = doc.get("_id") or uuid.uuid4().hex
        rev_enviada = doc.pop("_rev", None)
        registro = banco["docs"].get(doc_id)

        if registro is None:
            # _rev para um documento que nunca existiu: o CouchDB responde 409.
            if rev_enviada is not None:
                return 409, {"id": doc_id, **CONFLITO}
            geracao = 0
        elif registro["apagado"]:
            # Documento apagado pode renascer sem _rev.
            if rev_enviada is not None and rev_enviada != registro["rev"]:
                return 409, {"id": doc_id, **CONFLITO}
            geracao = registro["geracao"]
        else:
            if rev_enviada != registro["rev"]:
                return 409, {"id": doc_id, **CONFLITO}
            geracao = registro["geracao"]

        geracao += 1
        apagado = bool(doc.pop("_deleted", False))
        doc.pop("_id", None)
        conteudo = json.dumps([doc_id, geracao, apagado, doc], sort_keys=True)
        rev = f"{geracao}-{hashlib.md5(conteudo.encode('utf-8')).hexdigest()}"

        salvo = {"_id": doc_id, "_rev": rev}
        if apagado:
            salvo["_deleted"] = True
        else:
            salvo.update(doc)

        banco["docs"][doc_id] = {"rev": rev, "geracao": geracao, "apagado": apagado, "doc": salvo}
        return 201, {"ok": True, "id": doc_id, "rev": rev}

    def _documento(self, banco, metodo, doc_id, parametros, corpo):
        registro = banco["docs"].get(doc_id)

        if metodo == "GET":
            if registro is None:
                return 404, {"error": "not_found", "reason": "missing"}
            if registro["apagado"]:
                return 404, {"error": "not_found", "reason": "deleted"}
            return 200, copy.deepcopy(registro["doc"])

        if metodo == "PUT":
            status, resultado = self._gravar(banco, {**(corpo or {}), "_id": doc_id})
            if status == 409:
                return 409, dict(CONFLITO)
            return status, resultado

        if metodo == "DELETE":
            if registro is None or registro["apagado"]:
                return 404, {"error": "not_found", "reason": "deleted" if registro else "missing"}
            if parametros.get("rev") != registro["rev"]:
                return 409, dict(CONFLITO)
            _, resultado = self._gravar(banco, {"_id": doc_id, "_rev": registro["rev"], "_deleted": True})
            return 200, resultado

        return 405, {"error": "method_not_allowed", "reason": metodo}

    def _all_docs(self, banco, parametros, corpo):
        incluir = parametros.get("include_docs") == "true"
        linhas = []

        def linha_de(doc_id, registro):
            linha = {"id": doc_id, "key": doc_id, "value": {"rev": registro["rev"]}}
            if registro["apagado"]:
                linha["value"]["deleted"] = True
            if incluir:
                linha["doc"] = None if registro["apagado"] else copy.deepcopy(registro["doc"])
            return linha

        if corpo and "keys" in corpo:
            for chave in corpo["keys"]:
                registro = banco["docs"].get(chave)
                linhas.append({"key": chave, "error": "not_found"} if registro is None else linha_de(chave, registro))
        else:
            inicio = json.loads(parametros["startkey"]) if "startkey" in parametros else None
            fim = json.loads(parametros["endkey"]) if "endkey" in parametros else None
            # _all_docs ordena pelo código dos caracteres, não pela colação ICU.
            for doc_id in sorted(banco["docs"]):
                registro = banco["docs"][doc_id]
                if registro["apagado"]:
                    continue
                if (inicio is not None and doc_id < inicio) or (fim is not None and doc_id > fim):
                    continue
                linhas.append(linha_de(doc_id, registro))

        return 200, {"total_rows": len(banco["docs"]), "offset": 0, "rows": linhas}

    def _bulk_docs(self, banco, corpo):
        resultados = []
        for doc in (corpo or {}).get("docs", []):
            status, resultado = self._gravar(banco, doc)
            if status == 409:
                resultados.append({"id": resultado["id"], **CONFLITO})
            else:
                resultados.append(resultado)
        # 201 mesmo com documentos recusados: lote não é transação.
        return 201, resultados

    # --- índices e consultas ------------------------------------------------

    @staticmethod
    def _nome_do_campo(campo):
        return campo if isinstance(campo, str) else next(iter(campo))

    def _index(self, banco, corpo):
        campos = [self._nome_do_campo(c) for c in corpo["index"]["fields"]]
        assinatura = hashlib.sha1(json.dumps(campos).encode()).hexdigest()
        ddoc = corpo.get("ddoc") or assinatura
        if not ddoc.startswith("_design/"):
            ddoc = "_design/" + ddoc
        nome = corpo.get("name") or assinatura

        for indice in banco["indices"]:
            if indice["ddoc"] == ddoc and indice["name"] == nome:
                resultado = "exists" if indice["fields"] == campos else "created"
                indice["fields"] = campos
                return 200, {"result": resultado, "id": ddoc, "name": nome}

        banco["indices"].append({"ddoc": ddoc, "name": nome, "fields": campos})
        self._gravar(banco, {"_id": ddoc, "language": "query", "views": {nome: {"options": {"def": {"fields": campos}}}}})
        return 200, {"result": "created", "id": ddoc, "name": nome}

    def _find(self, banco, corpo, explicar):
        corpo = corpo or {}
        seletor = corpo.get("selector")
        if not isinstance(seletor, dict):
            return 400, {"error": "bad_request", "reason": "invalid_selector_json"}

        ordem = []
        for item in corpo.get("sort", []):
            ordem.append((item, "asc") if isinstance(item, str) else next(iter(item.items())))
        if len({direcao for _, direcao in ordem}) > 1:
            return 400, {"error": "unsupported_mixed_sort", "reason": "Sorts currently only support a single direction for all fields."}
        campos_ordem = [campo for campo, _ in ordem]

        indice = self._escolher_indice(banco, seletor, campos_ordem, corpo.get("use_index"))
        if campos_ordem and indice is None:
            return 400, {"error": "no_usable_index", "reason": "No index exists for this sort, try indexing by the sort fields."}

        limite = corpo.get("limit", 25)
        pular = corpo.get("skip", 0)

        if explicar:
            if indice is None:
                descricao = {"ddoc": None, "name": "_all_docs", "type": "special", "def": {"fields": [{"_id": "asc"}]}}
            else:
                descricao = {"ddoc": indice["ddoc"], "name": indice["name"], "type": "json",
                             "def": {"fields": [{campo: "asc"} for campo in indice["fields"]]}}
            return 200, {"index": descricao, "selector": seletor, "limit": limite, "skip": pular}

        docs = [
            copy.deepcopy(registro["doc"])
            for doc_id, registro in banco["docs"].items()
            if not registro["apagado"] and not doc_id.startswith("_design/")
        ]
        if indice is not None:
            # Um índice JSON só contém documentos que têm TODOS os campos indexados.
            docs = [doc for doc in docs if all(self._campo(doc, campo)[0] for campo in indice["fields"])]
        docs = [doc for doc in docs if self._casa(seletor, doc)]

        if ordem:
            docs.sort(
                key=lambda doc: [self._colacao(self._campo(doc, campo)[1]) for campo in campos_ordem],
                reverse=ordem[0][1] == "desc",
            )
        docs = docs[pular:pular + limite]

        if corpo.get("fields"):
            docs = [{campo: doc[campo] for campo in corpo["fields"] if campo in doc} for doc in docs]

        resposta = {"docs": docs, "bookmark": "nil"}
        if indice is None:
            resposta["warning"] = "No matching index found, create an index to optimize query time."
        return 200, resposta

    def _escolher_indice(self, banco, seletor, campos_ordem, use_index):
        utilizaveis = [i for i in banco["indices"] if self._utilizavel(i, seletor, campos_ordem)]
        if use_index:
            ddoc, nome = (use_index, None) if isinstance(use_index, str) else (use_index[0], (use_index[1:] or [None])[0])
            if not ddoc.startswith("_design/"):
                ddoc = "_design/" + ddoc
            preferidos = [i for i in utilizaveis if i["ddoc"] == ddoc and nome in (None, i["name"])]
            if preferidos:
                return preferidos[0]
        return utilizaveis[0] if utilizaveis else None

    def _utilizavel(self, indice, seletor, campos_ordem):
        """A mesma regra do mango_idx_view:is_usable do CouchDB."""
        exigidos = self._campos_exigidos(seletor)
        for coluna in indice["fields"]:
            if coluna not in campos_ordem and coluna not in exigidos:
                return False
        return self._pode_ordenar(indice["fields"], campos_ordem, seletor)

    def _pode_ordenar(self, colunas, campos_ordem, seletor):
        if not campos_ordem:
            return True
        if not colunas:
            return False
        if colunas[0] == campos_ordem[0]:
            return colunas[: len(campos_ordem)] == campos_ordem
        condicao = seletor.get(colunas[0])
        constante = not isinstance(condicao, dict) or list(condicao) == ["$eq"]
        if colunas[0] in seletor and constante:
            return self._pode_ordenar(colunas[1:], campos_ordem, seletor)
        return False

    def _campos_exigidos(self, seletor):
        campos = set()
        for chave, condicao in seletor.items():
            if chave == "$and":
                for parte in condicao:
                    campos |= self._campos_exigidos(parte)
            elif not chave.startswith("$"):
                if isinstance(condicao, dict) and condicao.get("$exists") is False:
                    continue
                campos.add(chave)
        return campos

    # --- seletores ----------------------------------------------------------

    @staticmethod
    def _campo(doc, caminho):
        atual = doc
        for parte in caminho.split("."):
            if not isinstance(atual, dict) or parte not in atual:
                return False, None
            atual = atual[parte]
        return True, atual

    def _casa(self, seletor, doc):
        for chave, condicao in seletor.items():
            if chave == "$and":
                if not all(self._casa(parte, doc) for parte in condicao):
                    return False
            elif chave == "$or":
                if not any(self._casa(parte, doc) for parte in condicao):
                    return False
            elif chave == "$not":
                if self._casa(condicao, doc):
                    return False
            else:
                existe, valor = self._campo(doc, chave)
                operadores = (
                    condicao
                    if isinstance(condicao, dict) and condicao and all(op.startswith("$") for op in condicao)
                    else {"$eq": condicao}
                )
                for operador, esperado in operadores.items():
                    if not self._operar(operador, existe, valor, esperado):
                        return False
        return True

    def _operar(self, operador, existe, valor, esperado):
        if operador == "$exists":
            return existe == bool(esperado)
        if not existe:
            return False
        if operador == "$eq":
            return self._igual(valor, esperado)
        if operador == "$ne":
            return not self._igual(valor, esperado)
        if operador == "$in":
            return any(self._igual(valor, item) for item in esperado)
        if operador == "$nin":
            return not any(self._igual(valor, item) for item in esperado)
        a, b = self._colacao(valor), self._colacao(esperado)
        return {"$gt": a > b, "$gte": a >= b, "$lt": a < b, "$lte": a <= b}[operador]

    @staticmethod
    def _igual(a, b):
        def numero(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        if numero(a) and numero(b):
            return a == b
        return type(a) is type(b) and a == b

    @classmethod
    def _colacao(cls, valor):
        # Ordem de tipos do CouchDB: null < false < true < números < textos
        # < listas < objetos. Texto compara sem acento e sem caixa primeiro,
        # como a colação ICU faz.
        if valor is None:
            return (0,)
        if valor is False:
            return (1,)
        if valor is True:
            return (2,)
        if isinstance(valor, (int, float)):
            return (3, valor)
        if isinstance(valor, str):
            base = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode().lower()
            return (4, base, valor)
        if isinstance(valor, list):
            return (5, [cls._colacao(item) for item in valor])
        return (6, json.dumps(valor, sort_keys=True))
