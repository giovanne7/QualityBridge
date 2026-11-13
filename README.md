# QualityBridge

Agente de testes de software da AEP. Executa testes **já existentes** em projetos pytest, Playwright e Jest, seleciona suítes por configuração e gera relatórios HTML, JSON e JUnit XML, logs e histórico SQLite. Não usa LLM nem gera testes automaticamente. O MVP é uma aplicação de linha de comando, conforme `AEP_Entrega1_3agentes.pdf`.

## Instalação

Requisitos: Python 3.11 ou superior. Para projetos web, Node.js e Playwright instalados no próprio projeto; para containers, Docker disponível.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\qualitybridge --help
```

Em Linux/macOS, use `.venv/bin/python` e `.venv/bin/qualitybridge`. Após ativar o ambiente, os exemplos abaixo podem ser executados diretamente. Alternativa: `uv sync --extra dev` e `uv run qualitybridge ...`. O arquivo `uv.lock` fixa as dependências Python para reprodução.

Nesta pasta, o ambiente `.venv` já foi preparado. Para executar agora no PowerShell, use `.\.venv\Scripts\qualitybridge.exe run`. Para demonstrar as suítes completa e rápida dos dois exemplos, execute `.\.venv\Scripts\python.exe scripts/demo.py`; o script também reconhece o Chromium instalado localmente em `.tools/browsers`. Essas pastas de ferramentas não devem ser versionadas nem copiadas entre máquinas.

## Primeiro uso em um projeto

```powershell
qualitybridge init --runner pytest --name "Meu projeto"
qualitybridge validate
qualitybridge run
qualitybridge run --suite quick
qualitybridge history
qualitybridge history --json
qualitybridge flaky
qualitybridge quarantine
qualitybridge suggestions
```

`init` cria `qualitybridge.yaml` na pasta atual e recusa sobrescrever um arquivo existente. Ajuste os seletores e marque os testes rápidos antes de executar `quick`. Também é possível fornecer `--config caminho/qualitybridge.yaml` em cada comando, sem mudar o diretório do terminal.

```yaml
version: 1
project: Meu projeto
runner: pytest                 # pytest, playwright ou jest
root: .                       # relativo ao arquivo YAML
output_dir: .qualitybridge     # relativo à raiz do projeto
timeout_seconds: 300
suites:
  full:
    paths: [tests]
  quick:
    paths: [tests]
    filter: smoke              # marcador pytest; regex --grep no Playwright
```

É possível adicionar suítes nomeadas. A suíte `full` é obrigatória; uma suíte desconhecida causa erro. `paths: []` usa a descoberta padrão da ferramenta. Para pytest, registre os marcadores em `pytest.ini`; para Playwright, use por exemplo `filter: '@smoke'` e títulos com essa tag.

Campos opcionais: `python` seleciona o executável Python do projeto (por padrão usa o Python do agente); `node` seleciona o executável Node (padrão `node`); `env` recebe um mapa de strings de ambiente. O ambiente do terminal é herdado. Os valores de `env` não são gravados diretamente no relatório, mas os próprios testes podem imprimir dados sensíveis nos logs. Não versionar segredos.

## Exemplos completos

Python, sem dependências adicionais além da instalação de desenvolvimento:

```powershell
qualitybridge run -c examples/pytest-project/qualitybridge.yaml
qualitybridge run -c examples/pytest-project/qualitybridge.yaml -s quick
```

Web, com duas verificações reais em Chromium, sem servidor externo:

```powershell
cd examples/playwright-project
npm ci
npx playwright install chromium
cd ../..
qualitybridge run -c examples/playwright-project/qualitybridge.yaml
qualitybridge run -c examples/playwright-project/qualitybridge.yaml -s quick
```

O agente usa o Playwright local em `node_modules/@playwright/test/cli.js`. A instalação de dependências e browsers é uma etapa explícita de preparação; a execução não baixa pacotes. Os testes usam o arquivo de configuração e as fixtures do próprio projeto. Conforme as interfaces oficiais de [pytest](https://docs.pytest.org/en/stable/how-to/output.html) e [Playwright](https://playwright.dev/docs/test-reporters), o agente coleta resultados JUnit e os normaliza.

## Resultados e códigos de saída

Cada execução cria `.qualitybridge/runs/<data-UTC>-<id>/` contendo `report.html`, `report.json`, `junit.xml` e `execution.log`. Quando emitido pelo executor, `raw-junit.xml` também é preservado. Abra `report.html` no navegador. O HTML é independente de internet e escapa o conteúdo dos testes.

O JSON tem versão de esquema, ID, projeto, ferramenta, suíte, horário UTC, duração, estado, código original do processo, comando, casos de teste, diagnóstico e caminhos relativos dos artefatos. O histórico fica em `.qualitybridge/history.sqlite3`. Os relatórios são mantidos sem política automática de exclusão.

| Estado | Saída da CLI | Significado |
|---|---:|---|
| passed | 0 | Os casos selecionados não apresentaram falhas; pode incluir ignorados |
| failed | 1 | Uma ou mais asserções falharam |
| error / no_tests | 2 | Configuração, preparação, coleta, executor ou relatório inválido; ou nenhum teste |
| timeout | 124 | Tempo máximo excedido |
| cancelled | 130 | Interrompido com Ctrl+C |

Erros de infraestrutura geram um caso sintético de erro no JUnit para que consumidores de CI não interpretem um relatório vazio como sucesso. `--timeout 60` substitui temporariamente o limite do YAML. Ctrl+C e timeout encerram a árvore de processos do executor. Erros na configuração são detectados antes da criação de uma execução.

## DetecÃ§Ã£o de testes intermitentes

Para demonstrar o fluxo ponta a ponta, execute `qualitybridge flaky -c examples/flaky-project/qualitybridge.yaml --rerun 4`. O teste de exemplo alterna deliberadamente entre falha e sucesso; a análise deve classificá-lo como suspeito. Para sinalizá-lo, informe a identidade completa apresentada na saída:

```powershell
qualitybridge quarantine -c examples/flaky-project/qualitybridge.yaml --action quarantine --test test_alternates_between_pass_and_failure --classname examples.flaky-project.tests.test_flaky_demo --reason "intermitência demonstrada" --actor "seu nome"
qualitybridge run -c examples/flaky-project/qualitybridge.yaml
qualitybridge quarantine -c examples/flaky-project/qualitybridge.yaml --action release --test test_alternates_between_pass_and_failure --classname examples.flaky-project.tests.test_flaky_demo --reason "revisão concluída" --actor "seu nome"
```

A execução continua mesmo em quarentena; o estado e o motivo aparecem nos relatórios HTML, JSON e JUnit. O comando `qualitybridge quarantine --audit` mostra o histórico das decisões.

`qualitybridge flaky` calcula, a partir do histÃ³rico, execuÃ§Ãµes, taxa de falhas, alternÃ¢ncias e classificaÃ§Ã£o por caso, considerando runner e suÃ­te. SÃ£o necessÃ¡rias trÃªs execuÃ§Ãµes por padrÃ£o; `--min-runs` e `--failure-threshold` ajustam os limites e `--limit` define quantas execuÃ§Ãµes recentes consultar. `--rerun N` repete a suÃ­te selecionada atÃ© 20 vezes, registrando cada resultado no histÃ³rico antes da anÃ¡lise. A quarentena Ã© uma recomendaÃ§Ã£o (`quarantine_recommended`), sem desabilitar testes. VariÃ¡veis de ambiente nÃ£o sÃ£o armazenadas; runner e suÃ­te identificam as condiÃ§Ãµes de execuÃ§Ã£o comparadas.

O contexto persistido inclui plataforma, versÃµes detectadas de Python e Node, modo/imagem/rede Docker, timeout e apenas os nomes das variÃ¡veis configuradas; valores de ambiente nunca sÃ£o gravados. `qualitybridge quarantine --action quarantine --test NOME --suite full --reason "motivo" --actor "responsavel"` registra uma decisÃ£o manual. Use `--action release` para removÃª-la e `--audit` para consultar o histÃ³rico de alteraÃ§Ãµes. A quarentena Ã© administrativa e nÃ£o pula a execuÃ§Ã£o: os resultados continuam sendo coletados. `qualitybridge suggestions` apresenta hipÃ³teses baseadas em padrÃµes de mensagens e stack traces, com ocorrÃªncias, exemplos de evidÃªncia e nÃ­vel de confianÃ§a; revise-as antes de alterar o teste.

Em execuções locais, as versões são lidas dos executáveis usados pelo runner. Em Docker, o contexto registra a imagem configurada e não tenta inferir versões internas do container. Registros antigos continuam legíveis; execuções anteriores à inclusão do contexto aparecem sem esses dados.

## Docker

A imagem fornecida contém Python, QualityBridge e pytest:

```powershell
docker build -t qualitybridge:local .
qualitybridge run --docker
```

O YAML da raiz já define `docker.image: qualitybridge:local` e `docker.network: none`. Para outro projeto, construa uma imagem com as dependências desse projeto e configure seu nome no YAML. Para Playwright, a imagem precisa conter Node, o navegador e suas bibliotecas de sistema, e o projeto montado precisa ter suas dependências Linux compatíveis; a imagem Python fornecida não inclui esses componentes.

O modo Docker monta o projeto em `/project` e os resultados em `/qualitybridge-output`, com rede desativada por padrão (`bridge` pode ser configurada). Containers são removidos após a execução. Os mounts permitem escrita: use apenas projetos e imagens confiáveis. Docker aqui oferece isolamento de ambiente, não uma fronteira para executar código hostil. Imagens e dependências precisam ser preparadas antes de uma execução sem rede.

## Desenvolvimento e arquitetura

```powershell
python -m pytest
ruff check src tests
qualitybridge run             # executa os testes do próprio agente e gera seus relatórios
```

Veja [docs/architecture.md](docs/architecture.md) para camadas, padrões e decisões e [docs/acceptance.md](docs/acceptance.md) para o roteiro de demonstração. O agente funciona sozinho. Comunicação com os agentes Arquiteto e Documentação e gatilhos automáticos de CI/agendamento ficam fora do MVP descrito no PDF.
