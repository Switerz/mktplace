// Testes da POLITICA DE IDENTIDADE do Oraculo (Auth0 Post-Login Action).
//
// A Action roda no Auth0, nao no Next — mas a decisao dela e' uma funcao pura,
// e e' isso que se testa aqui: dominios aprovados, vizinhos maliciosos,
// verificacao de e-mail, conexao, audience e isolamento de outros clientes.
//
// ESCOPO: isto prova a LOGICA. Nao prova que a Action esta publicada no tenant,
// nem em qual trigger — isso e' configuracao de dashboard e so' o proprietario
// pode confirmar.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require_ = createRequire(import.meta.url);
const action = require_("../../../infra/auth0/actions/oracle-corporate-access.js") as {
  decideOracleAccess: (input: Record<string, unknown>) => {
    applies: boolean;
    allow: boolean;
    reason: string;
  };
  extractEmailDomain: (raw: unknown) => string | null;
  resolveHostedDomain: (event: Record<string, unknown>) => string;
  ALLOWED_DOMAINS: readonly string[];
  ORACLE_SCOPE: string;
  onExecutePostLogin: (event: unknown, api: unknown) => Promise<void>;
};

const { decideOracleAccess, extractEmailDomain } = action;

const ORACLE_CLIENT = "cid_oraculo_org_a";
const OUTRA_ORG = "cid_oraculo_org_b";
const AUDIENCE = "https://mktplace-gobeaute.vercel.app/api/mcp";

/** Contexto valido; cada teste altera só o que quer provar. */
function ctx(over: Record<string, unknown> = {}) {
  return {
    clientId: ORACLE_CLIENT,
    oracleClientIds: [ORACLE_CLIENT, OUTRA_ORG],
    expectedAudience: AUDIENCE,
    requestedAudience: AUDIENCE,
    connectionStrategy: "google-oauth2",
    connectionName: "google-oauth2",
    allowedConnections: [],
    email: "pessoa@gocase.com",
    emailVerified: true,
    ...over,
  };
}

// --- dominios aceitos -------------------------------------------------------

test("aceita os dois dominios corporativos", () => {
  for (const email of ["pessoa@gocase.com", "pessoa@gobeaute.com.br"]) {
    const d = decideOracleAccess(ctx({ email }));
    assert.equal(d.applies, true, email);
    assert.equal(d.allow, true, email);
    assert.equal(d.reason, "granted", email);
  }
});

test("normaliza caixa e espacos nas pontas", () => {
  const variacoes = [
    "Pessoa@GoCase.com",
    "PESSOA@GOCASE.COM",
    "  pessoa@gocase.com  ",
    "\tPessoa@GoBeaute.Com.BR\n",
  ];
  for (const email of variacoes) {
    const d = decideOracleAccess(ctx({ email }));
    assert.equal(d.allow, true, JSON.stringify(email));
  }
});

test("vale tanto no login inicial quanto na renovacao (mesma funcao pura)", () => {
  // A politica nao le nada de "primeiro login": o mesmo input decide igual.
  const primeiro = decideOracleAccess(ctx());
  const renovacao = decideOracleAccess(ctx());
  assert.deepEqual(primeiro, renovacao);
  assert.equal(primeiro.allow, true);
});

// --- dominios vizinhos maliciosos -------------------------------------------

test("nega dominios semelhantes — prefixo, sufixo e subdominio", () => {
  const maliciosos = [
    "pessoa@gocase.com.evil.example",
    "pessoa@evilgocase.com",
    "pessoa@gobeaute.com.br.evil.example",
    "pessoa@evilgobeaute.com.br",
    "pessoa@gocase.com.br",
    "pessoa@gobeaute.com",
    "pessoa@sub.gocase.com",
    "pessoa@gocase.com.",
    "pessoa@xgocase.com",
    "pessoa@gocase.como",
  ];

  for (const email of maliciosos) {
    const d = decideOracleAccess(ctx({ email }));
    assert.equal(d.applies, true, email);
    assert.equal(d.allow, false, "PRECISA negar: " + email);
    assert.equal(d.reason, "domain_not_allowed", email);
  }
});

test("a comparacao e' de igualdade, nunca endsWith nem includes", () => {
  // `endsWith("gocase.com")` aceitaria isto; a implementacao nao pode aceitar.
  assert.equal(decideOracleAccess(ctx({ email: "a@evilgocase.com" })).allow, false);
  // `includes("gocase.com")` aceitaria isto.
  assert.equal(decideOracleAccess(ctx({ email: "a@gocase.com.evil.example" })).allow, false);
  // E o dominio exato continua passando.
  assert.equal(decideOracleAccess(ctx({ email: "a@gocase.com" })).allow, true);
});

// --- contas pessoais e e-mails inutilizaveis --------------------------------

test("nega Gmail pessoal e outros dominios externos", () => {
  for (const email of [
    "pessoa@gmail.com",
    "pessoa@googlemail.com",
    "pessoa@outlook.com",
    "pessoa@parceiro.com.br",
  ]) {
    const d = decideOracleAccess(ctx({ email }));
    assert.equal(d.allow, false, email);
    assert.equal(d.reason, "domain_not_allowed", email);
  }
});

test("nega e-mail ausente, vazio ou nao-string", () => {
  for (const email of [undefined, null, "", "   ", 42, {}]) {
    const d = decideOracleAccess(ctx({ email }));
    assert.equal(d.allow, false, JSON.stringify(email));
    assert.equal(d.reason, "email_missing", JSON.stringify(email));
  }
});

test("nega e-mail nao verificado — `true` estrito", () => {
  for (const verified of [false, undefined, null, "true", 1, 0]) {
    const d = decideOracleAccess(ctx({ emailVerified: verified }));
    assert.equal(d.allow, false, JSON.stringify(verified));
    assert.equal(d.reason, "email_not_verified", JSON.stringify(verified));
  }
});

test("nega e-mail malformado: multiplos @, sem @, partes vazias, espaco interno", () => {
  for (const email of [
    "a@b@gocase.com",
    "pessoa@@gocase.com",
    "pessoagocase.com",
    "@gocase.com",
    "pessoa@",
    "pes soa@gocase.com",
    "pessoa@go case.com",
  ]) {
    const d = decideOracleAccess(ctx({ email }));
    assert.equal(d.allow, false, email);
    assert.ok(
      d.reason === "email_malformed" || d.reason === "domain_not_allowed",
      email + " -> " + d.reason,
    );
  }
});

test("extractEmailDomain devolve null em vez de adivinhar", () => {
  assert.equal(extractEmailDomain("pessoa@gocase.com"), "gocase.com");
  assert.equal(extractEmailDomain("  Pessoa@GoCase.COM "), "gocase.com");
  assert.equal(extractEmailDomain("a@b@c"), null);
  assert.equal(extractEmailDomain("semarroba"), null);
  assert.equal(extractEmailDomain("@dominio.com"), null);
  assert.equal(extractEmailDomain("local@"), null);
  assert.equal(extractEmailDomain(""), null);
  assert.equal(extractEmailDomain(undefined), null);
  assert.equal(extractEmailDomain(123), null);
});

// --- conexao ----------------------------------------------------------------

test("nega conexao que nao seja Google", () => {
  for (const strategy of ["auth0", "windowslive", "github", "samlp", "ad", "", undefined]) {
    const d = decideOracleAccess(ctx({ connectionStrategy: strategy }));
    assert.equal(d.allow, false, String(strategy));
    assert.equal(d.reason, "connection_not_allowed", String(strategy));
  }
});

test("conexao nominal e' respeitada quando configurada", () => {
  const restrito = { allowedConnections: ["google-corporativo"] };
  assert.equal(decideOracleAccess(ctx({ ...restrito, connectionName: "google-oauth2" })).allow, false);
  assert.equal(
    decideOracleAccess(ctx({ ...restrito, connectionName: "google-corporativo" })).allow,
    true,
  );
});

// --- isolamento de outros clientes ------------------------------------------

test("cliente fora do Oraculo NAO e' tocado — nem negado, nem enriquecido", () => {
  const outros = ["cid_outra_aplicacao", "cid_dashboard_interno", "", undefined, null];

  for (const clientId of outros) {
    // De proposito com dados que reprovariam se a Action se aplicasse.
    const d = decideOracleAccess(
      ctx({ clientId, email: "qualquer@gmail.com", emailVerified: false }),
    );
    assert.equal(d.applies, false, String(clientId));
    assert.equal(d.reason, "not_oracle_client", String(clientId));
  }
});

test("allowlist vazia nao transforma cliente algum em cliente do Oraculo", () => {
  const d = decideOracleAccess(ctx({ oracleClientIds: [] }));
  assert.equal(d.applies, false, "sem allowlist, a Action nao interfere");
});

test("cada organizacao Claude tem seu Client ID, e todos entram pela mesma politica", () => {
  for (const clientId of [ORACLE_CLIENT, OUTRA_ORG]) {
    const d = decideOracleAccess(ctx({ clientId }));
    assert.equal(d.applies, true, clientId);
    assert.equal(d.allow, true, clientId);
  }
  // Revogar uma organizacao e' remover o Client ID da allowlist; as demais seguem.
  const semOrgB = decideOracleAccess(ctx({ clientId: OUTRA_ORG, oracleClientIds: [ORACLE_CLIENT] }));
  assert.equal(semOrgB.applies, false, "org revogada sai da politica sem afetar as outras");
});

// --- audience ---------------------------------------------------------------

test("cliente do Oraculo pedindo audience divergente e' negado", () => {
  for (const requestedAudience of [
    "https://outra-api.example/api",
    "https://mktplace-gobeaute.vercel.app/api/mcp/",
    "https://mktplace-gobeaute.vercel.app/api/outro",
  ]) {
    const d = decideOracleAccess(ctx({ requestedAudience }));
    assert.equal(d.applies, true, requestedAudience);
    assert.equal(d.allow, false, requestedAudience);
    assert.equal(d.reason, "audience_mismatch", requestedAudience);
  }
});

test("audience ausente no runtime nao reprova; audience esperada ausente reprova", () => {
  // Nem todo contexto expoe `resource_server`; a ausencia nao pode quebrar login.
  assert.equal(decideOracleAccess(ctx({ requestedAudience: undefined })).allow, true);
  assert.equal(decideOracleAccess(ctx({ requestedAudience: "" })).allow, true);
  // Mas se o runtime informa uma audience e a Action nao sabe qual esperar, nega.
  const d = decideOracleAccess(ctx({ expectedAudience: "" }));
  assert.equal(d.allow, false);
  assert.equal(d.reason, "audience_mismatch");
});

// --- hosted domain ----------------------------------------------------------

test("hosted domain do Google, quando presente, precisa concordar", () => {
  assert.equal(decideOracleAccess(ctx({ hostedDomain: "gocase.com" })).allow, true);
  const d = decideOracleAccess(ctx({ hostedDomain: "outro.com" }));
  assert.equal(d.allow, false);
  assert.equal(d.reason, "hosted_domain_mismatch");
});

test("ausencia de hosted domain NAO quebra o login corporativo", () => {
  for (const hd of [undefined, null, "", "   "]) {
    assert.equal(decideOracleAccess(ctx({ hostedDomain: hd })).allow, true, JSON.stringify(hd));
  }
});

// --- origem do hosted domain: idp_tenant_domain e' a fonte oficial ----------

const { resolveHostedDomain } = action;

test("idp_tenant_domain e' a fonte PRIMARIA do hosted domain", () => {
  assert.equal(
    resolveHostedDomain({ user: { idp_tenant_domain: "gocase.com" } }),
    "gocase.com",
    "o Auth0 mapeia o `hd` do Google para idp_tenant_domain",
  );
});

test("fallback coincidente NAO mascara divergencia da fonte primaria", () => {
  // Armadilha real: se o codigo fizesse `primary || fallback`, um app_metadata
  // "certo" esconderia um idp_tenant_domain "errado". Nao pode.
  const resolvido = resolveHostedDomain({
    user: {
      idp_tenant_domain: "outro-tenant.com",
      app_metadata: { hd: "gocase.com" },
    },
    connection: { metadata: { hd: "gocase.com" } },
  });

  assert.equal(resolvido, "outro-tenant.com", "a fonte primaria prevalece, mesmo divergente");

  const d = decideOracleAccess(ctx({ hostedDomain: resolvido }));
  assert.equal(d.allow, false, "e a divergencia precisa negar");
  assert.equal(d.reason, "hosted_domain_mismatch");
});

test("fallbacks so' valem quando a fonte primaria esta AUSENTE", () => {
  assert.equal(
    resolveHostedDomain({ user: { app_metadata: { hd: "gocase.com" } } }),
    "gocase.com",
    "sem idp_tenant_domain, app_metadata vale",
  );
  assert.equal(
    resolveHostedDomain({ user: {}, connection: { metadata: { hd: "gocase.com" } } }),
    "gocase.com",
    "depois, a conexao",
  );
  // Primaria presente porem inutilizavel: trata como ausente de valor, sem
  // cair para fallback — a fonte oficial ja' se manifestou.
  assert.equal(resolveHostedDomain({ user: { idp_tenant_domain: "" }, connection: { metadata: { hd: "gocase.com" } } }), "");
  assert.equal(resolveHostedDomain({ user: { idp_tenant_domain: 42 }, connection: { metadata: { hd: "gocase.com" } } }), "");
});

test("conta Google pessoal nao tem hosted domain, e e' barrada pelo dominio", () => {
  // O Auth0 documenta que a ausencia de `hd` indica conta fora de dominio
  // hospedado. Aqui a ausencia nao reprova sozinha — quem barra e' o dominio.
  assert.equal(resolveHostedDomain({ user: { email: "pessoa@gmail.com" } }), "");
  const d = decideOracleAccess(ctx({ email: "pessoa@gmail.com", hostedDomain: "" }));
  assert.equal(d.allow, false);
  assert.equal(d.reason, "domain_not_allowed");
});

test("hosted domain e' normalizado com trim e lowercase, sem endsWith", () => {
  assert.equal(decideOracleAccess(ctx({ hostedDomain: "GoCase.COM" })).allow, true);
  assert.equal(decideOracleAccess(ctx({ hostedDomain: "  gocase.com  " })).allow, true);
  // Vizinho malicioso jamais pode passar por coincidencia de sufixo.
  assert.equal(decideOracleAccess(ctx({ hostedDomain: "evilgocase.com" })).allow, false);
  assert.equal(decideOracleAccess(ctx({ hostedDomain: "gocase.com.evil.example" })).allow, false);
});

test("idp_tenant_domain percorre o handler real ponta a ponta", async () => {
  const igual = await executar({
    user: { email: "pessoa@gocase.com", email_verified: true, idp_tenant_domain: "gocase.com" },
  });
  assert.deepEqual(igual.concedidos, ["oracle:read"], "hd concordante concede");

  const divergente = await executar({
    user: { email: "pessoa@gocase.com", email_verified: true, idp_tenant_domain: "outro.com" },
  });
  assert.deepEqual(divergente.concedidos, [], "hd divergente nao concede");
  assert.equal(divergente.negas.length, 1, "e nega");

  const ausente = await executar({
    user: { email: "pessoa@gocase.com", email_verified: true },
  });
  assert.deepEqual(ausente.concedidos, ["oracle:read"], "hd ausente mantem a decisao por e-mail");
});

// --- poder concedido --------------------------------------------------------

test("a Action so' consegue conceder oracle:read", async () => {
  const concedidos: string[] = [];
  const negas: string[] = [];
  const api = {
    accessToken: { addScope: (s: string) => concedidos.push(s) },
    access: { deny: (m: string) => negas.push(m) },
  };

  await action.onExecutePostLogin(
    {
      client: { client_id: ORACLE_CLIENT },
      secrets: { ORACLE_CLIENT_IDS: ORACLE_CLIENT, ORACLE_API_AUDIENCE: AUDIENCE },
      resource_server: { identifier: AUDIENCE },
      connection: { strategy: "google-oauth2", name: "google-oauth2" },
      user: { email: "pessoa@gocase.com", email_verified: true },
    },
    api,
  );

  assert.deepEqual(concedidos, ["oracle:read"], "um unico scope, e read-only");
  assert.equal(negas.length, 0);
  assert.equal(action.ORACLE_SCOPE, "oracle:read");

  // Nenhum scope de escrita existe no vocabulario da Action.
  const fonte = require_("node:fs").readFileSync(
    require_.resolve("../../../infra/auth0/actions/oracle-corporate-access.js"),
    "utf8",
  ) as string;
  for (const proibido of ["oracle:write", "oracle:admin", "write", "delete", "update"]) {
    const regex = new RegExp('addScope\\("[^"]*' + proibido, "i");
    assert.ok(!regex.test(fonte), "nenhum addScope de escrita: " + proibido);
  }
});

test("conta reprovada nega e NAO recebe scope algum", async () => {
  const concedidos: string[] = [];
  const negas: string[] = [];
  const api = {
    accessToken: { addScope: (s: string) => concedidos.push(s) },
    access: { deny: (m: string) => negas.push(m) },
  };

  await action.onExecutePostLogin(
    {
      client: { client_id: ORACLE_CLIENT },
      secrets: { ORACLE_CLIENT_IDS: ORACLE_CLIENT, ORACLE_API_AUDIENCE: AUDIENCE },
      resource_server: { identifier: AUDIENCE },
      connection: { strategy: "google-oauth2", name: "google-oauth2" },
      user: { email: "externo@gmail.com", email_verified: true },
    },
    api,
  );

  assert.deepEqual(concedidos, [], "conta externa nao pode receber scope");
  assert.equal(negas.length, 1, "precisa negar explicitamente");
  // A mensagem ao usuario nao pode ecoar e-mail nem dominio.
  assert.ok(!negas[0].includes("gmail"), "mensagem nao ecoa o dominio");
  assert.ok(!negas[0].includes("externo"), "mensagem nao ecoa o e-mail");
});

test("aplicacao de terceiros passa intacta: sem deny, sem addScope", async () => {
  const concedidos: string[] = [];
  const negas: string[] = [];
  const api = {
    accessToken: { addScope: (s: string) => concedidos.push(s) },
    access: { deny: (m: string) => negas.push(m) },
  };

  await action.onExecutePostLogin(
    {
      client: { client_id: "cid_de_outra_aplicacao" },
      secrets: { ORACLE_CLIENT_IDS: ORACLE_CLIENT, ORACLE_API_AUDIENCE: AUDIENCE },
      connection: { strategy: "auth0", name: "Username-Password-Authentication" },
      user: { email: "pessoa@gmail.com", email_verified: false },
    },
    api,
  );

  assert.deepEqual(concedidos, [], "nao enriquece outra aplicacao");
  assert.deepEqual(negas, [], "nao derruba login de outra aplicacao");
});

// --- login inicial x refresh token exchange ---------------------------------

/** Roda o handler real e devolve o que foi concedido/negado. */
async function executar(over: Record<string, unknown> = {}) {
  const concedidos: string[] = [];
  const negas: string[] = [];
  const api = {
    accessToken: { addScope: (s: string) => concedidos.push(s) },
    access: { deny: (m: string) => negas.push(m) },
  };

  await action.onExecutePostLogin(
    {
      client: { client_id: ORACLE_CLIENT },
      secrets: { ORACLE_CLIENT_IDS: ORACLE_CLIENT, ORACLE_API_AUDIENCE: AUDIENCE },
      resource_server: { identifier: AUDIENCE },
      connection: { strategy: "google-oauth2", name: "google-oauth2" },
      user: { email: "pessoa@gocase.com", email_verified: true },
      transaction: { protocol: "oidc-basic-profile" },
      ...over,
    },
    api,
  );

  return { concedidos, negas };
}

test("Post-Login cobre login inicial E refresh — um unico handler", async () => {
  const inicial = await executar({ transaction: { protocol: "oidc-basic-profile" } });
  const refresh = await executar({ transaction: { protocol: "oauth2-refresh-token" } });

  assert.deepEqual(inicial.concedidos, ["oracle:read"], "login inicial concede");
  assert.deepEqual(refresh.concedidos, ["oracle:read"], "refresh tambem concede");
  assert.deepEqual(inicial, refresh, "decisao IDENTICA nos dois fluxos");
});

test("refresh nao e' caminho para escapar da politica de dominio", async () => {
  // Se um refresh pudesse burlar a regra, bastaria renovar para manter acesso
  // depois de o dominio deixar de ser aprovado.
  const refreshExterno = await executar({
    transaction: { protocol: "oauth2-refresh-token" },
    user: { email: "externo@gmail.com", email_verified: true },
  });

  assert.deepEqual(refreshExterno.concedidos, [], "refresh externo nao recebe scope");
  assert.equal(refreshExterno.negas.length, 1, "refresh externo e' negado igual ao login");
});

test("transaction ausente nao quebra a decisao", async () => {
  const semTransaction = await executar({ transaction: undefined });
  assert.deepEqual(semTransaction.concedidos, ["oracle:read"]);
});

test("NAO existe onExecuteCredentialsExchange — trigger errado para refresh", () => {
  // Credentials Exchange e' Client Credentials/M2M: evento sem `event.user`.
  // Exportar o handler de Post-Login sob aquele nome misturaria contratos e
  // ainda assim nao cobriria refresh, que ja' passa por Post-Login.
  assert.equal(
    (action as unknown as Record<string, unknown>).onExecuteCredentialsExchange,
    undefined,
    "nenhum segundo handler pode existir",
  );

  const fonte = require_("node:fs").readFileSync(
    require_.resolve("../../../infra/auth0/actions/oracle-corporate-access.js"),
    "utf8",
  ) as string;
  assert.ok(
    !/exports\.onExecuteCredentialsExchange\s*=/.test(fonte),
    "o arquivo nao pode exportar onExecuteCredentialsExchange",
  );
  assert.ok(/exports\.onExecutePostLogin\s*=/.test(fonte), "Post-Login e' o unico handler");
});

// --- higiene ----------------------------------------------------------------

test("o arquivo da Action nao carrega segredo, e-mail nem Client ID real", () => {
  const fonte = require_("node:fs").readFileSync(
    require_.resolve("../../../infra/auth0/actions/oracle-corporate-access.js"),
    "utf8",
  ) as string;

  for (const padrao of [
    /client_secret\s*[:=]\s*["'][^"']+["']/i,
    /eyJ[A-Za-z0-9_-]{10,}/,
    /BEGIN (RSA |EC )?PRIVATE KEY/,
    /[A-Za-z0-9._%-]+@(gocase\.com|gobeaute\.com\.br)/,
    /\.us\.auth0\.com/,
  ]) {
    assert.ok(!padrao.test(fonte), "Action nao pode conter: " + padrao);
  }

  // Client IDs e audience vêm de secrets, nunca embutidos.
  assert.ok(fonte.includes("secrets.ORACLE_CLIENT_IDS"), "Client IDs vêm de secret");
  assert.ok(fonte.includes("secrets.ORACLE_API_AUDIENCE"), "audience vem de secret");
});

test("os dominios aprovados sao exatamente dois", () => {
  assert.deepEqual([...action.ALLOWED_DOMAINS].sort(), ["gobeaute.com.br", "gocase.com"]);
});
