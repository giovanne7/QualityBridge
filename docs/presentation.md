# Roteiro de apresentação

Roteiro de 6 a 8 minutos para apresentar o QualityBridge, sua arquitetura e as evidências de validação. Execute as demonstrações antes da apresentação e use os IDs exibidos pela CLI.

## Slide 1 - Problema e objetivo (45 s)

**Fala:** Cada framework apresenta falhas em um formato diferente. O QualityBridge executa testes existentes, normaliza resultados e reúne relatórios e histórico para apoiar a investigação. Ele não cria nem altera testes automaticamente.

## Slide 2 - Arquitetura integrada (90 s)

Apresente o diagrama de [`architecture.md`](architecture.md) e siga o fluxo: CLI -> controlador -> adapter/runner -> resultado comum -> relatórios e histórico. Mostre que flaky, RCA e auto-healing recebem o mesmo contrato de execução.

**Ponto-chave:** `run_project` coordena a execução; cada adapter isola os detalhes de pytest, Playwright e Jest.

## Slide 3 - Execução e artefatos (90 s)

Na raiz do repositório, demonstre:

```powershell
qualitybridge validate -c examples/pytest-project/qualitybridge.yaml
qualitybridge run -c examples/pytest-project/qualitybridge.yaml
qualitybridge run -c examples/pytest-project/qualitybridge.yaml --suite quick
qualitybridge history -c examples/pytest-project/qualitybridge.yaml --limit 2
```

Abra o `report.html` indicado pela CLI e mostre o estado dos casos e os links para JSON e JUnit. Compare os IDs das execuções no histórico.

## Slide 4 - Flaky e causa raiz (90 s)

```powershell
qualitybridge flaky -c examples/flaky-project/qualitybridge.yaml --rerun 4
python scripts/validate_rca.py
```

O exemplo flaky alterna resultados. O validador RCA usa causas conhecidas e calcula acerto de categoria, commit, fundamentação e tempo. Explique que os cenários sintéticos medem consistência com casos previstos e não substituem validação em um projeto real.

## Slide 5 - Testes e confiabilidade (60 s)

Mostre os arquivos em `tests/` e os caminhos de integração: falha de asserção, erro de coleta, timeout, formato inválido, persistência e artefatos. Consulte [`validation.md`](validation.md) para distinguir resultados registrados de resultados reproduzidos na sessão de entrega.

## Slide 6 - Limites e próximos passos (45 s)

Informe quais verificações foram executadas no ambiente da apresentação e quais ficaram pendentes. Para fechar a evidência empírica, guarde relatório HTML, JSON, JUnit, log e captura da tela, com ID e versões dos runners. Demonstre Docker com o Docker Engine ativo.

## Preparação

1. Instale as dependências dos exemplos e prepare o Chromium do Playwright.
2. Execute os comandos do roteiro e confirme os resultados esperados em `acceptance.md`.
3. Abra os relatórios HTML e capture as telas que serão exibidas.
4. Se uma demonstração falhar, apresente o resultado real e registre a causa; não substitua a saída por números do relatório histórico.
