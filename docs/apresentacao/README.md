# Apresentação — como gerar de novo

Os 12 slides existem em três formatos, e o texto de todos mora num lugar só:
[`templates/apresentacao.json`](../../templates/apresentacao.json). Mudou uma
frase ou um número, muda ali e gera de novo.

Duas marcações valem nos três formatos:

- `**trecho**` sai em destaque;
- `[PREENCHER: descrição]` marca o que ainda não aconteceu no projeto. Aparece
  destacado no próprio slide, em vez de um número inventado.

| Formato | Onde fica | De onde sai |
|---|---|---|
| Web | `/apresentacao` na loja | `templates/apresentacao.html` + `public/static/apresentacao.css`, sem JavaScript |
| PowerPoint | `public/static/Torra_e_Terra_NoSQL.pptx` | `gerar.js`, com pptxgenjs, notas do apresentador em todos os slides |
| PDF | `public/static/Torra_e_Terra_NoSQL.pdf` | a versão web impressa pelo Chrome |

## 1. PowerPoint e QR code

```bash
cd docs/apresentacao
```

```bash
npm install
```

```bash
npm run gerar
```

Gera o `.pptx` e o `public/static/whatsapp_qr.svg`. O pptxgenjs grava o ZIP
sem compressão, então o `gerar.js` regrava com deflate: 408 KB viram 62 KB.

Para conferir se algum texto estoura a caixa, o próprio PowerPoint mede cada
uma (precisa do PowerPoint instalado):

```bash
powershell -ExecutionPolicy Bypass -File docs/apresentacao/verificar_pptx.ps1
```

## 2. PDF

Com a loja rodando localmente (`flask --app app run`), imprima a página com o
Chrome sem cabeçalho e rodapé:

```bash
chrome --headless --no-pdf-header-footer --virtual-time-budget=15000 --print-to-pdf=public/static/Torra_e_Terra_NoSQL.pdf http://127.0.0.1:5000/apresentacao
```

O `--virtual-time-budget` dá tempo para as fontes do Google carregarem antes
de imprimir. O CSS de impressão fixa a página em 338 × 190 mm e cada slide em
uma página. Confira que o PDF tem exatamente 12 páginas.

Duas armadilhas já resolvidas no `apresentacao.css`, e que não podem voltar:

- **tamanho de fonte com unidade de viewport** sai errado no papel. Na tela,
  tudo usa `--u`; na impressão, `--u` é fixo em pt;
- **o `<main>` da loja tem padding** (40 px em cima, 96 px embaixo). Sem zerar,
  a capa descia 10,6 mm, empurrava uma faixa para a página 2 e o PDF saía com
  14 páginas.
