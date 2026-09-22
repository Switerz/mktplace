from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://user:password@localhost:5432/mktplace_control"
    datamart_database_url: str = ""
    datamart_host: str = ""
    datamart_port: int = 5432
    datamart_db: str = ""
    datamart_user: str = ""
    datamart_password: str = ""
    # Gate G4: timeout de CONEXAO aplicado exclusivamente ao engine do Data
    # Mart. Causa raiz diagnosticada (ver docs/DRILLDOWN_ARCHITECTURE.md §8.9):
    # o Render nao tem conectividade com o RDS, entao `connect()` fica pendurado
    # 45-120s sem receber byte algum e as 4 rotas servidas pelo gold_service
    # (/brand-detail, /tempo-real, /inteligencia, /operacoes) so' falham depois
    # dessa espera. Falhar rapido nao restaura o dado — apenas encurta a espera
    # para algo que o frontend ja' representa como indisponibilidade.
    # Default conservador para nao atrapalhar o uso local via VPN em rede lenta;
    # faixa validada pelo proprio Pydantic (sem dependencia nova).
    datamart_connect_timeout_seconds: int = Field(default=10, ge=1, le=30)
    app_env: str = "development"
    log_level: str = "INFO"
    api_port: int = 8080
    cors_origins: str = "http://localhost:3000"

    # Metabase â€” mantido apenas para referencia/debug, nao usado pelo router principal
    metabase_url: str = "https://metabase.gobeaute.com.br"
    metabase_api_key: str = ""
    metabase_database_id: int = 43

    # -----------------------------------------------------------------------
    # Gate SH-API-2D — limiar heuristico do indice operacional de maturacao
    # dos Produtos Shopee.
    #
    # O INDICE e:
    #     SUM(gmv) de marts.fact_shopee_product_monthly      (GMV concluido)
    #   / SUM(gmv) de marts.fact_marketplace_daily_performance (Shopee)
    #
    # NAO e percentual de conclusao, share nem completude. Numerador e
    # denominador vem de POPULACOES DIFERENTES — subtotal de item no mart de
    # Produtos contra GMV liquido do shop stats na diaria — e por isso o indice
    # passa de 1,00 em meses fechados, o que num percentual seria absurdo.
    # Serve para separar REGIME maduro de regime imaturo, nada mais.
    #
    # O limiar e HEURISTICO e configuravel por variavel de ambiente
    # (SHOPEE_MATURATION_THRESHOLD), sem deploy de codigo. Nao e meta, nem
    # constante de negocio, nem SLA. Foi escolhido por cair dentro da faixa
    # VAZIA entre os dois regimes medidos em 30 pares marca x competencia no
    # Neon (2026-01..2026-08, ver docs/runbook_sync_produtos.md):
    #   - meses fechados e maduros : indice entre 1,0047 e 1,1234 (30 pares)
    #   - competencia imatura      : indice entre 0,6643 e 0,7700
    #   - competencia sem conclusao: indice 0,0000
    # Qualquer limiar dentro de (0,7700 ; 1,0047) separa os dois regimes; 0,99
    # e o default provisorio por ficar nessa faixa e coincidir com o valor
    # medido de forma independente na silver do Data Mart (0,9987). Deve ser
    # revisto quando houver mais meses fechados.
    shopee_maturation_threshold: float = Field(default=0.99, gt=0.0, le=2.0)

    # ------------------------------------------------------------------
    # Gate PMA-2C1A — feature flags do monitoramento multicanal
    # ------------------------------------------------------------------
    # As TRES nascem DESLIGADAS e permanecem assim nesta rodada. Elas nao sao
    # "configuracao": sao a fronteira entre o que ja' esta publicado e o que
    # ainda nao tem migration.
    #
    # `marts.fact_channel_offer_observation` NAO EXISTE no Neon (verificado por
    # leitura: `to_regclass` devolve NULL, e o head Alembic e' 015 em todas as
    # refs). Com a flag desligada o servico devolve estado `unavailable`
    # ESTRUTURADO sem emitir uma unica consulta — nao ha SELECT contra tabela
    # inexistente, nao ha 500, e nao ha fallback para o ML. Um fallback
    # silencioso seria pior que o erro: responderia sobre outro canal a pergunta
    # que o cliente fez sobre este.
    pma_shopee_enabled: bool = Field(default=False)
    pma_tiktok_enabled: bool = Field(default=False)

    # Troca a metrica do ML de `v1_all_active` para `v2_product_type_aware`
    # (politica P2). Efeito medido e congelado em teste: comparaveis 139 -> 135,
    # na/acima 121 -> 117, abaixo 18 -> 18, com quatro anuncios da Rituaria
    # migrando para `kit_composition_missing`. E' evolucao DELIBERADA da
    # metrica publicada, nao correcao de defeito, portanto nao pode ligar
    # sozinha: ativacao e' decisao de negocio, fora desta rodada.
    pma_ml_metric_v2_enabled: bool = Field(default=False)

    # ------------------------------------------------------------------
    # Gate EXP-2A — API read-only da Expedicao
    # ------------------------------------------------------------------
    # Nasce DESLIGADA. A camada de dados esta publicada, mas expor a superficie
    # e decisao separada: com a flag off o servico devolve `unavailable`
    # ESTRUTURADO sem emitir uma unica consulta.
    expedicao_api_enabled: bool = Field(default=False)
    #: Exposicao do Mercado Livre na Expedicao (EXP-3C1). Separada da
    #: flag geral para que ligar a Expedicao nao exponha o ML sem
    #: querer, e para que adiar o ML nao exija derrubar a Shopee.
    expedicao_ml_api_enabled: bool = Field(default=False)

    # Chave do identificador OPACO de pedido. Vazia por default, e vazia
    # significa NAO PUBLICAR: `order_ref` sai nulo.
    #
    # Esta API nao tem autenticacao (nenhum router declara dependencia de auth).
    # `order_sn` permite consultar o pedido no painel do marketplace, entao nao
    # vai cru numa rota publica. Hash SEM chave tambem nao serve: o espaco de
    # `order_sn` e curto e enumeravel, e uma tabela arco-iris o reverte. Com
    # segredo configurado o valor vira HMAC-SHA256 truncado — estavel entre
    # execucoes e inutil para quem nao tem a chave.
    expedicao_order_ref_secret: str = ""

    # ------------------------------------------------------------------
    # Gate FULL-SH-1C — superficie de desempenho FBS da Shopee
    # ------------------------------------------------------------------
    # NASCE DESLIGADA e permanece assim nesta rodada. A fato
    # `marts.fact_shopee_fbs_daily` JA' existe e esta publicada (migration 019,
    # 1.648 linhas), mas a superficie ainda nao foi revisada por quem decide o
    # que a Torre afirma -- e ela afirma algo delicado: um total de Shopee que
    # NAO inclui Kokeshi, a maior marca por volume.
    #
    # Com a flag desligada o endpoint devolve estado `unavailable`
    # ESTRUTURADO, em 200, SEM emitir uma unica consulta. Nao ha 404 (a rota
    # existe), nao ha 500 (nada quebrou) e nao ha fallback para outro canal.
    #
    # `bool` do Pydantic aceita "1"/"true"/"yes"; a ausencia da variavel cai no
    # default False. Ligar e' decisao de negocio, fora deste gate.
    shopee_fbs_enabled: bool = Field(default=False)

    @property
    def datamart_url(self) -> str:
        if self.datamart_database_url:
            return self.datamart_database_url
        if not self.datamart_host or not self.datamart_db:
            return ""
        return (
            f"postgresql://{self.datamart_user}:{self.datamart_password}"
            f"@{self.datamart_host}:{self.datamart_port}/{self.datamart_db}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()

