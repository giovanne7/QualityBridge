from abc import ABC, abstractmethod

from qualitybridge.config import ConfigurationError
from qualitybridge.models import ProjectConfig, SuiteConfig


class SuiteStrategy(ABC):
    @abstractmethod
    def select(self, config: ProjectConfig) -> SuiteConfig: ...


class FullSuite(SuiteStrategy):
    def select(self, config: ProjectConfig) -> SuiteConfig:
        return config.suites["full"]


class NamedSuite(SuiteStrategy):
    def __init__(self, name: str):
        self.name = name

    def select(self, config: ProjectConfig) -> SuiteConfig:
        if self.name not in config.suites:
            raise ConfigurationError(
                f"Suíte '{self.name}' não definida. Disponíveis: " + ", ".join(config.suites)
            )
        return config.suites[self.name]


def select_suite(config: ProjectConfig, name: str) -> SuiteConfig:
    strategy = FullSuite() if name == "full" else NamedSuite(name)
    return strategy.select(config)
