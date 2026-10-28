# ihc
Projeto de bot local para a disciplina de Interação Humano Computador.

Agente text-to-SQL: o usuário pergunta (texto ou áudio) no Telegram, o LLM gera a SQL e o bot responde
com o resultado da consulta.

- `server.py`: banco SQLite (departamentos, produtos e lotes de estoque com fabricação/validade)
  encapsulado na classe `BancoLojas` e exposto via FastAPI (rotas GET). A rota `GET /consulta?sql=...`
  executa SQL em modo somente leitura.
- `bot.py`: gerador de SQL em DSPy (Chain of Thought por padrão, RLM opcional), validação da SQL num SQLite em
  memória (só `SELECT`), avaliação, otimização com GEPA e bot do Telegram com transcrição de voz (Whisper).

## Como rodar

```bash
pip install -r requirements.txt

# 1. API + banco (cria e popula o lojas.db na primeira execução)
python server.py                      # ou: uvicorn server:app --port 8000

# 2. LLM local compatível com OpenAI em http://localhost:1337/v1
#    (troque com LM_MODEL / LM_API_BASE / LM_API_KEY)

# 3. Bot
export TELEGRAM_BOT_TOKEN=...
python bot.py                         # Telegram, gerador Chain of Thought (ja otimizado com GEPA)
python bot.py --modo rlm              # Telegram, gerador RLM (mais lento: varias chamadas ao LLM por pergunta)
python bot.py --pergunta "quais lotes vencem nos proximos 30 dias?"
```

## Avaliação e otimização

O dataset (`DATASET` em `bot.py`) tem 24 perguntas com a SQL esperada, divididas em
treino (12) / validação (6) / teste (6). A métrica compara o **resultado** da SQL gerada com o da esperada.

```bash
python bot.py --avaliar --base [--modo rlm]   # acurácia sem otimização no conjunto de teste
python bot.py --otimizar [--modo rlm]         # GEPA em treino/validação; mede antes/depois no teste
python bot.py --avaliar [--modo rlm]          # acurácia do programa otimizado

# PC lento: limita o GEPA a N execuções da métrica e a K perguntas de treino por tentativa
python bot.py --otimizar --orcamento 40 --lote 4
# nova rodada partindo do programa já otimizado
python bot.py --otimizar --continuar --orcamento 40
```

Cada avaliação salva `avaliacao_<modo>_<base|otimizado>.json`; o GEPA salva
`generator_otimizado_<modo>.json`, carregado automaticamente pelo bot. Para usar um modelo maior
na reflexão do GEPA, defina `REFLECTION_MODEL` (e `REFLECTION_API_BASE` / `REFLECTION_API_KEY`).

No treino o GEPA também usa `TREINO_EXTRA` (8 perguntas mais difíceis: `DISTINCT`, soma de vários lotes,
`HAVING`), porque o CoT acertava todo o treino original e, sem erros, o GEPA não tinha o que corrigir.

### Resultado (modo CoT, Gemma 4 E2B local)

| | Instrução original | Instrução otimizada pelo GEPA |
|---|---|---|
| Teste (6 perguntas) | 5/6 (83%) | **6/6 (100%)** |
| Validação (6 perguntas) | 5/6 | 5/6 |

A instrução original errava "quais produtos tem algum lote vencido?" (faltava `DISTINCT` e o leite
aparecia duas vezes); a otimizada acerta. O GEPA propôs a nova instrução por ela ser melhor no treino
(4/4 contra 3/4); como empatou na validação, ele manteve a original por padrão, e a otimizada foi
escolhida manualmente depois de medida no teste. O programa otimizado está em
`generator_otimizado_cot.json` e é carregado automaticamente: quem clonar o repositório já usa o bot
otimizado. Resultados em `avaliacao_cot_base.json` e `avaliacao_cot_otimizado.json`.
