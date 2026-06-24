from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Banco local (destino das transformaÃ§Ãµes)
    database_url: str = "postgresql://user:password@localhost:5432/mktplace_control"

    # Data Mart (fonte â€” read-only)
    datamart_database_url: str = ""
    datamart_host: str = ""
    datamart_port: int = 5432
    datamart_db: str = ""
    datamart_user: str = ""
    datamart_password: str = ""

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

    # Shopee â€” caminho local para os arquivos xlsx/csv exportados manualmente
    shopee_data_path: str = ""

    # Controle de sync
    backfill_days_default: int = 90
    log_level: str = "INFO"


settings = PipelineSettings()

