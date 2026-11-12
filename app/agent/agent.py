"""Loop do agente: Gemini com function calling MANUAL.

Fluxo de cada mensagem do usuário:
  1. guardrail de entrada (código, sem LLM);
  2. registra um novo "turno do usuário" na conta (base da regra de confirmação);
  3. chama o modelo; se ele pedir ferramentas, executamos em Python e devolvemos
     o resultado; repetimos até ele responder em texto (máx. N iterações);
  4. guardrail de SAÍDA: todo valor em R$ no texto precisa vir das ferramentas
     ou da conta; se não, a resposta é trocada por uma mensagem determinística;
  5. o frontend recebe também o snapshot da conta direto do backend, então o
     saldo exibido nunca depende do texto gerado pelo modelo.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from app.agent import output_guard
from app.agent.guardrails import validar_entrada
from app.agent.prompt import SYSTEM_PROMPT
from app.agent.tools import FERRAMENTAS, FERRAMENTAS_DE_ESCRITA, executar_ferramenta
from app.bank.sessions import Sessao
from app.config import (
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_MODELOS_RESERVA, MAX_HISTORICO, MAX_ITERACOES_FERRAMENTAS,
    TIMEOUT_MODELO_S,
)

log = logging.getLogger("bank_agent")

ESPERA_ENTRE_RODADAS = 2  # segundos; só se TODOS os modelos falharem na 1ª rodada
RODADAS = 2
_TRANSITORIOS = ("429", "RESOURCE_EXHAUSTED", "500", "INTERNAL", "503", "UNAVAILABLE",
                 "504", "DEADLINE_EXCEEDED", "timed out", "timeout", "Timeout")

RESPOSTA_PADRAO = "Posso ajudar você a consultar o saldo, fazer um depósito ou uma transferência. O que deseja?"


@dataclass
class ChamadaFerramenta:
    nome: str
    args: dict
    resultado: dict


@dataclass
class RespostaAgente:
    texto: str
    chamadas: list[ChamadaFerramenta] = field(default_factory=list)
    erro: str | None = None
    # None = fluxo normal | "ENTRADA_INVALIDA" | "ERRO_API" | "LIMITE_ITERACOES"
    tipo: str | None = None
    # trechos com valores que a IA "inventou" e foram barrados pelo guardrail de saída
    guardrail_saida: list[str] = field(default_factory=list)
    modelo: str | None = None  # modelo que efetivamente respondeu (pode ser o de reserva)


class BankAgent:
    def __init__(self, client: object | None = None, model: str = GEMINI_MODEL,
                 reservas: tuple[str, ...] = GEMINI_MODELOS_RESERVA) -> None:
        self._client = client
        self.model = model
        self.modelos = list(dict.fromkeys([model, *reservas]))  # principal primeiro, sem duplicatas
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=FERRAMENTAS,
            temperature=0,
            # Desligamos a execução automática do SDK: NÓS executamos as ferramentas.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.AUTO)
            ),
        )

    @property
    def client(self):
        if self._client is None:
            if not GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY não configurada. Copie .env.example para .env.")
            self._client = genai.Client(
                api_key=GEMINI_API_KEY,
                http_options=types.HttpOptions(
                    timeout=TIMEOUT_MODELO_S * 1000,
                    # Desliga a retentativa interna do SDK (5x com backoff de até 60 s):
                    # quem decide repetir ou trocar de modelo é _gerar_com_fallback.
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    # ------------------------------------------------------------------
    def responder(self, sessao: Sessao, mensagem: str) -> RespostaAgente:
        erro_entrada = validar_entrada(mensagem)
        if erro_entrada:
            return RespostaAgente(texto=erro_entrada, tipo="ENTRADA_INVALIDA")

        with sessao.lock:
            conta = sessao.conta
            conta.novo_turno_usuario()
            historico = sessao.historico
            inicio_turno = len(historico)
            historico.append(types.Content(role="user", parts=[types.Part.from_text(text=mensagem.strip())]))
            chamadas: list[ChamadaFerramenta] = []
            modelo_usado = None

            try:
                for _ in range(MAX_ITERACOES_FERRAMENTAS + 1):
                    resp, modelo_usado = self._gerar_com_fallback(historico)
                    conteudo = _conteudo_do_modelo(resp)
                    function_calls = [p.function_call for p in conteudo.parts or [] if p.function_call]

                    if not function_calls:
                        texto = _texto(conteudo) or RESPOSTA_PADRAO
                        texto, barrados = self._aplicar_guardrail_saida(sessao, mensagem, texto, chamadas)
                        if barrados:  # o histórico guarda a versão SEGURA, nunca o texto alucinado
                            conteudo = types.Content(role="model", parts=[types.Part.from_text(text=texto)])
                        historico.append(conteudo)
                        self._aparar_historico(sessao)
                        return RespostaAgente(texto=texto, chamadas=chamadas, guardrail_saida=barrados,
                                              modelo=modelo_usado)

                    if len(chamadas) >= MAX_ITERACOES_FERRAMENTAS:
                        break  # limite de segurança: evita loops de ferramentas

                    historico.append(conteudo)
                    partes_resposta = []
                    for fc in function_calls:
                        args = dict(fc.args or {})
                        resultado = executar_ferramenta(conta, fc.name, args)
                        log.info("tool %s(%s) -> %s", fc.name, json.dumps(args, ensure_ascii=False), resultado)
                        chamadas.append(ChamadaFerramenta(fc.name, args, resultado))
                        partes_resposta.append(
                            types.Part.from_function_response(name=fc.name, response={"result": resultado})
                        )
                    historico.append(types.Content(role="user", parts=partes_resposta))

                # Excedeu o limite de iterações
                del historico[inicio_turno:]
                return RespostaAgente(
                    texto=_mensagem_falha(chamadas, conta, "Não consegui concluir seu pedido."),
                    chamadas=chamadas,
                    erro="LIMITE_ITERACOES",
                    tipo="LIMITE_ITERACOES",
                    modelo=modelo_usado,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("falha ao chamar o modelo")
                # Desfaz o turno no HISTÓRICO (o estado da conta só muda via ferramentas,
                # e informamos ao usuário exatamente o que foi executado).
                del historico[inicio_turno:]
                motivo = "O serviço de IA está temporariamente indisponível."
                if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                    motivo = "O limite de uso gratuito da IA foi atingido no momento. Aguarde alguns segundos."
                return RespostaAgente(
                    texto=_mensagem_falha(chamadas, conta, motivo), chamadas=chamadas, erro=str(exc), tipo="ERRO_API",
                    modelo=modelo_usado,
                )

    def _aplicar_guardrail_saida(self, sessao: Sessao, mensagem: str, texto: str,
                                 chamadas: list[ChamadaFerramenta]) -> tuple[str, list[str]]:
        conta = sessao.conta
        resultados = [c.resultado for c in chamadas]
        # Valores confiáveis acumulados na sessão (resultados de ferramentas e mensagens do usuário)
        sessao.valores_confiaveis |= output_guard.valores_permitidos(conta, resultados, mensagem)
        barrados = output_guard.verificar(
            texto, sessao.valores_confiaveis, output_guard.saldos_validos(conta, resultados)
        )
        if not barrados:
            return texto, []
        log.warning("guardrail de saída barrou valores %s na resposta: %r", barrados, texto[:200])
        return output_guard.resposta_segura(conta, [(c.nome, c.resultado) for c in chamadas]), barrados

    def _gerar_com_fallback(self, historico: list):
        """Chama o modelo principal; em erro transitório (429/503/timeout) troca NA HORA
        para o modelo de reserva, sem esperar. Só se todos falharem fazemos uma pausa
        curta e uma última rodada. Chamar o modelo é idempotente (não executa
        ferramentas), então repetir é seguro. Devolve (resposta, modelo usado)."""
        ultimo_erro: Exception | None = None
        for rodada in range(RODADAS):
            if rodada:
                time.sleep(ESPERA_ENTRE_RODADAS)
            for modelo in self.modelos:
                inicio = time.perf_counter()
                try:
                    resp = self.client.models.generate_content(model=modelo, contents=historico, config=self.config)
                    log.info("modelo %s respondeu em %.2f s", modelo, time.perf_counter() - inicio)
                    return resp, modelo
                except Exception as exc:  # noqa: BLE001
                    if not _transitorio(exc):
                        raise
                    log.warning("modelo %s indisponível após %.2f s (%s); tentando o próximo",
                                modelo, time.perf_counter() - inicio, str(exc)[:60])
                    ultimo_erro = exc
        raise ultimo_erro

    def _aparar_historico(self, sessao: Sessao) -> None:
        """Mantém o contexto enxuto, sempre começando numa mensagem de texto do usuário."""
        h = sessao.historico
        if len(h) <= MAX_HISTORICO:
            return
        corte = len(h) - MAX_HISTORICO
        while corte < len(h) and not (h[corte].role == "user" and _texto(h[corte])):
            corte += 1
        del h[:corte]


# ---------------------------------------------------------------------------
def _transitorio(exc: Exception) -> bool:
    return any(c in f"{type(exc).__name__} {exc}" for c in _TRANSITORIOS)


def _conteudo_do_modelo(resp) -> types.Content:
    if not resp.candidates or resp.candidates[0].content is None:
        return types.Content(role="model", parts=[types.Part.from_text(text=RESPOSTA_PADRAO)])
    conteudo = resp.candidates[0].content
    if not conteudo.parts:
        conteudo.parts = [types.Part.from_text(text=RESPOSTA_PADRAO)]
    conteudo.role = "model"
    return conteudo


def _texto(conteudo: types.Content) -> str:
    return "".join(p.text for p in conteudo.parts or [] if p.text and not p.thought).strip()


def _mensagem_falha(chamadas: list[ChamadaFerramenta], conta, motivo: str) -> str:
    executou_escrita = any(c.resultado.get("ok") and c.nome in FERRAMENTAS_DE_ESCRITA for c in chamadas)
    texto = f"{motivo} Tente novamente."
    if executou_escrita:
        saldo = conta.consultar_saldo()["saldo"]  # valor vem da conta, não do modelo
        texto += f" Atenção: operações já concluídas nesta mensagem foram mantidas. Saldo atual: {saldo}."
    return texto
