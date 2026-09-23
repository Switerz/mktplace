"""Gate AVH-5B — sessao de navegador para a captura read-only da Avoe.

Este e' o unico ponto do repositorio que abre um navegador. Ele nao sabe
usuario, nao sabe senha, e nao tem como saber: nao le' ambiente, nao le'
arquivo de configuracao, e os unicos argumentos que aceita sao a URL da tela
de login e o diretorio de saida.

COMO FUNCIONA
-------------
1. Abre um Chromium COM INTERFACE, em contexto efemero — sem perfil no disco,
   sem `storage_state`, sem `user_data_dir`.
2. Instala o guard de somente-leitura JA na abertura, ainda desarmado: uma
   unica requisicao de autenticacao pode passar, e nada mais que mute.
3. Navega ate a tela de login e PARA. Quem digita a credencial e' a pessoa,
   na tela. Este processo nao toca no teclado e nao le' os campos.
4. Espera a sessao autenticada aparecer. Quando aparece, ARMA o guard: dali em
   diante nem a autenticacao passa.
5. Coleta pelo `snapshot_collect`, que le' pela sessao ja aberta.
6. Encerra a sessao e descarta o contexto inteiro.

O QUE ELE NUNCA FAZ
-------------------
Nao preenche credencial, nao repete login, nao salva cookie nem token, nao
grava dentro do repositorio, e nao imprime uma linha de dado sequer.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pipelines.avoe.snapshot_collect import (
    BrowserPageReader,
    SnapshotCollectError,
    collect_snapshot,
    install_read_only_guard,
)

# Sonda de prontidao. Roda DENTRO da pagina e devolve UM booleano: a aplicacao
# esta autenticada e pronta para leitura? Nao devolve identificador, nao
# devolve perfil, e nao devolve pedaco nenhum da sessao.
#
# A tela de login nao serve como criterio: ela pode estar oculta antes de o app
# decidir o que mostrar, e foi assim que a primeira tentativa deste gate deu
# "sucesso" com zero linha. O criterio e' a sessao que de fato AUTORIZA a
# leitura — se ela nao existir, o navegador le' com a chave publica e a fonte
# devolve vazio sem erro.
#
# A sonda devolve UMA de tres palavras fixas. Nenhum identificador, nenhum
# perfil, nenhum pedaco de sessao atravessa.
PRONTO = "pronto"
SEM_SESSAO_DE_LEITURA = "sem_sessao_de_leitura"
AGUARDANDO = "aguardando"

_JS_PRONTO = """
async () => {
  if (typeof supaClient === 'undefined' || !supaClient.auth) return 'aguardando';
  let temSessao = false;
  try {
    const r = await supaClient.auth.getSession();
    temSessao = !!(r && r.data && r.data.session);
  } catch (e) { temSessao = false; }
  if (temSessao) return 'pronto';
  const tela = document.getElementById('login-screen');
  const entrou = tela ? tela.classList.contains('hidden') : false;
  return entrou ? 'sem_sessao_de_leitura' : 'aguardando';
}
"""

# Encerramento de sessao. Tambem devolve booleano e nada mais.
_JS_SAIR = """
async () => {
  try {
    if (typeof supaClient !== 'undefined' && supaClient.auth) {
      await supaClient.auth.signOut();
    }
  } catch (e) { /* a sessao morre junto com o contexto de qualquer jeito */ }
  try { localStorage.clear(); sessionStorage.clear(); } catch (e) {}
  return true;
}
"""


def espera_sessao(page, timeout_s: int, intervalo_s: float = 2.0) -> None:
    """Aguarda o operador concluir o login. Nao interfere na tela."""
    limite = time.monotonic() + timeout_s
    while time.monotonic() < limite:
        estado = AGUARDANDO
        try:
            estado = page.evaluate(_JS_PRONTO)
        except Exception:
            pass  # navegacao em curso; tenta de novo
        if estado == PRONTO:
            return
        if estado == SEM_SESSAO_DE_LEITURA:
            raise SnapshotCollectError(
                "a tela entrou, mas a conta nao tem sessao que autorize a "
                "leitura das tabelas. Nesse estado o navegador consulta a "
                "fonte com a chave publica e ela responde vazio SEM erro — "
                "o que produziria um snapshot vazio de aparencia valida. A "
                "captura para aqui: e' pendencia de permissao na origem, nao "
                "de codigo."
            )
        time.sleep(intervalo_s)
    raise SnapshotCollectError(
        f"sessao de leitura nao apareceu em {timeout_s}s. Se houve MFA, "
        f"CAPTCHA ou erro de login, a captura para aqui para intervencao "
        f"humana — e nenhuma segunda tentativa e' feita."
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipelines.avoe.snapshot_collect_session",
        description=(
            "Abre uma sessao de navegador para captura read-only da Avoe. "
            "A credencial e' digitada na tela pelo operador e nunca passa "
            "por aqui."
        ),
    )
    p.add_argument("--url", required=True,
                   help="URL da tela de login. Nao e' versionada no repositorio.")
    p.add_argument("--out-dir", required=True, metavar="DIR",
                   help="Diretorio de captura, obrigatoriamente fora do repositorio.")
    p.add_argument("--auth-path", action="append", default=None, metavar="TRECHO",
                   help=(
                       "Trecho de caminho que identifica um passo de "
                       "autenticacao da aplicacao (repetivel). Cada passo "
                       "declarado passa UMA vez antes do armamento; repetir o "
                       "mesmo passo e' segunda tentativa e fica bloqueado. "
                       "Padrao: a rota de auth do Supabase."
                   ))
    p.add_argument("--login-timeout-seconds", type=int, default=300,
                   help="Quanto esperar pelo login humano. Padrao: 300.")
    p.add_argument("--page-size", type=int, default=None,
                   help="Tamanho de pagina. Padrao: o teto da fonte.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("FALHA: playwright nao instalado nesta maquina.", file=sys.stderr)
        return 3

    from pipelines.avoe.snapshot_collect import (
        DEFAULT_AUTH_PATHS,
        SOURCE_PAGE_SIZE,
        assert_fora_do_repositorio,
    )

    destino = Path(args.out_dir)
    try:
        assert_fora_do_repositorio(destino)
    except SnapshotCollectError as exc:
        print(f"FALHA: {exc}", file=sys.stderr)
        return 2

    page_size = args.page_size or SOURCE_PAGE_SIZE

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=False)
        # Contexto efemero: nada de `storage_state`, nada de `user_data_dir`.
        contexto = navegador.new_context()
        rotas_auth = tuple(args.auth_path) if args.auth_path else DEFAULT_AUTH_PATHS

        def avisa_bloqueio(metodo: str, host: str, caminho: str) -> None:
            # Quem opera precisa ver o bloqueio na hora: um login que falha
            # por rota nao declarada e' indistinguivel de senha errada na tela.
            print(f"  [guard] BLOQUEADO {metodo} {host}{caminho}", flush=True)

        guard = install_read_only_guard(contexto, auth_paths=rotas_auth,
                                        on_block=avisa_bloqueio)
        page = contexto.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            print("Navegador aberto. Faca o login na tela. Uma unica tentativa "
                  "e' permitida.", flush=True)

            espera_sessao(page, args.login_timeout_seconds)
            guard.arm()
            print("Sessao autenticada. Guard ARMADO: nenhuma escrita sai daqui.",
                  flush=True)

            resultado = collect_snapshot(
                BrowserPageReader(page), destino,
                blocked_mutations=guard.count, page_size=page_size,
            )
        except SnapshotCollectError as exc:
            print(f"FALHA DE COLETA: {exc}", file=sys.stderr)
            return 2
        finally:
            try:
                page.evaluate(_JS_SAIR)
            except Exception:
                pass
            contexto.close()
            navegador.close()

    print("")
    for linha in resultado.resumo_sanitizado():
        print(linha)
    print("")
    print(f"tentativas de login permitidas pelo guard: {guard.auth_attempts}")
    print(f"mutacoes bloqueadas apos o armamento      : {guard.count}")
    print("ESCRITA NA AVOE: nenhuma. PUBLICACAO: nenhuma — a captura e' arquivo.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
