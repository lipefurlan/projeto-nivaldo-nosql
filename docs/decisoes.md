# Decisões de projeto — versão NoSQL

Registro das escolhas que fogem do óbvio ou do material da disciplina, com a
justificativa. Serve para a defesa oral: toda decisão aqui tem um "por quê".

As decisões do projeto relacional (D01 a D08) estão em
`docs/decisoes.md` na tag `v1-relacional`. As desta versão usam o prefixo **N**.

---

## N01 — CouchDB, e não MongoDB

A aula teórica de NoSQL usa o MongoDB como exemplo de banco de documentos, mas
o **projeto prático** é explícito: "e-commerce com Apache CouchDB". Os
critérios de avaliação pesam "CouchDB/índices" em 20%, e as entregas pedem
Fauxton, `_rev`, Mango e teste de conflito — tudo específico do CouchDB.

---

## N02 — Vercel + IBM Cloudant, no lugar do Railway

**Motivo:** custo. O Railway cobra por uso, e manter a aplicação e mais um
banco rodando passou a pesar.

- O slide 48 lista para o Flask "Render/Railway/Fly/VM — verificar cota
  vigente". O Vercel ocupa o mesmo papel e roda Flask sem configuração.
- Para o banco, o mesmo slide cita o **IBM Cloudant** como opção compatível.
  O plano Lite é gratuito e não expira.
- O Vercel não hospeda banco nenhum; um CouchDB próprio exigiria uma VM. O
  Cloudant resolve sem operação.
- A função do Vercel (Washington, D.C.) e o Cloudant (Washington DC) ficam na
  mesma região.

**Custos aceitos.** A conta da IBM exige cartão para verificação. O plano Lite
limita a vazão (10 escritas/s, 5 consultas/s), e por isso o `banco.py` trata o
HTTP 429. Uma função serverless pode morrer entre duas gravações, e por isso
existe a reconciliação.

**Plano B.** Um CouchDB em container (numa VM ou no próprio Railway) fala a
mesma API: basta trocar o `COUCHDB_URL`. Nenhuma linha de código muda.

---

## N03 — Repositório novo, com o histórico do relacional

A comparação entre os dois projetos é metade do objetivo do material. Copiar o
repositório preserva o histórico inteiro, com a autoria do grupo, e a tag
`v1-relacional` marca o último estado PostgreSQL. A versão NoSQL começa
logo depois, e o `git log` conta a migração passo a passo.

Saíram da árvore os artefatos que descreviam o PostgreSQL (SQL/, apresentação,
relatório, capturas, decks). Continuam na tag e no repositório original.

---

## N04 — O domínio continua sendo café especial

O projeto-base do professor vende teclado, mouse e mochila. Mantivemos o café
por três motivos: a comparação fica direta (mesmo domínio nos dois bancos); a
**moagem escolhida na compra** continua sendo o argumento central da
modelagem; e o catálogo de produção migrou pronto (`docs/cafes.json`).

---

## N05 — HTTP puro com `requests`, sem biblioteca de CouchDB

O material usa `requests` e um wrapper HTTP centralizado (slides 29 e 32).
Antes de escrever o `banco.py` procuramos no GitHub bibliotecas e projetos
Flask + CouchDB: nada acima de 50 estrelas nem mantido. O SDK oficial do
Cloudant existe, mas prenderia o código ao Cloudant e esconderia exatamente o
que o trabalho precisa mostrar — o `_rev`, o 409 e o `_bulk_docs`.

---

## N06 — Dinheiro em centavos inteiros

JSON não tem tipo decimal, e o JavaScript do CouchDB trata todo número como
ponto flutuante binário, que não representa 0,10 de forma exata. Guardar
`preco_centavos: 14800` mantém a regra do relacional — "nunca FLOAT para
dinheiro" — num formato que o JSON representa sem erro. A conversão para
`R$ 148,00` acontece só na tela.

---

## N07 — Regras no banco, com `validate_doc_update`

No relacional, a regra D05 dizia: a constraint vive no banco. O CouchDB não
tem `CHECK`, mas executa uma função JavaScript a cada gravação e recusa o
documento com HTTP 403 se ela lançar erro. `couchdb/validacao.js` é essa
função; ela vale para a loja, para o Fauxton, para um `curl` e para a
replicação.

A aplicação confere as mesmas regras antes de gravar (`validar_produto`,
`validar_pedido`), só para dar mensagem amigável e poupar uma ida ao banco.

A função vai além do que o `CHECK` fazia: depois de gravado, os itens de um
pedido não mudam mais, e um pedido cancelado não volta a valer.

---

## N08 — E-mail único por documento-chave

O CouchDB não tem `UNIQUE`. A única unicidade garantida é a do `_id`. Por
isso o cadastro grava primeiro `email:<endereço>`; se outro cadastro chegou
antes, o banco responde 409. Uma consulta prévia ao índice não bastaria:
entre ela e a gravação, outro cadastro pode entrar.

Se o cliente não chega a ser gravado, o documento do e-mail é apagado na hora
(compensação). Se a função morrer entre as duas gravações, a reconciliação
libera o e-mail depois.

---

## N09 — Checkout como saga, com marcas de reserva

Sem transação entre documentos, o tudo-ou-nada virou responsabilidade nossa:
pedido `PENDENTE`, reserva de estoque com uma marca `{pedido_id: quantidade}`
no próprio café, confirmação e, se algo falhar, compensação. A marca é o que
torna a compensação exata depois de um timeout. Detalhes em
[`checkout_saga.md`](checkout_saga.md).

O pedido cancelado **fica registrado**, com o motivo no histórico, em vez de
ser apagado. No relacional o ROLLBACK não deixava rastro; aqui o rastro é a
trilha de auditoria de uma compra que começou e não fechou.

---

## N10 — Testes com dublê em memória e com CouchDB de verdade

A regra do relacional era testar contra o banco de verdade (não SQLite). Ela
continua: o GitHub Actions roda a suíte inteira contra o Apache CouchDB 3.5.

A máquina do grupo não tem Docker, então a mesma suíte também roda contra um
dublê em memória (`tests/couchdb_falso.py`), que substitui a rede e reproduz
`_rev`, 409, `_bulk_docs`, `_find` e a regra de uso de índices do Mango. Ele
não executa JavaScript: os testes da `validate_doc_update` só rodam contra o
CouchDB de verdade — no CI ou com `TEST_COUCHDB_URL`.

Os cenários de concorrência forçam o 409 trocando um método do cliente HTTP
na hora certa, e isso funciona igual nos dois modos.

---

## N11 — O cliente logado vem da sessão

No relacional, toda página buscava o cliente no banco só para escrever "Sair"
no menu. No Cloudant isso seria uma leitura cobrada por página. O nome do
cliente é gravado na sessão no login; o banco só é consultado quando a página
precisa de dados de verdade.
