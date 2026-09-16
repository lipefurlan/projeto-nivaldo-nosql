# Torra & Terra — E-commerce de café especial (versão NoSQL)

Projeto acadêmico da disciplina **Tratamento e Armazenamento da Informação** (FACAMP, Prof. Nivaldo T. Marcusso). Segundo projeto: a mesma loja do projeto relacional, agora sobre um **banco de documentos (Apache CouchDB)**. O objetivo do material é comparar na prática o ciclo NoSQL com o relacional — a versão PostgreSQL está na tag `v1-relacional` e é o outro lado da comparação, não código morto.

## Regras que valem para o projeto inteiro

1. **Tudo em português** — documentos, campos, rotas, variáveis, mensagens, comentários, commits.  
2. **Stack fechada.** Python, Flask, Jinja, requests, python-dotenv, pytest. Banco: Apache CouchDB 3.5 local (docker-compose) e IBM Cloudant em produção — mesma API HTTP. Deploy: Vercel. Nada além disso sem me perguntar antes (sem ORM, sem SDK do Cloudant, sem gunicorn).  
3. **As regras vivem no banco também.** `couchdb/validacao.js` (a `validate_doc_update`, JavaScript ES5) é onde o CouchDB aplica as regras que no relacional eram `CHECK`. `validar_produto` e `validar_pedido` em `app.py` espelham as mesmas regras. Mudou uma regra: muda nos dois lugares e no teste.  
4. **Comente decisões, não o óbvio.** Um comentário explicando por que a marca de reserva mora no documento do café vale mais que dez dizendo `# grava o pedido`.  
5. **Nenhum segredo no código.** `COUCHDB_URL`, `COUCHDB_IAM_APIKEY` e `SECRET_KEY` sempre de variável de ambiente. `.env` no `.gitignore`, `.env.example` versionado. Credencial nunca aparece em log: o `banco.py` tira usuário e senha da URL.  
6. **Rode os testes e me mostre a saída real.** Nunca afirme que passou sem ter rodado. Sem `TEST_COUCHDB_URL`, a suíte roda contra o dublê em memória e **pula** os testes da `validate_doc_update` — diga isso ao mostrar o resultado. A prova contra CouchDB de verdade é o GitHub Actions.  
7. **Pare entre as etapas.** Construa na ordem combinada e espere meu OK antes de seguir.

## Modelo de dados

Documentos JSON num único banco (`torra_terra`), com `tipo`, `_id` legível prefixado pelo tipo e `versao_esquema`:

categoria  _id categoria:<slug>   nome, regiao, descricao

produto    _id produto:<slug>     nome, descricao, preco_centavos, estoque, torra,
                                   nota_sensorial, pontuacao_sca, peso_g, ativo,
                                   categoria_id, categoria {nome, regiao}, reservas {}

cliente    _id cliente:<uuid>     nome, email, senha_hash, criado_em

email      _id email:<endereço>   cliente_id, criado_em

pedido     _id pedido:<chave>     cliente_id, status, total_centavos, criado_em,
                                   itens [{produto_id, nome, moagem, quantidade,
                                           preco_unitario_centavos}],
                                   historico [{status, em, motivo?}]

Regras obrigatórias (na `validate_doc_update` e na aplicação):

- `_id` começa pelo `tipo` — é o que permite listar uma família pelo índice primário  
- Dinheiro em **centavos inteiros** (`preco_centavos`, `preco_unitario_centavos`, `total_centavos`) — nunca float  
- `estoque` inteiro `>= 0`; `pontuacao_sca` entre 80 e 100; `peso_g > 0`; `ativo` booleano  
- `torra` em `CLARA`, `MEDIA`, `ESCURA`; `moagem` em `GRAO`, `MEDIA`, `FINA`  
- `status` em `PENDENTE`, `CRIADO`, `PAGO`, `ENVIADO`, `CANCELADO`  
- `quantidade > 0`; `total_centavos` igual à soma dos itens; mesmo café na mesma moagem é uma linha só  
- Cliente nunca grava `senha`, só `senha_hash`; e-mail em minúsculas  
- Pedido gravado não muda os itens; pedido `CANCELADO` não volta a valer

Índices Mango — só estes, cada um justificado pela consulta em `couchdb/indices.json`: `idx_produtos_catalogo`, `idx_produtos_categoria`, `idx_clientes_email`, `idx_pedidos_cliente`. Categorias saem do índice primário (`_all_docs` com faixa `categoria:`). A consulta de pedidos `PENDENTE` da reconciliação roda sem índice de propósito. O teste `test_indices_atendem_as_consultas` confere tudo via `_explain`.

**Por que `moagem` mora no item:** o cliente escolhe a moagem na hora da compra. Preserve isso.

**Por que os itens são embutidos e o nome e o preço são snapshot:** o item é lido junto com o pedido e nunca sozinho; se o preço do café mudar amanhã, o pedido antigo mantém o valor da época.

**Por que existe o documento `email:<endereço>`:** o CouchDB não tem `UNIQUE`; a única unicidade garantida é a do `_id`.

## O checkout: uma saga

O coração do trabalho. O CouchDB não tem transação entre documentos, então o tudo-ou-nada é da aplicação:

1. ler e validar tudo, sem gravar  ·  estoque insuficiente → nada gravado, mensagem dizendo **qual** café faltou  
2. gravar o pedido `PENDENTE` com `_id` = chave de idempotência  ·  segundo clique → 409 → sem pedido duplicado  
3. reservar estoque num `_bulk_docs`, com a marca `reservas: {pedido_id: quantidade}` no café  ·  409 → relê e tenta de novo  
4. marcar o pedido `CRIADO` — o ponto sem volta  
5. limpar as marcas (melhor esforço)

Falha em 3 ou 4 → compensação: primeiro o pedido vira `CANCELADO` (com motivo no histórico), depois o estoque volta **só onde há marca**. Se ao reler o pedido ele já está `CRIADO`, nada é desfeito. `flask --app app reconciliar` termina sagas interrompidas. Tudo idempotente, cada cenário com teste em `tests/test_checkout.py`.

## Design — linguagem visual

Minimalismo quente e editorial. Muito espaço em branco, blocos modulares, hierarquia por peso de fonte e não por caixa colorida. Nada de cinza corporativo, nada de gradiente, nada de sombra pesada.

:root {

  --bg:      #faf9f7;   /* off-white quente, fundo de tudo */

  --surface: #ffffff;   /* cards */

  --ink:     #14120f;   /* quase-preto, texto principal */

  --muted:   #6b6560;   /* texto secundário */

  --line:    #e8e4de;   /* bordas — 1px, nunca mais */

  --accent:  #d4622a;   /* laranja torra: CTA, preço, destaque */

  --accent-soft: #fbf0e9;

  --r-sm: 6px;  --r-md: 10px;  --r-lg: 16px;

  --space: 8px; /* escala 8 / 16 / 24 / 40 / 64 / 96 */

}

- **Tipografia:** `Inter` (Google Fonts) para interface e corpo. `Instrument Serif` só para o nome da loja e os títulos de produto — é o toque editorial que combina com café especial. Fallback: `system-ui, sans-serif` / `Georgia, serif`.  
- **Botões:** primário sólido `--accent` com texto branco, raio `--r-md`, sem sombra. Secundário com borda `1px solid var(--line)` e fundo transparente.  
- **Cards de produto:** fundo `--surface`, borda 1px, raio `--r-lg`, padding 24px. No hover, só a borda escurece — sem levantar, sem escalar.  
- **Preço:** peso 600, cor `--accent`, sempre formatado `R$ 0,00`.  
- **Labels pequenas** (torra, região, pontuação SCA): caixa alta, 11px, letter-spacing 0.08em, cor `--muted`.  
- **Layout:** container de 1120px, grade de 3 colunas no catálogo, 1 coluna no mobile. Mobile-first de verdade — o professor pode abrir no celular.  
- CSS puro num único `public/static/style.css` (a pasta que o Vercel serve do CDN). Sem Tailwind, sem Bootstrap, sem framework de componente.  
- Acessibilidade: contraste mínimo AA, `<label>` em todo input, foco visível.

**Não** invente logo com órbita, elipse ou esfera — essa é a identidade de outro projeto meu e não entra aqui. A marca do Torra & Terra é só o nome em `Instrument Serif`, com o "&" em `--accent`.

## Estrutura

├── app.py · banco.py · requirements.txt · requirements-dev.txt · pytest.ini

├── .env.example · docker-compose.yml · .vercelignore · FAUXTON_ROTEIRO.md · README.md

├── couchdb/{validacao.js, indices.json, seed.json}

├── templates/{base,catalogo,produto,cadastro,login,carrinho,checkout,meus_pedidos,indisponivel}.html

├── public/static/style.css

├── tests/{conftest,apoio,couchdb_falso,test_checkout,test_validacao,test_banco,test_rotas,test_seguranca}.py

├── .github/workflows/testes.yml

└── docs/{requisitos,modelo_documental,consultas_e_indices,checkout_saga,comparacao_relacional_nosql,decisoes,deploy_vercel,plano_nosql}.md

## Ambiente

- **Local:** `docker compose up -d` (CouchDB 3.5, Fauxton em `http://127.0.0.1:5984/_utils/`) ou o próprio Cloudant pelo `.env`. Esta máquina **não tem Docker**.  
- **Produção:** Vercel (Flask detectado pelo `app.py`, região `iad1`) + IBM Cloudant Lite (Washington DC). `VERCEL=1` liga o modo produção; sem `SECRET_KEY` ou `COUCHDB_URL` a loja não sobe.  
- O `.env` é carregado com caminho explícito: esta pasta mora dentro do projeto relacional, que tem outro `.env`.  
- Cloudant Lite: 10 escritas/s e 5 consultas globais/s — o `banco.py` repete no 429. Prefira lookup por `_id` e `_all_docs` a `_find` quando der.

Comandos: `flask --app app init-db` · `seed-db` · `reset-db` · `reconciliar`

Guia de deploy: `docs/deploy_vercel.md`.
