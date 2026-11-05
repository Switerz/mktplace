# Kokeshi nas abas CX e Performance: rotina do bot do Seller Centre

A Kokeshi não tem app na Shopee Open Platform. As outras quatro lojas (Ápice, Barbour's, Lescent e Rituária) chegam pela API, via Airflow. A Kokeshi chega pelo bot que já carrega o export de pedidos da Expedição ([expedicao_kokeshi_export_runbook.md](expedicao_kokeshi_export_runbook.md)).

Com este runbook, a Kokeshi passa a aparecer em:

- **Performance → Shopee**: nota da loja, métricas da Saúde da Conta com a meta da Shopee, pontos de penalidade e punições.
- **CX → Atendimento**: % respondido, nota, conversas não lidas, conversas no período, aguardando resposta e contact rate.
- **CX → Pareto dos problemas**: cancelamentos com motivo, devoluções com motivo e o valor reembolsado.

## O que o bot já faz e não muda

**Pedidos e cancelamentos** saem do export de pedidos que o bot já carrega a cada 2 h (`shopee_export_cli`), com a coluna "Cancelar Motivo". A aba CX passa a ler esse export: **não há nada novo a coletar**.

Uma condição: o export **geral** (todos os status, últimos 30 dias) precisa rodar pelo menos uma vez por dia. O export da aba "A Enviar" não traz cancelados.

## O que o bot passa a coletar

Tudo vai num **arquivo JSON**. As seções são independentes: o bot pode mandar só a que leu naquele momento.

| Seção | Onde, no Seller Centre | Frequência |
|---|---|---|
| `saude_conta` | **Saúde da Conta** (Minha Conta → Saúde da Conta, `seller.shopee.com.br/portal/accounthealth`) | 1x por dia, depois das 08:00 |
| `chat` (não lidas) | **Chat** do vendedor: contador de conversas não lidas | a cada 2 h, junto com a Expedição |
| `chat` (painel) | **Dados de Negócio → Chat** (Informações Gerenciais → Chat), período "Últimos 30 dias" e "Últimos 7 dias" | 1x por dia |
| `devoluções` | **Pedidos → Devolução/Reembolso**, solicitações dos últimos 30 dias (todas as abas de status) | 1x por dia |

### 1. `saude_conta`: tela Saúde da Conta

| Campo do JSON | O que ler na tela |
|---|---|
| `nivel` | Classificação geral da loja: `Excelente`, `Bom`, `Precisa melhorar` ou `Ruim` (ou 4, 3, 2, 1) |
| `pontos_penalidade` | Pontos de penalidade no trimestre |
| `punicoes_em_andamento` | Quantas punições estão ativas (0 se não houver) |
| `metricas` | Uma entrada por métrica da tabela abaixo: `{"valor": ..., "anterior": ...}` |

`valor` é o número do período atual. `anterior` é o do período anterior, quando a tela mostra. Percentuais vão sem o `%` (`6.2` ou `"6,2%"`; os dois servem). A meta já vem da Shopee (a mesma que a API devolve para as outras lojas). Só mande `meta` se o Seller Centre mostrar outro valor.

| Código no JSON | Nome na tela (pode variar um pouco) | Unidade | Meta da Shopee |
|---|---|---|---|
| `taxa_envio_atrasado` | Taxa de envio atrasado | % | < 5 |
| `taxa_nao_cumprimento` | Taxa de pedidos não concluídos | % | < 8 |
| `taxa_cancelamento` | Taxa de cancelamento | % | < 8 |
| `taxa_devolucao_reembolso` | Taxa de devolução/reembolso | % | < 8 |
| `envios_aos_sabados` | Envios aos sábados | % | ≥ 20 |
| `tempo_medio_preparacao_dias` | Tempo médio de preparação | dias | < 1,3 |
| `taxa_entrega_no_prazo` | Taxa de entrega no prazo | % | ≥ 75 |
| `anuncios_pre_venda_pct` | % de anúncios em pré-venda | % | ≤ 20 |
| `violacoes_graves` | Violações graves de anúncio | número | ≤ 0 |
| `outras_violacoes` | Outras violações de anúncio | número | ≤ 0 |
| `taxa_resposta_chat` | Taxa de resposta do chat | % | ≥ 60 |
| `nota_loja` | Avaliação da loja | nota 0–5 | ≥ 4,5 |

Métrica que não aparece na tela fica fora do JSON. Ela some da tabela da Torre, e nunca vira zero.

### 2. `chat`

| Campo do JSON | O que ler |
|---|---|
| `nao_lidas` | Contador de conversas não lidas no chat do vendedor |
| `aguardando_resposta` | Conversas em que a última mensagem é do comprador (filtro "Não respondidas", se houver) |
| `conversas_7d` | Painel de Chat, últimos 7 dias: número de conversas/chats recebidos |
| `conversas_30d` | O mesmo, nos últimos 30 dias |
| `periodo_dias` | Período usado nos campos abaixo (normalmente 30) |
| `visitantes` | Visitantes/compradores que abriram o chat |
| `taxa_resposta_pct` | Taxa de resposta do chat (%) |
| `tempo_resposta_medio_min` | Tempo médio de resposta, **em minutos** |
| `csat_pct` | Satisfação (CSAT) do chat (%) |
| `conversao_pct` | Taxa de conversão do chat (%) |

Todos os campos são opcionais. Campo ausente aparece como "—" na tela.

### 3. `devolucoes`: uma linha por solicitação

| Campo do JSON | O que ler |
|---|---|
| `id` | Número da solicitação de devolução/reembolso (obrigatório) |
| `pedido` | ID do pedido |
| `criada_em` | Data da solicitação (`DD/MM/AAAA HH:MM` ou `AAAA-MM-DD HH:MM`, horário de Brasília) |
| `atualizada_em` | Última atualização (opcional) |
| `status` | Status **como aparece na tela** (obrigatório). Exemplos: "Reembolso concluído", "Cancelado", "Em disputa", "Solicitado" |
| `motivo` | Motivo **como aparece na tela**. Exemplos: "Não recebi o pedido", "Produto danificado" |
| `valor_reembolso` | Valor reembolsado (`59.90` ou `"R$ 59,90"`) |

Mande **todas** as solicitações criadas nos últimos 30 dias, inclusive as canceladas. A lista inteira é a foto do momento. O status de uma devolução muda, e a Torre guarda sempre o último status recebido.

**Não mande** nome, endereço, telefone, CPF, fotos, vídeos nem o texto livre do comprador.

Status e motivo são traduzidos para os códigos da API. Se um status ou motivo novo não for reconhecido, a carga **não para**: o relatório lista o texto, que entra na tela como está. Avise o time da Torre para incluir a tradução.

## Exemplo de arquivo

```json
{
  "conta": "kokeshi",
  "capturado_em": "05/11/2026 08:30",
  "saude_conta": {
    "nivel": "Bom",
    "pontos_penalidade": 0,
    "punicoes_em_andamento": 0,
    "metricas": {
      "taxa_envio_atrasado": {"valor": 1.2, "anterior": 0.9},
      "taxa_nao_cumprimento": {"valor": 0.8},
      "taxa_cancelamento": {"valor": 0.5},
      "taxa_devolucao_reembolso": {"valor": 0.3},
      "tempo_medio_preparacao_dias": {"valor": 0.7},
      "taxa_resposta_chat": {"valor": 92},
      "nota_loja": {"valor": 4.9},
      "violacoes_graves": {"valor": 0},
      "outras_violacoes": {"valor": 0}
    }
  },
  "chat": {
    "nao_lidas": 6,
    "aguardando_resposta": 4,
    "conversas_7d": 310,
    "conversas_30d": 1250,
    "periodo_dias": 30,
    "visitantes": 4800,
    "taxa_resposta_pct": 92,
    "tempo_resposta_medio_min": 35,
    "csat_pct": 90.5,
    "conversao_pct": 12.1
  },
  "devolucoes": [
    {"id": "2511010ABCDEF", "pedido": "251031XYZ", "criada_em": "01/11/2026 10:22",
     "status": "Reembolso concluído", "motivo": "Não recebi o pedido", "valor_reembolso": "R$ 59,90"}
  ]
}
```

`capturado_em` é a hora em que o bot leu as telas, no horário de Brasília. É obrigatório, e o arquivo é recusado se essa hora tiver mais de 3 dias.

## Como gravar

Mesma máquina, mesmo repositório e mesmo `DATABASE_URL` do export de pedidos. Antes da primeira vez, atualize o repositório (`git pull`).

1. Prévia, que mostra os números e não grava nada:

   ```bash
   python -m pipelines.cx.kokeshi_cli --arquivo "/caminho/kokeshi_cx.json"
   ```

2. Confira o relatório: nota, métricas fora da meta, não lidas, conversas, devoluções e textos sem tradução.
3. Grave com o mesmo comando mais `--apply`.

| Saída | Motivo | O que fazer |
|---|---|---|
| 0 | Ok | — |
| 3 `JSON RECUSADO` | Campo fora do formato. A lista mostra **todos** os problemas de uma vez | Corrigir o JSON e rodar de novo |
| 6 | Migration 036 ausente, ou conta `kokeshi` não cadastrada/inativa | Avisar o time da Torre |
| 2 | Outra carga da Kokeshi em andamento | Esperar e rodar de novo |
| 1 | Falha de banco. Nada foi gravado | Avisar o time; **não** repetir em laço |

## Na tela

- A Kokeshi aparece na Performance → Shopee com o aviso "Capturada pelo bot no Seller Centre".
- Enquanto o bot não mandar uma seção, a Torre mostra "aguardando a primeira captura do bot", e nunca zero.
- Se a Saúde da Conta ficar mais de 2 dias sem atualização, a conta ganha o aviso "a captura diária está atrasada".

## Para o time da Torre (uma vez)

1. `cd apps/api && alembic upgrade 036` no Neon. Isso cria as 4 tabelas e não altera nenhuma tabela existente.
2. A migration dá acesso ao perfil `tower_dst_rw` se ele existir. Se o bot usar outro perfil:

   ```sql
   GRANT SELECT, INSERT, UPDATE, DELETE ON marts.shopee_saude_conta_export,
         marts.shopee_saude_metrica_export, marts.shopee_chat_export,
         marts.shopee_returns_export TO <perfil_do_bot>;
   ```
3. Tabelas: `marts.shopee_saude_conta_export` e `marts.shopee_saude_metrica_export` (Saúde da Conta), `marts.shopee_chat_export` (chat) e `marts.shopee_returns_export` (devoluções). Têm a mesma forma das tabelas `raw.*` do Data Mart, e a API lê as duas fontes com a mesma regra.
