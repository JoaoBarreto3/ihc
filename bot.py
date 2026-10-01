import os
import random
import sqlite3
import sys

import dspy
import requests

### telebot: pip install pytelegrambotapi
### whisper: pip install -U openai-whisper (requires ffmpeg)
### requests: pip install requests

SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8000").rstrip("/")
CAMINHO_PROGRAMA_OTIMIZADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generator_otimizado.json")


def criar_lm():
    return dspy.LM(
        os.environ.get("LM_MODEL", "openai/gemma-4-E2B-it-IQ4_XS"),
        api_base=os.environ.get("LM_API_BASE", "http://localhost:1337/v1"),
        api_key="not-needed",
    )


class TextToSQL(dspy.Signature):
    """Generate SQL from natural language."""
    dbschema = dspy.InputField(desc="Databases schema")
    question = dspy.InputField(desc="Natural language question")

    sql_query = dspy.OutputField(desc="Valid SQL query")


def apenas_select(action_code, arg1, arg2, db_name, trigger_name):
    """Autorizador do sqlite3 usado so para validar localmente a SQL gerada, sem tocar no banco real."""
    if action_code in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def obter_schema():
    """Busca o DDL do banco no server.py via HTTP."""
    resp = requests.get(f"{SERVER_URL}/schema", timeout=10)
    resp.raise_for_status()
    return resp.json()["ddl"]


class ReliableSQLGenerator(dspy.Module):
    def __init__(self):
        super().__init__()
        self.generate_sql = dspy.ChainOfThought(TextToSQL)

    def forward(self, question):
        schema = obter_schema()
        pred = self.generate_sql(dbschema=schema, question=question)
        query = pred.sql_query.strip().replace("```sql", "").replace("```", "").strip()
        pred.sql_query = query
        pred.erro = None

        try:
            conn = sqlite3.connect(":memory:")
            conn.executescript(schema)
            conn.set_authorizer(apenas_select)
            conn.execute(query)
            conn.close()
        except sqlite3.Error as e:
            pred.erro = str(e)

        return pred


def carregar_generator(usar_otimizado=True):
    """Cria o gerador e, se existir, carrega o prompt otimizado pelo GEPA (otimizar.py)."""
    generator = ReliableSQLGenerator()
    if usar_otimizado and os.path.exists(CAMINHO_PROGRAMA_OTIMIZADO):
        generator.load(CAMINHO_PROGRAMA_OTIMIZADO)
        print(f"Usando programa otimizado: {CAMINHO_PROGRAMA_OTIMIZADO}")
    return generator


def executar(query):
    """Roda a query contra o server.py via HTTP, que a executa em modo somente leitura."""
    resp = requests.get(f"{SERVER_URL}/consulta", params={"sql": query}, timeout=10)
    resp.raise_for_status()
    dados = resp.json()
    if dados["erro"]:
        raise sqlite3.Error(dados["erro"])
    return dados["colunas"], dados["linhas"]


def generate(generator, question):
    sql = generator(question)
    print(sql.sql_query)

    if sql.erro:
        return {"sql_query": sql.sql_query, "erro": sql.erro, "colunas": [], "linhas": []}

    try:
        colunas, linhas = executar(sql.sql_query)
    except (sqlite3.Error, requests.RequestException) as e:
        return {"sql_query": sql.sql_query, "erro": str(e), "colunas": [], "linhas": []}

    return {"sql_query": sql.sql_query, "erro": None, "colunas": colunas, "linhas": linhas}


def formatar(result):
    """Transforma o resultado num texto legivel para o usuario do Telegram."""
    if result["erro"]:
        return f"Nao consegui responder: {result['erro']}"

    linhas = result["linhas"]
    if not linhas:
        return "Nao encontrei nenhum resultado para essa pergunta."

    partes = []
    for linha in linhas[:20]:
        if len(linha) == 1:
            partes.append(str(linha[0]))
        else:
            partes.append(" | ".join(str(campo) for campo in linha))

    texto = "\n".join(partes)
    if len(linhas) > 20:
        texto += f"\n\n(mostrando 20 de {len(linhas)} resultados)"
    return texto


# -----------------------------------------Avaliacao (dataset + metrica)-----------------------------------------

DATASET = [
    dspy.Example(question="quantos departamentos existem?",
                 sql_esperado="SELECT COUNT(*) FROM departamentos").with_inputs("question"),
    dspy.Example(question="qual o preco do sabonete?",
                 sql_esperado="SELECT preco FROM produtos WHERE nome = 'sabonete'").with_inputs("question"),
    dspy.Example(question="quais produtos sao do departamento bebidas?",
                 sql_esperado="""SELECT produtos.nome FROM produtos
                     JOIN departamentos ON produtos.departamento_id = departamentos.id
                     WHERE departamentos.nome = 'bebidas'""").with_inputs("question"),
    dspy.Example(question="quantos produtos existem no departamento alimentos?",
                 sql_esperado="""SELECT COUNT(*) FROM produtos
                     JOIN departamentos ON produtos.departamento_id = departamentos.id
                     WHERE departamentos.nome = 'alimentos'""").with_inputs("question"),
    dspy.Example(question="qual produto tem o maior preco?",
                 sql_esperado="SELECT nome FROM produtos ORDER BY preco DESC LIMIT 1").with_inputs("question"),
    dspy.Example(question="qual a quantidade em estoque do produto agua?",
                 sql_esperado="""SELECT estoque.quantidade FROM estoque
                     JOIN produtos ON estoque.produto_id = produtos.id
                     WHERE produtos.nome = 'agua'""").with_inputs("question"),
    dspy.Example(question="liste os nomes de todos os produtos",
                 sql_esperado="SELECT nome FROM produtos").with_inputs("question"),
    dspy.Example(question="quantos lotes de estoque estao vencidos?",
                 sql_esperado="SELECT COUNT(*) FROM estoque WHERE data_validade < date('now')").with_inputs("question"),
    # TODO: adicione mais exemplos (o ideal e ter uns 20-30) para o GEPA ter o que aprender.
]


def _resumo(linhas, limite=5):
    texto = repr(linhas[:limite])
    return texto + (f" ... ({len(linhas)} linhas)" if len(linhas) > limite else "")


def _comparar(gold, pred):
    """Retorna (nota, feedback em texto). O feedback e o que o GEPA usa para reescrever o prompt."""
    if pred.erro is not None:
        return 0.0, (f"A SQL gerada e invalida para o schema SQLite: {pred.erro}. "
                     f"SQL gerada: {pred.sql_query!r}. Uma SQL correta seria: {gold.sql_esperado.strip()!r}.")
    try:
        _, esperado = executar(gold.sql_esperado)
    except Exception as e:
        return 0.0, f"(problema no exemplo de referencia: {e})"
    try:
        _, gerado = executar(pred.sql_query)
    except Exception as e:
        return 0.0, f"A SQL gerada falhou ao executar no banco: {e}. SQL gerada: {pred.sql_query!r}."

    # compara ignorando a ordem das linhas (mas contando repeticoes)
    if sorted(map(tuple, gerado), key=repr) == sorted(map(tuple, esperado), key=repr):
        return 1.0, "Correto: a SQL retornou exatamente o resultado esperado."

    return 0.0, (f"Resultado errado. Pergunta: {gold.question!r}. SQL gerada: {pred.sql_query!r} "
                 f"retornou {_resumo(gerado)}, mas o esperado era {_resumo(esperado)} "
                 f"(ex.: {gold.sql_esperado.strip()!r}). Lembre que os nomes no banco estao em minusculo e sem acento.")


def resultado_correto(gold, pred, trace=None, pred_name=None, pred_trace=None, program_trace=None):
    """Metrica compativel com dspy.Evaluate e com o GEPA (score + feedback)."""
    nota, feedback = _comparar(gold, pred)
    return dspy.Prediction(score=nota, feedback=feedback)


def avaliar(generator, dataset=DATASET):
    acertos = 0
    for exemplo in dataset:
        try:
            pred = generator(question=exemplo.question)
            nota, feedback = _comparar(exemplo, pred)
        except requests.RequestException as e:
            raise RuntimeError("server.py nao esta respondendo. Rode o servidor antes.") from e
        acertos += nota
        print(f"{'OK  ' if nota == 1.0 else 'ERRO'} | {exemplo.question}")
        if nota < 1.0:
            print(f"      {feedback}")
    taxa = acertos / len(dataset)
    print(f"\nAcertos: {int(acertos)}/{len(dataset)} ({taxa:.0%})")
    return taxa


# -----------------------------------------Otimizacao (GEPA)-----------------------------------------

def dividir_dataset(dataset, proporcao_treino=0.6):
    exemplos = list(dataset)
    random.Random(0).shuffle(exemplos)
    corte = max(1, int(len(exemplos) * proporcao_treino))
    return exemplos[:corte], exemplos[corte:] or exemplos[:corte]


def otimizar():
    task_lm = criar_lm()
    dspy.configure(lm=task_lm)

    # O GEPA usa o reflection_lm para ler os erros e reescrever a instrucao do prompt.
    # Um modelo maior aqui faz MUITA diferenca; se nao tiver, ele cai no mesmo modelo do bot.
    reflection_model = os.environ.get("REFLECTION_MODEL")
    reflection_lm = (
        dspy.LM(reflection_model, api_base=os.environ.get("REFLECTION_API_BASE", "http://localhost:1337/v1"),
                api_key=os.environ.get("REFLECTION_API_KEY", "not-needed"), temperature=1.0, max_tokens=8000)
        if reflection_model else task_lm
    )

    trainset, valset = dividir_dataset(DATASET)
    generator = ReliableSQLGenerator()

    print("=== Antes da otimizacao ===")
    avaliar(generator, valset)

    otimizador = dspy.GEPA(
        metric=resultado_correto,
        auto="light",            # ou max_metric_calls=150 para controlar o custo exato
        reflection_lm=reflection_lm,
        num_threads=1,           # servidor local costuma atender uma requisicao por vez
        track_stats=True,
        log_dir="gepa_logs",     # permite retomar a otimizacao se for interrompida
    )
    otimizado = otimizador.compile(generator, trainset=trainset, valset=valset)

    print("=== Depois da otimizacao ===")
    avaliar(otimizado, valset)

    otimizado.save(CAMINHO_PROGRAMA_OTIMIZADO)
    print(f"\nPrograma otimizado salvo em {CAMINHO_PROGRAMA_OTIMIZADO}")
    print("Nova instrucao:\n", otimizado.generate_sql.predict.signature.instructions)
    return otimizado


# -----------------------------------------Telegram-----------------------------------------

def whisper_transcribe(filepath: str, model="tiny") -> str:
    """
    Function to perform ASR on a .mp3 file
    :param filepath: Path to the .mp3 audiofile.
    :param model: Set the model type for whisper
    ["tiny", "base", "small", "medium", "large"].
    Larger model means more parameters, higher memory requirements and
    slower speed.
    :return: transcribed audio.
    """
    import whisper
    # Choose tiny model for faster output.
    model = whisper.load_model(model)
    result = model.transcribe(filepath)

    return result["text"]


def main():
    import telebot

    dspy.configure(lm=criar_lm())
    generator = carregar_generator()

    API_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not API_TOKEN:
        raise RuntimeError("Defina a variavel de ambiente TELEGRAM_BOT_TOKEN antes de rodar o bot.")
    bot = telebot.TeleBot(API_TOKEN)

    @bot.message_handler(commands=['start', 'help'])
    def ajuda(message):
        bot.reply_to(
            message,
            "Oi! Faca uma pergunta sobre a base de lojas e eu consulto pra voce.\n\n"
            "Exemplos:\n"
            "- Em qual departamento fica o sabonete?\n"
            "- Quais produtos existem no departamento de bebidas?"
        )

    @bot.message_handler(content_types=['voice'])
    def transcribe_voice_message(message):
        file_id = message.voice.file_id
        # Get url to audio file.
        file_path = bot.get_file_url(file_id)

        # Transcribe the audio using Whisper AI
        text = whisper_transcribe(file_path)

        result = generate(generator, text)
        bot.reply_to(message, formatar(result))

    @bot.message_handler(func=lambda message: True)
    def responder(message):
        result = generate(generator, message.text)
        bot.reply_to(message, formatar(result))

    bot.polling()


# Modos de uso:
#   python bot.py                    -> sobe o bot do Telegram (usa o prompt otimizado, se existir)
#   python bot.py --otimizar         -> roda o GEPA e salva generator_otimizado.json
#   python bot.py --avaliar          -> mede a acuracia do prompt otimizado (ou do base, se nao houver)
#   python bot.py --avaliar --base   -> mede a acuracia do prompt sem otimizacao
if __name__ == "__main__":
    if "--otimizar" in sys.argv:
        otimizar()
    elif "--avaliar" in sys.argv:
        dspy.configure(lm=criar_lm())
        avaliar(carregar_generator(usar_otimizado="--base" not in sys.argv))
    else:
        main()
