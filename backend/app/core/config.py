"""Application settings. All values come from environment variables (prefix FINOPS_) or a local .env file.

Secrets are held as SecretStr and are never logged.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProviderName = Literal["offline", "openai", "azure_openai", "watsonx"]
EmbeddingProviderName = Literal["local", "openai", "azure_openai", "watsonx"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FINOPS_", env_file=".env", extra="ignore")

    # --- runtime -----------------------------------------------------------------
    environment: Literal["development", "production"] = "development"
    # Never bind all interfaces. In containers the entrypoint binds the pod/container IP (see deploy/entrypoint.sh).
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"
    index_on_start: bool = False

    # --- security ----------------------------------------------------------------
    api_keys: SecretStr = SecretStr("")  # comma separated; required when environment=production
    cors_origins: list[str] = []
    rate_limit_per_minute: int = 60
    max_diff_bytes: int = 1_000_000
    max_file_bytes: int = 1_000_000
    max_question_chars: int = 2_000

    # --- paths -------------------------------------------------------------------
    data_dir: Path = Path("./data")
    repos_config: Path = Path("config/repositories.yaml")
    cost_reports_dir: Path = Path("../sample-data/cost-reports")
    knowledge_dir: Path = Path("knowledge")
    static_dir: Path = Path("static")

    # --- IBM Cloud Logs cost model -------------------------------------------------
    priority_retention_days: Literal[7, 14, 30, 60, 90] = 7
    # DEBUG/VERBOSE are normally disabled in production; they contribute no cost unless this is true.
    debug_enabled_in_prod: bool = False
    # PR risk thresholds (net GB/day added, or % growth of the repository's current volume)
    pr_high_gb_per_day: float = 5.0
    pr_medium_gb_per_day: float = 1.0
    pr_high_growth_pct: float = 10.0
    pr_medium_growth_pct: float = 3.0

    # --- repository sync / history ------------------------------------------------
    git_token: SecretStr | None = None  # used via http.extraHeader, never embedded in URLs
    history_commits: int = 50
    cache_ttl_s: int = 300

    # --- GitHub / GitLab PR integration ------------------------------------------
    # GitHub personal access token or App token with repo:read scope.
    # Secret — put in .env, never in code.
    github_token: SecretStr | None = None
    # GitHub organisation or user owning the repositories (e.g. "IBM")
    github_org: str = ""
    # GitLab support — set github_token + gitlab_base_url for self-hosted instances
    gitlab_base_url: str = "https://gitlab.com"

    # --- LLM / embeddings ----------------------------------------------------------
    llm_provider: LLMProviderName = "offline"
    embedding_provider: EmbeddingProviderName = "local"
    llm_timeout_s: float = 60.0
    llm_max_tokens: int = 900
    llm_temperature: float = 0.1

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1"
    openai_embedding_model: str = "text-embedding-3-small"

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str | None = None
    azure_openai_embedding_deployment: str | None = None

    watsonx_url: str = "https://us-south.ml.cloud.ibm.com"
    watsonx_api_key: SecretStr | None = None
    watsonx_project_id: str | None = None
    watsonx_model_id: str = "ibm/granite-3-8b-instruct"
    watsonx_embedding_model_id: str = "ibm/granite-embedding-278m-multilingual"
    watsonx_api_version: str = "2024-05-02"
    iam_url: str = "https://iam.cloud.ibm.com/identity/token"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.get_secret_value().split(",") if k.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
