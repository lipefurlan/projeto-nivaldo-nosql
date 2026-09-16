# Deploy — Vercel + IBM Cloudant

A aplicação Flask roda no **Vercel** e o banco é o **IBM Cloudant**, a versão
gerenciada do CouchDB que o material cita como opção compatível (slide 48). Os
dois no plano gratuito.

```
navegador ──HTTPS──> Vercel (função Python, Washington D.C.)
                          │
                          └──HTTPS──> IBM Cloudant (Washington DC)
```

A região é a mesma de propósito: cada página faz de uma a seis idas ao banco,
e cada ida atravessando um oceano somaria centenas de milissegundos.

---

## Custos e limites

| Serviço | Plano | O que custa | Limites que importam aqui |
|---|---|---|---|
| Vercel | Hobby | nada | uso pessoal e não comercial; uma região de função |
| IBM Cloudant | Lite | nada, sem expirar | 1 GB; 20 leituras/s, 10 escritas/s, 5 consultas globais/s; uma instância Lite por conta |

A conta da IBM Cloud **pede cartão de crédito** na criação, só para
verificação: a IBM faz uma retenção de cerca de US$ 1 e não cobra os planos
Lite.

Passar do limite de vazão do Cloudant não derruba a loja: ele responde
HTTP 429 antes de processar, e o `banco.py` espera e tenta de novo.

---

## Passo 1 — Criar o Cloudant

1. Crie a conta em [cloud.ibm.com](https://cloud.ibm.com).
2. **Catalog** → busque **Cloudant** → abra o serviço.
3. Preencha:
   - **Plan:** Lite
   - **Location:** Washington DC
   - **Authentication method:** **Use both legacy credentials and IAM**
4. **Create**. A instância leva alguns minutos para ficar ativa.
5. Na instância: **Service credentials** → **New credential** → papel
   **Manager** → **Add**.
6. Expanda a credencial criada e copie o campo **`url`**. Ele já vem com
   usuário e senha embutidos: `https://usuario:senha@xxxx-bluemix.cloudantnosqldb.appdomain.cloud`.

> **Por que "legacy credentials".** Com ela, a URL leva usuário e senha e
> funciona igual ao CouchDB do docker-compose — é o mesmo `COUCHDB_URL`. Se a
> instância for criada só com IAM, também funciona: use a URL sem usuário e
> senha e defina `COUCHDB_IAM_APIKEY` com o `apikey` da credencial. O
> `banco.py` troca a chave por um token e renova sozinho.

**Nunca cole essa URL em chat, issue ou print.** Ela é a senha do banco.

---

## Passo 2 — Preparar o banco a partir da sua máquina

Crie o `.env` na raiz do projeto (a partir do `.env.example`) com a URL copiada:

```
COUCHDB_URL=https://usuario:senha@xxxx-bluemix.cloudantnosqldb.appdomain.cloud
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

Para conferir: na instância do Cloudant, **Launch Dashboard** abre o painel
(baseado no Fauxton) com o banco `torra_terra`, os 16 documentos da carga e os
design documents dos índices.

### Opcional — rodar a suíte contra o Cloudant

Adicione ao `.env` a mesma URL como `TEST_COUCHDB_URL` e rode `pytest -v`. A
suíte cria um banco descartável `torra_terra_teste_<sorteio>`, roda inclusive
os testes da `validate_doc_update` e apaga o banco no fim. Por causa do limite
de vazão do plano Lite, ela demora mais que localmente.

---

## Passo 3 — Publicar no Vercel

1. Entre em [vercel.com](https://vercel.com) com a conta do GitHub.
2. **Add New** → **Project** → importe o repositório `projeto-nivaldo-nosql`.
3. O **Framework Preset** é detectado como **Flask**: o Vercel encontra o
   `app.py` com a variável `app`. Não há `vercel.json` — não é preciso.
4. Em **Environment Variables**, cadastre:

   | Nome | Valor |
   |---|---|
   | `COUCHDB_URL` | a URL da credencial do Cloudant |
   | `COUCHDB_DATABASE` | `torra_terra` |
   | `SECRET_KEY` | uma chave nova, gerada com `python -c "import secrets; print(secrets.token_hex(32))"` |

   Use uma `SECRET_KEY` **diferente** da local.
5. **Deploy**.

A partir daí, todo `git push` na `main` publica sozinho, e cada branch ganha
uma URL de *preview*.

### O que o Vercel faz com o projeto

- `requirements.txt` é instalado. O `pytest` fica de fora de propósito, em
  `requirements-dev.txt`: não precisa ir para a função.
- `public/static/style.css` é servido pelo CDN em `/static/style.css`, sem
  acordar o Python.
- `.vercelignore` tira `tests/`, `docs/` e o `docker-compose.yml` do pacote.
- `VERCEL=1` liga o modo produção: cookie de sessão `Secure`, e a loja se
  recusa a subir sem `SECRET_KEY` ou `COUCHDB_URL`.

---

## Passo 4 — Validar de ponta a ponta

1. **Saúde:** `https://<projeto>.vercel.app/saude` deve responder
   `{"aplicacao": "ok", "couchdb": "ok", "latencia_ms": ...}`.
2. **Catálogo:** os 12 cafés, o filtro por região, a página de um café.
3. **Cadastro e login** com um e-mail de teste.
4. **Compra:** adicione dois cafés, finalize, confira *Meus pedidos*.
5. **No painel do Cloudant:** o documento `pedido:<chave>` com
   `status: "CRIADO"` e o `historico`; o estoque do café baixado; o campo
   `reservas` vazio de novo; a revisão (`_rev`) do produto avançada.
6. **Estoque insuficiente:** tente comprar 2 unidades do Chapada Geisha
   (estoque 1). A loja volta ao carrinho dizendo qual café faltou, e nenhum
   pedido é gravado.

---

## Operação

| Tarefa | Como |
|---|---|
| Logs da aplicação | Vercel → projeto → **Logs** (inclui os avisos do `banco.py`) |
| Métricas do banco | Cloudant → **Monitoring**: vazão, 429, armazenamento |
| Checkout interrompido | com o `.env` apontando para o Cloudant: `flask --app app reconciliar` |
| Backup | no Fauxton **local** (docker-compose) → **Replication**: origem = a URL do Cloudant com `/torra_terra`, destino = um banco local. A replicação parte do CouchDB local porque o Cloudant não alcança a sua máquina |
| Restaurar | replicação no sentido inverso, do local para um banco **novo** no Cloudant; conferir as contagens antes de apontar a loja para ele |

A reconciliação ainda é manual. Uma evolução natural é agendá-la com Vercel
Cron chamando uma rota protegida por segredo.

---

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| Toda página dá erro 500 logo após o deploy | Faltou `SECRET_KEY` ou `COUCHDB_URL`. O log mostra "Variáveis de ambiente obrigatórias ausentes" |
| `/saude` responde `"couchdb": "indisponivel"` | URL errada, credencial apagada, ou instância só com IAM sem `COUCHDB_IAM_APIKEY` |
| Catálogo vazio | Faltou rodar `init-db` e `seed-db` apontando para o Cloudant |
| Página "Voltamos em instantes" | O banco não respondeu. Veja `/saude` e o painel do Cloudant |
| Lentidão com várias pessoas comprando | Limite do plano Lite; o `banco.py` está esperando e repetindo os 429 |

---

## E o Railway?

O projeto relacional continua no Railway, em `nivaldo.felipefurlan.com.br`.
Antes de desligá-lo, confirme que a avaliação dele terminou e faça o backup
do PostgreSQL com `pg_dump` — o passo a passo está no README da tag
[`v1-relacional`](https://github.com/lipefurlan/projeto-nivaldo-nosql/tree/v1-relacional).
