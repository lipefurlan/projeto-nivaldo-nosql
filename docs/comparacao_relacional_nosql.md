# Relacional × NoSQL: a mesma loja em dois bancos

O Torra & Terra foi construído duas vezes, com os mesmos requisitos:
PostgreSQL na tag `v1-relacional` e Apache CouchDB na versão atual. Cada
afirmação aponta para o arquivo onde pode ser conferida — os da tag com
`git show v1-relacional:<caminho>`. Os números da seção 5 foram medidos.

## 1. Visão geral

| Tema | PostgreSQL (`v1-relacional`) | CouchDB (atual) |
|---|---|---|
| Estrutura | 5 tabelas normalizadas: `categorias`, `clientes`, `produtos`, `pedidos`, `itens_pedido` | 5 tipos de documento JSON num banco só, com o campo `tipo`: `categoria`, `produto`, `cliente`, `email`, `pedido`. `itens_pedido` virou parte do pedido; `email` nasceu para a unicidade |
| Identidade | `SERIAL`: o banco gera o número; `flush()` para conhecê-lo antes do `COMMIT` | `_id` escolhido pela aplicação, com o tipo na frente: `produto:chapada-geisha` (legível, vira a URL), `cliente:<uuid>`, `pedido:<chave>`, `email:<endereço>` |
| Relacionamento | `FOREIGN KEY`, verificada pelo banco; `JOIN` ou relacionamento do ORM | **Embed** (itens no pedido; `categoria {nome, regiao}` no produto), **referência** (`cliente_id`, `produto_id`, `categoria_id` como texto) e **snapshot** (nome e preço no item) |
| Consulta | SQL montado pelo SQLAlchemy: `db.select(Produto).order_by(Produto.nome)` | Mango em JSON via `POST /_find` (`consulta_catalogo` em `app.py`), mais `_all_docs` por faixa de `_id` ou por lista de chaves |
| Índices | 4 `CREATE INDEX` B-tree em `SQL/schema.sql`; prova com `EXPLAIN ANALYZE` em `SQL/consultas.sql` | 4 índices Mango em `couchdb/indices.json`, criados por `POST /_index`; prova com `_explain` em `test_indices_atendem_as_consultas` |
| Regras | `CHECK`, `NOT NULL` e tipo de coluna no DDL — **no banco** | `validate_doc_update` em JavaScript (`couchdb/validacao.js`), executada pelo CouchDB a cada gravação — **também no banco** |
| Unicidade | `email VARCHAR(160) NOT NULL UNIQUE` e `IntegrityError` | `_id` do documento-chave `email:<endereço>` e HTTP 409 |
| Concorrência | Pessimista: `SELECT ... FOR UPDATE` | Otimista: `_rev`, 409 e nova tentativa |
| Atomicidade | Transação: `COMMIT` / `ROLLBACK` sobre quantas tabelas forem | Um documento por gravação; entre documentos, saga com compensação e reconciliação |
| Leitura após gravar | Depois do `COMMIT`, toda consulta enxerga o dado | Consulta por índice no Cloudant pode chegar atrasada; *Meus pedidos* busca o último pedido pelo `_id` |
| Dinheiro | `NUMERIC(10,2)` e `Decimal` | Centavos inteiros (`preco_centavos: 14800`); `R$` só na tela |
| Carrinho | Sessão do Flask (cookie assinado), `produto_id` inteiro | Sessão do Flask, `produto_id` como `"produto:chapada-geisha"` |
| Administração | `psql` / pgAdmin | Fauxton local (`/_utils/`) e painel do Cloudant, baseado no Fauxton |
| Setup local | PostgreSQL instalado e `psql -U postgres -f SQL/usuario_app.sql` | `docker compose up -d` (CouchDB 3.5), ou o Cloudant pelo `.env` |
| Criar a estrutura | `flask init-db` roda o `schema.sql` | `flask init-db` cria o banco, grava `_design/regras` e os índices; idempotente |
| Hospedagem | Railway: aplicação e PostgreSQL no mesmo projeto, gunicorn pelo `Procfile` | Vercel (função Python) e IBM Cloudant Lite, os dois no plano gratuito |
| Limite de vazão | Não se aplica | O Cloudant Lite responde 429; `banco.py` espera e repete |
| Testes | pytest contra PostgreSQL de verdade, num banco `_teste` | pytest contra CouchDB 3.5 de verdade no GitHub Actions, e contra um dublê em memória (`tests/couchdb_falso.py`) sem Docker |
| Acesso ao banco | Flask-SQLAlchemy, SQLAlchemy e o driver psycopg 3 | HTTP puro com `requests`, centralizado em `banco.py` |

## 2. Três comparações em código

### 2.1 Estoque nunca negativo

PostgreSQL — `SQL/schema.sql` da tag:

```sql
    estoque        INTEGER        NOT NULL DEFAULT 0,
    ...
    -- Estoque negativo nao existe no mundo fisico. Esta constraint e a
    -- ultima linha de defesa do checkout: mesmo que o SELECT ... FOR UPDATE
    -- falhe, o banco recusa a venda a descoberto.
    CONSTRAINT ck_produtos_estoque     CHECK (estoque >= 0),
```

CouchDB — `couchdb/validacao.js`:

```javascript
  function exigir(condicao, mensagem) {
    if (!condicao) {
      throw({ forbidden: mensagem });
    }
  }
  ...
  if (novo.tipo === 'produto') {
    ...
    // A última linha de defesa contra vender o que não existe.
    exigir(inteiro(novo.estoque) && novo.estoque >= 0,
      'estoque precisa ser inteiro e maior ou igual a zero');
```

**Igual:** a regra mora no banco e vale para quem não passa pela loja — `psql`
lá; Fauxton, `curl` ou replicação aqui. Nos dois, o checkout confere o estoque
antes, e a regra do banco é a última linha de defesa.

**Diferente:**

- No SQL, "inteiro" é o tipo da coluna; JSON só tem *number*, então ser inteiro
  também vira regra (`inteiro()`). E o `CHECK` pertence à tabela, enquanto a
  `validate_doc_update` é uma função só, chamada a cada gravação de qualquer
  documento, que separa os casos pelo `tipo`.
- A recusa chega como `IntegrityError` citando `ck_produtos_estoque` no
  PostgreSQL, e como HTTP 403 `forbidden`, com a mensagem, no CouchDB —
  `banco.py` traduz para `Recusado`.
- A função recebe também a versão anterior do documento, então expressa regras
  de transição que um `CHECK` não expressa: itens de pedido gravado não mudam,
  pedido `CANCELADO` não volta a valer e pedido decidido não volta a `PENDENTE`.
  No PostgreSQL isso pediria trigger, que o projeto relacional não tinha.
- Testes: `test_estoque_negativo_e_recusado_pelo_banco` (tag) e
  `test_banco_recusa_produto_invalido_gravado_direto[estoque--1]`, que só roda
  contra CouchDB de verdade — o dublê não executa JavaScript.

### 2.2 E-mail único

PostgreSQL — rota `cadastro` no `app.py` da tag (o `UNIQUE` está no `schema.sql`):

```python
        try:
            db.session.add(cliente)
            db.session.commit()
        except IntegrityError:
            # O UNIQUE de clientes.email e quem decide, nao um SELECT previo.
            # Consultar antes de inserir abriria uma janela de corrida entre
            # a checagem e o INSERT; deixar o banco recusar elimina a janela.
            db.session.rollback()
            flash("Este e-mail já tem cadastro. Tente entrar.", "erro")
```

CouchDB — `cadastrar_cliente` em `app.py`, resumido:

```python
    try:
        b.salvar(chave_email)
    except Conflito:
        raise EmailJaCadastrado(email) from None
    try:
        b.salvar(cliente)
    except ErroBanco as falha:
        ...  # confere se o cliente entrou: se sim, o cadastro vale;
        ...  # se não, apaga o documento email:<endereço>
        raise falha
```

**Igual:** quem decide é o banco, não uma consulta prévia. Os dois códigos
explicam em comentário a mesma janela de corrida entre consultar e gravar.

**Diferente:** no PostgreSQL, uma cláusula no DDL e um `INSERT`. No CouchDB,
dois documentos, uma ordem de gravação, uma conferência antes de desfazer (um
timeout não diz se o cliente entrou), uma compensação e um passo na
reconciliação. O `UNIQUE` também criava o índice do login; aqui, unicidade
(`email:`) e busca (`idx_clientes_email`) são coisas separadas. Casos e testes
em [`checkout_saga.md`](checkout_saga.md), seção 10.

### 2.3 O checkout

PostgreSQL — `app.py` da tag, o roteiro no comentário e o desfecho:

```python
#     BEGIN
#      |- SELECT ... FOR UPDATE  (trava o estoque de cada cafe)
#      |- valida existencia e saldo de TODOS os itens
#      |- INSERT do pedido
#      |- INSERT de cada item, com preco_unitario congelado
#      |- UPDATE do estoque
#      +- COMMIT   ·   ROLLBACK em qualquer falha
...
    except Exception:
        db.session.rollback()
        raise
```

CouchDB — `app.py` atual, o roteiro no comentário e o desfecho:

```python
#   1. ler e validar       nada é gravado se faltar estoque ou café
#   2. registrar intenção  o pedido nasce PENDENTE — é o diário da saga
#   3. reservar estoque    um _bulk_docs; cada café baixa o saldo e ganha
#                          uma marca {pedido_id: quantidade}; 409 -> relê
#   4. confirmar           o pedido vira CRIADO: o ponto sem volta
#   5. limpar marcas       melhor esforço; sobra só lixo inofensivo
...
    except (ErroCheckout, ErroBanco) as falha:
        ...
        try:
            situacao = cancelar_e_devolver(pedido_id, motivo)
        except ErroBanco:
            ...
            raise CheckoutInterrompido(MENSAGEM_INTERROMPIDO) from falha

        if situacao["status"] != "CANCELADO":
            # A confirmação tinha sido gravada; só a resposta se perdeu.
            _limpar_marcas(situacao)
            return situacao
```

No relacional, desfazer é `rollback()`: uma linha, porque o banco sabe desfazer.
Na saga, desfazer é código — reler o pedido, decidir se ainda há o que desfazer
e devolver o estoque só onde há marca. Fases, matriz de falhas e testes de cada
caso em [`checkout_saga.md`](checkout_saga.md).

## 3. O que mudou na prática

### Ficou mais fácil no CouchDB

- **Ler um pedido inteiro.** O pedido é um documento com itens, nome e preço
  dentro: chega num `GET` ou num `_find`, sem `JOIN`. No relacional, *Meus
  pedidos* percorria `pedido.itens` pelo ORM e mostrava `item.produto.nome`, o
  nome **atual** do café; aqui o nome também fica congelado no item.
- **Regras de transição no banco**, sem trigger, como na seção 2.1
  (`test_banco_nao_deixa_mudar_o_preco_de_um_pedido_gravado`).
- **Listar por tipo sem índice secundário.** O prefixo no `_id` permite listar
  as regiões por faixa do índice primário (`listar_por_prefixo("categoria:")`).
- **Enxergar o banco.** Cada operação é um verbo HTTP sobre uma URL: dá para
  repetir no `curl` e no Fauxton, e o mesmo `banco.py` fala com o CouchDB
  local e com o Cloudant.

### Ficou mais difícil no CouchDB

- **A atomicidade virou código nosso e teste nosso.** Saga, marcas,
  compensação, idempotência e reconciliação; 23 testes de checkout contra 12,
  que forçam 409 e timeout trocando métodos do cliente HTTP na hora certa.
  Cada gravação que pode ficar sem resposta vira um caso a pensar, no checkout
  e no cadastro (`checkout_saga.md`, seções 6 e 10).
- **Não existe FK.** A `validate_doc_update` só enxerga o documento gravado e a
  versão anterior dele; não consulta outro documento. Ela confere que
  `cliente_id` começa com `cliente:`, não que o cliente exista. Apagar um café
  citado em pedidos é permitido — o pedido continua legível só por causa do
  snapshot.
- **Unicidade custa duas gravações**, uma conferência, uma compensação e um
  passo de reconciliação.
- **A consulta depende do índice.** O Mango só ordena por campos do índice, na
  ordem do índice: `nome` e `criado_em` entram nos índices por isso, e as
  datas são texto ISO 8601 em UTC para ordenarem certo (`agora_iso`).
- **Consistência eventual.** No Cloudant, a consulta por índice pode não
  enxergar a gravação recém-feita; *Meus pedidos* guarda o último pedido na
  sessão e o lê pelo `_id` (`test_meus_pedidos_mostra_o_pedido_recem_feito_mesmo_com_indice_atrasado`).
- **O cluster.** No Cloudant, duas gravações quase simultâneas no mesmo
  documento podem ser aceitas em cópias diferentes (201 e 202) e virar revisões
  em conflito, sem 409. O `banco.py` só registra o 202 no log, e a loja não lê
  `_conflicts` (`checkout_saga.md`, seção 6).
- **Operação.** A reconciliação precisa rodar, e hoje roda à mão. O plano
  gratuito limita a vazão, e o 429 precisou de tratamento.
- **Testar a regra do banco.** Os 15 casos da `validate_doc_update` só rodam
  com CouchDB de verdade; sem `TEST_COUCHDB_URL`, o dublê os pula.

### Ficou igual

- Os requisitos, a moagem escolhida no item, o preço congelado e a mensagem
  que diz qual café faltou.
- O princípio de que a regra mora no banco, não só no formulário.
- Quatro índices, cada um justificado por uma consulta real.
- Carrinho na sessão, senha só como hash scrypt, CSRF, cabeçalhos de segurança
  e segredos em variável de ambiente, com os testes de segurança portados.
- A prova final contra o banco de verdade: PostgreSQL lá, CouchDB 3.5 no
  GitHub Actions aqui.

### Um achado da migração

Comparar os dois checkouts mostra um caso que o relacional não tratava: o mesmo
café em duas moagens, cada linha cabendo no estoque e a soma não. Pela leitura do
`app.py` da tag, a validação conferia linha a linha, e as duas passavam. O
`CHECK (estoque >= 0)` barraria a gravação no `COMMIT` — mas como
`IntegrityError`, que a rota não captura: o cliente veria erro 500, não a
mensagem. Não há teste na tag para esse caso, e ele não foi executado aqui. A
versão atual soma as quantidades por café antes de validar
(`test_moagens_diferentes_do_mesmo_cafe_somam_no_estoque`).

## 4. Quando escolher cada um

**Pedidos e estoque: relacional.** Vender o último Chapada Geisha uma vez só,
sem pedido pela metade, é o que transação e trava resolvem de graça. No
CouchDB o mesmo resultado custou a saga, a reconciliação e 23 testes de
checkout. Para uma loja com um banco só, o PostgreSQL é a escolha defensável
para o núcleo transacional.

**Onde o documental brilha:**

- **Catálogo flexível.** Cafés com atributos diferentes entre si entram sem
  `ALTER TABLE`; o campo `versao_esquema` em todo documento existe para
  conviver com formatos antigos e novos.
- **Leitura por agregado.** O pedido inteiro num `GET`, sem montar nada.
- **Replicação.** O CouchDB sincroniza bancos entre instâncias: base de
  backup, de distribuição e de cenários offline-first (slide 27). Aqui ela
  está prevista para backup (`docs/deploy_vercel.md`).

**Polyglot persistence.** O slide 59 da aula teórica dá o exemplo de
e-commerce: "PostgreSQL para pedidos + MongoDB para catálogo + Redis para
sessão + OpenSearch para busca", condicionado a padrões de acesso realmente
distintos e a uma operação que se justifique. Com 12 cafés, dois bancos seriam
custo sem retorno. Se o catálogo crescesse em variedade e em leitura, a divisão
natural seria pedidos e estoque no PostgreSQL e catálogo num banco de documentos
— e, antes disso, colunas `JSONB` no próprio PostgreSQL já dariam atributos
flexíveis sem abrir mão da transação.

**Para a banca, em uma frase:** o relacional dá consistência entre registros de
graça; o documental a cobra em código e em testes, e se paga quando o problema é
o formato do dado, a leitura por agregado ou a distribuição.

## 5. Números do projeto

Medidos em 16/09/2026, na tag `v1-relacional` e no código atual. As seções do
checkout vão do título do bloco até o título seguinte (comandos abaixo).

| Medida | Relacional | NoSQL |
|---|---|---|
| Linhas de Python da aplicação | 891 (`app.py`) | 1.841 (`app.py` 1.371 + `banco.py` 470) |
| Linhas do checkout | 122 (seção "A transacao de checkout") | 419: seções da saga e da reconciliação 335; `atualizar` e `atualizar_varios` 84 |
| Idas ao banco numa compra sem conflito | Não medido; só o `SELECT ... FOR UPDATE` já é um comando por linha do carrinho | 7 na saga e 8 no clique em Confirmar (a rota relê o carrinho antes), com 1 café ou com 3, contadas no dublê |
| Regras no banco | `schema.sql`, 193 linhas: 11 `CHECK`, 3 `UNIQUE`, 4 `FOREIGN KEY` | `validacao.js`, 174 linhas: 35 chamadas a `exigir` |
| Índices | 4 `CREATE INDEX` | 4 índices Mango |
| Funções de teste | 22 (`test_checkout` 12, `test_seguranca` 10) | 92 (`test_validacao` 24, `test_checkout` 23, `test_rotas` 22, `test_seguranca` 13, `test_banco` 10) |
| Linhas em `tests/` | 585 | 2.130, das quais 402 do dublê `couchdb_falso.py` |
| Dependências em `requirements.txt` | 7 | 3, e `pytest` em `requirements-dev.txt` |
| Comandos `flask` | 3: `init-db`, `seed-db`, `reset-db` | 4: os mesmos e `reconciliar` |

Suíte NoSQL rodada localmente, sem `TEST_COUCHDB_URL`: 109 casos coletados, 94
passaram e 15 foram pulados — os da `validate_doc_update`, que exigem CouchDB de
verdade. É o resultado do dublê em memória, não do CI. A suíte relacional não
foi rodada para este documento, porque exige PostgreSQL.

Para medir de novo:

```bash
git show v1-relacional:app.py | wc -l && wc -l app.py banco.py
git show v1-relacional:app.py | awk '/^# A transacao de checkout/,/^def formatar_brl/' | wc -l
awk '/^# Checkout — a saga/,/^# Preparação do banco/' app.py | wc -l
awk '/def atualizar\(/,/return \{doc_id: atuais/' banco.py | wc -l
grep -c "^def test_" tests/test_*.py && pytest --collect-only -q | tail -1
```
