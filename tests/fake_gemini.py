"""Cliente Gemini falso: devolve respostas roteirizadas (sem rede)."""

from google.genai import types


def chamada(nome: str, **args) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=nome, args=args))


def texto(t: str) -> types.Part:
    return types.Part.from_text(text=t)


class _Resp:
    def __init__(self, parts):
        self.candidates = [types.Candidate(content=types.Content(role="model", parts=parts))]


class FakeClient:
    def __init__(self, roteiro):
        self.roteiro = list(roteiro)  # lista de listas de Parts (uma por chamada ao modelo)
        self.chamadas_modelo = []
        self.modelos_chamados = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.chamadas_modelo.append(list(contents))
        self.modelos_chamados.append(model)
        if not self.roteiro:
            raise AssertionError("roteiro esgotado")
        item = self.roteiro.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)
