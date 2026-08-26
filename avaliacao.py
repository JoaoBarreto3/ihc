import sqlite3

import dspy

import db

DATASET = [
    dspy.Example(
        question="quantos departamentos existem?",
        sql_esperado="SELECT COUNT(*) FROM departamentos",
    ).with_inputs("question"),
    dspy.Example(
        question="qual o preco do sabonete?",
        sql_esperado="SELECT preco FROM produtos WHERE nome = 'sabonete'",
    ).with_inputs("question"),
    dspy.Example(
        question="quais produtos sao do departamento bebidas?",
        sql_esperado="""
            SELECT produtos.nome FROM produtos
            JOIN departamentos ON produtos.departamento_id = departamentos.id
            WHERE departamentos.nome = 'bebidas'
        """,
    ).with_inputs("question"),
    dspy.Example(
        question="quantos produtos existem no departamento alimentos?",
        sql_esperado="""
            SELECT COUNT(*) FROM produtos
            JOIN departamentos ON produtos.departamento_id = departamentos.id
            WHERE departamentos.nome = 'alimentos'
        """,
    ).with_inputs("question"),
    dspy.Example(
        question="qual produto tem o maior preco?",
        sql_esperado="SELECT nome FROM produtos ORDER BY preco DESC LIMIT 1",
    ).with_inputs("question"),
    dspy.Example(
        question="qual a quantidade em estoque do produto agua?",
        sql_esperado="""
            SELECT estoque.quantidade FROM estoque
            JOIN produtos ON estoque.produto_id = produtos.id
            WHERE produtos.nome = 'agua'
        """,
    ).with_inputs("question"),
    dspy.Example(
        question="liste os nomes de todos os produtos",
        sql_esperado="SELECT nome FROM produtos",
    ).with_inputs("question"),
    dspy.Example(
        question="quantos lotes de estoque estao vencidos?",
        sql_esperado="SELECT COUNT(*) FROM estoque WHERE data_validade < date('now')",
    ).with_inputs("question"),
]


def resultado_correto(gold, pred, trace=None, pred_name=None, pred_trace=None, program_trace=None):
    if pred.erro is not None:
        return 0.0

    conn = sqlite3.connect(db.DB_PATH)
    try:
        gerado = set(conn.execute(pred.sql_query).fetchall())
        esperado = set(conn.execute(gold.sql_esperado).fetchall())
    except sqlite3.Error:
        return 0.0
    finally:
        conn.close()

    return 1.0 if gerado == esperado else 0.0


def avaliar(generator, dataset=DATASET):
    acertos = 0
    for exemplo in dataset:
        pred = generator(exemplo.question)
        nota = resultado_correto(exemplo, pred)
        acertos += nota
        status = "OK  " if nota == 1.0 else "ERRO"
        print(f"{status} | {exemplo.question}")
        print(f"      sql gerada: {pred.sql_query!r}")
        if pred.erro is not None:
            print(f"      erro: {pred.erro}")

    taxa = acertos / len(dataset)
    print(f"\nAcertos: {int(acertos)}/{len(dataset)} ({taxa:.0%})")
    return taxa


if __name__ == "__main__":
    from bot import ReliableSQLGenerator

    lm = dspy.LM('openai/gemma-4-E2B-it-IQ4_XS', api_base='http://localhost:1337/v1', api_key='not-needed')
    dspy.configure(lm=lm)

    generator = ReliableSQLGenerator()
    avaliar(generator)
