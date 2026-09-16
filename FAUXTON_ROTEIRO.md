# Roteiro Fauxton — evidências do Torra & Terra

O Fauxton é a interface web do CouchDB. Este roteiro produz as evidências do
material — documentos e revisões (slide 20), Mango e conflito (slide 42) e a
Entrega 5, "teste de conflito" —, cada passo com o que fazer, observar e printar.

## Antes de começar

**CouchDB local** (precisa de Docker; Fauxton em `http://127.0.0.1:5984/_utils/`,
usuário `admin`, senha `admin`), com `COUCHDB_URL=http://admin:admin@127.0.0.1:5984`
no `.env`:

```bash
docker compose up -d
flask --app app init-db
flask --app app seed-db
```

**CouchDB no Railway (produção):** o Fauxton fica em
`https://couchdb-production-ed47.up.railway.app/_utils`, com login do `admin`.
É o mesmo Fauxton do CouchDB local, então os passos 1 a 8 valem igual; 9 e 10
usam a loja e o terminal; 11 copia o banco do Railway para o CouchDB local.

**Cuidados:** nenhum print mostra URL com usuário e senha, a tela de login com
a senha digitada, as variáveis do Railway ou do Vercel, ou o `.env`. Os passos
3, 8, 9 e 10 gravam no banco: no Railway, use um banco só de evidências
(`COUCHDB_DATABASE=torra_terra_evidencias`, mais `init-db` e `seed-db`), para
não mexer no estoque da loja que está no ar. O `seed-db` devolve os cafés ao
estoque inicial.

## 1. Os tipos pelo prefixo do `_id`

- **Fazer:** em **Databases**, abra `torra_terra` (abre em **All Documents**).
- **Observar:** em ordem de `_id`, a lista sai agrupada por tipo: `_design/`,
  `categoria:`, `cliente:`, `email:`, `pedido:`, `produto:` (os do meio vêm com
  o uso da loja). Daí as regiões saírem da faixa `categoria:` do índice primário.
- **Print:** a lista com os prefixos.

## 2. Um documento e o seu `_rev`

- **Fazer:** abra `produto:chapada-geisha`.
- **Observar:** `_id` legível, `"preco_centavos": 14800` (centavos inteiros),
  região embutida em `categoria` e referenciada em `categoria_id`,
  `"reservas": {}` e o `_rev`, algo como `1-<hash>`: o número conta as versões.
- **Print:** o JSON com `_id` e `_rev`.

## 3. Editar e ver o `_rev` mudar

- **Fazer:** troque `"estoque": 1` por `3` e clique em **Save Changes**; o
  Fauxton volta para a lista, então reabra o documento.
- **Observar:** *Document saved successfully.* e o `_rev` com o número seguinte
  e outro hash. Na loja, o café passa a mostrar "Últimas 3 unidades".
- **Print:** o `_rev` antes e depois.

## 4. Consulta Mango com índice e o Explain

- **Fazer:** **Run A Query with Mango**, cole a consulta do catálogo (a de
  `consulta_catalogo()`, com menos campos e sem `use_index`), **Run Query** e
  depois **Explain**.

  ```json
  {
    "selector": { "tipo": "produto", "ativo": true },
    "fields": ["_id", "nome", "preco_centavos", "estoque"],
    "sort": [{ "tipo": "asc" }, { "ativo": "asc" }, { "nome": "asc" }]
  }
  ```

- **Observar:** os 12 cafés em ordem alfabética, só com os campos pedidos. No
  Explain, **Selected Index** é `idx_produtos_catalogo`, escolhido pelo próprio
  CouchDB; o botão **JSON** mostra o mesmo em `"index"`.
- **Print:** o resultado e o Explain.

## 5. A mesma consulta sem índice

- **Fazer:** apague a linha do `sort` (e a vírgula antes dela) e rode de novo.
- **Observar:** o aviso *No matching index found, create an index to optimize
  query time.* e mais *Documents examined* em **Execution Statistics**. No
  Explain, o escolhido vira `_all_docs`, a varredura, e `idx_produtos_catalogo`
  vai para **Unsuitable Indexes** com `field_mismatch`: índice JSON só serve se
  todos os seus campos estão no seletor ou na ordenação — daí o `sort` da loja.
- **Print:** o aviso e o Explain.

## 6. Os índices e a `validate_doc_update`

- **Fazer:** na tela do Mango, **Manage Indexes**; depois abra `_design/regras`
  (topo de All Documents, ou `http://127.0.0.1:5984/torra_terra/_design/regras`).
- **Observar:** os quatro índices de `couchdb/indices.json` e o `_all_docs`, o
  índice primário. Em `_design/regras`, a `validate_doc_update` como texto: é o
  `couchdb/validacao.js` gravado pelo `init-db`, que regrava se alguém mexer.
- **Print:** a lista de índices e o `_design/regras`.

## 7. O banco recusa um documento inválido (403)

- **Fazer:** em `produto:chapada-geisha`, troque o estoque por `-1` e salve.
- **Observar:** *Save failed: estoque precisa ser inteiro e maior ou igual a
  zero*, frase da `validate_doc_update`: o CouchDB recusou com HTTP 403, sem a
  loja no caminho. **Cancel** e reabra: nada mudou. Repita com `"torra": "QUEIMADA"`.
- **Print:** a mensagem de erro com o JSON editado.

## 8. Conflito em duas abas (409) — Entrega 5

- **Fazer:** abra `produto:chapada-geisha` em duas abas. Na A, estoque `4`, e
  salve. Na B, que ainda tem o `_rev` antigo, estoque `5`, e salve.
- **Observar:** a A grava; a B mostra *Save failed: Document update conflict.*
  Não há trava: a primeira gravação vence, e a outra precisa reler e decidir de
  novo — o que a loja faz na reserva de estoque (`atualizar_varios`, `banco.py`).
- **Print:** a aba B com a mensagem e o `_rev` antigo.

## 9. As revisões de um café depois de uma compra

- **Fazer:** suba a loja (`flask --app app run --debug`), crie uma conta e
  compre 2 Piatã Altitude. No navegador em que entrou no Fauxton, abra
  `http://127.0.0.1:5984/torra_terra/produto:piata-altitude?revs_info=true`.
- **Observar:** duas revisões novas. Troque `?revs_info=true` por
  `?rev=<a do meio>`: estoque baixado e a marca `"reservas": {"pedido:<chave>": 2}`
  (fase 3 da saga); a atual tem `"reservas": {}` (fase 5). O `pedido:<chave>`
  está `CRIADO`, com `historico` e itens com nome e preço copiados. Revisão
  antiga some na compactação (`"status": "missing"`): `_rev` não é histórico.
- **Print:** o `_revs_info`, a revisão com a marca e o pedido.

## 10. Um pedido cancelado pela compensação

Pela loja, a compensação depende de uma corrida de milissegundos. Para provocá-la
no banco **local**, reproduza `test_cafe_que_acaba_no_meio_da_saga_e_compensado`
em `flask --app app shell`, com o e-mail do passo 9: outra compra leva o Geisha.

```python
from app import banco, buscar_cliente_por_email, finalizar_pedido
b = banco()
lote_original = b.gravar_lote
def outra_compra_leva_o_geisha(docs):
    b.salvar(dict(b.obter("produto:chapada-geisha"), estoque=0))
    b.gravar_lote = lote_original
    return lote_original(docs)

b.gravar_lote = outra_compra_leva_o_geisha
cliente = buscar_cliente_por_email("seu-email@exemplo.com")
finalizar_pedido(cliente["_id"], [
    {"produto_id": "produto:piata-altitude", "quantidade": 2, "moagem": "FINA"},
    {"produto_id": "produto:chapada-geisha", "quantidade": 1, "moagem": "GRAO"},
])
```

- **Observar:** o shell termina com `EstoqueInsuficiente`. O pedido novo está
  `CANCELADO`, com o motivo no último evento do `historico`; o Piatã voltou ao
  estoque de antes, com `"reservas": {}` e duas revisões novas, reserva e
  devolução. Em Meus pedidos ele aparece cancelado, com o motivo. Mude o
  `status` para `CRIADO` no Fauxton: *Save failed: pedido cancelado não volta a valer*.
- **Print:** o pedido com o `historico` e a tela Meus pedidos.

## 11. Replicação: backup do Railway no CouchDB local

- **Fazer:** no Fauxton **local**, **Replication** → **New Replication**.
  Origem *Remote database*: a URL do Railway **sem** usuário e senha
  (`https://couchdb-production-ed47.up.railway.app/torra_terra`), autenticação
  *Username and password* com o `admin` do Railway. Destino *New local
  database*, `torra_terra_backup`; tipo *One time*; **Start Replication** (se
  pedir, a senha do `admin` local).
- **Observar:** a replicação concluída em **Replicator DB Activity**; em
  **Databases**, a mesma contagem de documentos do Railway, e cada documento
  com o **mesmo `_rev`** — replicar copia revisões, não só conteúdo. Para testar
  a restauração, rode a loja com `COUCHDB_DATABASE=torra_terra_backup`.
- **Print:** a replicação concluída e as duas contagens, com a URL cortada.

O documento em `_replicator` guarda a credencial em base64, reversível: nada de
print dele, e apague-o ao terminar. Sem Docker, replique no próprio Fauxton do
Railway para um banco novo — prova o mecanismo, mas não é backup: a cópia fica
no mesmo servidor e no mesmo volume.

## Os mesmos testes pelo terminal

Banco local, em bash (Git Bash, WSL, Linux, macOS). No PowerShell, use
`curl.exe` e passe o JSON por arquivo: `--data-binary "@consulta.json"`.

```bash
BANCO=http://admin:admin@127.0.0.1:5984/torra_terra; JSON="Content-Type: application/json"

# 409: o 1º PUT grava (201); o 2º leva o mesmo _rev, já velho: "Document update conflict."
curl -s "$BANCO/produto:chapada-geisha" -o backup_geisha.json   # backup*.json está no .gitignore
for vez in 1 2; do curl -s -w " HTTP %{http_code}\n" -X PUT "$BANCO/produto:chapada-geisha" -H "$JSON" --data-binary @backup_geisha.json; done

# 403: "forbidden", "estoque precisa ser inteiro e maior ou igual a zero"; um GET depois dá 404
curl -s -w " HTTP %{http_code}\n" -X PUT "$BANCO/produto:teste-invalido" -H "$JSON" \
  -d '{"tipo":"produto","nome":"Teste","preco_centavos":1000,"estoque":-1,"torra":"CLARA","peso_g":250,"ativo":true,"categoria_id":"categoria:sul-de-minas"}'

# _find sem "warning"; _explain com "idx_produtos_catalogo". Sem o "sort": "warning" e "_all_docs"
CONSULTA='{"selector":{"tipo":"produto","ativo":true},"fields":["_id","nome"],"sort":[{"tipo":"asc"},{"ativo":"asc"},{"nome":"asc"}]}'
curl -s -X POST "$BANCO/_find" -H "$JSON" -d "$CONSULTA"
curl -s -X POST "$BANCO/_explain" -H "$JSON" -d "$CONSULTA"
```

## Checklist de prints

- [ ] 01 — `torra_terra` em All Documents, com os prefixos de tipo
- [ ] 02 — `produto:chapada-geisha` com `_id`, centavos e o `_rev` antes e depois da edição
- [ ] 03 — consulta do catálogo e Explain com `idx_produtos_catalogo`
- [ ] 04 — a mesma consulta sem `sort`: o aviso e o `_all_docs`
- [ ] 05 — os quatro índices e o `_design/regras`
- [ ] 06 — *Save failed* com a mensagem da `validate_doc_update` (403)
- [ ] 07 — *Document update conflict.* na segunda aba (409)
- [ ] 08 — `_revs_info` do café, a revisão com a marca e o pedido `CRIADO`
- [ ] 09 — pedido `CANCELADO` com o motivo, no Fauxton e em Meus pedidos
- [ ] 10 — replicação concluída e contagens iguais
- [ ] 11 — saídas do terminal: 409, 403 e `_explain`
