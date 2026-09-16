function (novo, antigo, usuario) {
  // ===================================================================
  // Regras de integridade do Torra & Terra, aplicadas PELO BANCO.
  //
  // No projeto relacional estas regras eram CHECK, NOT NULL e UNIQUE no
  // schema.sql. O CouchDB não tem constraint declarativa, mas executa esta
  // função a cada gravação de documento — venha ela da loja, do Fauxton, de
  // um curl ou de uma replicação. Se ela lança {forbidden: ...}, a gravação
  // é recusada com HTTP 403 e o documento não muda.
  //
  // A aplicação confere as mesmas regras antes de gravar, para mostrar uma
  // mensagem amigável. Esta função é a última linha de defesa: vale também
  // para quem não passa pela aplicação.
  //
  // Escrita em JavaScript ES5 de propósito — var, function, sem arrow
  // function nem let. É o que roda com segurança em qualquer motor do
  // CouchDB e no Cloudant.
  // ===================================================================

  function exigir(condicao, mensagem) {
    if (!condicao) {
      throw({ forbidden: mensagem });
    }
  }

  function inteiro(valor) {
    return typeof valor === 'number' && isFinite(valor) && Math.floor(valor) === valor;
  }

  function texto(valor) {
    return typeof valor === 'string' && valor.replace(/\s/g, '').length > 0;
  }

  function umDe(valor, permitidos) {
    return permitidos.indexOf(valor) !== -1;
  }

  function comecaCom(valor, prefixo) {
    return typeof valor === 'string' && valor.indexOf(prefixo) === 0;
  }

  // Design documents (esta função e os índices Mango) não são dados da loja,
  // e só administradores conseguem gravá-los.
  if (comecaCom(novo._id, '_design/')) {
    return;
  }

  // Apagar um documento grava uma revisão "túmulo" com _deleted: true e sem
  // os demais campos. Não há o que validar nela.
  if (novo._deleted === true) {
    return;
  }

  exigir(umDe(novo.tipo, ['categoria', 'produto', 'cliente', 'email', 'pedido']),
    'tipo desconhecido: ' + novo.tipo);

  // O tipo é o prefixo do _id. É o que permite listar uma família inteira de
  // documentos pelo índice primário, sem índice secundário.
  exigir(comecaCom(novo._id, novo.tipo + ':'),
    'o _id de um documento ' + novo.tipo + ' precisa começar com "' + novo.tipo + ':"');

  if (antigo && !antigo._deleted) {
    exigir(antigo.tipo === novo.tipo, 'o tipo de um documento não muda');
  }

  // --- categoria (região produtora) ----------------------------------
  if (novo.tipo === 'categoria') {
    exigir(texto(novo.nome), 'categoria sem nome');
    exigir(texto(novo.regiao), 'categoria sem região');
  }

  // --- produto ---------------------------------------------------------
  if (novo.tipo === 'produto') {
    exigir(texto(novo.nome), 'produto sem nome');

    // Centavos inteiros: JSON não tem tipo decimal e ponto flutuante não
    // representa 0,10 exatamente. É o NUMERIC(10,2) + CHECK (preco >= 0).
    exigir(inteiro(novo.preco_centavos) && novo.preco_centavos >= 0,
      'preco_centavos precisa ser inteiro e maior ou igual a zero');

    // A última linha de defesa contra vender o que não existe.
    exigir(inteiro(novo.estoque) && novo.estoque >= 0,
      'estoque precisa ser inteiro e maior ou igual a zero');

    exigir(umDe(novo.torra, ['CLARA', 'MEDIA', 'ESCURA']),
      'torra precisa ser CLARA, MEDIA ou ESCURA');

    // Café especial, pela definição da SCA, pontua 80 ou mais.
    exigir(novo.pontuacao_sca === undefined || novo.pontuacao_sca === null ||
      (typeof novo.pontuacao_sca === 'number' && novo.pontuacao_sca >= 80 && novo.pontuacao_sca <= 100),
      'pontuacao_sca precisa estar entre 80 e 100');

    exigir(inteiro(novo.peso_g) && novo.peso_g > 0, 'peso_g precisa ser inteiro e maior que zero');
    exigir(typeof novo.ativo === 'boolean', 'ativo precisa ser true ou false');
    exigir(comecaCom(novo.categoria_id, 'categoria:'), 'categoria_id precisa referenciar uma categoria');

    // Marcas de reserva do checkout: pedido_id -> quantidade reservada.
    if (novo.reservas !== undefined) {
      exigir(typeof novo.reservas === 'object' && novo.reservas !== null && !Array.isArray(novo.reservas),
        'reservas precisa ser um objeto');
      for (var pedidoId in novo.reservas) {
        if (novo.reservas.hasOwnProperty(pedidoId)) {
          exigir(comecaCom(pedidoId, 'pedido:'), 'reserva que não aponta para um pedido: ' + pedidoId);
          exigir(inteiro(novo.reservas[pedidoId]) && novo.reservas[pedidoId] > 0,
            'quantidade reservada precisa ser inteira e maior que zero');
        }
      }
    }
  }

  // --- cliente ---------------------------------------------------------
  if (novo.tipo === 'cliente') {
    exigir(texto(novo.nome), 'cliente sem nome');
    exigir(typeof novo.email === 'string' && novo.email.indexOf('@') > 0 &&
      novo.email === novo.email.toLowerCase(),
      'e-mail precisa ser válido e estar em minúsculas');

    // A senha em texto puro nunca chega ao banco — nem por engano.
    exigir(novo.senha === undefined, 'senha em texto puro não é aceita; grave só o hash');
    exigir(comecaCom(novo.senha_hash, 'scrypt:') || comecaCom(novo.senha_hash, 'pbkdf2:'),
      'senha_hash precisa ser um hash gerado pelo werkzeug');
  }

  // --- email (a unicidade do e-mail mora no _id deste documento) -------
  if (novo.tipo === 'email') {
    exigir(comecaCom(novo.cliente_id, 'cliente:'), 'o registro de e-mail precisa apontar para um cliente');
  }

  // --- pedido ----------------------------------------------------------
  if (novo.tipo === 'pedido') {
    exigir(comecaCom(novo.cliente_id, 'cliente:'), 'pedido sem cliente');
    exigir(umDe(novo.status, ['PENDENTE', 'CRIADO', 'PAGO', 'ENVIADO', 'CANCELADO']),
      'status inválido: ' + novo.status);
    exigir(Array.isArray(novo.itens) && novo.itens.length > 0, 'pedido sem itens');

    var soma = 0;
    var vistos = {};
    for (var i = 0; i < novo.itens.length; i++) {
      var item = novo.itens[i];
      exigir(comecaCom(item.produto_id, 'produto:'), 'item sem produto');
      exigir(inteiro(item.quantidade) && item.quantidade > 0, 'quantidade precisa ser inteira e maior que zero');
      exigir(umDe(item.moagem, ['GRAO', 'MEDIA', 'FINA']), 'moagem precisa ser GRAO, MEDIA ou FINA');
      exigir(inteiro(item.preco_unitario_centavos) && item.preco_unitario_centavos >= 0,
        'preco_unitario_centavos precisa ser inteiro e maior ou igual a zero');

      // Mesmo café na mesma moagem é uma linha só: o UNIQUE (pedido_id,
      // produto_id, moagem) do projeto relacional.
      var chave = item.produto_id + '|' + item.moagem;
      exigir(!vistos[chave], 'item repetido: ' + chave);
      vistos[chave] = true;

      soma += item.quantidade * item.preco_unitario_centavos;
    }
    exigir(novo.total_centavos === soma, 'total_centavos não bate com a soma dos itens');

    if (antigo && !antigo._deleted) {
      // Preço congelado garantido pelo banco: depois de gravado, nenhum item
      // do pedido muda. Só o status evolui.
      exigir(antigo.itens.length === novo.itens.length, 'os itens de um pedido não podem ser alterados');
      for (var j = 0; j < novo.itens.length; j++) {
        exigir(antigo.itens[j].produto_id === novo.itens[j].produto_id &&
          antigo.itens[j].moagem === novo.itens[j].moagem &&
          antigo.itens[j].quantidade === novo.itens[j].quantidade &&
          antigo.itens[j].preco_unitario_centavos === novo.itens[j].preco_unitario_centavos,
          'os itens de um pedido não podem ser alterados');
      }
      exigir(antigo.cliente_id === novo.cliente_id, 'o cliente de um pedido não muda');
      exigir(antigo.status !== 'CANCELADO' || novo.status === 'CANCELADO', 'pedido cancelado não volta a valer');
    }
  }
}
