import os
import sqlite3

import dspy
import requests

### telebot: pip install pytelegrambotapi
### whisper: pip install -U openai-whisper (requires ffmpeg)
### requests: pip install requests

SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8000").rstrip("/")


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

    lm = dspy.LM('openai/gemma-4-E2B-it-IQ4_XS', api_base='http://localhost:1337/v1', api_key='not-needed')
    dspy.configure(lm=lm)

    generator = ReliableSQLGenerator()

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


if __name__ == "__main__":
    main()
