# ihc
Projeto de bot local para a disciplina de Interação Humano Computador.

Agente text-to-SQL: o usuário pergunta (texto ou áudio) no Telegram, o LLM gera a SQL e o bot responde
com o resultado da consulta.

- `server.py`: banco SQLite (departamentos, produtos e lotes de estoque com fabricação/validade)
  encapsulado na classe `BancoLojas` e exposto via FastAPI (rotas GET). A rota `GET /consulta?sql=...`
  executa SQL em modo somente leitura.
- `bot.py`: gerador de SQL em DSPy (RLM por padrão, CoT opcional), validação da SQL num SQLite em
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
python bot.py                         # Telegram, gerador RLM
python bot.py --modo cot              # Telegram, gerador Chain of Thought
python bot.py --pergunta "quais lotes vencem nos proximos 30 dias?"
```

## Avaliação e otimização

O dataset (`DATASET` em `bot.py`) tem 24 perguntas com a SQL esperada, divididas em
treino (12) / validação (6) / teste (6). A métrica compara o **resultado** da SQL gerada com o da esperada.

```bash
python bot.py --avaliar --base [--modo cot]   # acurácia sem otimização no conjunto de teste
python bot.py --otimizar [--modo cot]         # GEPA em treino/validação; mede antes/depois no teste
python bot.py --avaliar [--modo cot]          # acurácia do programa otimizado
```

Cada avaliação salva `avaliacao_<modo>_<base|otimizado>.json`; o GEPA salva
`generator_otimizado_<modo>.json`, carregado automaticamente pelo bot. Para usar um modelo maior
na reflexão do GEPA, defina `REFLECTION_MODEL` (e `REFLECTION_API_BASE` / `REFLECTION_API_KEY`).
