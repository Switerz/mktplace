# Decisões de arquitetura

## Pendente — Reavaliar o Neon como camada de serving na Fase 2

**Registrada em:** 21/07/2026  
**Status:** decisão futura importante; não executar durante a estabilização atual.

O Neon permanece como banco consumido pela API no curto prazo. A API, as dimensões e os contratos canônicos ainda dependem de `marts.*` no Neon.

Por outro lado, a cópia Data Mart → Neon introduz atraso, lacunas quando o sync para, divergências após revisões históricas da fonte e custo operacional de reconciliação. Quando ML, TikTok e Shopee estiverem automatizados e padronizados no Data Mart, será necessário decidir formalmente entre:

1. manter o Neon e tornar a sincronização automática e observável; ou
2. criar uma camada `serving` estável no Data Mart e fazer a API consultá-la diretamente, eliminando a cópia analítica no Neon.

### Critérios obrigatórios para a decisão

- contrato canônico dos três canais no Data Mart;
- conectividade segura e read-only entre Render e RDS;
- desempenho, índices e concorrência com as cargas do warehouse;
- comportamento da read replica sob `conflict with recovery`;
- disponibilidade, custo, rollback e plano de migração da API;
- reconciliação em paralelo entre a resposta atual do Neon e a nova camada de serving.

Até essa avaliação, o Data Mart continua como fonte analítica e o Neon como camada de serving da Torre. Esta decisão é prioritária para a Fase 2, mas não bloqueia a correção atual de consistência e completude dos dados.
