# DRE-MVP-1 — API da DRE por canal e marca

Data: 2026-10-13
Contrato de origem: `docs/dre_contrato_0.md` (PR #65). Roteiro das pendências: `docs/dre_reunioes_fontes_financeiras.md`.
Escopo desta entrega: tipos, serviço, endpoint e testes. **Sem tela, sem migration, sem publisher, sem escrita em banco.**

## 1. Endpoint

`GET /api/v1/dre`

| Parâmetro | Regra |
|---|---|
| `date_from`, `date_to` | Meses inteiros (dia 1 ao último dia). Período desalinhado → `422` |
| `ref_month` | Alias de um mês (`YYYY-MM`) |
| `channels` | `all`, `tiktok`, `ml`, `shopee` ou combinação |
| `brands` | `brand_key` separados por vírgula; omitido = marcas ativas |
| sem período | mês anterior (padrão dos endpoints agregados) |

A resposta traz quatro recortes calculados a partir da mesma célula base `mês × canal × marca`: `total`, `por_canal`,
`por_marca` e `por_canal_marca`. As células base também vêm em `celulas_base`, para auditoria.

Cada célula traz:

- `componentes`: `receita`, `devolucoes`, `taxas`, `afiliados`, `ads`, `frete`, `cmv`, `impostos`, cada um com `valor`,
  `pct_receita`, `status`, `motivo`, `fonte` e `somavel`;
- `componentes_disponiveis` e `componentes_ausentes` (com status e motivo);
- `completude` (`"4 de 6 custos"`);
- `classificacao` (`COMPLETA` / `PARCIAL` / `NAO_CALCULAVEL`) e `motivos_classificacao`;
- `avisos_qualidade`;
- `data_maxima` (último dia de receita, carga do mart, última emissão fiscal, atualização do repasse TikTok);
- `margem_contribuicao_conhecida` com `valor`, `pct_receita`, `formula`, `componentes_usados` e `faltam`.

## 2. Regras aplicadas

| Regra | Implementação |
|---|---|
| Ausência ≠ zero | `valor = null` + `status`; nenhum `COALESCE` em custo |
| Sinal | ML e Shopee gravam custo positivo; repasse TikTok, negativo. Normalizado num único ponto; sinal contrário vira `sinal_inesperado`, nunca `abs()` |
| Afiliado TikTok | Dentro de `taxas` (contrato §6). `somavel = false`; nunca entra na margem. Afiliado maior que a taxa → célula `NAO_CALCULAVEL` |
| Comissão ML | `total_fees` do mart, uma única vez, como `taxas` |
| Taxa e frete TikTok | Repasse por coorte de pedido, publicado no Neon por `pipelines.dre.insumo_publisher` (DRE-DATA-BRIDGE-1). O `total_fees` TikTok do mart **não** é usado: está congelado (contrato §4.5). Identidade do repasse violada → `nao_reconciliado` |
| Shopee | Taxas e frete com o sinal atual, mas `nao_confirmado`: sem contraprova independente (pergunta C4) |
| CMV | Fiscal, só se receita fiscal ÷ GMV ∈ [0,95; 1,05] na célula. CMV 100% provisório → `provisorio` |
| Impostos e devoluções | Sempre `indisponivel` (perguntas F8, C6, F5) |
| Maturidade | Mês M só fecha depois que M+2 terminou (decisão D5) |
| Nome | "Margem de contribuição conhecida". Nada se chama margem líquida ou lucro |

### Classificação

| Estado | Condição |
|---|---|
| `COMPLETA` | Todos os custos aplicáveis disponíveis, inclusive impostos e devoluções. **Inalcançável hoje** |
| `PARCIAL` | Receita completa, `taxas` e `cmv` disponíveis e reconciliados, mês maduro, guardrail de afiliado ok |
| `NAO_CALCULAVEL` | Qualquer requisito acima falhou; o motivo vem em `motivos_classificacao` e a margem é `null` |

Agregação: valores somam célula a célula; a classificação é a pior entre as células; a margem é a soma das margens das
células e só existe se nenhuma for `NAO_CALCULAVEL`. `faltam` lista o custo que faltou em pelo menos uma célula.

## 3. Estado por canal (leitura de 13/10/2026)

| Canal | Classificação possível hoje | Por quê |
|---|---|---|
| Mercado Livre | `PARCIAL` em jul/2026 | Receita, comissão, Ads, frete e CMV reconciliado. Jun: comissão em 5 de 30 dias. Ago: imaturo. Set: CMV 100% provisório |
| TikTok Shop | `PARCIAL` em jun–jul/2026 | Taxa e frete pelo repasse, CMV reconciliado, afiliado como decomposição. Barbours jun: CMV não reconcilia (razão 0,62). Sem Ads |
| Shopee | `NAO_CALCULAVEL` | Taxas e frete sem contraprova (C4); CMV não reconcilia em 4 de 5 marcas (F1) |
| Total e marcas | `NAO_CALCULAVEL` com a Shopee no filtro | A pior célula manda. Filtrar `channels=tiktok,ml` devolve o agregado calculável |

## 4. Limitações

1. ~~Produção não alcança o Data Mart~~ — **resolvido por DRE-DATA-BRIDGE-1** (`docs/dre_data_bridge_1.md`): a rota lê só
   o Neon. Até a migration 025 ser aplicada e o primeiro lote publicado, CMV e taxa/frete TikTok saem `snapshot_ausente`.
   Destrava com um publisher que grave o insumo mensal no Neon — exige migration e autorização.
2. ~~Latência de 8–30 s~~ — **resolvido por DRE-DATA-BRIDGE-1**: a varredura do repasse saiu da requisição e foi para o
   publisher; a rota mediu ~1,1 s contra o Neon real.
3. A fonte do repasse e a fonte fiscal mudam depois da carga; o mesmo mês pode variar entre duas leituras.
4. A receita do TikTok inclui frete pago pelo comprador; ML e Shopee não.
5. Cortes de coorte: o repasse TikTok usa `order_create_time` sem fuso; o GMV usa o dia do mart.
6. Sem comparação entre períodos (`compare` é ignorado).

## 5. Validação executada

- 63 testes novos em `apps/api/tests/test_dre_mvp.py`: sinais, ausência × zero, afiliado não duplicado, comissão ML
  única, classificação, Data Mart inacessível sem fallback, agregação, filtros de período/marca/canal, schema.
- Mutação: tornar o afiliado somável, trocar o sinal por `abs()` e cair para o `total_fees` do mart quebram 2, 5 e 1
  testes, respectivamente.
- Reconciliação read-only contra Neon e Data Mart, jul/2026: receita, comissão, Ads, frete e CMV do ML; receita,
  frete, CMV e afiliados do TikTok; receita, taxas e CMV da Shopee — iguais aos valores medidos no contrato do PR #65.
  Taxa TikTok pelo repasse a −0,002% do valor medido em 09/10 (a fonte segue atualizando).
- Suíte da API: mesmas 43 falhas da `main` (ausência de `.env` na worktree), nenhuma nova. Com banco: 1 falha,
  preexistente na `main` pura (`test_shopee_sku_consolidation::test_cardinalidade_do_join_por_marca_e_mes`).

---

## 6. Especificação do componente visual (para a frente de UX)

Sem tela nesta entrega, para não conflitar com o Redesign UX. Esta é a especificação para incorporar.

**Decisão que a tela apoia:** quanto cada canal e marca deixa depois dos custos conhecidos, e o que ainda falta para
confiar no número.

**Estrutura sugerida** — tabela `canal × marca` com linhas da DRE em colunas, ou uma DRE vertical por recorte:

| Linha | Exibição |
|---|---|
| Receita | valor; selo "inclui frete do comprador" no TikTok |
| Devoluções | "Indisponível" (nunca R$ 0) |
| Taxas | valor + % |
| ↳ dos quais afiliados | recuada, itálico, **sem** entrar em nenhum total; tooltip "já incluído em Taxas" |
| Ads, Frete, CMV | valor + % ou chip de status |
| Impostos | "Indisponível — fonte não ratificada" |
| **Margem de contribuição conhecida** | valor + %, ou "Não calculável" + motivo; nunca "margem líquida" |

**Chips de status** (`componentes.*.status`):

| status | rótulo | tom |
|---|---|---|
| `disponivel` | — (mostra o valor) | neutro |
| `parcial` | Parcial | atenção |
| `nao_confirmado` | Não confirmado | atenção |
| `provisorio` | Provisório | atenção |
| `nao_reconciliado` | Não reconcilia | erro |
| `sinal_inesperado` | Sinal inesperado | erro |
| `indisponivel` | Indisponível | apagado |
| `snapshot_ausente` | Insumo não publicado | apagado |
| `snapshot_vencido` | Insumo desatualizado | atenção |
| `nao_aplicavel` | N/A | apagado |

**Selo de classificação:** `PARCIAL` → "Parcial · 4 de 6 custos"; `NAO_CALCULAVEL` → "Não calculável", com
`motivos_classificacao` em tooltip ou drill-down; `COMPLETA` → "Completa".

**Regras de tela:**

1. `valor = null` nunca vira "R$ 0" nem "—" ambíguo: mostrar o chip do status.
2. `fontes.insumos_dre.estado` diferente de `ok` → faixa no topo com `fontes.insumos_dre.motivo` e
   `fontes.insumos_dre.publicado_em` (ex.: "Insumos financeiros publicados há mais de 48 h").
3. Mostrar `formula` e `faltam` junto da margem (tooltip ou linha de rodapé).
4. Frescor: `data_maxima.receita_ultimo_dia` e `cmv_ultima_emissao` visíveis.
5. Estados: carregando (~1 s medido contra o Neon), vazio (nenhuma célula para o filtro), erro (503/422 com
   a mensagem do servidor).
6. Seletor de período só por mês inteiro.

**Tipos (TypeScript) mínimos:**

```ts
type Classificacao = "COMPLETA" | "PARCIAL" | "NAO_CALCULAVEL";
type StatusComponente =
  | "disponivel" | "parcial" | "indisponivel" | "snapshot_ausente" | "snapshot_vencido" | "nao_reconciliado"
  | "nao_confirmado" | "provisorio" | "sinal_inesperado" | "nao_aplicavel";

interface Componente {
  valor: number | null; pct_receita: number | null; status: StatusComponente;
  motivo: string | null; fonte: string | null; somavel: boolean;
  partes: Record<string, number | null> | null;
  data_as_of: string | null; publicado_em: string | null;  // so' insumos publicados
}

interface CelulaDRE {
  canal?: string; canal_label?: string; marca?: string; marca_label?: string; mes?: string;
  classificacao: Classificacao; motivos_classificacao: string[]; avisos_qualidade: string[];
  componentes: Record<"receita" | "devolucoes" | "taxas" | "afiliados" | "ads" | "frete" | "cmv" | "impostos", Componente>;
  completude: { usados: number; aplicaveis: number; texto: string };
  margem_contribuicao_conhecida: {
    valor: number | null; pct_receita: number | null; rotulo: string;
    formula: string | null; componentes_usados: string[]; faltam: string[];
  };
  data_maxima: Record<string, string | null>;
}
```

## 7. Próximo passo (requer autorização)

Implementado em DRE-DATA-BRIDGE-1 (`docs/dre_data_bridge_1.md`): migration 025, publisher e serving só no Neon.
Aplicação e primeira publicação aguardam autorização.
