# QA — Filtros Globais (checklist versionado)

Atualizado: 2026-07-08

Status: **checklist criado, QA visual ainda NÃO executado** (sem ferramenta de browser disponível nesta sessão). Não declarar QA como concluído até que cada linha tenha evidência registrada.

Ver contrato funcional em [`filtros_globais_contrato.md`](filtros_globais_contrato.md) e achados de revisão de código no plano de commits (não versionado, ver handoff da sessão).

## Pré-requisitos

### API local (backend)

```bash
cd apps/api
uv sync --frozen --extra dev
uv run uvicorn app.main:app --reload --port 8000
```

- Não requer credencial do Data Mart para os endpoints `/overview`, `/brands`, `/canais`, `/financeiro`, `/pedidos`, `/trend` (usam `DATABASE_URL` → Neon).
- Endpoints que dependem de `DATAMART_DATABASE_URL` (RDS/gold — ex.: parte de `/quality`, Tempo Real, Inteligência, Operações, Brand Detail) retornarão erro/",mock" se a credencial não estiver configurada localmente. Isso é esperado e não bloqueia o QA dos filtros globais nas 6 telas principais.

### Frontend

```bash
cd apps/web
npm install
npm run dev
```

- Local: `http://localhost:3000`
- `NEXT_PUBLIC_API_BASE_URL` deve apontar para a API local (ver `.env.local`, não versionado — não imprimir seu conteúdo).

### Sem credenciais

Todo o QA abaixo deve ser executável com a API local rodando apenas contra Neon (`DATABASE_URL`). Nenhum passo deste checklist deve exigir a credencial do Data Mart.

---

## Matriz de QA

Preencher **Resultado** com `Aprovado` / `Reprovado` / `Bloqueado` e **Evidência** com print, vídeo ou trecho de log (sem PII, sem credenciais, sem valores de `.env`).

### 1. Responsividade

| # | Caso | Resolução | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|---|
| 1.1 | Layout geral do Overview | 1280px | Filtros, KPIs, tabela e gráfico de tendência visíveis sem scroll horizontal | | |
| 1.2 | Layout geral do Overview | 768px | Filtros colapsam/empilham sem sobrepor conteúdo; tabela com scroll interno se necessário | | |
| 1.3 | Layout geral do Overview | 375px | Filtros acessíveis via menu/drawer; nenhum texto cortado ou sobreposto | | |
| 1.4 | Brand Detail | 1280px / 768px / 375px | Mesmo padrão acima, incluindo pills de troca de marca | | |

### 2. Filtro de canal (multicanal)

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 2.1 | Selecionar "Todos" | Dados agregados de TikTok + ML + Shopee | | |
| 2.2 | Selecionar 1 canal isolado (ex.: Shopee) | Dados exclusivos daquele canal; URL reflete `channels=shopee` | | |
| 2.3 | Selecionar 2 canais (ex.: TikTok + ML) | Dados agregados dos 2; Shopee excluído | | |
| 2.4 | Desmarcar o último canal restante | Bloqueado — não deve permitir zero canais selecionados | | |
| 2.5 | Compatibilidade com parâmetro legado `marketplace=` na URL | Continua funcionando como alias | | |

### 3. Filtro de marca (multimarcas)

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 3.1 | Nenhuma marca selecionada | Todas as marcas do(s) canal(is) atual(is) agregadas | | |
| 3.2 | Selecionar 1 marca | Dados filtrados só daquela marca | | |
| 3.3 | Selecionar múltiplas marcas | Dados agregados só das marcas selecionadas | | |
| 3.4 | Trocar canal com marca selecionada, marca inválida no novo canal | Seleção de marca é resetada corretamente (ex.: Apice só existe em TikTok/Shopee) | | |
| 3.5 | Marca inexistente/inativa via URL manual | Erro 422 tratado com mensagem clara, sem crash | | |

### 4. Presets de período

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 4.1 | Preset "7 dias" | `date_from`/`date_to` corretos, dados atualizam | | |
| 4.2 | Preset "30 dias" | Idem | | |
| 4.3 | Preset "mês atual" | Idem | | |
| 4.4 | Preset "mês anterior" | Idem | | |
| 4.5 | Nenhum preset/URL limpa (default) | Overview/Qualidade/Brand Detail: mês calendário anterior completo + comparação ativa (MoM visível). Canais/Financeiro: mês calendário anterior, sem comparação. Pedidos: últimos 30 dias, sem comparação. Ver [`filtros_globais_contrato.md`](filtros_globais_contrato.md#gate-3--default-por-rota-e-regra-url-explícita-vence-correção-de-regressão). Corrigido — validar que a URL materializa `date_from`/`date_to` concretos do mês (não "últimos 30 dias") | | |

### 5. Datas personalizadas

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 5.1 | Selecionar `date_from`/`date_to` customizados válidos | Dados refletem exatamente o intervalo | | |
| 5.2 | Apenas `date_from` preenchido, sem `date_to` | Erro tratado (422) — não deve buscar com um lado só | | |
| 5.3 | `date_from > date_to` | Erro tratado, sem crash | | |

### 6. Datas inválidas / futuras

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 6.1 | `date_to` = amanhã ou além | Rejeitado (422 no backend, bloqueado antes do submit no frontend) | | |
| 6.2 | `date_from`/`date_to` fora do formato `YYYY-MM-DD` | Erro tratado, mensagem clara | | |

### 7. Intervalo acima de 366 dias

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 7.1 | Intervalo de 367+ dias | Rejeitado (422), sem truncamento silencioso | | |
| 7.2 | Intervalo de exatamente 366 dias | Aceito | | |

### 8. Comparação (MoM)

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 8.1 | Ativar "Comparar com período anterior" | Badges de variação (%) aparecem nos KPIs e na tabela de marcas | | |
| 8.2 | Desativar comparação | Badges somem ou voltam ao estado neutro, sem erro | | |
| 8.3 | Comparação com período customizado | `compare_date_from`/`compare_date_to` calculados corretamente (mesmo tamanho de janela, deslocado) | | |
| 8.4 | **Default sem interação do usuário** | Corrigido: Overview/Qualidade abrem com `compare=true` materializado na URL e mostram o MoM calendário (mês vs mês anterior) automaticamente, idêntico ao legado. Desligar o toggle remove `compare` da URL e o MoM desaparece — inclusive após reload | | |

### 9. Navegador — back/forward

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 9.1 | Aplicar filtro, clicar "voltar" no navegador | Estado anterior de filtros é restaurado corretamente | | |
| 9.2 | Aplicar filtro, voltar, avançar | Estado do filtro avançado é restaurado | | |
| 9.3 | Reload (F5) com filtros na URL | Filtros persistem após reload | | |

### 10. Navegação Gerencial ↔ Brand Detail

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 10.1 | Ir de Overview (com filtros ativos) para Brand Detail via tabela de marcas | Filtros de canal/data/comparação chegam preservados na querystring | | |
| 10.2 | Voltar de Brand Detail para Overview ("← Dashboard") | Filtros originais preservados | | |
| 10.3 | Trocar de marca dentro do Brand Detail (pills) | Canal/data/comparação atuais preservados, só a marca muda | | |

### 11. Troca rápida de filtros (race condition)

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 11.1 | Alternar rapidamente entre 2-3 canais várias vezes seguidas | Tela final reflete o último filtro clicado, nunca um estado intermediário obsoleto | | |
| 11.2 | Trocar marca e período quase simultaneamente | Idem — nenhuma resposta atrasada sobrescreve o estado atual | | |

### 12. Estados de loading, vazio, erro e API offline

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 12.1 | Loading inicial de cada tela | Skeleton/spinner visível, sem flash de conteúdo vazio | | |
| 12.2 | Filtro sem nenhum dado no período/marca selecionada | Estado vazio explícito, não tabela quebrada nem "undefined" | | |
| 12.3 | API respondendo erro 5xx | Mensagem de erro + opção de retry, sem crash da tela | | |
| 12.4 | API offline (parar `uvicorn` propositalmente) | Fallback para mock é indicado visualmente (aviso de limitação), nunca apresentado como dado real sem aviso | | |
| 12.5 | API offline + marca selecionada | `mockLimitationNote` avisa que o mock ignora o filtro de marca | | |
| 12.6 | API offline + apenas período customizado alterado (sem marca) | Corrigido: `mockLimitationNote` agora também avisa quando o período difere do default da tela (`detectPreset(dateFrom, dateTo) !== defaultPreset`), mesmo sem marca selecionada | | |

### 13. Gráfico de tendência

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 13.1 | Gráfico de tendência carrega com os mesmos filtros do Overview | Sim, mesmo canal/marca/data | | |
| 13.2 | Trocar filtro | Gráfico atualiza junto com os KPIs, sem delay perceptível diferente | | |
| 13.3 | Hover em um ponto do gráfico | Tooltip com data e valor legível | | |

### 14. Reconciliação visual tendência × KPI

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 14.1 | Somar manualmente os pontos visíveis do gráfico de tendência no período | Soma bate com o KPI agregado do card (mesma métrica, mesmo filtro) | | |
| 14.2 | Comparar em pelo menos 2 combinações de filtro diferentes (ex.: 1 canal vs "todos") | Reconciliação se mantém em ambos os casos | | |

### 15. Textos, overflow e acessibilidade básica

| # | Caso | Resultado esperado | Resultado | Evidência |
|---|---|---|---|---|
| 15.1 | Nome de marca longo na lista de multi-select | Texto não estoura o card nem sobrepõe outros elementos | | |
| 15.2 | Navegação por teclado (Tab) nos controles de filtro | Foco visível, ordem lógica, sem esconder-se atrás de outros elementos | | |
| 15.3 | Contraste de texto dos badges de MoM (positivo/negativo) | Legível em modo claro e escuro (se aplicável) | | |
| 15.4 | Labels dos filtros com leitor de tela (verificação básica de `aria-label`/`label`) | Presentes nos controles principais (data, canal, marca) | | |

---

## Registro de execução

| Data | Executor | Ambiente | Itens cobertos | Observações |
|---|---|---|---|---|
| | | | | |

**Regra**: nenhuma linha deste checklist pode ser marcada "Aprovado" sem evidência anexada (print, vídeo curto ou log). Se o QA visual não puder ser executado (sem ferramenta de browser disponível), este arquivo permanece com as colunas "Resultado"/"Evidência" em branco — isso não deve ser reportado como "QA concluído".
