import sys
from abc import ABC, abstractmethod
from pathlib import Path

from qualitybridge.models import ProjectConfig, SuiteConfig


class TestAdapter(ABC):
    @abstractmethod
    def command(
        self,
        config: ProjectConfig,
        suite: SuiteConfig,
        root: Path,
        report: Path,
        docker: bool = False,
    ) -> list[str]: ...

    def environment(self, report: Path) -> dict[str, str]:
        return {}


class PytestAdapter(TestAdapter):
    def command(self, config, suite, root, report, docker=False):
        executable = config.python or ("python" if docker else sys.executable)
        args = [executable, "-m", "pytest", f"--junitxml={report}", "-o", "junit_family=xunit2"]
        if suite.filter:
            args += ["-m", suite.filter]
        return args + suite.paths


class PlaywrightAdapter(TestAdapter):
    def command(self, config, suite, root, report, docker=False):
        # Use the project's installed runner; never download packages during a test run.
        cli = "node_modules/@playwright/test/cli.js"
        args = [config.node, cli, "test", "--reporter=junit"]
        if suite.filter:
            args += ["--grep", suite.filter]
        return args + suite.paths

    def environment(self, report: Path) -> dict[str, str]:
        return {"PLAYWRIGHT_JUNIT_OUTPUT_FILE": str(report), "PLAYWRIGHT_JUNIT_STRIP_ANSI": "true"}


class JestAdapter(TestAdapter):
    def command(self, config, suite, root, report, docker=False):
        cli = "node_modules/jest/bin/jest.js"
        args = [
            config.node,
            cli,
            "--json",
            f"--outputFile={report}",
        ]

        if suite.filter:
            args += ["--testNamePattern", suite.filter]

        return args + suite.paths


class ExecutorFactory(ABC):
    @abstractmethod
    def create_adapter(self) -> TestAdapter: ...


class PytestFactory(ExecutorFactory):
    def create_adapter(self) -> TestAdapter:
        return PytestAdapter()


class PlaywrightFactory(ExecutorFactory):
    def create_adapter(self) -> TestAdapter:
        return PlaywrightAdapter()


class JestFactory(ExecutorFactory):
    def create_adapter(self) -> TestAdapter:
        return JestAdapter()


def factory_for(runner: str) -> ExecutorFactory:
    factories = {
        "pytest": PytestFactory,
        "playwright": PlaywrightFactory,
        "jest": JestFactory,
    }
    return factories[runner]()
