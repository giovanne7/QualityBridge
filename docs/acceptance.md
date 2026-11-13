# Roteiro de aceitação e apresentação

1. Instale o agente em ambiente virtual e execute `qualitybridge --help`.
2. Execute `qualitybridge validate -c examples/pytest-project/qualitybridge.yaml`.
3. Execute a suíte completa desse exemplo: devem aparecer 4 aprovados e 1 ignorado.
4. Repita com `--suite quick`: deve aparecer apenas 1 aprovado.
5. Abra o HTML indicado na CLI e confira os casos, duração e links JSON/JUnit/log.
6. Execute `qualitybridge history -c examples/pytest-project/qualitybridge.yaml`: as duas execuções devem aparecer com IDs diferentes.
7. Prepare o exemplo Playwright conforme o README. Execute a suíte completa (2 aprovados) e rápida (1 aprovado).
8. Execute `python -m pytest`: os testes do agente exercitam falhas reais de asserção, coleta, ausência de testes, timeout, histórico, configuração inválida, XML inseguro e escape HTML.
9. Execute `qualitybridge run` para gerar um relatório dos próprios testes do agente.
10. Se Docker estiver disponível, construa a imagem do README e execute `qualitybridge run --docker`.

11. Demonstre `examples/flaky-project`: `qualitybridge flaky -c examples/flaky-project/qualitybridge.yaml --rerun 4` deve registrar quatro resultados alternados, recomendar quarentena e exibir taxas, alternâncias e condições de execução. Aplique e libere a quarentena e confira HTML/JSON/JUnit e `qualitybridge quarantine --audit`.
Na apresentação, relacione as camadas e classes com os padrões descritos em `architecture.md`. A relação com a ODS 9 é indireta: automação da verificação de software, redução de retrabalho e manutenção de sistemas mais confiáveis.
