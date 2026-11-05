// Para onde o gatilho repassa e como o status da API vira status do cron.
// A regra do alerta NAO mora aqui: ver apps/api/app/services/alerta_vencidos.py.

const API = "https://mktplace-api.onrender.com/api/v1/alertas";

/** Mantido por compatibilidade: a rota do alerta de vencidos (A1). */
export const URL_DA_API = `${API}/expedicao-vencidos`;

/**
 * Caminho do cron no gatilho -> rota da API. Mapa dos alertas em
 * docs/torre_bot_alertas.md. Cron de cada um (UTC):
 *   /cron/alerta-vencidos   a cada 15 min
 *   /cron/alertas-criticos  a cada 15 min
 *   /cron/report-diario     todo dia 11:00 UTC (8h de Brasilia)
 */
export const ROTAS = {
  "/cron/alerta-vencidos": URL_DA_API,
  "/cron/alertas-criticos": `${API}/criticos`,
  "/cron/report-diario": `${API}/report-diario`,
};

/**
 * Status da API do Render -> status devolvido ao cron do GoDeploy.
 * O GoDeploy repete 5xx (ate' 3 tentativas, 30 s) e nao repete 4xx.
 *  - 2xx -> 200;
 *  - falha de rede (null) ou 5xx -> 502: vale tentar de novo (a API acorda,
 *    o Slack volta; quem ja' recebeu a DM e' pulado pela API);
 *  - 4xx -> 424: segredo errado ou rota ausente nao se resolve repetindo.
 * @param {number | null} upstream
 * @returns {number}
 */
export function statusParaGoDeploy(upstream) {
  if (upstream === null || upstream >= 500) return 502;
  if (upstream >= 200 && upstream < 300) return 200;
  return 424;
}
