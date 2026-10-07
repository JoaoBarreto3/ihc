import argparse
import json
import os
import random
import sqlite3
import tempfile

import dspy
import requests

from server import autorizador_somente_leitura


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8000").rstrip("/")
MODO_PADRAO = os.environ.get("GERADOR", "rlm")


def criar_lm():
    return dspy.LM(
        os.environ.get("LM_MODEL", "openai/gemma-4-E2B-it-IQ4_XS"),
        api_base=os.environ.get("LM_API_BASE", "http://localhost:1337/v1"),
        api_key=os.environ.get("LM_API_KEY", "not-needed"),
    )


def caminho_programa_otimizado(modo):
    return os.path.join(BASE_DIR, f"generator_otimizado_{modo}.json")


class ClienteServidor:
    def __init__(self, url=SERVER_URL):
        self.url = url
        self._schema = None

    def schema(self):
        if self._schema is None:
            resp = requests.get(f"{self.url}/schema", timeout=10)
            resp.raise_for_status()
            self._schema = resp.json()["ddl"]
        return self._schema

    def consultar(self, sql):
        resp = requests.get(f"{self.url}/consulta", params={"sql": sql}, timeout=10)
        resp.raise_for_status()
        dados = resp.json()
        if dados["erro"]:
            raise sqlite3.Error(dados["erro"])
        return dados["colunas"], dados["linhas"]


servidor = ClienteServidor()


def limpar_sql(texto):
    return (texto or "").strip().replace("```sqlite", "").replace("```sql", "").replace("```", "").strip()


def validar_sql(schema, sql):
    if not sql:
        return "nenhuma SQL foi gerada"
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema)
        conn.set_authorizer(autorizador_somente_leitura)
        conn.execute(sql)
    except sqlite3.Error as e:
        if "not authorized" in str(e):
            return "apenas consultas de leitura (SELECT) sao permitidas"
        return str(e)
    finally:
        conn.close()
    return None


def testar_sql(sql: str) -> str:
    sql = limpar_sql(sql)
    try:
        erro = validar_sql(servidor.schema(), sql)
        if erro:
            return f"ERRO: {erro}"
        colunas, linhas = servidor.consultar(sql)
    except (sqlite3.Error, requests.RequestException) as e:
        return f"ERRO: {e}"
    extra = f" ... ({len(linhas)} linhas no total)" if len(linhas) > 10 else ""
    return f"OK. colunas={colunas} linhas={linhas[:10]}{extra}"


class TextToSQL(dspy.Signature):
    """Generate ONE read-only SQLite SELECT query that answers the question using only the tables and
    columns of the schema. Text values in the database are lowercase and without accents. Dates are
    TEXT in the format YYYY-MM-DD; use date('now') for today."""
    dbschema: str = dspy.InputField(desc="Database schema (SQLite DDL)")
    question: str = dspy.InputField(desc="Natural language question")

    sql_query: str = dspy.OutputField(desc="A single valid SQLite SELECT query, without markdown")


class ReliableSQLGenerator(dspy.Module):
    def __init__(self, modo=MODO_PADRAO):
        super().__init__()
        if modo not in ("rlm", "cot"):
            raise ValueError(f"modo invalido: {modo!r} (use 'rlm' ou 'cot')")
        self.modo = modo
        if modo == "rlm":
            self.generate_sql = dspy.RLM(TextToSQL, max_iters=8, max_llm_calls=10, tools=[testar_sql])
        else:
            self.generate_sql = dspy.ChainOfThought(TextToSQL)

    def forward(self, question):
        try:
            schema = servidor.schema()
            pred = self.generate_sql(dbschema=schema, question=question)
            pred.sql_query = limpar_sql(pred.sql_query)
        except Exception as e:
            return dspy.Prediction(sql_query="", erro=f"falha ao gerar a SQL: {e}")

        pred.erro = validar_sql(schema, pred.sql_query)
        return pred


def carregar_generator(modo=MODO_PADRAO, usar_otimizado=True):
    generator = ReliableSQLGenerator(modo)
    caminho = caminho_programa_otimizado(modo)
    if usar_otimizado and os.path.exists(caminho):
        generator.load(caminho)
        print(f"Usando programa otimizado: {caminho}")
    return generator


class AgenteSQL:
    def __init__(self, generator):
        self.generator = generator

    def responder(self, pergunta):
        pred = self.generator(question=pergunta)
        print(f"[{self.generator.modo}] {pergunta!r} -> {pred.sql_query!r}")
        if pred.erro:
            return {"sql_query": pred.sql_query, "erro": pred.erro, "colunas": [], "linhas": []}
        try:
            colunas, linhas = servidor.consultar(pred.sql_query)
        except (sqlite3.Error, requests.RequestException) as e:
            return {"sql_query": pred.sql_query, "erro": str(e), "colunas": [], "linhas": []}
        return {"sql_query": pred.sql_query, "erro": None, "colunas": colunas, "linhas": linhas}

    @staticmethod
    def formatar(result, limite=20):
        if result["erro"]:
            return f"Nao consegui responder: {result['erro']}"

        linhas = result["linhas"]
        if not linhas:
            return "Nao encontrei nenhum resultado para essa pergunta."

        partes = []
        if len(result["colunas"]) > 1:
            partes.append(" | ".join(result["colunas"]))
        for linha in linhas[:limite]:
            partes.append(" | ".join(str(campo) for campo in linha))

        texto = "\n".join(partes)
        if len(linhas) > limite:
            texto += f"\n\n(mostrando {limite} de {len(linhas)} resultados)"
        return texto


def _exemplo(question, sql_esperado):
    return dspy.Example(question=question, sql_esperado=sql_esperado).with_inputs("question")


JOIN_PROD = "FROM produtos p JOIN departamentos d ON p.departamento_id = d.id"
JOIN_EST = "FROM estoque e JOIN produtos p ON e.produto_id = p.id JOIN departamentos d ON p.departamento_id = d.id"

DATASET = [
    _exemplo("quantos departamentos existem?", "SELECT COUNT(*) FROM departamentos"),
    _exemplo("qual o preco do sabonete?", "SELECT preco FROM produtos WHERE nome = 'sabonete'"),
    _exemplo("quais produtos sao do departamento bebidas?", f"SELECT p.nome {JOIN_PROD} WHERE d.nome = 'bebidas'"),
    _exemplo("quantos produtos existem no departamento alimentos?",
             f"SELECT COUNT(*) {JOIN_PROD} WHERE d.nome = 'alimentos'"),
    _exemplo("qual produto tem o maior preco?", "SELECT nome FROM produtos ORDER BY preco DESC LIMIT 1"),
    _exemplo("qual a quantidade total em estoque do produto agua?",
             "SELECT SUM(e.quantidade) FROM estoque e JOIN produtos p ON e.produto_id = p.id WHERE p.nome = 'agua'"),
    _exemplo("liste os nomes de todos os produtos", "SELECT nome FROM produtos"),
    _exemplo("quantos lotes de estoque estao vencidos?",
             "SELECT COUNT(*) FROM estoque WHERE data_validade < date('now')"),
    _exemplo("em qual departamento fica o shampoo?", f"SELECT d.nome {JOIN_PROD} WHERE p.nome = 'shampoo'"),
    _exemplo("quais produtos custam menos de 5 reais?", "SELECT nome FROM produtos WHERE preco < 5"),
    _exemplo("qual o preco medio dos produtos de laticinios?",
             f"SELECT AVG(p.preco) {JOIN_PROD} WHERE d.nome = 'laticinios'"),
    _exemplo("quantos produtos cada departamento tem?", f"SELECT d.nome, COUNT(p.id) {JOIN_PROD} GROUP BY d.nome"),
    _exemplo("qual a data de validade do lote L003?", "SELECT data_validade FROM estoque WHERE lote = 'L003'"),
    _exemplo("quais produtos tem algum lote vencido?",
             f"SELECT DISTINCT p.nome {JOIN_EST} WHERE e.data_validade < date('now')"),
    _exemplo("qual lote de leite vence primeiro?",
             f"SELECT e.lote {JOIN_EST} WHERE p.nome = 'leite' ORDER BY e.data_validade LIMIT 1"),
    _exemplo("qual a quantidade total de itens em estoque?", "SELECT SUM(quantidade) FROM estoque"),
    _exemplo("quais lotes vencem nos proximos 30 dias?",
             "SELECT lote FROM estoque WHERE data_validade BETWEEN date('now') AND date('now', '+30 days')"),
    _exemplo("qual departamento tem mais unidades em estoque?",
             f"SELECT d.nome {JOIN_EST} GROUP BY d.nome ORDER BY SUM(e.quantidade) DESC LIMIT 1"),
    _exemplo("qual o valor total em reais do estoque de bebidas?",
             f"SELECT SUM(e.quantidade * p.preco) {JOIN_EST} WHERE d.nome = 'bebidas'"),
    _exemplo("quais produtos tem lotes fabricados em 2025?",
             f"SELECT DISTINCT p.nome {JOIN_EST} WHERE strftime('%Y', e.data_fabricacao) = '2025'"),
    _exemplo("quantos lotes de arroz existem?",
             "SELECT COUNT(*) FROM estoque e JOIN produtos p ON e.produto_id = p.id WHERE p.nome = 'arroz'"),
    _exemplo("qual o produto mais barato do departamento higiene?",
             f"SELECT p.nome {JOIN_PROD} WHERE d.nome = 'higiene' ORDER BY p.preco LIMIT 1"),
    _exemplo("quais produtos tem mais de 100 unidades em estoque somando todos os lotes?",
             f"SELECT p.nome {JOIN_EST} GROUP BY p.nome HAVING SUM(e.quantidade) > 100"),
    _exemplo("qual o preco da cerveja?", "SELECT preco FROM produtos WHERE nome = 'cerveja'"),
]


def dividir_dataset(dataset=DATASET, semente=0):
    exemplos = list(dataset)
    random.Random(semente).shuffle(exemplos)
    n = len(exemplos)
    corte_treino, corte_val = n // 2, n // 2 + n // 4
    return exemplos[:corte_treino], exemplos[corte_treino:corte_val], exemplos[corte_val:]


def _normalizar(linhas):
    def celula(v):
        return round(v, 2) if isinstance(v, float) else v
    return sorted((tuple(celula(v) for v in linha) for linha in linhas), key=repr)


def _resumo(linhas, limite=5):
    texto = repr(linhas[:limite])
    return texto + (f" ... ({len(linhas)} linhas)" if len(linhas) > limite else "")


def comparar(gold, pred):
    if pred.erro is not None:
        return 0.0, (f"A SQL gerada e invalida: {pred.erro}. SQL gerada: {pred.sql_query!r}. "
                     f"Uma SQL correta seria: {gold.sql_esperado!r}.")
    try:
        _, esperado = servidor.consultar(gold.sql_esperado)
    except Exception as e:
        return 0.0, f"(problema no exemplo de referencia: {e})"
    try:
        _, gerado = servidor.consultar(pred.sql_query)
    except Exception as e:
        return 0.0, f"A SQL gerada falhou ao executar no banco: {e}. SQL gerada: {pred.sql_query!r}."

    if _normalizar(gerado) == _normalizar(esperado):
        return 1.0, "Correto: a SQL retornou exatamente o resultado esperado."

    return 0.0, (f"Resultado errado. Pergunta: {gold.question!r}. SQL gerada: {pred.sql_query!r} "
                 f"retornou {_resumo(gerado)}, mas o esperado era {_resumo(esperado)} "
                 f"(ex.: {gold.sql_esperado!r}). Retorne somente as colunas pedidas; "
                 f"os nomes no banco estao em minusculo e sem acento.")


def resultado_correto(gold, pred, trace=None, pred_name=None, pred_trace=None, program_trace=None):
    nota, feedback = comparar(gold, pred)
    return dspy.Prediction(score=nota, feedback=feedback)


def avaliar(generator, dataset, nome="avaliacao", salvar=True):
    servidor.schema()
    detalhes, acertos = [], 0.0
    for exemplo in dataset:
        pred = generator(question=exemplo.question)
        nota, feedback = comparar(exemplo, pred)
        acertos += nota
        detalhes.append({"pergunta": exemplo.question, "sql_gerada": pred.sql_query,
                         "sql_esperada": exemplo.sql_esperado, "acertou": nota == 1.0, "feedback": feedback})
        print(f"{'OK  ' if nota == 1.0 else 'ERRO'} | {exemplo.question}")
        if nota < 1.0:
            print(f"      {feedback}")

    taxa = acertos / len(dataset)
    print(f"\n[{nome}] Acertos: {int(acertos)}/{len(dataset)} ({taxa:.0%})\n")
    if salvar:
        caminho = os.path.join(BASE_DIR, f"{nome}.json")
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"nome": nome, "acertos": int(acertos), "total": len(dataset), "taxa": taxa,
                       "detalhes": detalhes}, f, ensure_ascii=False, indent=2)
        print(f"Resultado salvo em {caminho}")
    return taxa


def otimizar(modo=MODO_PADRAO):
    task_lm = criar_lm()
    dspy.configure(lm=task_lm)

    reflection_model = os.environ.get("REFLECTION_MODEL")
    reflection_lm = (
        dspy.LM(reflection_model, api_base=os.environ.get("REFLECTION_API_BASE", "http://localhost:1337/v1"),
                api_key=os.environ.get("REFLECTION_API_KEY", "not-needed"), temperature=1.0, max_tokens=8000)
        if reflection_model else task_lm
    )

    trainset, valset, testset = dividir_dataset()
    generator = ReliableSQLGenerator(modo)

    print(f"=== [{modo}] Antes da otimizacao (conjunto de teste) ===")
    taxa_antes = avaliar(generator, testset, nome=f"avaliacao_{modo}_base")

    otimizador = dspy.GEPA(
        metric=resultado_correto,
        auto="light",
        reflection_lm=reflection_lm,
        num_threads=1,
        track_stats=True,
        log_dir=os.path.join(BASE_DIR, f"gepa_logs_{modo}"),
    )
    otimizado = otimizador.compile(generator, trainset=trainset, valset=valset)

    print(f"=== [{modo}] Depois da otimizacao (conjunto de teste) ===")
    taxa_depois = avaliar(otimizado, testset, nome=f"avaliacao_{modo}_otimizado")

    caminho = caminho_programa_otimizado(modo)
    otimizado.save(caminho)
    print(f"Programa otimizado salvo em {caminho}")
    print(f"Teste: {taxa_antes:.0%} -> {taxa_depois:.0%}")
    for nome, predictor in otimizado.named_predictors():
        print(f"\nNova instrucao de {nome}:\n{predictor.signature.instructions}")
    return otimizado


class BotTelegram:
    def __init__(self, agente, token, modelo_whisper="tiny"):
        import telebot

        self.agente = agente
        self.bot = telebot.TeleBot(token)
        self.nome_modelo_whisper = modelo_whisper
        self._whisper = None
        self._registrar_handlers()

    def transcrever(self, caminho_audio):
        if self._whisper is None:
            import whisper
            self._whisper = whisper.load_model(self.nome_modelo_whisper)
        return self._whisper.transcribe(caminho_audio)["text"]

    def _responder(self, message, pergunta):
        try:
            texto = self.agente.formatar(self.agente.responder(pergunta))
        except Exception as e:
            texto = f"Nao consegui responder: {e}"
        self.bot.reply_to(message, texto)

    def _registrar_handlers(self):
        bot = self.bot

        @bot.message_handler(commands=['start', 'help'])
        def ajuda(message):
            bot.reply_to(
                message,
                "Oi! Faca uma pergunta (por texto ou audio) sobre a base de lojas e eu consulto pra voce.\n\n"
                "Exemplos:\n"
                "- Em qual departamento fica o sabonete?\n"
                "- Quais produtos existem no departamento de bebidas?\n"
                "- Quais lotes vencem nos proximos 30 dias?"
            )

        @bot.message_handler(content_types=['voice'])
        def voz(message):
            try:
                arquivo = bot.get_file(message.voice.file_id)
                with tempfile.NamedTemporaryFile(suffix=".ogg") as tmp:
                    tmp.write(bot.download_file(arquivo.file_path))
                    tmp.flush()
                    pergunta = self.transcrever(tmp.name)
            except Exception as e:
                bot.reply_to(message, f"Nao consegui entender o audio: {e}")
                return
            self._responder(message, pergunta)

        @bot.message_handler(func=lambda message: True)
        def texto(message):
            self._responder(message, message.text)

    def rodar(self):
        self.bot.infinity_polling(timeout=30, long_polling_timeout=20)


def main():
    parser = argparse.ArgumentParser(description="Agente text-to-SQL (Telegram + voz) sobre o server.py")
    parser.add_argument("--modo", choices=["rlm", "cot"], default=MODO_PADRAO,
                        help="gerador de SQL: rlm (Recursive Language Model, padrao) ou cot (Chain of Thought)")
    acao = parser.add_mutually_exclusive_group()
    acao.add_argument("--otimizar", action="store_true", help="roda o GEPA e salva o programa otimizado")
    acao.add_argument("--avaliar", action="store_true", help="mede a acuracia no conjunto de teste")
    acao.add_argument("--pergunta", help="responde uma pergunta no terminal, sem Telegram")
    parser.add_argument("--base", action="store_true", help="ignora o programa otimizado (com --avaliar/--pergunta)")
    parser.add_argument("--completo", action="store_true", help="com --avaliar, usa o dataset inteiro")
    args = parser.parse_args()

    if args.otimizar:
        otimizar(args.modo)
        return

    dspy.configure(lm=criar_lm())
    generator = carregar_generator(args.modo, usar_otimizado=not args.base)

    if args.avaliar:
        dataset = DATASET if args.completo else dividir_dataset()[2]
        variante = "base" if args.base or not os.path.exists(caminho_programa_otimizado(args.modo)) else "otimizado"
        avaliar(generator, dataset, nome=f"avaliacao_{args.modo}_{variante}{'_completo' if args.completo else ''}")
        return

    agente = AgenteSQL(generator)
    if args.pergunta:
        print(agente.formatar(agente.responder(args.pergunta)))
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Defina a variavel de ambiente TELEGRAM_BOT_TOKEN antes de rodar o bot.")
    BotTelegram(agente, token).rodar()


if __name__ == "__main__":
    main()
