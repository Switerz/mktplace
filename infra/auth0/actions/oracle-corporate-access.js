/**
 * Auth0 Post-Login Action — acesso corporativo automatico ao Oraculo.
 *
 * TRIGGER OBRIGATORIO: Login / Post Login, **versao 3**. `api.accessToken.addScope`
 * nao existe nas versoes anteriores; publicar em v2 quebra silenciosamente a
 * concessao de scope.
 *
 * UM UNICO HANDLER, DOIS FLUXOS. O Post-Login roda no login inicial E na troca
 * por refresh token; o fluxo e' identificavel por
 * `event.transaction.protocol === "oauth2-refresh-token"`, valor documentado
 * pelo Auth0 para esse campo. Nao existe segundo handler: `onExecutePostLogin`
 * cobre os dois. `onExecuteCredentialsExchange` seria ERRADO aqui — e' o
 * trigger de Client Credentials/M2M, com contrato de evento diferente (sem
 * `event.user`), e nao tem relacao com refresh token.
 *
 * A politica nao ramifica por protocolo: a mesma decisao de dominio vale nos
 * dois fluxos, de proposito. Um refresh nao pode ser caminho para escapar da
 * regra que o login inicial aplicou.
 *
 * O QUE FAZ. Para os OAuth Clients do Oraculo — e somente para eles — concede
 * `oracle:read` a quem autenticou pelo Google com e-mail corporativo VERIFICADO
 * em `gocase.com` ou `gobeaute.com.br`. Qualquer outra situacao e' negada.
 *
 * POR QUE addScope E NAO ROLE. O resource server une os claims `permissions` e
 * `scope` ao decidir (`effectiveScopes` em `src/server/oracle/oauth.ts`), entao
 * um scope adicionado aqui autoriza sem depender de RBAC. Isso evita credencial
 * M2M, evita rate limit da Management API e vale ja' no PRIMEIRO token — uma
 * atribuicao de role via Management API so' valeria a partir do login seguinte.
 *
 * FAIL-CLOSED. Nada aqui concede acesso por omissao: a decisao parte de "negar"
 * e so' vira "conceder" com todas as condicoes satisfeitas. Se o contexto
 * necessario faltar, nega.
 *
 * ISOLAMENTO. Um cliente que nao esteja na allowlist do Oraculo sai desta Action
 * sem qualquer efeito — nenhum login de outra aplicacao do tenant e' afetado,
 * nem negado, nem enriquecido.
 *
 * PRIVACIDADE. Nao registra e-mail, token, claim pessoal nem Client ID. O
 * vocabulario de motivo e' fechado e nao deriva de entrada do usuario.
 *
 * SECRETS ESPERADOS (configurados no painel da Action, nunca no repositorio):
 *   ORACLE_CLIENT_IDS    — Client IDs do Oraculo, separados por virgula
 *   ORACLE_API_AUDIENCE  — identifier da API do Oraculo
 *   ORACLE_CONNECTIONS   — (opcional) nomes de conexao Google aceitos
 */

/** Unico scope que esta Action e' capaz de conceder. Nao ha caminho de escrita. */
const ORACLE_SCOPE = "oracle:read";

/** Dominios corporativos aprovados. Comparacao por IGUALDADE, nunca sufixo. */
const ALLOWED_DOMAINS = Object.freeze(["gocase.com", "gobeaute.com.br"]);

/** Estrategias de conexao aceitas. Google corporativo apenas. */
const DEFAULT_ALLOWED_STRATEGIES = Object.freeze(["google-oauth2"]);

/**
 * Extrai o dominio de um e-mail, ou `null` se ele nao for utilizavel.
 *
 * Rejeita: nao-string, vazio, espaco interno, zero ou multiplos `@`, parte local
 * vazia, dominio vazio. `null` sempre significa "nao da' para decidir com
 * seguranca" — e quem chama trata isso como negacao.
 */
function extractEmailDomain(rawEmail) {
  if (typeof rawEmail !== "string") return null;

  const email = rawEmail.trim().toLowerCase();
  if (email.length === 0) return null;
  // Espaco no meio nunca e' e-mail valido; `trim` so' cuida das pontas.
  if (/\s/.test(email)) return null;

  const parts = email.split("@");
  // Exatamente um `@`. "a@b@c" e "semarroba" caem aqui.
  if (parts.length !== 2) return null;

  const local = parts[0];
  const domain = parts[1];
  if (local.length === 0 || domain.length === 0) return null;

  return domain;
}

/**
 * Decide o acesso. Pura: nenhum I/O, nenhuma dependencia do runtime do Auth0,
 * nada de `event`/`api` — e' isto que a torna testavel sem simular o Auth0.
 *
 * Devolve `{ applies, allow, reason }`:
 *   applies=false  -> cliente fora do Oraculo; a Action nao deve fazer NADA.
 *   applies=true, allow=false -> negar.
 *   applies=true, allow=true  -> conceder `oracle:read`.
 *
 * `reason` pertence a um vocabulario fechado e nunca embute entrada do usuario.
 */
function decideOracleAccess(input) {
  const oracleClientIds = Array.isArray(input.oracleClientIds) ? input.oracleClientIds : [];
  const clientId = typeof input.clientId === "string" ? input.clientId.trim() : "";

  // Cliente do Oraculo? Igualdade exata contra a allowlist configurada.
  const isOracleClient = clientId.length > 0 && oracleClientIds.indexOf(clientId) !== -1;
  if (!isOracleClient) {
    return { applies: false, allow: false, reason: "not_oracle_client" };
  }

  // Daqui para baixo estamos num cliente do Oraculo: tudo e' fail-closed.

  // A audience so' e' checada quando o runtime a expoe. Quando expoe e diverge,
  // nega: um cliente do Oraculo pedindo outra API e' configuracao errada.
  const expectedAudience =
    typeof input.expectedAudience === "string" ? input.expectedAudience.trim() : "";
  const requestedAudience =
    typeof input.requestedAudience === "string" ? input.requestedAudience.trim() : "";
  if (requestedAudience.length > 0) {
    if (expectedAudience.length === 0 || requestedAudience !== expectedAudience) {
      return { applies: true, allow: false, reason: "audience_mismatch" };
    }
  }

  const allowedStrategies =
    Array.isArray(input.allowedStrategies) && input.allowedStrategies.length > 0
      ? input.allowedStrategies
      : DEFAULT_ALLOWED_STRATEGIES;
  const strategy = typeof input.connectionStrategy === "string" ? input.connectionStrategy : "";
  if (allowedStrategies.indexOf(strategy) === -1) {
    return { applies: true, allow: false, reason: "connection_not_allowed" };
  }

  // Conexao nominal, quando restringida por configuracao.
  const allowedConnections = Array.isArray(input.allowedConnections) ? input.allowedConnections : [];
  if (allowedConnections.length > 0) {
    const connection = typeof input.connectionName === "string" ? input.connectionName : "";
    if (allowedConnections.indexOf(connection) === -1) {
      return { applies: true, allow: false, reason: "connection_not_allowed" };
    }
  }

  if (typeof input.email !== "string" || input.email.trim().length === 0) {
    return { applies: true, allow: false, reason: "email_missing" };
  }

  // `true` estrito: "true", 1 ou undefined NAO contam como verificado.
  if (input.emailVerified !== true) {
    return { applies: true, allow: false, reason: "email_not_verified" };
  }

  const domain = extractEmailDomain(input.email);
  if (domain === null) {
    return { applies: true, allow: false, reason: "email_malformed" };
  }

  // IGUALDADE, jamais `endsWith`: `endsWith` aceitaria `evilgocase.com`, e
  // prefixo aceitaria `gocase.com.evil.example`.
  if (ALLOWED_DOMAINS.indexOf(domain) === -1) {
    return { applies: true, allow: false, reason: "domain_not_allowed" };
  }

  // Defesa adicional: o Auth0 mapeia o claim `hd` do Google para
  // `idp_tenant_domain` no perfil. Presente, precisa concordar EXATAMENTE com o
  // dominio do e-mail. Ausente, NAO reprova — contas Google pessoais nao tem
  // `hd`, e o Auth0 documenta a ausencia como sinal de conta fora de um dominio
  // hospedado; quem barra essas contas aqui e' a checagem de dominio acima.
  const hostedDomain =
    typeof input.hostedDomain === "string" ? input.hostedDomain.trim().toLowerCase() : "";
  if (hostedDomain.length > 0 && hostedDomain !== domain) {
    return { applies: true, allow: false, reason: "hosted_domain_mismatch" };
  }

  return { applies: true, allow: true, reason: "granted" };
}

/**
 * Resolve o hosted domain do Google, com PRECEDENCIA estrita.
 *
 * `event.user.idp_tenant_domain` e' a fonte oficial: o Auth0 mapeia para ela o
 * claim `hd` do Google. Se ela existir — ainda que com valor divergente —, e'
 * ela que vale; um fallback coincidente NAO pode mascarar essa divergencia.
 * So' quando a principal esta ausente e' que os fallbacks sao consultados.
 */
function resolveHostedDomain(event) {
  const user = event.user || {};

  const primary = user.idp_tenant_domain;
  if (typeof primary === "string" && primary.trim().length > 0) return primary;
  // Presente porem inutilizavel (nao-string, vazia): trata como ausente, sem
  // cair para fallback — a fonte oficial ja' se manifestou.
  if (primary !== undefined && primary !== null) return "";

  const appMetadata = user.app_metadata || {};
  if (typeof appMetadata.hd === "string" && appMetadata.hd.trim().length > 0) return appMetadata.hd;

  const connectionMetadata = (event.connection && event.connection.metadata) || {};
  if (typeof connectionMetadata.hd === "string" && connectionMetadata.hd.trim().length > 0) {
    return connectionMetadata.hd;
  }

  return "";
}

/** Lista a partir de secret separado por virgula. Entradas vazias sao descartadas. */
function parseList(raw) {
  if (typeof raw !== "string") return [];
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/**
 * Handler do Post-Login (trigger v3).
 *
 * @param {Event} event
 * @param {PostLoginAPI} api
 */
exports.onExecutePostLogin = async (event, api) => {
  const secrets = event.secrets || {};

  const decision = decideOracleAccess({
    clientId: event.client && event.client.client_id,
    oracleClientIds: parseList(secrets.ORACLE_CLIENT_IDS),
    expectedAudience: secrets.ORACLE_API_AUDIENCE,
    requestedAudience: event.resource_server && event.resource_server.identifier,
    connectionStrategy: event.connection && event.connection.strategy,
    connectionName: event.connection && event.connection.name,
    allowedConnections: parseList(secrets.ORACLE_CONNECTIONS),
    email: event.user && event.user.email,
    emailVerified: event.user && event.user.email_verified,
    // PRECEDENCIA: `idp_tenant_domain` e' onde o Auth0 mapeia o `hd` do Google.
    // E' a fonte primaria; os demais sao fallback e nunca podem MASCARAR uma
    // divergencia da principal — por isso so' sao consultados quando ela esta
    // ausente, e nao combinados com `||` sobre um valor ja' presente.
    hostedDomain: resolveHostedDomain(event),
  });

  // Cliente de outra aplicacao: sair sem tocar em nada.
  if (!decision.applies) return;

  if (!decision.allow) {
    // Mensagem generica e estavel: nao revela qual condicao reprovou, nem
    // ecoa e-mail ou dominio. O motivo detalhado fica no log do Auth0.
    api.access.deny("Acesso ao Oraculo restrito a contas corporativas autorizadas.");
    return;
  }

  api.accessToken.addScope(ORACLE_SCOPE);
};

// NAO existe `onExecuteCredentialsExchange` aqui, e nao deve existir: aquele e'
// o trigger de Client Credentials/M2M, cujo evento nao carrega `event.user` —
// exportar o handler de Post-Login sob aquele nome misturaria contratos e ainda
// assim NAO cobriria refresh. O refresh ja' passa por `onExecutePostLogin`.
//
// OFFBOARDING permanece exigindo revogacao de grant por outra razao: um access
// token JA' EMITIDO vive ate `exp`, independentemente de a politica ter mudado.
// Bloquear o usuario nao invalida o que ja' esta na mao dele.

// Exportados para teste. O runtime do Auth0 ignora exports extras.
exports.decideOracleAccess = decideOracleAccess;
exports.extractEmailDomain = extractEmailDomain;
exports.resolveHostedDomain = resolveHostedDomain;
exports.ALLOWED_DOMAINS = ALLOWED_DOMAINS;
exports.ORACLE_SCOPE = ORACLE_SCOPE;
