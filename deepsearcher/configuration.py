from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal

import yaml

from deepsearcher.collection_manifest import bind_embedding_identity

if TYPE_CHECKING:
    from deepsearcher.agent import NaiveRAG
    from deepsearcher.agent.rag_router import RAGRouter
    from deepsearcher.embedding.base import BaseEmbedding
    from deepsearcher.entailment import BaseEntailmentChecker
    from deepsearcher.llm.base import BaseLLM
    from deepsearcher.loader.file_loader.base import BaseLoader
    from deepsearcher.loader.web_crawler.base import BaseCrawler
    from deepsearcher.vector_db.base import BaseVectorDB
    from deepsearcher.web_search.base import BaseWebSearch

current_dir = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_YAML_PATH = os.path.join(current_dir, "config.yaml")
DEFAULT_ANSWER_AGENT = "naive"
ANSWER_AGENT_ORDER = ("deep_search", "chain_of_rag", "naive")

FeatureType = Literal[
    "llm",
    "embedding",
    "file_loader",
    "web_crawler",
    "web_search",
    "vector_db",
]


class RuntimeInitializationError(RuntimeError):
    """Safe startup error that identifies the component that failed."""

    code = "RUNTIME_INITIALIZATION_FAILED"
    safe_message = "DeepSearcher runtime initialization failed."
    retryable = True

    def __init__(self, component: str):
        super().__init__(self.safe_message)
        self.component = component


@dataclass(frozen=True)
class RuntimeComponents:
    """Application-owned runtime resources created from one config snapshot."""

    config: "Configuration"
    module_factory: "ModuleFactory"
    llm: BaseLLM
    embedding_model: BaseEmbedding
    file_loader: BaseLoader
    vector_db: BaseVectorDB
    web_crawler: BaseCrawler
    web_search: BaseWebSearch
    default_searcher: RAGRouter
    naive_rag: NaiveRAG
    entailment_checker: BaseEntailmentChecker | None


class Configuration:
    """
    Configuration class for DeepSearcher.

    This class manages the configuration settings for various components of the DeepSearcher system,
    including LLM providers, embedding models, file loaders, web crawlers, and vector databases.
    It loads configurations from a YAML file and provides methods to get and set provider configurations.
    """

    def __init__(self, config_path: str = DEFAULT_CONFIG_YAML_PATH):
        """
        Initialize the Configuration object.

        Args:
            config_path: Path to the configuration YAML file. Defaults to the config.yaml in the project root.
        """
        # Initialize default configurations
        config_data = self.load_config_from_yaml(config_path)
        self.provide_settings = config_data["provide_settings"]
        self.query_settings = config_data["query_settings"]
        self.load_settings = config_data["load_settings"]

    def load_config_from_yaml(self, config_path: str):
        """
        Load configuration from a YAML file.

        Args:
            config_path: Path to the configuration YAML file.

        Returns:
            The loaded configuration data as a dictionary.
        """
        with open(config_path, "r", encoding="utf-8") as file:
            return yaml.safe_load(file)

    def set_provider_config(self, feature: FeatureType, provider: str, provider_configs: dict):
        """
        Set the provider and its configurations for a given feature.

        Args:
            feature: The feature to configure (e.g., 'llm', 'file_loader', 'web_crawler').
            provider: The provider name (e.g., 'openai', 'deepseek').
            provider_configs: A dictionary with configurations specific to the provider.

        Raises:
            ValueError: If the feature is not supported.
        """
        if feature not in self.provide_settings:
            raise ValueError(f"Unsupported feature: {feature}")

        self.provide_settings[feature]["provider"] = provider
        self.provide_settings[feature]["config"] = provider_configs

    def get_provider_config(self, feature: FeatureType):
        """
        Get the current provider and configuration for a given feature.

        Args:
            feature: The feature to retrieve (e.g., 'llm', 'file_loader', 'web_crawler').

        Returns:
            A dictionary with provider and its configurations.

        Raises:
            ValueError: If the feature is not supported.
        """
        if feature not in self.provide_settings:
            raise ValueError(f"Unsupported feature: {feature}")

        return self.provide_settings[feature]


class ModuleFactory:
    """
    Factory class for creating instances of various modules in the DeepSearcher system.

    This class creates instances of LLMs, embedding models, file loaders, web crawlers,
    and vector databases based on the configuration settings.
    """

    def __init__(self, config: Configuration):
        """
        Initialize the ModuleFactory.

        Args:
            config: The Configuration object containing provider settings.
        """
        self.config = config

    def _create_module_instance(self, feature: FeatureType, module_name: str):
        """
        Create an instance of a module based on the feature and module name.

        Args:
            feature: The feature type (e.g., 'llm', 'embedding').
            module_name: The module name to import from.

        Returns:
            An instance of the specified module.
        """
        # e.g.
        # feature = "file_loader"
        # module_name = "deepsearcher.loader.file_loader"
        class_name = self.config.provide_settings[feature]["provider"]
        module = __import__(module_name, fromlist=[class_name])
        class_ = getattr(module, class_name)
        return class_(**self.config.provide_settings[feature]["config"])

    def create_llm(self) -> BaseLLM:
        """
        Create an instance of a language model.

        Returns:
            An instance of a BaseLLM implementation.
        """
        return self._create_module_instance("llm", "deepsearcher.llm")

    def create_embedding(self) -> BaseEmbedding:
        """
        Create an instance of an embedding model.

        Returns:
            An instance of a BaseEmbedding implementation.
        """
        instance = self._create_module_instance("embedding", "deepsearcher.embedding")
        settings = self.config.provide_settings["embedding"]
        return bind_embedding_identity(
            instance,
            provider=settings["provider"],
            config=settings.get("config") or {},
            identity=settings.get("identity") or {},
        )

    def create_file_loader(self) -> BaseLoader:
        """
        Create an instance of a file loader.

        Returns:
            An instance of a BaseLoader implementation.
        """
        return self._create_module_instance("file_loader", "deepsearcher.loader.file_loader")

    def create_web_crawler(self) -> BaseCrawler:
        """
        Create an instance of a web crawler.

        Returns:
            An instance of a BaseCrawler implementation.
        """
        return self._create_module_instance("web_crawler", "deepsearcher.loader.web_crawler")

    def create_web_search(self) -> BaseWebSearch:
        """Create the optional web-search provider, disabled for legacy configs."""
        provide_settings = getattr(self.config, "provide_settings", {})
        if "web_search" not in provide_settings:
            from deepsearcher.web_search import DisabledWebSearch

            return DisabledWebSearch()
        return self._create_module_instance("web_search", "deepsearcher.web_search")

    def create_vector_db(self) -> BaseVectorDB:
        """
        Create an instance of a vector database.

        Returns:
            An instance of a BaseVectorDB implementation.
        """
        return self._create_module_instance("vector_db", "deepsearcher.vector_db")


config = Configuration()

module_factory: ModuleFactory = None
llm: BaseLLM = None
embedding_model: BaseEmbedding = None
file_loader: BaseLoader = None
vector_db: BaseVectorDB = None
web_crawler: BaseCrawler = None
web_search: BaseWebSearch = None
default_searcher: RAGRouter = None
naive_rag: NaiveRAG = None
entailment_checker: BaseEntailmentChecker = None


def _create_runtime_component(component: str, factory: Callable):
    try:
        return factory()
    except Exception as exc:
        raise RuntimeInitializationError(component) from exc


def _load_agent_classes():
    from deepsearcher.agent import ChainOfRAG, DeepSearch, NaiveRAG
    from deepsearcher.agent.rag_router import RAGRouter

    return DeepSearch, ChainOfRAG, RAGRouter, NaiveRAG


def build_runtime(config: Configuration) -> RuntimeComponents:
    """Build isolated runtime resources without mutating module globals."""
    from deepsearcher.trust import temporal_timezone_from_query_settings

    _create_runtime_component(
        "trust_temporal",
        lambda: temporal_timezone_from_query_settings(config.query_settings),
    )
    factory = ModuleFactory(config)
    llm_instance = _create_runtime_component("llm", factory.create_llm)
    from deepsearcher.entailment import build_entailment_checker

    trust_settings = config.query_settings.get("trust", {})
    entailment_settings = (
        trust_settings.get("entailment", {}) if isinstance(trust_settings, dict) else {}
    )
    entailment_checker_instance = _create_runtime_component(
        "entailment_checker",
        lambda: build_entailment_checker(llm_instance, entailment_settings),
    )
    embedding_instance = _create_runtime_component("embedding", factory.create_embedding)
    file_loader_instance = _create_runtime_component("file_loader", factory.create_file_loader)
    web_crawler_instance = _create_runtime_component("web_crawler", factory.create_web_crawler)
    web_search_instance = _create_runtime_component("web_search", factory.create_web_search)
    vector_db_instance = _create_runtime_component("vector_db", factory.create_vector_db)
    deep_search_class, chain_of_rag_class, router_class, naive_rag_class = _load_agent_classes()

    def create_searchers():
        chain_settings = config.query_settings.get("chain_of_rag", {})
        deep_search_settings = config.query_settings.get("deep_search", {})
        naive = naive_rag_class(
            llm=llm_instance,
            embedding_model=embedding_instance,
            vector_db=vector_db_instance,
            top_k=10,
            route_collection=True,
            text_window_splitter=True,
            query_decomposition_enabled=bool(
                config.query_settings.get("document_aware_query_decomposition", {}).get(
                    "enabled", False
                )
            ),
        )
        agents = {
            "deep_search": deep_search_class(
                llm=llm_instance,
                embedding_model=embedding_instance,
                vector_db=vector_db_instance,
                max_iter=config.query_settings["max_iter"],
                route_collection=True,
                text_window_splitter=True,
                web_search=web_search_instance,
                web_search_queries_per_iteration=max(
                    int(deep_search_settings.get("web_search_queries_per_iteration", 2)),
                    1,
                ),
                web_search_results_per_query=max(
                    int(deep_search_settings.get("web_search_results_per_query", 5)),
                    1,
                ),
            ),
            "chain_of_rag": chain_of_rag_class(
                llm=llm_instance,
                embedding_model=embedding_instance,
                vector_db=vector_db_instance,
                max_iter=config.query_settings["max_iter"],
                early_stopping=bool(chain_settings.get("early_stopping", True)),
                min_evidence_for_stop=max(
                    int(chain_settings.get("min_evidence_for_stop", 2)),
                    1,
                ),
                route_collection=True,
                text_window_splitter=True,
            ),
            "naive": naive,
        }
        default_agent = str(
            config.query_settings.get("default_agent", DEFAULT_ANSWER_AGENT)
        ).strip()
        if default_agent not in agents:
            raise ValueError(f"Unsupported default answer agent: {default_agent}")
        default = router_class(
            llm=llm_instance,
            rag_agents=[agents[name] for name in ANSWER_AGENT_ORDER],
            fallback_agent_index=ANSWER_AGENT_ORDER.index(default_agent),
        )
        return default, naive

    default_searcher_instance, naive_rag_instance = _create_runtime_component(
        "searcher", create_searchers
    )
    return RuntimeComponents(
        config=config,
        module_factory=factory,
        llm=llm_instance,
        embedding_model=embedding_instance,
        file_loader=file_loader_instance,
        vector_db=vector_db_instance,
        web_crawler=web_crawler_instance,
        web_search=web_search_instance,
        default_searcher=default_searcher_instance,
        naive_rag=naive_rag_instance,
        entailment_checker=entailment_checker_instance,
    )


def init_config(config: Configuration) -> RuntimeComponents:
    """
    Initialize the global configuration and create instances of all required modules.

    This function initializes the global variables for the LLM, embedding model,
    file loader, web crawler, vector database, and RAG agents.

    Args:
        config: The Configuration object to use for initialization.
    """
    global \
        module_factory, \
        llm, \
        embedding_model, \
        file_loader, \
        vector_db, \
        web_crawler, \
        web_search, \
        default_searcher, \
        naive_rag, \
        entailment_checker
    runtime = build_runtime(config)
    module_factory = runtime.module_factory
    llm = runtime.llm
    embedding_model = runtime.embedding_model
    file_loader = runtime.file_loader
    web_crawler = runtime.web_crawler
    web_search = runtime.web_search
    vector_db = runtime.vector_db
    default_searcher = runtime.default_searcher
    naive_rag = runtime.naive_rag
    entailment_checker = runtime.entailment_checker
    return runtime
