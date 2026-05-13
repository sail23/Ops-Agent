from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI-compatible API (optional; stub works without)
    openai_api_base: str = ""
    openai_api_key: str = ""
    openai_chat_model: str = "deepseek-chat"
    openai_timeout_seconds: float = 300.0

    # Zhipu AI Embedding (for Graph RAG and Vector RAG)
    zhipu_api_key: str = ""
    zhipu_embedding_model: str = "embedding-3"
    zhipu_embedding_dim: int = 256
    zhipu_verify_ssl: bool = True  # Set to False if behind proxy

    # Chroma Vector Database (for Vector RAG)
    chroma_persist_directory: str = "./chroma_data"  # Relative to project root

    # Neo4j Graph Database (for Graph RAG)
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # OpenRCA Dataset (for root cause analysis)
    openrca_dataset_path: str = "dataset"
    openrca_system: str = "Bank"  # Bank / Telecom / Market

    # Prometheus (remote monitoring integration)
    prometheus_base_url: str = "http://127.0.0.1:9090"
    prometheus_bearer_token: str = ""
    prometheus_verify_tls: bool = True
    prometheus_timeout_seconds: int = 10
    prometheus_mock_mode: bool = False

    # Code Agent Configuration (always uses DynamicCodeAgent)
    code_execution_enabled: bool = False  # Enable code execution (security risk!)
    code_execution_timeout: int = 30  # Code execution timeout in seconds

    # Debate Orchestrator Configuration (Stage 1: Ops + SRE × 2 rounds + Report)
    debate_enabled: bool = False  # Enable debate mode instead of linear pipeline
    debate_max_rounds: int = 2  # Maximum debate rounds (Stage 1 default: 2)
    debate_max_turns: int = 10  # Maximum total turns per debate (safety limit)

    # Service
    api_host: str = "0.0.0.0"
    api_port: int = 8000


def get_settings() -> Settings:
    return Settings()
