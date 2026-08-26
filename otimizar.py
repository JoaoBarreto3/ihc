import dspy

from avaliacao import DATASET, resultado_correto
from bot import ReliableSQLGenerator

CAMINHO_PROGRAMA_OTIMIZADO = "generator_otimizado.json"


def dividir_dataset(dataset, proporcao_treino=0.75):
    corte = max(1, int(len(dataset) * proporcao_treino))
    return dataset[:corte], dataset[corte:]


def otimizar(lm):
    dspy.configure(lm=lm)

    generator = ReliableSQLGenerator()
    trainset, valset = dividir_dataset(DATASET)

    otimizador = dspy.GEPA(
        metric=resultado_correto,
        auto="light",
        reflection_lm=lm,
    )
    generator_otimizado = otimizador.compile(
        generator,
        trainset=trainset,
        valset=valset or trainset,
    )
    generator_otimizado.save(CAMINHO_PROGRAMA_OTIMIZADO)
    print(f"Programa otimizado salvo em {CAMINHO_PROGRAMA_OTIMIZADO}")
    return generator_otimizado


if __name__ == "__main__":
    lm = dspy.LM('openai/gemma-4-E2B-it-IQ4_XS', api_base='http://localhost:1337/v1', api_key='not-needed')
    otimizar(lm)
