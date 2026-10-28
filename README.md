# ihc
Projeto de bot local para a disciplina de Interação Humano Computador.

Agente text-to-SQL: o usuário pergunta (texto ou áudio) no Telegram, o LLM gera a SQL e o bot responde
com o resultado da consulta.

- `server.py`: banco SQLite (departamentos, produtos e lotes de estoque com fabricação/validade)
  encapsulado na classe `BancoLojas` e exposto via FastAPI (rotas GET). A rota `GET /consulta?sql=...`
  executa SQL em modo somente leitura.
- `bot.py`: gerador de SQL em DSPy (RLM por padrão, Chain of Thought opcional), validação da SQL num SQLite em
  memória (só `SELECT`), avaliação, otimização com GEPA e bot do Telegram com transcrição de voz (Whisper).

## Como rodar

São três processos ao mesmo tempo, cada um num terminal: a API do banco, o LLM e o bot.

### Pré-requisitos

- Python 3 e as dependências: `pip install -r requirements.txt`
- `ffmpeg` instalado no sistema (só para mensagens de áudio no Telegram):
  `sudo apt install ffmpeg` / `brew install ffmpeg` / `choco install ffmpeg`
- O modelo **Gemma 4 E2B** (`gemma-4-E2B-it-IQ4_XS`, arquivo GGUF), o mesmo usado na otimização
- Um token de bot do Telegram: no Telegram, fale com o **@BotFather**, mande `/newbot`, escolha um nome
  e copie o token que ele devolve

### 1. API + banco

```bash
python server.py                      # porta 8000; cria e popula o lojas.db na primeira execução
```

### 2. LLM local na porta 1337

O bot espera uma API compatível com OpenAI em `http://localhost:1337/v1`, com o modelo chamado
`gemma-4-E2B-it-IQ4_XS`.

- **Jan:** baixe o Gemma 4 E2B pelo Jan e ligue o *Local API Server* (porta 1337 é o padrão).
- **llama.cpp** (se o Jan não abrir, dá para usar o `llama-server` e o modelo que ele já baixou):

  ```bash
  llama-server -m caminho/para/model.gguf --alias gemma-4-E2B-it-IQ4_XS \
    --host 127.0.0.1 --port 1337 -c 8192 --jinja
  ```

  No Linux com Jan via Flatpak, o modelo fica em
  `~/.var/app/ai.jan.Jan/data/Jan/data/llamacpp/models/gemma-4-E2B-it-IQ4_XS/model.gguf` e o
  `llama-server` em `~/.var/app/ai.jan.Jan/data/Jan/data/llamacpp/backends/<versao>/<backend>/build/bin/`
  (rode com `LD_LIBRARY_PATH` apontando para essa pasta).

Outro modelo, endereço ou chave: `LM_MODEL=openai/<nome>`, `LM_API_BASE`, `LM_API_KEY`. O programa
otimizado continua sendo carregado, mas o resultado da avaliação pode mudar com outro modelo.

Sem GPU, cada pergunta nova leva alguns minutos para ser respondida; perguntas repetidas voltam na hora
(cache do DSPy).

### 3. Bot

O gerador padrão é o **RLM** (`dspy.RLM`): o LLM escreve código Python num sandbox e chama a ferramenta
`testar_sql` para executar e corrigir a SQL de forma recursiva antes de responder. O **CoT**
(`--modo cot`) é mais rápido (uma chamada ao LLM por pergunta) e é o que foi otimizado com GEPA; quando
existe `generator_otimizado_<modo>.json`, o bot o carrega automaticamente (aparece "Usando programa
otimizado").

```bash
export TELEGRAM_BOT_TOKEN=...         # token do @BotFather
python bot.py                         # Telegram, gerador RLM (padrao; varias chamadas ao LLM por pergunta)
python bot.py --modo cot              # Telegram, gerador Chain of Thought (otimizado com GEPA, mais rapido)
python bot.py --pergunta "quais lotes vencem nos proximos 30 dias?"   # sem Telegram, responde no terminal
python bot.py --rlm-iters 3           # RLM com no maximo 3 rodadas por pergunta (mais rapido sem GPU)
```

## Avaliação e otimização

O dataset (`DATASET` em `bot.py`) tem 24 perguntas com a SQL esperada, divididas em
treino (12) / validação (6) / teste (6). A métrica compara o **resultado** da SQL gerada com o da esperada.

```bash
python bot.py --avaliar --base [--modo cot]   # acurácia sem otimização no conjunto de teste
python bot.py --otimizar [--modo cot]         # GEPA em treino/validação; mede antes/depois no teste
python bot.py --avaliar [--modo cot]          # acurácia do programa otimizado
python bot.py --avaliar --base --modo rlm --rlm-iters 3   # avaliação do RLM usada no README

# PC lento: limita o GEPA a N execuções da métrica e a K perguntas de treino por tentativa
python bot.py --otimizar --modo cot --orcamento 40 --lote 4
# nova rodada partindo do programa já otimizado
python bot.py --otimizar --modo cot --continuar --orcamento 40
```

Cada avaliação salva `avaliacao_<modo>_<base|otimizado>.json`; o GEPA salva
`generator_otimizado_<modo>.json`, carregado automaticamente pelo bot. Para usar um modelo maior
na reflexão do GEPA, defina `REFLECTION_MODEL` (e `REFLECTION_API_BASE` / `REFLECTION_API_KEY`).

No treino o GEPA também usa `TREINO_EXTRA` (8 perguntas mais difíceis: `DISTINCT`, soma de vários lotes,
`HAVING`), porque o CoT acertava todo o treino original e, sem erros, o GEPA não tinha o que corrigir.

### Resultados (Gemma 4 E2B local, só CPU)

| Gerador | Teste (6 perguntas) | Validação (6 perguntas) | Tempo por pergunta |
|---|---|---|---|
| CoT, instrução original | 5/6 (83%) | 5/6 | 1 a 4 min |
| CoT, instrução otimizada pelo GEPA | **6/6 (100%)** | 5/6 | 1 a 4 min |
| RLM (`--rlm-iters 3`), sem otimização | **6/6 (100%)** | – | 8 a 13 min |

O RLM foi avaliado com no máximo 3 rodadas por pergunta (o padrão do bot é 8) para caber em cerca de
uma hora sem GPU; ele não foi otimizado com GEPA porque cada pergunta leva vários minutos e o GEPA faz
centenas de chamadas. Resultado em `avaliacao_rlm_base.json`.

A instrução original errava "quais produtos tem algum lote vencido?" (faltava `DISTINCT` e o leite
aparecia duas vezes); a otimizada acerta. O GEPA propôs a nova instrução por ela ser melhor no treino
(4/4 contra 3/4); como empatou na validação, ele manteve a original por padrão, e a otimizada foi
escolhida manualmente depois de medida no teste. O programa otimizado está em
`generator_otimizado_cot.json` e é carregado automaticamente: quem clonar o repositório e rodar com
`--modo cot` já usa o bot otimizado. Resultados em `avaliacao_cot_base.json` e `avaliacao_cot_otimizado.json`.
