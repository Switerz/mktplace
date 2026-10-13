# DRE — Roteiro das reuniões com as fontes financeiras

Data: 2026-10-13
Base: `docs/dre_contrato_0.md` (DRE-CONTRATO-0). Numeração F/C/D igual à das §10–§12 de lá.
Objetivo: sair das duas conversas com as respostas que destravam a primeira DRE por canal e marca.

## Antes de entrar

- **O que já está provado:** CMV do ML e do TikTok reconcilia com o GMV da Torre em jul–ago; o afiliado do
  TikTok já está dentro das taxas do repasse; impostos sobre venda pesam 11,6%–14,6% da receita fiscal.
- **O que bloqueia:** CMV da Shopee, impostos não ratificados, devoluções em valor, taxa TikTok congelada no
  mart em ago/set, CMV de setembro 100% provisório, Ads TikTok ausente.
- **O que não será apresentado:** nenhum percentual de margem. A linha final se chama "margem conhecida" e
  não é margem real enquanto faltarem impostos e devoluções.

## Regra para registrar respostas

1. Uma resposta só fecha a pergunta quando vier com a **evidência** pedida na tabela.
2. Resposta verbal sem evidência fica registrada como **pendente**, com dono e data. Não completar por
   inferência.
3. Cada resposta vira uma linha na ata: pergunta, resposta, evidência, dono, data, status
   (`aceita` / `pendente` / `rejeitada`).

---

## 1. Reunião com o responsável por Fiscal/CMV (30 min)

Prioridade: **F1, F2 e F8**. As demais podem seguir por escrito.

| # | Pergunta | Evidência para aceitar | O que destrava |
|---|---|---|---|
| F1 | Onde estão as NFs de venda da Shopee (canal, emissor, plataforma)? | Regra ou lista de onde estão; somadas, a receita fiscal Shopee fica entre 0,95 e 1,05 do GMV da Torre em jul e ago, **por marca** | CMV Shopee → margem conhecida da Shopee (bloqueio B2) |
| F2 | Quando o CMV de um mês fecha? O provisório é revisado? Qual versão vale? | Data de fechamento mensal e campo que indica "definitivo" | Regra de mês maduro para o CMV; libera setembro |
| F8 | `raw.webgex_impostos_gobeaute` (circuito VX, débito) é a fonte oficial de imposto sobre venda? Inclui DIFAL/ST? Por que PIS/COFINS variam tanto por plataforma? | Confirmação do filtro correto e regime por emissor; total de um mês batendo com apuração fiscal | Linha de impostos → primeira "margem após impostos" (bloqueio B1) |
| F3 | Qual é a hierarquia oficial das fontes de custo? | Ordem de precedência escrita; destino da receita sem fonte de custo | Confiança no CMV por SKU (base para margem por produto) |
| F4 | Como o custo de kit é formado na NF? | Regra de composição e um kit conferido manualmente | CMV de kits sem erro de composição |
| F5 | Qual chave liga uma devolução fiscal à venda e à plataforma? A devolução estorna CMV? | Chave e uma amostra agregada casando devoluções com plataforma | Devoluções fiscais por canal (bloqueio B3, lado fiscal) |
| F6 | A remessa ao Full (ML/Shopee) fica fora da receita e do custo? | Confirmação da regra e do campo que separa remessa de venda | Evita CMV contado duas vezes no Full |
| F7 | A faixa 0,95–1,05 é tolerância aceitável? CMV entra em valor absoluto ou como % do GMV? | Tolerância e método aprovados por escrito | Regra de "reconciliado" do publisher |

---

## 2. Reunião com o responsável por Conciliação/Repasse (30 min)

Prioridade: **C2, C4 e C8**. As demais podem seguir por escrito.

| # | Pergunta | Evidência para aceitar | O que destrava |
|---|---|---|---|
| C2 | No TikTok, a taxa oficial é o `fee_and_tax` do repasse por mês do pedido? Com quantos dias o mês fica final? | Um mês já conciliado batendo com o `fee_and_tax` por coorte; prazo de maturação | Fonte e maturidade da taxa TikTok (bloqueio B4) |
| C4 | Shopee: qual é a fonte do repasse real? Comissão + taxa de serviço do export é a taxa conciliada? "Valor estimado do frete" é custo do vendedor? | Repasse de um mês conciliado comparado às taxas e ao frete do mart | Taxas e frete Shopee com contraprova (bloqueio B5) |
| C8 | Ads do ML e da Shopee são descontados do repasse ou pagos por fora? | Extrato ou regra mostrando onde o Ads é cobrado | Evita dupla contagem de Ads, análoga à do afiliado |
| C1 | Quais canais, meses e marcas já estão conciliados, e em que grão? | Lista canal × mês × marca com status | Define o que pode sair como "reconciliado" |
| C3 | Comissão de agência e GMV Max Ads do TikTok saem fora do repasse? Na Shopee, o afiliado está dentro da comissão? | Regra por canal, com o lançamento correspondente | Custos adicionais legítimos fora do repasse |
| C5 | No ML, `sale_fee × quantidade` em pedidos pagos bate com o repasse? Como entram estornos? | Mês conciliado comparado à comissão do mart | Confirma a taxa ML e o tratamento de estorno |
| C6 | A conciliação tem devolução/reembolso **em valor** por canal e marca? | Fonte, grão e um mês de exemplo | Devoluções em valor (bloqueio B3, lado comercial) |
| C7 | Qual tolerância e materialidade a conciliação usa para aceitar uma divergência? | Número escrito | Limiar de aborto do publisher |

---

## 3. Decisões do dono da Torre

Tomar depois das duas reuniões, salvo D7, que pode ser fechada antes.

| # | Decisão | Recomendação do contrato | Depende de |
|---|---|---|---|
| D1 | Nome da linha final enquanto faltar componente | "Margem conhecida", com "faltam: …" visível | — |
| D2 | Publicar margem de um canal sem paridade entre os três | Sim: ML e TikTok; Shopee como "indisponível" com motivo | F1 |
| D3 | TikTok inclui frete pago pelo comprador na receita; ML e Shopee não | Manter e declarar na tela | — |
| D4 | Competência: pedido para receita e custos; emissão de NF para CMV | Aceitar enquanto a razão ficar em 0,95–1,05 | F7, C7 |
| D5 | Mês recente: prévia sem margem ou esconder | Prévia com receita, taxas e Ads, sem margem, até o mês maduro | F2, C2 |
| D6 | Impostos: linha estimada agora ou só após ratificação | Só após ratificação | F8 |
| D7 | Escopo de marcas e contas | 5 marcas; Denavita e Gocase fora; ML sem Apice | — |
| D8 | Dono do indicador de margem | Gestor, com os responsáveis por Fiscal/CMV e por Conciliação/Repasse como fontes | — |

---

## 4. Caminho crítico da primeira DRE

| Resposta aceita | Efeito na DRE |
|---|---|
| C2 | Taxa TikTok sai da fonte certa → TikTok com margem conhecida em meses maduros |
| F2 + C2 | Regra de mês maduro → define quais meses o publisher fecha |
| F7 + C7 | Regra de reconciliado → limiar que aborta a publicação |
| C8 | Ads ML/Shopee sem dupla contagem → linha de Ads confiável |
| F1 | CMV Shopee → Shopee deixa de ser "indisponível" |
| F8 | Impostos ratificados → primeira célula "completa" fica possível |
| F5 + C6 | Devoluções em valor → receita líquida deixa de ser só a bruta |

**Mínimo para começar o publisher do MVP (ML + TikTok):** C2, F2, F7/C7 e C8 aceitas, mais D1, D2 e D5
decididas. F1, F8 e F5/C6 ampliam o escopo depois, sem bloquear o primeiro corte.
