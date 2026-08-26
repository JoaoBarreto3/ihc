import sqlite3
import dspy
import json
import os
import db
### telebot: pip install pytelegrambotapi
### whisper: pip install -U openai-whisper (requires ffmpeg)

class TextToSQL(dspy.Signature):
    """Generate SQL from natural language.

        Database schema:
          - produtos: nome, departamento
    """
    dbschema = dspy.InputField(desc="Databases schema")
    question = dspy.InputField(desc="Natural language question")

    sql_query = dspy.OutputField(desc="Valid SQL query")

def apenas_select(action_code, arg1, arg2, db_name, trigger_name):
    if action_code in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class ReliableSQLGenerator(dspy.Module):
    def __init__(self):
        super().__init__()
        self.generate_sql = dspy.ChainOfThought(TextToSQL)

    def forward(self, question):
        schema = db.get_schema_ddl()
        pred = self.generate_sql(dbschema=schema, question=question)
        query = pred.sql_query.strip().replace("```sql", "").replace("```", "").strip()
        pred.sql_query = query
        pred.erro = None

        try:
            conn = sqlite3.connect(":memory:")
            conn.executescript(db.get_schema_ddl())
            conn.set_authorizer(apenas_select)
            conn.execute(query)
            conn.close()
        except sqlite3.Error as e:
            pred.erro = str(e)

        return pred


def generate(generator, question):
    sql = generator(question)
    print(sql.sql_query)
    # TODO: executar sql.sql_query contra o banco real (nao especificado pelo professor ainda)
    return {"sql_query": sql.sql_query, "erro": sql.erro}


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

    API_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not API_TOKEN:
        raise RuntimeError("Defina a variavel de ambiente TELEGRAM_BOT_TOKEN antes de rodar o bot.")
    bot = telebot.TeleBot(API_TOKEN)

    @bot.message_handler(func=lambda message: True)
    def reply_hi(message):
        result = generate(generator, message.text)
        bot.reply_to(message, json.dumps(result))

    @bot.message_handler(content_types=['voice'])
    def transcribe_voice_message(message):
        file_id = message.voice.file_id
        # Get url to audio file.
        file_path = bot.get_file_url(file_id)

        # Transcribe the audio using Whisper AI
        text = whisper_transcribe(file_path)

        result = generate(generator, text)
        bot.reply_to(message, json.dumps(result))

    bot.polling()


if __name__ == "__main__":
    main()
