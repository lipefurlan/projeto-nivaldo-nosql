# Consultas e índices — Torra & Terra

Em NoSQL a consulta vem antes do modelo: primeiro se lista o que a loja pergunta ao banco, depois se
desenha o documento e o índice que respondem (slides 16 e 17). Este é o catálogo de acessos da
versão CouchDB. As consultas Mango estão em `app.py`, na seção "Catálogo de consultas"; os índices,
cada um com a consulta que o justifica, em `couchdb/indices.json`. Os documentos estão descritos em
[`modelo_documental.md`](modelo_documental.md).

## 1. Catálogo de acessos ao banco

Toda ida ao banco que a aplicação faz, conferida rota a rota registrando cada requisição que o
`banco.py` envia. Frequência: **alta** em toda visita, **média** a cada ação de compra, **baixa**
uma vez por compra ou por conta, **manual** fora do tráfego da loja.

| # | Operação | Rota · função | Frequência | Forma de acesso | Índice |
|---|---|---|---|---|---|
| 1 | Catálogo | `GET /` · `consulta_catalogo()` | alta | Mango `_find` com índice | `idx_produtos_catalogo` |
| 2 | Catálogo por região | `GET /?regiao=<slug>` · `consulta_catalogo(categoria_id)` | média | Mango `_find` com índice | `idx_produtos_categoria` |
| 3 | Regiões do filtro | `GET /` · `listar_por_prefixo("categoria:")` | alta | faixa de `_id` em `_all_docs` | primário |
| 4 | Detalhe do café | `GET /produto/<slug>` · `obter_ou_none` | alta | lookup por `_id` | primário |
| 5 | Descrição da região, por referência | `GET /produto/<slug>` · `obter_ou_none(categoria_id)` | alta | lookup por `_id` | primário |
| 6 | Café existe e está ativo | `POST /carrinho/adicionar/<slug>` | média | lookup por `_id` | primário |
| 7 | Carrinho e tela de confirmação | `GET /carrinho`, `GET` e `POST /checkout` · `carrinho_detalhado()` | média | `_all_docs` com `keys` | primário |
| 8 | Login | `POST /login` · `consulta_cliente_por_email()` | média | Mango `_find` com índice | `idx_clientes_email` |
| 9 | Cadastro | `POST /cadastro` · `cadastrar_cliente()` | baixa | `PUT email:<endereço>`, `PUT cliente:<uuid>`; se o cliente falhar, GET dele e, se não existir, `DELETE` da chave | — |
| 10 | Checkout: compra repetida? | `finalizar_pedido()` · `obter_ou_none` | baixa | lookup por `_id` do pedido (404 esperado) | primário |
| 11 | Fase 1: ler e validar | `finalizar_pedido()` · `obter_varios` | baixa | `_all_docs` com `keys` | primário |
| 12 | Fase 2: pedido `PENDENTE` | `finalizar_pedido()` · `salvar` | baixa | `PUT pedido:<chave>` | — |
| 13 | Fase 3: reservar | `atualizar_varios(_reservar)` | baixa | `_bulk_docs`; no 409, relê com `_all_docs` e repete, até 6 rodadas | — |
| 14 | Fase 4: confirmar | `finalizar_pedido()` · `salvar` | baixa | `PUT pedido:<chave>` com `_rev` | — |
| 15 | Fase 5: limpar marcas | `_limpar_marcas()` | baixa | `_all_docs` com `keys` + `_bulk_docs` | primário |
| 16 | Compensação | `cancelar_e_devolver()` | rara | GET e `PUT` do pedido; `_all_docs` com `keys` + `_bulk_docs` dos cafés | primário |
| 17 | Meus pedidos | `GET /meus-pedidos` · `consulta_pedidos_do_cliente()` | baixa | Mango `_find` com índice | `idx_pedidos_cliente` |
| 18 | Último pedido garantido | `GET /meus-pedidos` · `obter_ou_none(ultimo_pedido)` | só se faltar na consulta | lookup por `_id` | primário |
| 19 | Saúde | `GET /saude` · `info_banco()` | a cada checagem do monitor | `GET /torra_terra` | — |
| 20 | Pedidos `PENDENTE` antigos | comando `reconciliar` · `consulta_pedidos_pendentes()` | manual | Mango `_find` **sem índice** | nenhum, de propósito |
| 21 | Marcas e chaves `email:` órfãs | comando `reconciliar` · `listar_por_prefixo` | manual | faixa de `_id` (`produto:`, `email:`); GET, `PUT` e `DELETE` por `_id` | primário |
| 22 | Estrutura e carga | comandos `init-db` e `seed-db` | uma vez por ambiente | `PUT` do banco, GET e `PUT` de `_design/regras`, 4 × `POST _index`; `_all_docs` com `keys` + `_bulk_docs` | — |

Não tocam o banco: remover item, esvaziar o carrinho, sair, as telas de login e cadastro, a
`/apresentacao` e o menu, que lê o nome do cliente da sessão (decisão N11).

## 2. Os quatro índices

Três regras do Mango explicam a forma de cada índice:

- **Um índice JSON só guarda documentos que têm todos os campos indexados.** Por isso os índices são
  pequenos — e por isso o `tipo` está em todos: `idx_pedidos_cliente` também recebe os documentos
  `email`, que têm `tipo`, `cliente_id` e `criado_em`, e é o `tipo` que os separa dos pedidos.
- **Igualdade primeiro, ordenação por último.** Os campos fixados no seletor abrem o índice; o campo
  que ordena fecha. A faixa lida já sai na ordem pedida.
- **O `sort` só funciona com campos do índice, na mesma ordem e numa direção só.** A documentação
  do CouchDB pede um índice com todos os campos do `sort` na mesma ordem, ao menos um deles no
  seletor, e todos `asc` ou todos `desc`. Por isso o `sort` lista também `tipo` e `ativo`: fica
  idêntico ao índice. O CouchDB 3.5 aceitaria omitir os campos iniciais fixados por igualdade
  (`can_use_sort`, em `mango_idx_view.erl`), mas a forma completa não depende disso. Sem índice que
  sirva, consulta com `sort` não roda: o CouchDB responde 400 `no_usable_index`, e a rota cai com
  erro 500, porque `ErroBanco` não é tratado como banco indisponível.

`use_index` aponta o índice pretendido, em vez de deixar a escolha para o planejador.

### `idx_produtos_catalogo` — catálogo (RF01)

```json
{ "index": { "fields": ["tipo", "ativo", "nome"] }, "name": "idx_produtos_catalogo", "ddoc": "idx_produtos_catalogo", "type": "json" }
```

```json
{
  "selector": { "tipo": "produto", "ativo": true },
  "fields": ["_id", "nome", "preco_centavos", "estoque", "torra",
             "nota_sensorial", "pontuacao_sca", "peso_g", "categoria"],
  "sort": [{ "tipo": "asc" }, { "ativo": "asc" }, { "nome": "asc" }],
  "use_index": ["_design/idx_produtos_catalogo", "idx_produtos_catalogo"],
  "limit": 100
}
```

`tipo` e `ativo` são igualdades; `nome` é a ordem alfabética do catálogo e vem por último. `fields`
é o `SELECT coluna` do Mango: o cartão não recebe `descricao` nem `reservas`
(`test_consulta_do_catalogo_traz_so_os_campos_do_cartao`). É a consulta mais frequente da loja; sem
o índice, cada visita à página inicial daria erro — ou, sem o `sort`, varreria o banco inteiro,
clientes e pedidos junto.

### `idx_produtos_categoria` — catálogo por região (RF01)

```json
{ "index": { "fields": ["tipo", "categoria_id", "nome"] }, "name": "idx_produtos_categoria", "ddoc": "idx_produtos_categoria", "type": "json" }
```

```json
{
  "selector": { "tipo": "produto", "categoria_id": "categoria:sul-de-minas", "ativo": true },
  "fields": ["_id", "nome", "preco_centavos", "estoque", "torra",
             "nota_sensorial", "pontuacao_sca", "peso_g", "categoria"],
  "sort": [{ "tipo": "asc" }, { "categoria_id": "asc" }, { "nome": "asc" }],
  "use_index": ["_design/idx_produtos_categoria", "idx_produtos_categoria"],
  "limit": 100
}
```

`categoria_id` entra entre `tipo` e `nome` pela mesma regra. O `ativo` está no seletor, mas não no
índice: o índice leva aos três cafés da região e o `ativo` é conferido em memória sobre eles — um
quarto campo não se pagaria. `idx_produtos_catalogo` não serve aqui: `[tipo, categoria_id, nome]`
não é prefixo de `[tipo, ativo, nome]`. Sem este índice, o filtro por região não roda.

### `idx_clientes_email` — login (RF04)

```json
{ "index": { "fields": ["tipo", "email"] }, "name": "idx_clientes_email", "ddoc": "idx_clientes_email", "type": "json" }
```

```json
{
  "selector": { "tipo": "cliente", "email": "ana@exemplo.com" },
  "use_index": ["_design/idx_clientes_email", "idx_clientes_email"],
  "limit": 1
}
```

Dois campos de igualdade, sem ordenação. O índice **não** garante e-mail único — quem garante é o
`_id` de `email:<endereço>` (`modelo_documental.md`, 3.4). Sem ele a consulta ainda roda, porque não
tem `sort`, mas vira varredura: o CouchDB lê o banco até achar o cliente, e um e-mail inexistente —
toda tentativa de login com endereço errado — leria o banco inteiro.

### `idx_pedidos_cliente` — Meus pedidos (RF06)

```json
{ "index": { "fields": ["tipo", "cliente_id", "criado_em"] }, "name": "idx_pedidos_cliente", "ddoc": "idx_pedidos_cliente", "type": "json" }
```

```json
{
  "selector": { "tipo": "pedido", "cliente_id": "cliente:3f6d2a9e-8c41-4b7e-9d25-6a0e1c7b4f83" },
  "sort": [{ "tipo": "desc" }, { "cliente_id": "desc" }, { "criado_em": "desc" }],
  "use_index": ["_design/idx_pedidos_cliente", "idx_pedidos_cliente"],
  "limit": 50
}
```

`criado_em` no fim entrega o pedido mais novo primeiro. Funciona porque `agora_iso()` grava toda
data em UTC, no mesmo formato ISO 8601 com milissegundos: a ordem do texto é a ordem do tempo.
`tipo` e `cliente_id` vão `desc` só porque o Mango não mistura direções; fixos no seletor, não
mudam o resultado. O filtro por `cliente_id` é também a segurança da tela
(`test_meus_pedidos_nao_mostra_pedido_de_outro_cliente`). Não há paginação: com `limit: 50`, só os
50 pedidos mais recentes aparecem. Sem o índice, a tela não abre.

## 3. O que ficou de fora de propósito

Todo índice a mais entra na conta do slide 7 da aula de NoSQL: "Índice ajuda leitura, mas custa
memória, storage e escrita". Ficaram de fora:

- **Pedidos `PENDENTE`.** A reconciliação procura `{"tipo": "pedido", "status": "PENDENTE"}` sem
  índice (`consulta_pedidos_pendentes()`). Um índice `[tipo, status]` receberia todo pedido e seria
  atualizado a cada gravação de pedido — duas por compra — para servir um comando manual, raro, que
  acha poucos documentos e não tem pressa. A consulta varre o banco com `limit: 500`. Se a
  reconciliação virar tarefa agendada, uma saída seria um índice parcial
  (`partial_filter_selector`), que guardaria só os pendentes.
- **`[tipo, nome]` para categorias.** As regiões saem de `_all_docs`, pela faixa de `_id` que começa
  com `categoria:` (`listar_por_prefixo`). O índice primário já existe e já ordena por `_id` — com
  slugs minúsculos e sem acento, a ordem alfabética. Não há índice secundário a manter.
- **Os índices do relacional que sumiram.** `idx_produtos_nome` virou o último campo de
  `idx_produtos_catalogo`. `idx_itens_pedido` não tem o que indexar: os itens chegam dentro do
  pedido, sem `JOIN`.
- **Um caminho que o modelo permitiria.** O login poderia ler `email:<endereço>` e depois o cliente
  pelo `_id`: duas leituras, sem índice. O código segue o padrão "cliente por e-mail: tipo + email"
  do slide 16 e usa `idx_clientes_email`.

## 4. Como provamos que cada consulta usa o índice certo

`test_indices_atendem_as_consultas`, em `tests/test_banco.py`, passa as cinco consultas de `app.py`
— as mesmas funções que as rotas chamam — para `banco.explicar()`, que faz `POST /<banco>/_explain`.
O `_explain` devolve o plano sem executar a consulta, e o teste confere o nome do índice escolhido
(`index.name`):

| Consulta | Índice esperado |
|---|---|
| `consulta_catalogo()` | `idx_produtos_catalogo` |
| `consulta_catalogo("categoria:sul-de-minas")` | `idx_produtos_categoria` |
| `consulta_cliente_por_email("ana@exemplo.com")` | `idx_clientes_email` |
| `consulta_pedidos_do_cliente("cliente:1")` | `idx_pedidos_cliente` |
| `consulta_pedidos_pendentes()` | `_all_docs` — sem índice, de propósito |

Se uma consulta mudar e perder o índice, o teste quebra antes de a loja varrer o banco em produção.
No job `couchdb-real` do GitHub Actions (`.github/workflows/testes.yml`), quem responde é o
planejador do Apache CouchDB 3.5 — essa é a prova. Sem `TEST_COUCHDB_URL`, responde o dublê
`tests/couchdb_falso.py`, que reimplementa a regra: pega regressão, mas não prova nada sobre o banco
real. `test_consultas_ordenadas_executam_de_verdade` roda de fato as consultas com `sort`.

Para repetir à mão, grave o corpo de uma consulta em `consulta.json` e rode (no PowerShell,
`curl.exe`):

```bash
curl -s -X POST http://admin:admin@127.0.0.1:5984/torra_terra/_explain -H "Content-Type: application/json" -d "@consulta.json"
```

No Cloudant, o endereço é a URL da credencial. No Fauxton (`http://127.0.0.1:5984/_utils/`), a tela
**Run A Query with Mango** do banco tem o botão **Explain**, que mostra o mesmo plano.

## 5. Consistência de leitura

A pergunta: logo depois de uma gravação, cada forma de acesso já enxerga o que foi gravado?

| Forma de acesso | Onde a loja usa | CouchDB de nó único (docker-compose, CI) | Cluster (Cloudant) |
|---|---|---|---|
| Lookup por `_id` | café, região, compra repetida, último pedido | imediato | lê por quórum: a maioria das cópias, que se cruza com a maioria que confirmou a gravação |
| `_all_docs` com `keys` | carrinho, checkout | imediato | cada chave é aberta como um lookup, com o mesmo quórum |
| `_all_docs` por faixa | regiões, reconciliação | imediato: o índice primário muda na própria gravação | lê uma cópia de cada shard, que pode ainda não ter recebido a gravação |
| `_find` com índice Mango | catálogo, login, Meus pedidos | imediato: o `_find` atualiza o índice antes de responder (`update`, padrão `true`) | eventualmente consistente: a cópia consultada pode estar atrasada |

A coluna do cluster segue o código do CouchDB 3.5 em cluster (`fabric_view_all_docs.erl` abre cada
chave com `fabric:open_doc`). O Cloudant é construído sobre o CouchDB, e a IBM o descreve como
eventualmente consistente. O atraso é característica do cluster, não do CouchDB de nó único.

Por isso **Meus pedidos** não confia só no índice (rota `meus_pedidos`, em `app.py`). O checkout
guarda o `_id` do pedido em `session["ultimo_pedido"]`; se a consulta a `idx_pedidos_cliente` não o
trouxer, a rota lê o pedido pelo `_id` e o põe no topo, depois de conferir que ele é do cliente
logado. No CouchDB de nó único esse GET não faz falta; no Cloudant, cobre a janela de atraso.

As outras leituras toleram atraso. O checkout grava com `_rev`: um café lido velho esbarra no 409 e
é relido. A reconciliação relê cada documento pelo `_id` antes de gravar (`banco.atualizar`) e
decide pelo estado do pedido, lido também pelo `_id`. As categorias só mudam no `seed-db`. E o login
logo depois do cadastro não passa pelo índice: o cadastro já abre a sessão.

## 6. Custo no Cloudant Lite

O plano Lite limita a vazão por segundo, por classe de requisição. A classificação é a da
documentação da IBM:

| Classe | Limite | O que conta | Onde a loja usa |
|---|---|---|---|
| Leitura | 20/s | lookup por `_id`; `_bulk_get`; consultas particionadas | café, região, adicionar ao carrinho, compra repetida, último pedido |
| Escrita | 10/s | criar, alterar ou apagar documento; `_bulk_docs` conta por documento | cadastro, pedido, reservas, limpeza, compensação |
| Consulta global | 5/s | `_all_docs`, `_view`, `_search` e `_find` fora de `_partition`, uma por requisição | catálogo, regiões, carrinho, checkout, login, Meus pedidos, reconciliação |

**`_all_docs` não é leitura barata no Cloudant.** Num banco global, ele cai na mesma classe do
`_find`. Sua vantagem sobre o `_find` é outra: não há índice secundário para manter, e com `keys` a
leitura é por quórum. Quem poupa a cota de consultas globais são os lookups por `_id` — página do
café, região por referência, compra repetida, último pedido. O nome na sessão poupa uma leitura por
página (N11).

| Rota (k = cafés distintos no carrinho) | Requisições | Leituras | Escritas | Consultas globais |
|---|---|---|---|---|
| `GET /`, com ou sem região | 2 | 0 | 0 | 2 |
| `GET /produto/<slug>` | 2 | 2 | 0 | 0 |
| `POST /carrinho/adicionar/<slug>` | 1 | 1 | 0 | 0 |
| `GET /carrinho` ou `GET /checkout` | 1 | 0 | 0 | 1 |
| `POST /login` | 1 | 0 | 0 | 1 |
| `POST /cadastro` | 2 | 0 | 2 | 0 |
| `POST /checkout` | 8 | 1 | 2k + 2 | 3 |
| `GET /meus-pedidos` | 1 ou 2 | 0 ou 1 | 0 | 1 |

O `GET /saude` faz um `GET /torra_terra`, que não aparece entre os exemplos das três classes.

**Um checkout, requisição por requisição** (`checkout` e `finalizar_pedido`, em `app.py`):

1. `POST _all_docs` com `keys` — `carrinho_detalhado()`, na rota, antes da saga;
2. `GET pedido:<chave>` — compra repetida? 404 esperado;
3. `POST _all_docs` com `keys` — fase 1, ler e validar;
4. `PUT pedido:<chave>` — fase 2, `PENDENTE`;
5. `POST _bulk_docs` com k cafés — fase 3, baixa e marca;
6. `PUT pedido:<chave>` com `_rev` — fase 4, `CRIADO`;
7. `POST _all_docs` com `keys` — fase 5, relê os cafés;
8. `POST _bulk_docs` com k cafés — fase 5, tira as marcas.

São oito porque a rota lê o carrinho antes da saga e a limpeza relê os cafés (`_limpar_marcas` não
reaproveita a leitura da fase 3). Com dois cafés: 6 escritas, 60% do limite de um segundo, e 3
consultas globais — a quarta vem no redirecionamento para Meus pedidos. Cada rodada de conflito na
fase 3 soma um `_all_docs` e um `_bulk_docs`.

**Quando passa do limite.** O Cloudant responde HTTP 429 antes de processar, então repetir é seguro
até para escrita. `_requisitar`, em `banco.py`, faz até 4 tentativas: espera o `Retry-After` quando
ele vem (no máximo 5 s) ou uma espera sorteada que dobra a cada vez (até 0,1 s, 0,2 s e 0,4 s). Se o
429 persistir, vira `BancoIndisponivel`: a página "Voltamos em instantes", com 503, ou, nas fases 3
e 4 do checkout, a compensação. `test_limite_de_vazao_429_e_repetido` prova a repetição.

Na prática, a página inicial gasta 2 das 5 consultas globais por segundo — três visitas no mesmo
segundo já esbarram no limite — e uma compra completa gasta 4. Opções, **não implementadas**: um
`_bulk_get` em `obter_varios` levaria carrinho e checkout para a classe de leitura (20/s), e a fase
1 poderia reaproveitar os cafés que a rota acabou de ler.
