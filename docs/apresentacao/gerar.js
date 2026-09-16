/**
 * Apresentação — Torra & Terra, versão NoSQL.
 *
 * Gera o PowerPoint e o QR code do WhatsApp a partir de
 * templates/apresentacao.json — o mesmo arquivo que a página /apresentacao
 * lê. Texto de slide mora lá; aqui só existe o desenho de cada slide.
 *
 *   cd docs/apresentacao && npm install && npm run gerar
 *
 * Saídas:
 *   public/static/Torra_e_Terra_NoSQL.pptx   (compactado com deflate)
 *   public/static/whatsapp_qr.svg            (usado pela versão web e pelo PDF)
 *
 * Sem capturas de tela: a loja é demonstrada ao vivo no último slide.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const pptxgen = require("pptxgenjs");
const QRCode = require("qrcode");

const RAIZ = path.resolve(__dirname, "..", "..");
const deck = JSON.parse(fs.readFileSync(path.join(RAIZ, "templates", "apresentacao.json"), "utf8"));
const S = Object.fromEntries(deck.slides.map((s) => [s.chave, s]));
const PUBLICO = path.join(RAIZ, "public", "static");

// Paleta do site, sem "#" — é o formato que o pptxgenjs espera.
const INK = "14120F", BG = "FAF9F7", SURF = "FFFFFF", MUT = "6B6560", LINE = "E8E4DE";
const LINE_FORTE = "CFC8BF", ACC = "D4622A", ACC_SOFT = "FBF0E9";
const OK = "2F6B45", OK_SOFT = "EEF6F1", ERR = "A8321F", ERR_SOFT = "FDF0ED";
const ESCURO_TEXTO = "C9C2BB", ESCURO_MUDO = "9B928A", ESCURO_BOLHA = "2A2622";
const SERIF = "Cambria", SANS = "Calibri", MONO = "Consolas";

const W = 13.33, M = 0.7, CW = W - M * 2;

const p = new pptxgen();
p.layout = "LAYOUT_WIDE";
p.author = "Felipe Furlan";
p.title = deck.titulo;

/* ---------------------------------------------------------------------
   Texto rico: as duas marcações do apresentacao.json
     **trecho**              -> destaque (negrito, ou cor no código)
     [PREENCHER: descrição]  -> lacuna honesta, visível no slide
   --------------------------------------------------------------------- */
function rico(texto, base = {}, destaque = { bold: true }) {
  const runs = [];
  String(texto).split("**").forEach((trecho, i) => {
    trecho.split(/(\[PREENCHER:[^\]]*\])/).forEach((pedaco) => {
      if (!pedaco) return;
      if (/^\[PREENCHER:/.test(pedaco)) {
        runs.push({ text: pedaco, options: { ...base, color: ERR, bold: true, highlight: ERR_SOFT } });
      } else {
        runs.push({ text: pedaco, options: i % 2 === 1 ? { ...base, ...destaque } : { ...base } });
      }
    });
  });
  return runs;
}

function texto(s, conteudo, opcoes) {
  s.addText(typeof conteudo === "string" ? rico(conteudo) : conteudo, {
    fontFace: SANS, color: INK, isTextBox: true, margin: 0, valign: "top", ...opcoes,
  });
}

function fundo(s, cor) { s.background = { color: cor }; }

function titulo(s, t, sub) {
  texto(s, t, { x: M, y: 0.45, w: CW, h: 0.7, fontFace: SERIF, fontSize: 32, valign: "middle" });
  if (sub) texto(s, sub, { x: M, y: 1.14, w: CW, h: 0.38, fontSize: 14.5, color: MUT });
}

function rotulo(s, t, x, y, w, cor = MUT) {
  texto(s, t.toUpperCase(), { x, y, w, h: 0.26, fontSize: 10, bold: true, color: cor, charSpacing: 1.2, valign: "middle" });
}

function cartao(s, x, y, w, h, fill = SURF) {
  s.addShape(p.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.09, fill: { color: fill }, line: { color: LINE, width: 0.75 },
  });
}

function bolha(s, x, y, n, cor = ACC, fill = ACC_SOFT, d = 0.44) {
  s.addShape(p.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill, width: 0 } });
  texto(s, String(n), { x, y, w: d, h: d, fontSize: 13, bold: true, color: cor, align: "center", valign: "middle" });
}

function linhaDivisoria(s, x, y, w) {
  s.addShape(p.ShapeType.line, { x, y, w, h: 0, line: { color: LINE, width: 0.75 } });
}

function nota(s, t, y, h = 0.45, cor = MUT) {
  texto(s, t, { x: M, y, w: CW, h, fontSize: 13, italic: true, color: cor });
}

function pagina(s, n, escuro = false) {
  texto(s, `${n} / ${deck.slides.length}`, {
    x: W - M - 1, y: 7.02, w: 1, h: 0.25, fontSize: 9, color: escuro ? ESCURO_MUDO : MUT, align: "right",
  });
}

async function gerar() {
  /* ============ QR code do WhatsApp ============ */
  const zap = S.demonstracao.whatsapp;
  const svg = await QRCode.toString(zap.link, {
    type: "svg", errorCorrectionLevel: "M", margin: 1, color: { dark: "#14120F", light: "#FFFFFF" },
  });
  fs.writeFileSync(path.join(PUBLICO, zap.imagem), svg);
  const qrPng = path.join(os.tmpdir(), "torra_terra_whatsapp_qr.png");
  await QRCode.toFile(qrPng, zap.link, {
    type: "png", errorCorrectionLevel: "M", margin: 1, width: 900, color: { dark: "#14120F", light: "#FFFFFF" },
  });

  let s, d;

  /* ============ 1 · CAPA ============ */
  d = S.capa; s = p.addSlide(); fundo(s, INK);
  rotulo(s, d.rotulo, M, 1.4, 11, ESCURO_MUDO);
  texto(s, [{ text: "Torra " }, { text: "&", options: { color: ACC } }, { text: " Terra" }], {
    x: M, y: 1.8, w: CW, h: 1.35, fontFace: SERIF, fontSize: 62, color: "FFFFFF", valign: "middle",
  });
  texto(s, d.lema, { x: M, y: 3.2, w: 10, h: 0.7, fontFace: SERIF, fontSize: 30, color: ACC, valign: "middle" });
  texto(s, d.texto, { x: M, y: 4.05, w: 9.2, h: 0.75, fontSize: 17, color: ESCURO_TEXTO, lineSpacingMultiple: 1.15 });
  texto(s, rico(`${d.professor}  ·  ${d.grupo}`, { color: ESCURO_MUDO }), { x: M, y: 5.75, w: 10, h: 0.4, fontSize: 13 });
  s.addNotes(d.notas);

  /* ============ 2 · COMO TRABALHAMOS ============ */
  d = S.etapas; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  d.etapas.forEach(([n, t, det], i) => {
    const col = i % 4, lin = Math.floor(i / 4);
    const x = M + col * 3.04, y = 1.9 + lin * 1.85;
    cartao(s, x, y, 2.8, 1.6, n === d.destaque ? ACC_SOFT : SURF);
    bolha(s, x + 0.28, y + 0.24, n, ACC, n === d.destaque ? SURF : ACC_SOFT);
    texto(s, t, { x: x + 0.28, y: y + 0.82, w: 2.35, h: 0.32, fontSize: 14, bold: true });
    texto(s, det, { x: x + 0.28, y: y + 1.14, w: 2.35, h: 0.32, fontSize: 11, color: MUT });
  });
  texto(s, d.nota, {
    x: M + 3 * 3.04, y: 1.9 + 1.85, w: 2.8, h: 1.6, fontSize: 12, italic: true, color: MUT,
    valign: "middle", lineSpacingMultiple: 1.1,
  });
  pagina(s, 2); s.addNotes(d.notas);

  /* ============ 3 · POR QUE NOSQL ============ */
  d = S.por_que_nosql; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  d.cartoes.forEach(([t, det], i) => {
    const y = 1.85 + i * 1.25;
    cartao(s, M, y, 6.35, 1.12);
    texto(s, t, { x: M + 0.3, y: y + 0.16, w: 5.8, h: 0.3, fontSize: 14, bold: true });
    texto(s, det, { x: M + 0.3, y: y + 0.48, w: 5.9, h: 0.55, fontSize: 12, color: MUT, lineSpacingMultiple: 1.05 });
  });
  cartao(s, M + 6.6, 1.85, CW - 6.6, 3.62, ACC_SOFT);
  rotulo(s, d.consultas_titulo, M + 6.9, 2.05, 4.8, ACC);
  d.consultas.forEach(([pergunta, indice], i) => {
    const y = 2.48 + i * 0.74;
    if (i > 0) linhaDivisoria(s, M + 6.9, y - 0.09, CW - 7.2);
    texto(s, pergunta, { x: M + 6.9, y, w: 4.7, h: 0.3, fontSize: 13 });
    texto(s, indice, { x: M + 6.9, y: y + 0.3, w: 4.7, h: 0.28, fontFace: MONO, fontSize: 11, color: ACC });
  });
  nota(s, d.nota, 5.8);
  pagina(s, 3); s.addNotes(d.notas);

  /* ============ 4 · ARQUITETURA ============ */
  d = S.arquitetura; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  d.fluxo.forEach(([peca, papel, forte], i) => {
    const x = M + i * 2.47;
    cartao(s, x, 1.8, 2.05, 0.95, forte ? ACC_SOFT : SURF);
    texto(s, peca, { x: x + 0.1, y: 1.93, w: 1.85, h: 0.32, fontSize: 13.5, bold: true, color: forte ? ACC : INK, align: "center" });
    texto(s, papel, { x: x + 0.1, y: 2.26, w: 1.85, h: 0.36, fontSize: 10.5, color: MUT, align: "center" });
    if (i < d.fluxo.length - 1) {
      texto(s, "›", { x: x + 2.05, y: 2.02, w: 0.42, h: 0.5, fontSize: 22, color: LINE_FORTE, align: "center", valign: "middle" });
    }
  });
  texto(s, d.volta, { x: M, y: 2.88, w: CW, h: 0.3, fontSize: 11.5, color: MUT });
  rotulo(s, d.pecas_titulo, M, 3.34, 6, ACC);
  d.pecas.forEach(([peca, lingua, frase], i) => {
    const y = 3.68 + i * 0.44;
    if (i > 0) linhaDivisoria(s, M, y - 0.04, CW);
    texto(s, peca, { x: M, y, w: 1.4, h: 0.4, fontSize: 12.5, bold: true, valign: "middle" });
    texto(s, lingua, { x: M + 1.45, y, w: 1.0, h: 0.4, fontFace: MONO, fontSize: 10.5, color: ACC, valign: "middle" });
    texto(s, rico(frase, { color: MUT }, { bold: true, color: INK }), { x: M + 2.5, y, w: CW - 2.5, h: 0.4, fontSize: 12.5, valign: "middle" });
  });
  nota(s, d.nota, 6.43, 0.4);
  pagina(s, 4); s.addNotes(d.notas);

  /* ============ 5 · FERRAMENTAS ============ */
  d = S.ferramentas; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  const coresGrupo = [ACC, INK, OK];
  d.grupos.forEach(([grupo, lista], g) => {
    const x = M + g * 4.05;
    cartao(s, x, 1.8, 3.82, 3.3);
    rotulo(s, grupo, x + 0.28, 2.0, 3.3, coresGrupo[g]);
    lista.forEach(([ferramenta, paraQue], i) => {
      const y = 2.4 + i * 0.66;
      texto(s, ferramenta, { x: x + 0.28, y, w: 3.3, h: 0.28, fontFace: MONO, fontSize: 12, bold: true });
      texto(s, paraQue, { x: x + 0.28, y: y + 0.28, w: 3.3, h: 0.3, fontSize: 11, color: MUT });
    });
  });
  cartao(s, M, 5.3, CW, 1.22, ACC_SOFT);
  texto(s, d.ia.nome, { x: M + 0.35, y: 5.42, w: 3.0, h: 0.55, fontFace: SERIF, fontSize: 26, valign: "middle" });
  rotulo(s, d.ia.detalhe, M + 0.35, 6.0, 3.0, ACC);
  texto(s, [...rico(d.ia.analogia, { color: ACC, bold: true }), { text: " " + d.ia.texto, options: { color: INK } }], {
    x: M + 3.45, y: 5.45, w: CW - 3.8, h: 0.95, fontSize: 13, valign: "middle", lineSpacingMultiple: 1.08,
  });
  nota(s, d.nota, 6.66, 0.35);
  pagina(s, 5); s.addNotes(d.notas);

  /* ============ 6 · MODELAGEM ============ */
  d = S.modelagem; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  const codigo = (linhas) => {
    const runs = [];
    linhas.forEach((l, i) => {
      rico(l, {}, { color: ACC, bold: true, highlight: ACC_SOFT }).forEach((r, j, todos) => {
        const ultima = j === todos.length - 1 && i < linhas.length - 1;
        runs.push({ text: r.text, options: { ...r.options, breakLine: ultima } });
      });
    });
    return runs;
  };
  const [produto, cliente, pedido] = d.fichas;
  [[produto, 1.8], [cliente, 3.95]].forEach(([[nome, linhas], y]) => {
    cartao(s, M, y, 3.75, 1.95);
    rotulo(s, `ficha · ${nome}`, M + 0.25, y + 0.14, 3.2);
    texto(s, codigo(linhas), { x: M + 0.25, y: y + 0.46, w: 3.3, h: 1.35, fontFace: MONO, fontSize: 10.5, lineSpacingMultiple: 1.0 });
  });
  cartao(s, M + 3.95, 1.8, 4.35, 4.1);
  rotulo(s, `ficha · ${pedido[0]}`, M + 4.2, 1.94, 3.6, ACC);
  texto(s, codigo(pedido[1]), { x: M + 4.2, y: 2.26, w: 3.9, h: 2.75, fontFace: MONO, fontSize: 10.5, lineSpacingMultiple: 1.0 });
  texto(s, d.moagem, { x: M + 4.2, y: 4.45, w: 3.9, h: 0.6, fontSize: 12.5, bold: true, color: ACC });
  d.decisoes.forEach(([rot, resumo, frase], i) => {
    const y = 1.8 + i * 1.4;
    cartao(s, M + 8.5, y, CW - 8.5, 1.3);
    rotulo(s, rot, M + 8.75, y + 0.13, 2.9, ACC);
    texto(s, resumo, { x: M + 8.75, y: y + 0.4, w: CW - 9.0, h: 0.3, fontSize: 12.5, bold: true });
    texto(s, frase, { x: M + 8.75, y: y + 0.71, w: CW - 9.0, h: 0.52, fontSize: 11, color: MUT, lineSpacingMultiple: 1.0 });
  });
  texto(s, d.apoio, { x: M, y: 6.12, w: CW, h: 0.6, fontSize: 12.5, italic: true, color: MUT });
  pagina(s, 6); s.addNotes(d.notas);

  /* ============ 7 · COMPARAÇÃO ============ */
  d = S.comparacao; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  d.analogias.forEach(([rot, frase], i) => {
    const x = M + i * 6.08;
    cartao(s, x, 1.75, 5.85, 1.05, i === 1 ? ACC_SOFT : SURF);
    rotulo(s, rot, x + 0.28, 1.87, 5.2, i === 1 ? ACC : MUT);
    texto(s, frase, { x: x + 0.28, y: 2.14, w: 5.3, h: 0.58, fontFace: SERIF, fontSize: 15, lineSpacingMultiple: 1.0 });
  });
  const colunas = [[M, 2.0], [M + 2.1, 4.3], [M + 6.5, CW - 6.5]];
  rotulo(s, "PostgreSQL", colunas[1][0], 3.0, colunas[1][1]);
  rotulo(s, "CouchDB", colunas[2][0], 3.0, colunas[2][1], ACC);
  d.linhas.forEach(([tema, pg, couch], i) => {
    const y = 3.32 + i * 0.45;
    const ultima = i === d.linhas.length - 1;
    linhaDivisoria(s, M, y - 0.04, CW);
    texto(s, tema, { x: colunas[0][0], y, w: colunas[0][1], h: 0.4, fontSize: 12.5, bold: true, color: ultima ? ACC : INK, valign: "middle" });
    texto(s, pg, { x: colunas[1][0], y, w: colunas[1][1], h: 0.4, fontSize: 12, color: ultima ? ACC : MUT, bold: ultima, valign: "middle" });
    texto(s, couch, { x: colunas[2][0], y, w: colunas[2][1], h: 0.4, fontSize: 12, color: ultima ? ACC : INK, bold: ultima, valign: "middle" });
  });
  pagina(s, 7); s.addNotes(d.notas);

  /* ============ 8 · CHECKOUT ============ */
  d = S.checkout; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  [[d.risco, ERR_SOFT, ERR, INK], [d.base, SURF, MUT, MUT]].forEach(([[rot, t], fill, corRot, corTexto], i) => {
    const x = M + i * 6.08;
    cartao(s, x, 1.75, 5.85, 1.02, fill);
    rotulo(s, rot, x + 0.28, 1.87, 5.2, corRot);
    texto(s, t, { x: x + 0.28, y: 2.15, w: 5.3, h: 0.55, fontSize: 12.5, color: corTexto, lineSpacingMultiple: 1.0 });
  });
  d.passos.forEach(([n, t, det], i) => {
    const x = M + i * 2.43;
    cartao(s, x, 2.97, 2.2, 1.72);
    bolha(s, x + 0.22, 3.12, n);
    texto(s, t, { x: x + 0.22, y: 3.64, w: 1.85, h: 0.3, fontSize: 13, bold: true });
    texto(s, det, { x: x + 0.22, y: 3.95, w: 1.85, h: 0.68, fontSize: 10.5, color: MUT, lineSpacingMultiple: 1.0 });
    if (i < d.passos.length - 1) {
      texto(s, "›", { x: x + 2.2, y: 3.58, w: 0.23, h: 0.4, fontSize: 16, color: LINE_FORTE, align: "center", valign: "middle" });
    }
  });
  [[d.estorno, ERR_SOFT, ERR, M, 7.2], [d.retry, OK_SOFT, OK, M + 7.43, CW - 7.43]].forEach(([[rot, t], fill, cor, x, w]) => {
    cartao(s, x, 4.9, w, 0.95, fill);
    rotulo(s, rot, x + 0.28, 5.02, w - 0.5, cor);
    texto(s, t, { x: x + 0.28, y: 5.3, w: w - 0.5, h: 0.45, fontSize: 13, bold: true, color: INK });
  });
  nota(s, d.rodape, 6.08, 0.6);
  pagina(s, 8); s.addNotes(d.notas);

  /* ============ 9 · TESTE DE CONFLITO ============ */
  d = S.conflito; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  d.passos.forEach(([t, det], i) => {
    const y = 1.82 + i * 0.73;
    const [cor, fill] = i === 2 ? [ERR, ERR_SOFT] : i === 4 ? [OK, OK_SOFT] : [ACC, ACC_SOFT];
    bolha(s, M, y + 0.02, i + 1, cor, fill, 0.42);
    texto(s, [
      { text: t, options: { bold: true, color: INK } },
      { text: " — " + det, options: { color: MUT } },
    ], { x: M + 0.62, y, w: 6.55, h: 0.64, fontSize: 12.5, lineSpacingMultiple: 1.0 });
  });
  d.resultados.forEach(([rot, t, teste], i) => {
    const y = 1.8 + i * 2.2;
    cartao(s, M + 7.4, y, CW - 7.4, 2.05, OK_SOFT);
    rotulo(s, rot, M + 7.68, y + 0.16, 4.0, OK);
    texto(s, t, { x: M + 7.68, y: y + 0.46, w: CW - 7.95, h: 0.95, fontSize: 13, lineSpacingMultiple: 1.05 });
    texto(s, teste, { x: M + 7.68, y: y + 1.5, w: CW - 7.95, h: 0.4, fontFace: MONO, fontSize: 9, color: OK });
  });
  pagina(s, 9); s.addNotes(d.notas);

  /* ============ 10 · PROBLEMAS ============ */
  d = S.problemas; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo, d.sub);
  d.problemas.forEach(([t, problema, solucao], i) => {
    const x = M + (i % 2) * 6.08, y = 1.78 + Math.floor(i / 2) * 2.4;
    cartao(s, x, y, 5.85, 2.25);
    bolha(s, x + 0.28, y + 0.24, i + 1, ERR, ERR_SOFT);
    texto(s, t, { x: x + 0.92, y: y + 0.2, w: 4.75, h: 0.55, fontSize: 13.5, bold: true, lineSpacingMultiple: 1.0 });
    texto(s, problema, { x: x + 0.92, y: y + 0.8, w: 4.75, h: 0.6, fontSize: 11.5, color: MUT, lineSpacingMultiple: 1.0 });
    texto(s, solucao, { x: x + 0.92, y: y + 1.45, w: 4.75, h: 0.7, fontSize: 11.5, color: OK, bold: true, lineSpacingMultiple: 1.0 });
  });
  nota(s, d.rodape, 6.62, 0.4);
  pagina(s, 10); s.addNotes(d.notas);

  /* ============ 11 · NÚMEROS ============ */
  d = S.numeros; s = p.addSlide(); fundo(s, BG);
  titulo(s, d.titulo);
  d.numeros.forEach(([valor, t, det], i) => {
    const x = M + (i % 3) * 2.55, y = 1.55 + Math.floor(i / 3) * 1.9;
    cartao(s, x, y, 2.35, 1.72);
    texto(s, valor, { x: x + 0.25, y: y + 0.12, w: 1.9, h: 0.7, fontFace: SERIF, fontSize: 36, color: ACC, valign: "middle" });
    texto(s, t, { x: x + 0.25, y: y + 0.84, w: 1.95, h: 0.3, fontSize: 13, bold: true });
    texto(s, det, { x: x + 0.25, y: y + 1.14, w: 1.95, h: 0.5, fontSize: 10.5, color: MUT, lineSpacingMultiple: 1.0 });
  });
  texto(s, d.latencia, { x: M, y: 5.45, w: 7.4, h: 0.4, fontSize: 12 });
  cartao(s, M + 7.75, 1.55, CW - 7.75, 3.62, ACC_SOFT);
  rotulo(s, d.fora_titulo, M + 8.05, 1.75, 3.6, ACC);
  d.fora.forEach((item, i) => {
    const y = 2.18 + i * 0.58;
    s.addShape(p.ShapeType.ellipse, { x: M + 8.05, y: y + 0.1, w: 0.1, h: 0.1, fill: { color: ACC }, line: { color: ACC, width: 0 } });
    texto(s, item, { x: M + 8.3, y, w: CW - 8.6, h: 0.52, fontSize: 12.5, lineSpacingMultiple: 1.0 });
  });
  pagina(s, 11); s.addNotes(d.notas);

  /* ============ 12 · DEMONSTRAÇÃO ============ */
  d = S.demonstracao; s = p.addSlide(); fundo(s, INK);
  texto(s, d.titulo, { x: M, y: 0.8, w: 8, h: 0.9, fontFace: SERIF, fontSize: 46, color: "FFFFFF", valign: "middle" });
  texto(s, rico(d.url, { color: ACC, bold: true }), { x: M, y: 1.78, w: 8.2, h: 0.45, fontSize: 16 });
  d.passos.forEach((passo, i) => {
    const y = 2.6 + i * 0.66;
    const destaque = i + 1 === d.destaque;
    bolha(s, M, y + 0.04, i + 1, destaque ? "FFFFFF" : ESCURO_MUDO, destaque ? ACC : ESCURO_BOLHA, 0.4);
    texto(s, passo, {
      x: M + 0.62, y, w: 7.5, h: 0.56, fontSize: 14.5, bold: destaque,
      color: destaque ? "FFFFFF" : ESCURO_TEXTO, valign: "middle", lineSpacingMultiple: 1.0,
    });
  });
  texto(s, d.nota, { x: M, y: 6.1, w: 8, h: 0.4, fontSize: 13, italic: true, color: ESCURO_MUDO });

  // O cartão branco não é enfeite: um QR precisa de fundo claro em volta
  // para a câmera do celular ler.
  cartao(s, 9.2, 1.85, 3.43, 4.65, SURF);
  s.addImage({ path: qrPng, x: 9.62, y: 2.1, w: 2.6, h: 2.6 });
  texto(s, zap.titulo, { x: 9.45, y: 4.85, w: 2.93, h: 0.32, fontSize: 14, bold: true, align: "center" });
  texto(s, zap.texto, { x: 9.45, y: 5.18, w: 2.93, h: 0.52, fontSize: 11, color: MUT, align: "center", lineSpacingMultiple: 1.0 });
  texto(s, zap.numero, { x: 9.45, y: 5.8, w: 2.93, h: 0.32, fontSize: 13.5, bold: true, color: ACC, align: "center" });
  pagina(s, 12, true); s.addNotes(d.notas);

  const destino = path.join(PUBLICO, `${deck.arquivo}.pptx`);
  await p.writeFile({ fileName: destino });
  const semCompressao = fs.statSync(destino).size;

  // O pptxgenjs grava o ZIP sem compressão — a opção compression: true dele
  // não mudou isso na prática (0 de 84 entradas com deflate). Então o arquivo
  // é reaberto e regravado com deflate, na mesma ordem de entradas.
  const JSZip = require("jszip");
  const zip = await JSZip.loadAsync(fs.readFileSync(destino));
  const compactado = await zip.generateAsync({
    type: "nodebuffer", compression: "DEFLATE", compressionOptions: { level: 9 },
  });
  fs.writeFileSync(destino, compactado);
  console.log(
    "PowerPoint:", destino,
    `${Math.round(semCompressao / 1024)} KB sem compressão -> ${Math.round(compactado.length / 1024)} KB com deflate`,
  );
  console.log("QR code:", path.join(PUBLICO, zap.imagem));
}

gerar().catch((erro) => {
  console.error(erro);
  process.exit(1);
});
