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

