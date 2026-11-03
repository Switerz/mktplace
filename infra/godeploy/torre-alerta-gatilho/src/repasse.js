// Para onde o gatilho repassa e como o status da API vira status do cron.
// A regra do alerta NAO mora aqui: ver apps/api/app/services/alerta_vencidos.py.

export const URL_DA_API =
  "https://mktplace-api.onrender.com/api/v1/alertas/expedicao-vencidos";

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
