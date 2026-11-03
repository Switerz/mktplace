// torre-alerta-gatilho: o cron do GoDeploy (*/15 * * * *) chama
// POST /cron/alerta-vencidos, que so' confere a assinatura e repassa para a API
// do Render. Contrato em docs/expedicao_contrato.md ("Alerta de vencidos da
// Shopee no Slack"). Segredo: env.ALERTA_WEBHOOK_SECRET (setAppSecret), nunca
// em codigo nem em log.
import { URL_DA_API, statusParaGoDeploy } from "./repasse.js";

const ROTA = "/cron/alerta-vencidos";

/**
 * Confere o X-Godeploy-Cron contra env.GODEPLOY_CRON_KEY (copiado sem mudanca
 * da referencia de cron da skill godeploy).
 */
async function verifyCron(request, env) {
  const header = request.headers.get("x-godeploy-cron");
  if (!header) return false;
  const tsMatch = header.match(/(?:^|;)\s*t=(\d+)/);
  const sigMatch = header.match(/(?:^|;)\s*sig=([0-9a-f]+)/);
  if (!tsMatch || !sigMatch) return false;

  const ts = Number.parseInt(tsMatch[1], 10);
  const sigHex = sigMatch[1];
  // Reject replays outside a ±5 min window.
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - ts) > 300) return false;

  const key = env.GODEPLOY_CRON_KEY;
  if (!key) return false;

  // appId is the leftmost label of the request hostname — cron calls always
  // arrive on the appId subdomain (never the slug).
  const url = new URL(request.url);
  const appId = url.hostname.split(".")[0];
  const canonical = JSON.stringify([
    "v1",
    appId,
    request.method.toUpperCase(),
    url.pathname,
    ts,
  ]);

  const enc = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const sigBytes = new Uint8Array(
    sigHex.match(/.{1,2}/g).map((b) => Number.parseInt(b, 16)),
  );
  return crypto.subtle.verify(
    "HMAC",
    cryptoKey,
    sigBytes.buffer,
    enc.encode(canonical),
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== ROTA || request.method !== "POST") {
      return new Response("not found", { status: 404 });
    }
    if (!(await verifyCron(request, env))) {
      return new Response("forbidden", { status: 403 });
    }

    let status = null;
    let resumo = "";
    try {
      const resp = await fetch(URL_DA_API, {
        method: "POST",
        headers: { "X-Alerta-Token": env.ALERTA_WEBHOOK_SECRET ?? "" },
      });
      status = resp.status;
      if (resp.ok) {
        const corpo = await resp.json();
        resumo = ` situacao=${corpo.situacao} evento=${corpo.evento} enviados=${corpo.enviados} ja_tinham=${corpo.ja_tinham}`;
      }
    } catch (erro) {
      resumo = ` falha de rede (${erro?.name ?? "erro"})`;
    }
    const devolve = statusParaGoDeploy(status);
    console.log(`[torre-alerta-gatilho] api=${status}${resumo} -> ${devolve}`);
    return new Response(devolve === 200 ? "ok" : "falhou", { status: devolve });
  },
};
