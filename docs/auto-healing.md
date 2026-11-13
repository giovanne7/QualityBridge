# Auto-healing

## Objetivo

O módulo identifica falhas que podem indicar testes desatualizados, produz uma
proposta explícita e permite validá-la sem alterar permanentemente o projeto.
Ele não substitui a análise de causa raiz e não assume que toda falha pertence
ao teste.

## Arquitetura

- `auto_healing.py` recebe um `RunResult` e contextos opcionais de falha. O
  `AutoHealingAnalyzer` compara mensagem, stack trace, código do teste e diff
  recente.
- `HealingSuggestion` contém o caso afetado, classificação, evidências,
  arquivo, texto antigo, texto novo, diff e estado.
- `healing_validation.py` valida uma sugestão em uma cópia temporária. A API
  aceita somente um runner conhecido (`pytest`, `playwright` ou `jest`) e
  constrói internamente o comando autorizado.
- O campo `rca_evidence` permite que o módulo RCA forneça evidências depois,
  sem criar dependência direta.

O analisador inicial é determinístico. Um gerador baseado em LLM poderá
implementar uma interface futura e ainda deverá entregar a mesma estrutura,
com evidências e alteração verificável.

## Dados de entrada e saída

O analisador recebe um `RunResult` e zero ou mais `FailureContext`. O contexto
pode conter o arquivo do teste, seu código, código relacionado, diff recente e
evidências do RCA. Dados ausentes não são inventados.

A classificação pode ser `outdated_test`, `application_defect` ou
`insufficient_evidence`. O estado da sugestão pode ser `proposed`, `validated`,
`rejected` ou `inconclusive`.

## Geração de sugestões

Uma sugestão concreta só é gerada quando uma troca explícita do diff pode ser
aplicada exatamente ao código do teste. Os primeiros padrões suportados são:

- troca de seletores ou identificadores de interface;
- mudança de nome de parâmetro ou contrato de API;
- mudança explícita de valor esperado.

Uma asserção sem alteração correlacionada é tratada como possível defeito da
aplicação e não recebe patch automático. Contexto insuficiente produz uma
saída inconclusiva.

## Validação segura

O validador:

1. verifica que o arquivo da sugestão está dentro do projeto;
2. rejeita projetos com links simbólicos;
3. copia o projeto para um diretório temporário;
4. aplica uma única substituição exata somente nessa cópia;
5. executa o caso relevante com timeout e `shell=False`;
6. lê o relatório normalizado JUnit ou Jest;
7. só marca a sugestão como válida se o caso-alvo realmente passar.

Comandos, executáveis e diretório de trabalho não são recebidos da sugestão.
O mecanismo reduz alterações permanentes, mas `shell=False` não é isolamento
de segurança completo. Executar testes de projetos não confiáveis exige uma
camada adicional, como container endurecido, sandbox ou máquina isolada.

## Exemplo

Após instalar as dependências de desenvolvimento:

```powershell
python examples/auto-healing-demo.py
```

O exemplo cria uma aplicação cujo identificador mudou de `login-button` para
`signin-button`, executa o teste antigo, gera o diff da sugestão, valida a
correção em cópia temporária e depois tenta uma alteração para
`wrong-button`, que é rejeitada porque o teste continua falhando.

## Testes

```powershell
python -m pytest
ruff check src tests
```

Os testes usam fixtures locais e não dependem de LLM, rede, navegador ou
serviço externo.

## Limitações

- A geração depende de um diff explícito e de uma correspondência textual
  clara; mudanças sem mapeamento seguro ficam inconclusivas.
- Relatórios JUnit que não informam o arquivo do teste não recebem um caminho
  inventado; o orquestrador deve fornecer `FailureContext.test_file`.
- A cópia temporária não protege contra código malicioso durante a execução.
- RCA, LLM, aprovação humana persistente e integração de uma nova CLI continuam
  fora deste módulo.
