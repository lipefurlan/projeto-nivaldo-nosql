# Plano — Projeto NoSQL com CouchDB

> **Atualização de 16/09/2026 — plano executado.** Este documento foi escrito
> antes da migração e fica como registro. As decisões da seção 5 foram
> tomadas assim:
>
> 1. **Repositório novo**, copiado do relacional com todo o histórico; o
>    estado PostgreSQL ficou na tag `v1-relacional`.
> 2. **Tema café mantido.**
> 3. **Sem Docker nesta máquina:** os testes rodam contra um dublê em memória
>    localmente e contra o Apache CouchDB 3.5 de verdade no GitHub Actions. O
>    `docker-compose.yml` continua no projeto para quem tem Docker.
> 4. **Deploy no Vercel, com o CouchDB num container do Railway.** O site saiu
>    do Railway por custo. O IBM Cloudant foi o primeiro plano para o banco e
>    ficou de fora porque a conta exige cartão de crédito; o CouchDB entrou no
>    Railway sem mudar nenhuma linha de código.
>
> A seção 2.2 virou o centro do trabalho: a saga do checkout, com marcas de
> reserva e compensação, está em `docs/checkout_saga.md`. As justificativas
> estão em `docs/decisoes.md`.

Segundo projeto da disciplina: **CouchCommerce**, o mesmo e-commerce, agora
sobre um banco de documentos. O objetivo declarado no material é
*"comparar na prática o ciclo de desenvolvimento de uma aplicação NoSQL com o
projeto relacional"* — ou seja, **o projeto anterior não é lixo: é o outro lado
da comparação**, e metade da nota vem de saber explicar a diferença.

---

## 1. O que já temos e transfere direto

| O que | Estado | Aproveita? |
|---|---|---|
| Domínio: 12 cafés, 4 regiões, moagem, SCA | pronto | **100%** — vira JSON |
| `static/style.css` (680 linhas) | pronto | **100%** — a identidade não muda |
| Templates (base, catálogo, produto, carrinho, checkout, pedidos) | prontos | **~85%** — muda só o acesso aos dados |
| Requisitos, critérios de aceite, backlog | prontos | **~80%** — os RF batem quase 1:1 |
| Segurança: hash, CSRF, cabeçalhos, segredos em env | pronto | **100%** — o material pede o mesmo |
| Deploy no Railway + domínio + HTTPS | pronto | **~90%** — muda o serviço de banco |
| Apresentação, relatório, roteiro de evidências | prontos | **~70%** — reescrever a parte de banco |

**Tradução dos requisitos.** Os RF do material NoSQL são os nossos, com outro
número:

| Material NoSQL | O nosso | Situação |
|---|---|---|
| RF01 Listar produtos | RF01 Catálogo | feito |
| RF02 Cadastrar cliente | RF04 Cadastro | feito |
| RF03 Autenticar | RF04 Login | feito |
| RF04 Finalizar pedido | RF05 Checkout | **é aqui que tudo muda** |
| RF05 Consultar histórico | RF06 Meus pedidos | feito |

> Nosso RF02 (detalhe do produto) e RF03 (carrinho com moagem) não têm
> equivalente numerado no material NoSQL, mas continuam valendo — e a moagem
> segue sendo o nosso diferencial.

---

## 2. As diferenças reais

O material dedica dois slides inteiros à comparação (40 e 56). Esta é a tabela
que vale decorar:

| Tema | PostgreSQL (feito) | CouchDB (novo) |
|---|---|---|
| **Estrutura** | 5 tabelas normalizadas | documentos JSON com campo `tipo` |
| **Identidade** | `SERIAL` gerado pelo banco | `_id` que **nós** escolhemos: `produto:geisha` |
| **Relacionamento** | `FOREIGN KEY` + `JOIN` | **embed** (itens dentro do pedido) ou **reference** (`cliente_id`) |
| **Consulta** | SQL | Mango (JSON) via HTTP `POST /_find` |
| **Índice** | `CREATE INDEX` B-tree | `POST /_index` com lista de campos |
| **Regras de negócio** | `CHECK`, `NOT NULL`, `UNIQUE` **no banco** | **não existem no banco** — sobem para a aplicação |
| **Concorrência** | `SELECT … FOR UPDATE` (trava pessimista) | `_rev` + HTTP **409** (otimista, sem trava) |
| **Unidade de consistência** | a transação, quantas tabelas quiser | **um único documento** |
| **Tudo-ou-nada** | `COMMIT` / `ROLLBACK`, garantido pelo banco | **não existe** entre documentos |
| **Admin** | psql / pgAdmin | Fauxton (web) |
| **Setup local** | PostgreSQL instalado | Docker |

### 2.1 A diferença que importa de verdade

Todo o resto é vocabulário. **Esta é conceitual:**

No PostgreSQL, o nosso checkout é atômico porque o **banco garante**. Se o
estoque do último item não bate, o `ROLLBACK` desfaz o pedido, os itens e as
baixas de estoque — não sobra nada.

No CouchDB **isso não existe**. O `_bulk_docs` grava vários documentos numa
requisição, mas o material avisa em letras maiúsculas (slide 26):

> *"lote não significa transação ACID multi-documento"*

Se o pedido grava e a baixa de estoque de um produto falha por conflito de
revisão, **o pedido fica lá, gravado, sem a baixa correspondente**. Exatamente
o "pedido pela metade" que o projeto anterior existia para impedir.

**A responsabilidade migra do banco para a aplicação.** O material lista as
estratégias no slide 41: agregado, retry, compensação, idempotência, reserva.

### 2.2 Uma falha no projeto-base do professor

Extraí o ZIP embutido na animação e li o `app.py` dele. O checkout faz:

```python
result = couch("POST", "/_bulk_docs", json={"docs": updates + [pedido]})
if any(x.get('error') for x in result):
    flash('Conflito no checkout; recarregue e tente novamente.')
    return redirect(url_for('carrinho'))
```

**Isso avisa, mas não desfaz.** Se três produtos entram no lote e o segundo dá
conflito, o primeiro já foi gravado com o estoque baixado — e o cliente vê
"tente novamente" com o estoque já alterado. Tentar de novo baixa duas vezes.

O projeto-base é um ponto de partida didático, não uma solução acabada. Aqui
está a **nossa oportunidade**: fazer o que o slide 41 pede e o base não faz —
uma compensação de verdade. É o equivalente, neste projeto, ao que o
`SELECT … FOR UPDATE` foi no anterior.

---

## 3. Bloqueio a resolver antes de começar

**Docker não está instalado nesta máquina.** O material inteiro assume
`docker compose up -d` para subir o CouchDB. Três saídas:

| Opção | Prós | Contras |
|---|---|---|
| **Docker Desktop** | é o caminho do material, reproduz o laboratório | ~500 MB, exige WSL2, pede reinício |
| **CouchDB nativo no Windows** | instalador `.msi`, sem Docker | foge do `docker-compose.yml` que o professor espera ver |
| **IBM Cloudant (free)** | zero instalação, é CouchDB gerenciado | precisa de conta; o material cita como "compatível" |

**Recomendação:** Docker Desktop. O `docker-compose.yml` é entregável avaliado,
e o Fauxton local é o que permite a Entrega 5 (forçar conflito em duas abas).

---

## 4. O plano

Mesma disciplina do projeto anterior: uma etapa por vez, um commit cada,
parada para revisão entre elas.

### Etapa 0 — Requisitos e justificativa do NoSQL
Peso 15% na avaliação. Adaptar `docs/requisitos.md`, mas com uma seção nova e
obrigatória: **por que NoSQL aqui**. A dica 1 do slide 9 é explícita —
*"justifique NoSQL pelo padrão de acesso"*. E o slide 16 manda **listar as
consultas antes de modelar**, o inverso do que fizemos no relacional.

### Etapa 1 — Modelagem documental
Peso 25%, o maior. Três documentos: `produto`, `cliente`, `pedido`. Decidir e
**justificar** cada embed e cada reference:

- itens **embutidos** no pedido — são lidos junto e versionados junto
- `cliente_id` por **referência** — cliente tem vida própria
- nome e preço do item como **snapshot** — é o `preco_unitario` congelado do
  projeto anterior, resolvido de outro jeito
- `_id` legível: `produto:chapada-geisha`, não UUID

### Etapa 2 — CouchDB e índices Mango
Peso 20%. `docker-compose.yml`, criar o database, `seed` com os 12 cafés e os
índices por consulta (`tipo+ativo`, `tipo+email`, `tipo+cliente_id`,
`tipo+categoria`). Comando `flask --app app init-db`, como no material.

### Etapa 3 — Aplicação Flask sobre REST
Peso 20%. Wrapper HTTP centralizado com timeout, catálogo, detalhe, cadastro,
login, carrinho na sessão. Reaproveitar templates e o CSS inteiro.

### Etapa 4 — Checkout com compensação ⭐
**O coração deste projeto**, como o transacional foi do outro. Reserva, retry
no 409, e compensação de verdade quando o lote falha pela metade. É o que
diferencia o nosso do projeto-base.

### Etapa 5 — Testes, conflito e segurança
Peso 10%. Além do pytest: **forçar um 409 de propósito** e provar que a
compensação funcionou — o equivalente à demonstração de rollback do projeto
anterior, e provavelmente o momento mais forte da apresentação.

### Etapa 6 — Deploy, documentação e comparação
Peso 10%. Publicar, e escrever o documento de comparação lado a lado. Somado ao
`FAUXTON_ROTEIRO.md` que o material pede.

---

## 5. Decisões que dependem de você

1. **Repositório novo ou pasta no mesmo?** Um repositório separado deixa a
   comparação mais limpa; a mesma pasta facilita reaproveitar arquivos.
2. **Mantemos o tema café?** O projeto-base do professor usa teclado, mouse e
   mochila. Manter café preserva o argumento da moagem e todo o conteúdo já
   pronto — e torna a comparação direta, mesmo domínio nos dois bancos.
3. **Docker Desktop?** Ver seção 3.
4. **Deploy:** subir CouchDB em container no Railway, ou usar Cloudant?

---

## 6. O que já dá para adiantar sem decidir nada

- Converter os 12 cafés para documentos JSON (`docs/cafes.json` já é a base)
- Escrever a seção de justificativa do NoSQL
- Listar as consultas que a aplicação faz, que é o que orienta os índices
- Redigir o documento de comparação PostgreSQL × CouchDB
