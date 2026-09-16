# Deploy — Vercel + CouchDB no Railway

A aplicação Flask roda no **Vercel**. O banco é o **Apache CouchDB 3.5**, a
imagem Docker oficial, num serviço do **Railway** — a plataforma que já
hospedava o projeto relacional.

```
navegador ──HTTPS──> Vercel (função Python, Washington, D.C. · iad1)
                          │
                          └──HTTPS──> Railway: CouchDB 3.5.2 (US East, Virgínia)
                                      volume persistente em /opt/couchdb/data
```

A região é a mesma de propósito: cada página faz de uma a oito idas ao banco.
Medido em produção em 16/09/2026, com 15 chamadas a `/saude`: **20 ms** de
mediana por ida ao banco com a função aquecida. A primeira ida, que ainda abre
a conexão TLS, levou 168 ms.

> **O plano era o IBM Cloudant.** A conta da IBM Cloud exige cartão de crédito,
> e o grupo decidiu publicar sem cartão. O plano B da decisão N02 virou o
> plano, e **nenhuma linha de código mudou**: só o `COUCHDB_URL`. Detalhes no
> fim deste guia, em [E o Cloudant?](#e-o-cloudant).

---

## Custos e limites

| Serviço | O que custa | Limites que importam aqui |
|---|---|---|
| Vercel, plano Hobby | nada | uso pessoal e não comercial; uma região de função |
| Railway | entra na conta que o grupo já pagava pelo projeto relacional; o Railway cobra por uso de CPU, memória e disco | volume de 5 GB; nenhuma cota de requisições por segundo |

Consumo medido no Railway (`railway metrics --service couchdb`, última hora,
com a loja no ar e testada): **190 MB de memória**, **0,01 vCPU** em média,
**92 MB** de disco. A fatura estimada do workspace inteiro no ciclo, que
inclui o projeto relacional, era de **US$ 4,43** (`railway usage`).

Sem a cota do Cloudant, contar as idas ao banco por página
([`consultas_e_indices.md`](consultas_e_indices.md)) continua valendo por outro
motivo: cada ida é latência.

---

## Passo 1 — Criar o CouchDB no Railway

Com o [Railway CLI](https://docs.railway.com/guides/cli) instalado e logado
(`railway login`), a partir da pasta do projeto:

```bash
railway init --name torra-terra-nosql
```

```bash
railway add --image couchdb:3.5 --service couchdb --variables "COUCHDB_USER=admin"
```

A senha do administrador entra pela entrada padrão, para não ficar no
histórico do terminal. Guarde-a num arquivo fora do repositório:

```bash
railway variable set --service couchdb --stdin COUCHDB_PASSWORD < caminho/da/senha_admin
```

```bash
railway service link couchdb
```

```bash
railway scale --service couchdb us-east=1
```

**Uma réplica, e só uma.** Aqui o Railway manteve a réplica padrão numa
outra região, `sfo`, na costa oeste, além da nova em `us-east`. Um CouchDB
sem cluster com duas réplicas seria dois bancos separados, cada um com metade
dos dados. Zere a região que sobrar, pelo nome que ela aparecer:

```bash
railway scale --service couchdb sfo=0 us-east=1
```

```bash
railway volume add --mount-path /opt/couchdb/data
```

Sem o volume, todo restart ou redeploy apaga o banco. No **Git Bash do
Windows**, rode com `MSYS_NO_PATHCONV=1` na frente: sem isso o Git Bash
converte `/opt/couchdb/data` num caminho do Windows antes de o CLI ver.

```bash
railway domain --service couchdb --port 5984
```

O domínio gerado é o endereço público HTTPS do banco. Aqui:
`https://couchdb-production-ed47.up.railway.app`, com o Fauxton em `/_utils`.

---

## Passo 2 — Preparar o banco a partir da sua máquina

### 2.1 Bancos de sistema e usuário da aplicação

O CouchDB em nó único precisa dos bancos `_users` e `_replicator`. Nos
comandos abaixo, `<senha-do-admin>` e `<senha-do-app>` são marcadores: nunca
cole a senha de verdade num documento, num chat ou num print.

```bash
curl -X PUT https://admin:<senha-do-admin>@couchdb-production-ed47.up.railway.app/_users
```

```bash
curl -X PUT https://admin:<senha-do-admin>@couchdb-production-ed47.up.railway.app/_replicator
```

```bash
curl -X PUT https://admin:<senha-do-admin>@couchdb-production-ed47.up.railway.app/_users/org.couchdb.user:torra_app -H "Content-Type: application/json" -d "{\"name\": \"torra_app\", \"password\": \"<senha-do-app>\", \"roles\": [], \"type\": \"user\"}"
```

A loja no Vercel **não usa o admin**: usa o `torra_app`, que lê e grava
documentos mas não mexe nas regras do banco. É a decisão
[N12](decisoes.md#n12--usuário-próprio-no-couchdb).

### 2.2 Regras, índices e carga

Crie o `.env` na raiz do projeto (a partir do `.env.example`) com a URL do
**admin**, que só fica na sua máquina:

```
COUCHDB_URL=https://admin:<senha-do-admin>@couchdb-production-ed47.up.railway.app
COUCHDB_DATABASE=torra_terra
SECRET_KEY=qualquer-coisa-para-uso-local
```

Com o ambiente virtual ativo:

```bash
flask --app app init-db
```

```bash
flask --app app seed-db
```

A saída esperada do `init-db` lista o banco criado, a `validate_doc_update`
gravada em `_design/regras` e os quatro índices com `created`. Rodar de novo
mostra `exists` — os dois comandos são idempotentes.

### 2.3 Só o `torra_app` entra no banco da loja

```bash
curl -X PUT https://admin:<senha-do-admin>@couchdb-production-ed47.up.railway.app/torra_terra/_security -H "Content-Type: application/json" -d "{\"admins\": {\"names\": [], \"roles\": [\"_admin\"]}, \"members\": {\"names\": [\"torra_app\"], \"roles\": []}}"
```

Com um membro definido, quem não está na lista deixa de ler o banco. O que
foi conferido contra o Railway, requisição por requisição:

| Quem | Tenta | Resposta |
|---|---|---|
| anônimo | ler o banco | 401 |
| `torra_app` | ler o banco e consultar o catálogo pelo índice | 200, 12 cafés |
| `torra_app` | gravar e apagar um documento | 201 e 200; gravar de novo sem o `_rev` dá 409 |
| `torra_app` | gravar um `_design/...` (as regras e os índices) | 403 |
| `torra_app` | trocar o `_security` | recusado com 500 `no_majority`; relido com o admin, o `_security` continuou o mesmo |
| `torra_app` | apagar o banco | 401 |
| `torra_app` | gravar um café com estoque negativo | 403, recusado pela `validate_doc_update` |

**Cuidado com o `reset-db`:** ele apaga e recria o banco, e o `_security`
vai junto. Depois dele, rode o comando acima de novo, senão a loja perde o
acesso.

---

## Passo 3 — Variáveis no Vercel

1. Entre em [vercel.com](https://vercel.com) com a conta do GitHub.
2. **Add New** → **Project** → importe o repositório `projeto-nivaldo-nosql`.
   O **Framework Preset** é detectado como **Flask**: o Vercel encontra o
   `app.py` com a variável `app`. Não há `vercel.json` — não é preciso.
3. No projeto: **Settings** → **Environment Variables**. Cadastre:

   | Nome | Valor |
   |---|---|
   | `COUCHDB_URL` | `https://torra_app:<senha-do-app>@couchdb-production-ed47.up.railway.app` |
   | `COUCHDB_DATABASE` | `torra_terra` |
   | `SECRET_KEY` | uma chave nova: `python -c "import secrets; print(secrets.token_hex(32))"` |

   Dá para colar as três linhas de uma vez, no formato `CHAVE=valor`, direto
   no campo **Key**: o Vercel separa sozinho. Use uma `SECRET_KEY`
   **diferente** da local.
4. Confira que as variáveis estão marcadas para o ambiente **Production**.
5. **Deployments** → último deploy de produção → **⋯** → **Redeploy**.

**Variável salva não chega ao deploy que já está no ar.** Ela só vale para
um deploy novo — um Redeploy ou o próximo `git push` na `main`. Foi
exatamente o que aconteceu aqui: as variáveis estavam salvas, e a loja
continuava respondendo "faltam as variáveis" até o deploy seguinte.

A partir daí, todo `git push` na `main` publica sozinho, e cada branch ganha
uma URL de *preview*.

### O que o Vercel faz com o projeto

- `requirements.txt` é instalado. O `pytest` fica de fora de propósito, em
  `requirements-dev.txt`: não precisa ir para a função.
- `public/static/` é servido pelo CDN em `/static/`, sem acordar o Python.
- `.vercelignore` tira `tests/`, `docs/` e o `docker-compose.yml` do pacote.
- `VERCEL=1` liga o modo produção: cookie de sessão `Secure`, e a loja se
  recusa a atender sem `SECRET_KEY` ou `COUCHDB_URL`.

---

## Passo 4 — Validar de ponta a ponta

1. **Saúde:** `https://projeto-nivaldo-nosql.vercel.app/saude` deve responder
   `{"aplicacao": "ok", "couchdb": "ok", "latencia_ms": ...}`.
2. **Catálogo:** os 12 cafés, o filtro por região (3 na Chapada Diamantina),
   a página de um café.
3. **Cadastro e login** com um e-mail de teste; o mesmo e-mail de novo é
   recusado.
4. **Estoque insuficiente:** tente comprar 2 unidades do Chapada Geisha
   (estoque 1). A loja volta ao carrinho com "Estoque insuficiente de Chapada
   Geisha: você pediu 2 e temos 1 em estoque.", e nenhum estoque baixa.
5. **Compra:** finalize com outro café e confira *Meus pedidos*.
6. **No Fauxton** (`/_utils`, com o admin): o documento `pedido:<chave>` com
   `status: "CRIADO"` e o `historico`; o estoque do café baixado; o campo
   `reservas` vazio de novo; a revisão (`_rev`) do produto avançada.

Essa sequência rodou contra produção em 16/09/2026, por script, com 22
conferências — todas passaram. O pedido de teste foi cancelado depois, com o
motivo no histórico, e a unidade voltou ao estoque.

---

## Operação

| Tarefa | Como |
|---|---|
| Logs da aplicação | Vercel → projeto → **Logs** (inclui os avisos do `banco.py`) |
| Logs do banco | `railway logs --service couchdb` |
| Consumo | `railway metrics --service couchdb` e `railway usage` |
| Painel do banco | `https://couchdb-production-ed47.up.railway.app/_utils`, com o admin |
| Checkout interrompido | com o `.env` apontando para o Railway: `flask --app app reconciliar` |
| Backup | no Fauxton **local** (docker-compose) → **Replication**: origem = o banco `torra_terra` do Railway, destino = um banco local. Sem Docker: `curl` em `/torra_terra/_all_docs?include_docs=true` com o admin, salvando o JSON fora do repositório |
| Restaurar | replicação do local para um banco **novo** no Railway; conferir as contagens, aplicar o `_security` e só então apontar a loja para ele |

A reconciliação ainda é manual. Uma evolução natural é agendá-la com Vercel
Cron chamando uma rota protegida por segredo.

---

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| A loja responde 503 "faltam as variáveis de ambiente ..." | A variável não existe, não está marcada para **Production**, ou foi salva depois do deploy que está no ar. A própria mensagem diz o ambiente e o commit do deploy que respondeu. Confira e faça **Redeploy** |
| 503 "a COUCHDB_URL não é um endereço http(s) completo" | A URL foi colada sem `https://`, ou cortada |
| `/saude` responde `"couchdb": "indisponivel"` | Serviço do Railway parado, senha errada (401) ou `_security` perdido depois de um `reset-db` |
| Catálogo vazio | Faltou rodar `init-db` e `seed-db` apontando para o Railway |
| Página "Voltamos em instantes" | O banco não respondeu. Veja `/saude`, `railway logs --service couchdb` e `railway status` |
| Dados sumiram depois de um redeploy do Railway | O volume não está montado em `/opt/couchdb/data` |

---

## E o Cloudant?

O IBM Cloudant é o CouchDB gerenciado que o material cita como opção
compatível (slide 48), e foi o primeiro plano. Ficou de fora porque a conta
da IBM Cloud pede cartão de crédito, com uma retenção de verificação de cerca
de US$ 1, e o grupo decidiu publicar sem cartão.

O código continua pronto para ele: o `banco.py` troca a `COUCHDB_IAM_APIKEY`
por token e renova sozinho, e repete a requisição quando o plano Lite
responde 429 por excesso de vazão. Para migrar, basta criar a instância,
rodar `init-db` e `seed-db` com a URL dela e trocar o `COUCHDB_URL` no Vercel.

Duas diferenças mudariam o comportamento, e as duas estão registradas no
código e nos documentos:

- **Cota de vazão.** O Lite aceita 20 leituras, 10 escritas e 5 consultas
  globais por segundo; a IBM conta `_all_docs` e `_find` como consulta
  global.
- **Cluster.** O Cloudant grava em três cópias. Duas gravações quase
  simultâneas no mesmo documento podem ser aceitas em cópias diferentes
  (HTTP 202) e virar revisões em conflito, em vez de um 409. E o `_find` pode
  ser respondido por uma cópia que ainda não recebeu a gravação. No CouchDB
  de nó único do Railway, nada disso acontece: o 409 é garantido e o índice é
  atualizado antes de responder.

---

## E o projeto relacional?

O projeto relacional continua no Railway, em `nivaldo.felipefurlan.com.br`,
num projeto separado deste. Antes de desligá-lo, confirme que a avaliação
dele terminou e faça o backup do PostgreSQL com `pg_dump` — o passo a passo
está no README da tag
[`v1-relacional`](https://github.com/lipefurlan/projeto-nivaldo-nosql/tree/v1-relacional).
