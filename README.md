# Torra &amp; Terra — versão NoSQL

E-commerce de café especial em **Flask + Apache CouchDB**.

Projeto da disciplina **Tratamento e Armazenamento da Informação** — FACAMP,
Prof. Nivaldo T. Marcusso.

[![testes](https://github.com/lipefurlan/projeto-nivaldo-nosql/actions/workflows/testes.yml/badge.svg)](https://github.com/lipefurlan/projeto-nivaldo-nosql/actions/workflows/testes.yml)

É a mesma loja do projeto relacional, agora sobre um banco de documentos. O
objetivo é o do material: **comparar na prática o ciclo de uma aplicação
NoSQL com o do projeto relacional**. O lado PostgreSQL continua disponível:

- na tag [`v1-relacional`](https://github.com/lipefurlan/projeto-nivaldo-nosql/tree/v1-relacional) deste repositório, com todo o histórico;
- no repositório original, [hick12/projeto-nivaldo](https://github.com/hick12/projeto-nivaldo).

---

## O que ele faz

Catálogo de 12 cafés de origem única em 4 regiões produtoras. O cliente
filtra por região, escolhe **quantidade e moagem**, monta o carrinho, se
cadastra e finaliza a compra. O pedido guarda os itens com o preço da data da
compra, e o estoque baixa sem vender o mesmo lote duas vezes.

---

## O que mudou do PostgreSQL para o CouchDB

| No relacional | Aqui | Onde ver |
|---|---|---|
| 5 tabelas normalizadas | 5 tipos de documento JSON, com `tipo` e `_id` legível | [`couchdb/seed.json`](couchdb/seed.json) |
| `itens_pedido` com FK | itens **embutidos** no pedido, com nome e preço como *snapshot* | [`docs/modelo_documental.md`](docs/modelo_documental.md) |
| `CHECK` no DDL | `validate_doc_update`: o próprio CouchDB recusa o documento inválido | [`couchdb/validacao.js`](couchdb/validacao.js) |
| `UNIQUE (email)` | o `_id` de um documento `email:<endereço>` | [`app.py`](app.py) · `cadastrar_cliente` |
| `CREATE INDEX` | índices Mango, cada um justificado pela consulta | [`couchdb/indices.json`](couchdb/indices.json) |
| `SELECT ... FOR UPDATE` | controle otimista por `_rev`, nova tentativa no HTTP 409 | [`banco.py`](banco.py) |
| `COMMIT` / `ROLLBACK` | **saga**: reserva, confirmação e compensação | [`docs/checkout_saga.md`](docs/checkout_saga.md) |
| `NUMERIC(10,2)` | centavos inteiros — JSON não tem decimal | [`docs/modelo_documental.md`](docs/modelo_documental.md) |

A diferença que importa de verdade está no checkout. No PostgreSQL o banco
garantia o tudo-ou-nada. No CouchDB a unidade de consistência é **um
documento**, e `_bulk_docs` não é transação. O projeto-base do material avisa
o conflito, mas deixa gravado o que já tinha entrado. Aqui, se um café acaba
no meio da compra, o que foi reservado **volta para o estoque**, e há um
teste para cada jeito de falhar.

A comparação completa, lado a lado, está em
[`docs/comparacao_relacional_nosql.md`](docs/comparacao_relacional_nosql.md).

---

## Stack

Python 3.12 · Flask · Jinja · requests · python-dotenv · pytest

**Banco:** Apache CouchDB 3.5 na máquina local, IBM Cloudant em produção —
os dois falam a mesma API HTTP. **Deploy:** Vercel.

Sem driver: a API do CouchDB é o próprio HTTP, e toda chamada passa por
[`banco.py`](banco.py). CSS puro, sem framework.

---

## Rodar localmente

### 1. Pré-requisitos

Python 3.11+ e **um** CouchDB para apontar:

- **com Docker:** `docker compose up -d` sobe o CouchDB 3.5 do material, com o
  Fauxton em `http://127.0.0.1:5984/_utils/`;
- **sem Docker:** use a instância do Cloudant (ver
  [`docs/deploy_vercel.md`](docs/deploy_vercel.md)).

### 2. Instalar

```bash
python -m venv .venv
```

Ative o ambiente — `.venv\Scripts\activate` no Windows,
`source .venv/bin/activate` no Linux e no macOS. Depois:

```bash
pip install -r requirements-dev.txt
```

### 3. Configurar

```bash
cp .env.example .env
```

Com Docker, o `.env.example` já aponta para o CouchDB local. Com Cloudant,
troque o `COUCHDB_URL` pela URL da credencial. Gere a `SECRET_KEY` com:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

O `.env` está no `.gitignore` e **nunca** vai para o repositório.

### 4. Criar o banco e carregar o catálogo

```bash
flask --app app init-db
```

```bash
flask --app app seed-db
```

O `init-db` cria o banco, grava a `validate_doc_update` e os 4 índices Mango.
O `seed-db` grava as 4 regiões e os 12 cafés num único `_bulk_docs`. Os dois
podem rodar de novo sem duplicar nada.

### 5. Subir

```bash
flask --app app run --debug
```

A loja abre em `http://localhost:5000`.

---

## Variáveis de ambiente

| Variável | Obrigatória | Para que serve |
|---|:---:|---|
| `COUCHDB_URL` | sim | Endereço do CouchDB ou do Cloudant, com usuário e senha na URL. O `banco.py` tira as credenciais da URL antes de qualquer log |
| `COUCHDB_DATABASE` | não | Nome do banco. Padrão: `torra_terra` |
| `COUCHDB_IAM_APIKEY` | não | Só para instância do Cloudant sem credencial legada |
| `SECRET_KEY` | sim | Assina o cookie de sessão. Em produção a loja não sobe sem ela |
| `TEST_COUCHDB_URL` | não | CouchDB de verdade para o `pytest`. Sem ela, os testes usam o dublê em memória |

---

## Testes

```bash
pytest -v
```

A suíte roda de dois jeitos, com o mesmo código:

- **contra um CouchDB de verdade**, quando `TEST_COUCHDB_URL` está definida.
  Cada rodada cria um banco descartável e o apaga no fim. É assim que ela
  roda no GitHub Actions, com o Apache CouchDB 3.5 num container;
- **contra o dublê em memória** ([`tests/couchdb_falso.py`](tests/couchdb_falso.py)),
  quando não está. Ele substitui a rede, então o `banco.py` roda inteiro por
  cima dele. Os testes da `validate_doc_update` — JavaScript que só o CouchDB
  executa — são pulados nesse modo.

O que está coberto:

| Caso | O que prova |
|---|---|
| Compra normal | Pedido `CRIADO`, itens embutidos, estoque baixado, nenhuma marca pendurada |
| Estoque insuficiente | Nada gravado, estoque intacto, mensagem nomeando o café |
| Produto inexistente ou desativado | Pedido não é finalizado |
| 409 no meio do checkout | A saga relê o café e tenta de novo sem perder a baixa da outra compra |
| Café acaba no meio da saga | **Compensação:** pedido `CANCELADO`, estoque reservado volta |
| Timeout depois de gravar | As marcas de reserva dizem o que desfazer |
| Confirmação gravada sem resposta | A compra vale, nada é desfeito |
| Timeout ao registrar o pedido | O que entrou é cancelado na hora; nada fica "processando" sem reserva |
| Conflito que não para | Depois de 6 rodadas a saga desiste e estorna o que reservou |
| Compensação sem banco | O pedido fica pendente e `flask reconciliar` termina depois |
| Clique duplo em "Confirmar" | O `_id` do pedido barra a segunda compra, e um reenvio com o primeiro ainda em andamento não é dado como compra feita |
| Saga interrompida | `flask reconciliar` termina o serviço, sem devolver em dobro |
| Regras no banco | Preço negativo, SCA fora de 80–100, moagem inválida, senha em texto puro: o CouchDB responde 403 |
| Pedido gravado | Itens e preço congelado não podem mais mudar — regra do banco |
| E-mail único | Vale até com dois cadastros ao mesmo tempo, e quando a resposta do cadastro se perde |
| Índices | O `_explain` do CouchDB confirma o índice de cada consulta |
| Segurança | CSRF, cabeçalhos, cookie de sessão, redirecionamento pós-login |

---

## Comandos

| Comando | O que faz |
|---|---|
| `flask --app app init-db` | Cria o banco, a `validate_doc_update` e os índices |
| `flask --app app seed-db` | Grava as 4 regiões e os 12 cafés |
| `flask --app app reset-db` | Apaga e recria tudo. Só para desenvolvimento |
| `flask --app app reconciliar` | Termina checkouts interrompidos no meio. Idempotente |

---

## Estrutura

```
├── app.py                  configuração, regras, consultas, checkout, rotas e CLI
├── banco.py                cliente HTTP do CouchDB: _rev, 409, 429, credenciais
├── couchdb/
│   ├── validacao.js        validate_doc_update — as regras aplicadas pelo banco
│   ├── indices.json        os 4 índices Mango, cada um com a consulta que o justifica
│   └── seed.json           4 regiões e 12 cafés
├── templates/              base, catálogo, produto, cadastro, login, carrinho,
│                           checkout, meus_pedidos, indisponivel
├── public/static/style.css servido pelo CDN do Vercel
├── tests/                  suíte pytest e o dublê do CouchDB
├── docker-compose.yml      CouchDB 3.5 local
├── FAUXTON_ROTEIRO.md      roteiro de evidências no Fauxton
├── .github/workflows/      pytest contra CouchDB real a cada push
└── docs/
    ├── requisitos.md               RF, RNF, por que NoSQL, consultas antes do modelo
    ├── modelo_documental.md        documentos, embed x reference, onde foram as constraints
    ├── consultas_e_indices.md      catálogo de consultas e estratégia de índices
    ├── checkout_saga.md            a saga do checkout e a matriz de falhas
    ├── comparacao_relacional_nosql.md
    ├── decisoes.md                 escolhas de projeto justificadas
    ├── deploy_vercel.md            Cloudant + Vercel, passo a passo
    ├── plano_nosql.md              o plano de antes da migração
    └── cafes.json · cafes.csv      o catálogo de produção migrado
```

---

## Segurança

- Senhas apenas como **hash scrypt**; a `validate_doc_update` recusa até um
  documento de cliente que tente gravar senha em texto puro
- Credenciais do banco e `SECRET_KEY` só em variável de ambiente. O
  `banco.py` tira usuário e senha da URL, então eles não aparecem em log nem
  em mensagem de erro
- CSRF com token por sessão, cabeçalhos de segurança (CSP sem JavaScript,
  `X-Frame-Options`, HSTS), cookie `HttpOnly` e `SameSite=Lax` — as correções
  do OWASP ZAP do projeto relacional, com teste
- Login com credencial errada devolve mensagem única, e o redirecionamento
  pós-login só aceita destino interno

---

## Apresentação

A apresentação do trabalho está na própria loja, em `/apresentacao` — HTML e
CSS, sem JavaScript, um slide por tela. Os mesmos 12 slides saem em
[PowerPoint](public/static/Torra_e_Terra_NoSQL.pptx), com notas do
apresentador, e em [PDF](public/static/Torra_e_Terra_NoSQL.pdf). O texto de
todos os formatos mora num arquivo só, `templates/apresentacao.json`; como
gerar de novo está em [`docs/apresentacao/README.md`](docs/apresentacao/README.md).

---

## Limitações conhecidas

Sem pagamento, frete ou área administrativa. O carrinho vive na sessão. A
reconciliação é um comando manual — em produção ela seria agendada. O plano
gratuito do Cloudant limita a vazão (20 leituras, 10 escritas e 5 consultas
globais por segundo); o `banco.py` espera e repete quando recebe 429.

Duas limitações de cluster, documentadas em
[`docs/checkout_saga.md`](docs/checkout_saga.md) e
[`docs/modelo_documental.md`](docs/modelo_documental.md): no Cloudant, duas
gravações quase simultâneas no mesmo documento podem terminar como revisões em
conflito (HTTP 202) em vez de um 409, e a loja não lê `_conflicts`; e o
documento `email:<endereço>` põe o e-mail no `_id`, que aparece em logs e
fica no túmulo de exclusão — trocar por um hash do e-mail é o próximo passo.
Mais em [`docs/requisitos.md`](docs/requisitos.md).

---

## Deploy

Vercel para a aplicação e IBM Cloudant para o banco, os dois no plano
gratuito. Passo a passo em [`docs/deploy_vercel.md`](docs/deploy_vercel.md);
a escolha está justificada em [`docs/decisoes.md`](docs/decisoes.md).
