"""
Cliente HTTP do CouchDB.

Toda conversa com o banco passa por este arquivo — a dica 1 do material:
"centralize chamadas HTTP". O CouchDB não precisa de driver: a API dele é o
próprio HTTP, e cada operação é um verbo sobre uma URL.

    GET    /torra_terra/produto:chapada-geisha    lê um documento
    PUT    /torra_terra/produto:chapada-geisha    grava, exigindo o _rev atual
    POST   /torra_terra/_find                     consulta Mango
    POST   /torra_terra/_index                    cria índice Mango
    POST   /torra_terra/_bulk_docs                grava vários documentos

O mesmo código fala com o CouchDB do docker-compose e com o IBM Cloudant,
que implementa a mesma API.
"""

import copy
import json
import logging
import random
import time
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import requests

log = logging.getLogger("torra_terra.banco")

# (conexão, leitura), em segundos. A conexão curta faz a loja desistir rápido
# quando o banco está fora; a leitura mais longa acomoda um _find que precisa
# atualizar o índice antes de responder.
TIMEOUT_PADRAO = (3.05, 10)

# Quantas vezes uma requisição é tentada quando o Cloudant recusa por limite
# de vazão (429) ou, se ela só lê, quando a rede falha ou o servidor dá 5xx.
TENTATIVAS_HTTP = 4


# ---------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------

class ErroBanco(Exception):
    """Resposta de erro do CouchDB, já traduzida."""

    def __init__(self, status: int, erro: str, motivo: str = ""):
        self.status = status
        self.erro = erro
        self.motivo = motivo
        super().__init__(f"CouchDB respondeu {status} {erro}: {motivo}")


class NaoEncontrado(ErroBanco):
    """404 — documento ou banco que não existe, ou que foi apagado."""


class Conflito(ErroBanco):
    """409 — o _rev enviado não é mais o atual.

    Não é um erro misterioso: é o CouchDB avisando que outra gravação chegou
    antes. Quem recebe relê o documento e decide de novo.
    """


class Recusado(ErroBanco):
    """403 — a validate_doc_update do banco recusou o documento."""


class BancoIndisponivel(ErroBanco):
    """Sem resposta utilizável: rede, timeout, 5xx, 429 persistente ou credencial errada."""


# ---------------------------------------------------------------------
# Autenticação IAM (Cloudant)
# ---------------------------------------------------------------------

class TokenIAM:
    """Autenticação por IAM, para instâncias do Cloudant sem credencial legada.

    Troca a API key por um token de acesso, que vale 60 minutos, e renova com
    um minuto de folga. Usa uma sessão HTTP própria de propósito: a sessão do
    banco pode carregar usuário e senha, e eles não devem viajar para o IAM.
    """

    URL = "https://iam.cloud.ibm.com/identity/token"

    def __init__(self, apikey: str, sessao: requests.Session | None = None):
        self._apikey = apikey
        self._sessao = sessao or requests.Session()
        self._token: str | None = None
        self._expira_em = 0.0

    def cabecalho(self) -> str:
        if self._token is None or time.time() > self._expira_em - 60:
            try:
                resposta = self._sessao.post(
                    self.URL,
                    data={
                        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                        "apikey": self._apikey,
                    },
                    headers={"Accept": "application/json"},
                    timeout=TIMEOUT_PADRAO,
                )
            except requests.RequestException as erro:
                raise BancoIndisponivel(0, "iam", "sem resposta do IAM") from erro
            if resposta.status_code != 200:
                raise BancoIndisponivel(
                    resposta.status_code, "iam", "a API key foi recusada pelo IAM"
                )
            dados = resposta.json()
            self._token = dados["access_token"]
            self._expira_em = time.time() + int(dados.get("expires_in", 3600))
        return f"Bearer {self._token}"

    def invalidar(self) -> None:
        self._token = None


# ---------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------

class CouchDB:
    """Um banco do CouchDB, acessado por HTTP."""

    def __init__(
        self,
        url: str,
        banco: str,
        *,
        apikey_iam: str | None = None,
        sessao: requests.Session | None = None,
        timeout=TIMEOUT_PADRAO,
    ):
        partes = urlsplit(url)
        self.sessao = sessao or requests.Session()

        # As credenciais saem da URL e vão para a sessão. Assim o endereço que
        # aparece em log e em mensagem de erro nunca carrega a senha.
        if partes.username is not None:
            self.sessao.auth = (
                unquote(partes.username),
                unquote(partes.password or ""),
            )

        endereco = partes.hostname or ""
        if partes.port:
            endereco += f":{partes.port}"
        self.url_servidor = urlunsplit(
            (partes.scheme, endereco, partes.path.rstrip("/"), "", "")
        )
        self.banco = banco
        self.url_banco = f"{self.url_servidor}/{quote(banco, safe='')}"
        self.timeout = timeout
        self._iam = TokenIAM(apikey_iam) if apikey_iam else None

    # --- transporte -----------------------------------------------------

    def _requisitar(
        self,
        metodo: str,
        url: str,
        *,
        corpo=None,
        parametros: dict | None = None,
        somente_leitura: bool = False,
    ):
        tentativa = 0
        renovou_token = False

        while True:
            tentativa += 1
            cabecalhos = {"Accept": "application/json"}
            if self._iam:
                cabecalhos["Authorization"] = self._iam.cabecalho()

            try:
                resposta = self.sessao.request(
                    metodo,
                    url,
                    json=corpo,
                    params=parametros,
                    headers=cabecalhos,
                    timeout=self.timeout,
                )
            except requests.RequestException as erro:
                # Uma escrita que ficou sem resposta pode ter sido gravada ou
                # não. Repetir às cegas duplicaria o efeito, então só a
                # leitura é repetida aqui. Na escrita, quem chamou decide — no
                # checkout, as marcas de reserva dizem o que de fato entrou.
                if somente_leitura and tentativa < TENTATIVAS_HTTP:
                    self._esperar(tentativa)
                    continue
                raise BancoIndisponivel(
                    0, type(erro).__name__, "sem resposta do CouchDB"
                ) from erro

            if resposta.status_code == 401 and self._iam and not renovou_token:
                self._iam.invalidar()
                renovou_token = True
                continue

            # 429: o Cloudant recusou por limite de vazão ANTES de processar.
            # Nada foi gravado, então repetir é seguro até para escrita.
            if resposta.status_code == 429 and tentativa < TENTATIVAS_HTTP:
                self._esperar(tentativa, resposta.headers.get("Retry-After"))
                continue

            if (
                resposta.status_code >= 500
                and somente_leitura
                and tentativa < TENTATIVAS_HTTP
            ):
                self._esperar(tentativa)
                continue

            return self._interpretar(resposta)

    @staticmethod
    def _esperar(tentativa: int, retry_after: str | None = None) -> None:
        if retry_after and retry_after.isdigit():
            time.sleep(min(int(retry_after), 5))
            return
        # Espera exponencial com sorteio: dois clientes que colidiram não
        # voltam no mesmo instante para colidir de novo.
        time.sleep(random.uniform(0, 0.05 * 2**tentativa))

    @staticmethod
    def _interpretar(resposta: requests.Response):
        try:
            dados = resposta.json() if resposta.content else {}
        except ValueError:
            dados = {"error": "resposta_invalida", "reason": resposta.text[:200]}

        if resposta.status_code == 202:
            # Num cluster (o Cloudant é um), 202 quer dizer que a gravação foi
            # aceita sem confirmação da maioria das cópias. Duas gravações
            # quase simultâneas no mesmo documento podem terminar assim, e
            # ficar as duas guardadas como revisões em conflito — em vez do
            # 409 que um CouchDB de nó único daria. Fica no log para
            # investigar; ver docs/checkout_saga.md.
            log.warning("gravação aceita sem quórum (HTTP 202) em %s", resposta.request.path_url.split("?")[0])
        if resposta.status_code < 400:
            return dados

        erro = dados.get("error", "erro") if isinstance(dados, dict) else "erro"
        motivo = dados.get("reason", "") if isinstance(dados, dict) else ""
        classe = {
            401: BancoIndisponivel,
            403: Recusado,
            404: NaoEncontrado,
            409: Conflito,
        }.get(resposta.status_code)
        if classe is None:
            classe = (
                BancoIndisponivel
                if resposta.status_code == 429 or resposta.status_code >= 500
                else ErroBanco
            )
        raise classe(resposta.status_code, erro, motivo)

    def _url_documento(self, doc_id: str) -> str:
        # O _id vai codificado inteiro: "produto:x" vira "produto%3Ax", e um
        # e-mail com "+" ou "/" não quebra a URL. Design documents são a
        # exceção — o CouchDB espera a barra de "_design/" literal.
        if doc_id.startswith("_design/"):
            return f"{self.url_banco}/_design/{quote(doc_id[8:], safe='')}"
        return f"{self.url_banco}/{quote(doc_id, safe='')}"

    # --- servidor e banco -----------------------------------------------

    def info_servidor(self) -> dict:
        return self._requisitar("GET", f"{self.url_servidor}/", somente_leitura=True)

    def info_banco(self) -> dict:
        return self._requisitar("GET", self.url_banco, somente_leitura=True)

    def criar_banco(self) -> bool:
        """Cria o banco. Devolve False se ele já existia."""
        try:
            self._requisitar("PUT", self.url_banco)
            return True
        except ErroBanco as erro:
            if erro.status == 412:  # file_exists
                return False
            raise

    def apagar_banco(self) -> None:
        try:
            self._requisitar("DELETE", self.url_banco)
        except NaoEncontrado:
            pass

    # --- documentos -----------------------------------------------------

    def obter(self, doc_id: str) -> dict:
        return self._requisitar("GET", self._url_documento(doc_id), somente_leitura=True)

    def obter_ou_none(self, doc_id: str) -> dict | None:
        try:
            return self.obter(doc_id)
        except NaoEncontrado:
            return None

    def obter_varios(self, ids) -> dict[str, dict | None]:
        """Vários documentos numa ida só, pelo índice primário (_all_docs)."""
        ids = list(ids)
        if not ids:
            return {}
        resposta = self._requisitar(
            "POST",
            f"{self.url_banco}/_all_docs",
            corpo={"keys": ids},
            parametros={"include_docs": "true"},
            somente_leitura=True,
        )
        encontrados: dict[str, dict | None] = {doc_id: None for doc_id in ids}
        for linha in resposta.get("rows", []):
            if linha.get("doc"):
                encontrados[linha["key"]] = linha["doc"]
        return encontrados

    def salvar(self, doc: dict) -> dict:
        """Grava o documento. Se ele já existe, o _rev precisa ser o atual."""
        resposta = self._requisitar("PUT", self._url_documento(doc["_id"]), corpo=doc)
        doc["_rev"] = resposta["rev"]
        return doc

    def apagar(self, doc_id: str, rev: str) -> None:
        self._requisitar("DELETE", self._url_documento(doc_id), parametros={"rev": rev})

    def gravar_lote(self, docs: list[dict]) -> list[dict]:
        """_bulk_docs: vários documentos numa requisição.

        Lote NÃO é transação. Cada documento é aceito ou recusado sozinho, e a
        resposta traz um resultado por documento, na mesma ordem do envio. Um
        HTTP 201 não quer dizer que todos entraram — é preciso ler um por um.
        """
        if not docs:
            return []
        return self._requisitar("POST", f"{self.url_banco}/_bulk_docs", corpo={"docs": docs})

    # --- consultas ------------------------------------------------------

    def buscar(self, consulta: dict) -> list[dict]:
        """Consulta Mango (POST /_find). `consulta` é o corpo JSON inteiro."""
        resposta = self._requisitar(
            "POST", f"{self.url_banco}/_find", corpo=consulta, somente_leitura=True
        )
        if resposta.get("warning"):
            # O CouchDB avisa quando não achou índice e varreu o banco. Fora
            # das consultas de manutenção, isso é sinal de índice faltando.
            log.info("aviso do CouchDB para %s: %s", consulta.get("selector"), resposta["warning"])
        return resposta.get("docs", [])

    def explicar(self, consulta: dict) -> dict:
        """POST /_explain: qual índice o CouchDB usaria para a consulta."""
        return self._requisitar(
            "POST", f"{self.url_banco}/_explain", corpo=consulta, somente_leitura=True
        )

    def listar_por_prefixo(self, prefixo: str, *, com_documentos: bool = True) -> list[dict]:
        """Faixa do índice primário: todo _id que começa com o prefixo.

        É o ganho de escolher _id legível com o tipo na frente: listar as
        categorias não precisa de índice secundário nenhum.
        """
        parametros = {
            "startkey": json.dumps(prefixo),
            "endkey": json.dumps(prefixo + "￰"),
            "include_docs": "true" if com_documentos else "false",
        }
        resposta = self._requisitar(
            "GET", f"{self.url_banco}/_all_docs", parametros=parametros, somente_leitura=True
        )
        linhas = resposta.get("rows", [])
        if com_documentos:
            return [linha["doc"] for linha in linhas if linha.get("doc")]
        return [{"_id": linha["id"], "_rev": linha["value"]["rev"]} for linha in linhas]

    def criar_indice(self, definicao: dict) -> dict:
        return self._requisitar("POST", f"{self.url_banco}/_index", corpo=definicao)

    # --- ler, alterar, gravar -------------------------------------------

    def atualizar(self, doc_id: str, mutacao, *, doc: dict | None = None, tentativas: int = 6) -> dict:
        """Lê, altera e grava um documento, tentando de novo quando dá 409.

        É o ciclo do _rev: GET, altera o JSON, PUT com o _rev lido, e se
        alguém gravou antes, 409 — relê e decide de novo. `mutacao(doc)`
        devolve o documento alterado, ou None quando não há nada a fazer.
        """
        atual = copy.deepcopy(doc) if doc is not None else self.obter(doc_id)
        tentativa = 0
        while True:
            tentativa += 1
            novo = mutacao(copy.deepcopy(atual))
            if novo is None:
                return atual
            try:
                return self.salvar(novo)
            except Conflito:
                if tentativa >= tentativas:
                    raise
                self._esperar(tentativa)
                atual = self.obter(doc_id)

    def atualizar_varios(
        self,
        ids,
        mutacao,
        *,
        docs: dict | None = None,
        tentativas: int = 6,
        ignorar_ausentes: bool = False,
    ) -> dict[str, dict]:
        """O mesmo ciclo para vários documentos, com um _bulk_docs por rodada.

        Os documentos que voltam do lote com conflito são relidos e entram na
        rodada seguinte. Uma exceção levantada pela `mutacao` interrompe tudo
        — e o que entrou nas rodadas anteriores continua gravado, porque lote
        não é transação. Quem chama precisa saber desfazer.
        """
        atuais = {
            doc_id: copy.deepcopy(lido)
            for doc_id, lido in (docs or {}).items()
            if lido is not None
        }
        faltam = list(ids)
        tentativa = 0

        while faltam:
            tentativa += 1

            sem_leitura = [doc_id for doc_id in faltam if doc_id not in atuais]
            for doc_id, lido in self.obter_varios(sem_leitura).items():
                if lido is not None:
                    atuais[doc_id] = lido
                elif ignorar_ausentes:
                    faltam.remove(doc_id)
                else:
                    raise NaoEncontrado(404, "not_found", doc_id)

            lote = []
            for doc_id in faltam:
                novo = mutacao(copy.deepcopy(atuais[doc_id]))
                if novo is not None:
                    lote.append(novo)

            conflitos = []
            for novo, resultado in zip(lote, self.gravar_lote(lote)):
                if "error" not in resultado:
                    novo["_rev"] = resultado["rev"]
                    atuais[novo["_id"]] = novo
                elif resultado["error"] == "conflict":
                    conflitos.append(novo["_id"])
                    del atuais[novo["_id"]]  # relido na próxima rodada
                elif resultado["error"] == "forbidden":
                    raise Recusado(403, "forbidden", resultado.get("reason", ""))
                else:
                    raise ErroBanco(500, resultado["error"], resultado.get("reason", ""))

            if conflitos:
                if tentativa >= tentativas:
                    raise Conflito(409, "conflict", "conflito persistente em " + ", ".join(conflitos))
                self._esperar(tentativa)
            faltam = conflitos

        return {doc_id: atuais[doc_id] for doc_id in ids if doc_id in atuais}
