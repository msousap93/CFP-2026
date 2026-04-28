"""
Academia PF — Processador de PDFs
Monitora pastas de matérias, detecta PDFs novos e gera
resumos, flashcards e simulados via Claude API.

Como usar:
  python processar_pdfs.py              → processa tudo que for novo
  python processar_pdfs.py --watch      → fica monitorando em tempo real
  python processar_pdfs.py --forcar     → reprocessa tudo do zero
"""

import os
import sys
import json
import base64
import hashlib
import time
import argparse
from pathlib import Path
from datetime import datetime
import anthropic

# ─── CONFIGURAÇÃO ─────────────────────────────────────────────────────────────

# Raiz do projeto — ajuste se mover a pasta
BASE_DIR = Path(__file__).parent

# Onde estão as pastas de matérias
MATERIAS_DIR = BASE_DIR / "materias"

# Onde os dados gerados são salvos (lidos pela plataforma HTML)
DADOS_DIR = BASE_DIR / "dados"

# Arquivo que rastreia quais PDFs já foram processados
CACHE_FILE = DADOS_DIR / "cache_processados.json"

# Arquivo principal lido pela plataforma
DB_FILE = DADOS_DIR / "banco_dados.json"

# ─── CLIENTE ANTHROPIC ────────────────────────────────────────────────────────

client = anthropic.Anthropic()  # Lê ANTHROPIC_API_KEY do ambiente

# ─── UTILITÁRIOS ──────────────────────────────────────────────────────────────

def log(msg, tipo="INFO"):
    """Imprime mensagem com timestamp."""
    hora = datetime.now().strftime("%H:%M:%S")
    simbolos = {"INFO": "·", "OK": "✓", "ERRO": "✗", "PROC": "⟳", "NOVO": "★"}
    print(f"[{hora}] {simbolos.get(tipo, '·')} {msg}")

def hash_arquivo(caminho: Path) -> str:
    """Retorna hash MD5 de um arquivo para detectar mudanças."""
    h = hashlib.md5()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(8192), b""):
            h.update(bloco)
    return h.hexdigest()

def carregar_cache() -> dict:
    """Carrega o registro de arquivos já processados."""
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def salvar_cache(cache: dict):
    """Salva o registro de arquivos processados."""
    DADOS_DIR.mkdir(exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def carregar_banco() -> dict:
    """Carrega o banco de dados principal."""
    if DB_FILE.exists():
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "materias": {},
        "ultima_atualizacao": None,
        "total_flashcards": 0,
        "total_questoes": 0
    }

def salvar_banco(banco: dict):
    """Salva o banco de dados principal."""
    DADOS_DIR.mkdir(exist_ok=True)
    banco["ultima_atualizacao"] = datetime.now().isoformat()
    # Recalcula totais
    total_flash = sum(
        len(m.get("flashcards", []))
        for m in banco["materias"].values()
    )
    total_q = sum(
        len(m.get("questoes", []))
        for m in banco["materias"].values()
    )
    banco["total_flashcards"] = total_flash
    banco["total_questoes"] = total_q
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(banco, f, ensure_ascii=False, indent=2)
    log(f"Banco salvo — {total_flash} flashcards, {total_q} questões no total", "OK")

# ─── PROCESSAMENTO VIA CLAUDE ─────────────────────────────────────────────────

def pdf_para_base64(caminho: Path) -> str:
    """Converte PDF em base64 para enviar à API."""
    with open(caminho, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")

def processar_pdf_com_ia(caminho_pdf: Path, nome_materia: str) -> dict:
    """
    Envia o PDF ao Claude e solicita resumo, flashcards e questões.
    Retorna um dicionário com todo o conteúdo gerado.
    """
    log(f"Processando: {caminho_pdf.name}", "PROC")

    pdf_b64 = pdf_para_base64(caminho_pdf)

    prompt = f"""Você é um especialista em preparação para concursos da Polícia Federal Brasileira,
com foco no cargo de Escrivão. Analise este PDF da matéria "{nome_materia}" e gere:

1. RESUMO: Um resumo estruturado e completo do conteúdo, organizado por tópicos.
   Use markdown com títulos (##) e subtítulos (###). Seja denso e técnico.

2. FLASHCARDS: Exatamente 15 flashcards no formato pergunta/resposta,
   cobrindo os conceitos mais importantes e prováveis de cair em prova.

3. QUESTOES: Exatamente 10 questões de múltipla escolha (A/B/C/D/E),
   no estilo de concurso da PF, com gabarito e explicação detalhada.

Responda APENAS com um JSON válido, sem texto antes ou depois, neste formato exato:

{{
  "titulo": "título descritivo do conteúdo deste PDF",
  "resumo": "resumo em markdown aqui",
  "topicos_principais": ["topico1", "topico2", "topico3"],
  "flashcards": [
    {{
      "id": "fc_001",
      "pergunta": "pergunta aqui",
      "resposta": "resposta aqui",
      "dificuldade": "facil|medio|dificil",
      "vezes_errada": 0,
      "vezes_acertada": 0,
      "proxima_revisao": null
    }}
  ],
  "questoes": [
    {{
      "id": "q_001",
      "enunciado": "enunciado da questão",
      "alternativas": {{
        "A": "texto A",
        "B": "texto B",
        "C": "texto C",
        "D": "texto D",
        "E": "texto E"
      }},
      "gabarito": "A",
      "explicacao": "explicação detalhada do gabarito",
      "dificuldade": "facil|medio|dificil",
      "respondida": false,
      "acertou": null
    }}
  ]
}}"""

    resposta = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ],
            }
        ],
    )

    texto = resposta.content[0].text.strip()

    # Remove possíveis marcadores de código que o modelo possa ter inserido
    if texto.startswith("```"):
        linhas = texto.split("\n")
        texto = "\n".join(linhas[1:-1])

    dados = json.loads(texto)

    # Adiciona metadados
    dados["arquivo_origem"] = caminho_pdf.name
    dados["processado_em"] = datetime.now().isoformat()
    dados["tamanho_bytes"] = caminho_pdf.stat().st_size

    log(f"Gerado: {len(dados['flashcards'])} flashcards, {len(dados['questoes'])} questões", "OK")
    return dados

# ─── LÓGICA PRINCIPAL ─────────────────────────────────────────────────────────

def descobrir_materias() -> list[tuple[str, Path]]:
    """
    Descobre subpastas em materias/ e lista seus PDFs.
    Retorna lista de (nome_materia, caminho_pdf).
    """
    MATERIAS_DIR.mkdir(exist_ok=True)
    pares = []
    for pasta in sorted(MATERIAS_DIR.iterdir()):
        if pasta.is_dir() and not pasta.name.startswith("."):
            nome_materia = pasta.name
            for pdf in sorted(pasta.glob("*.pdf")):
                pares.append((nome_materia, pdf))
    return pares

def sincronizar(forcar=False):
    """
    Verifica todos os PDFs nas pastas de matérias e processa
    os que são novos ou foram modificados.
    """
    cache = carregar_cache()
    banco = carregar_banco()
    pares = descobrir_materias()

    if not pares:
        log("Nenhum PDF encontrado em materias/. Crie subpastas com PDFs.", "INFO")
        log(f"  Exemplo: {MATERIAS_DIR / 'Inquerito Policial' / 'Cap1.pdf'}", "INFO")
        return

    novos = 0
    for nome_materia, caminho_pdf in pares:
        chave = str(caminho_pdf.relative_to(BASE_DIR))
        hash_atual = hash_arquivo(caminho_pdf)

        ja_processado = (
            chave in cache and
            cache[chave].get("hash") == hash_atual and
            not forcar
        )

        if ja_processado:
            log(f"Sem mudanças: {caminho_pdf.name}", "INFO")
            continue

        log(f"Novo/modificado: {caminho_pdf.name}", "NOVO")

        try:
            conteudo = processar_pdf_com_ia(caminho_pdf, nome_materia)

            # Inicializa matéria no banco se não existir
            if nome_materia not in banco["materias"]:
                banco["materias"][nome_materia] = {
                    "nome": nome_materia,
                    "cor": _cor_para_materia(nome_materia),
                    "nota_atual": None,
                    "data_prova": None,
                    "flashcards": [],
                    "questoes": [],
                    "resumos": [],
                    "horas_estudadas": 0
                }

            materia = banco["materias"][nome_materia]

            # Adiciona resumo
            materia["resumos"].append({
                "titulo": conteudo["titulo"],
                "arquivo": caminho_pdf.name,
                "conteudo": conteudo["resumo"],
                "topicos": conteudo["topicos_principais"],
                "processado_em": conteudo["processado_em"]
            })

            # Adiciona flashcards (com IDs únicos)
            prefixo = nome_materia[:3].upper().replace(" ", "")
            offset = len(materia["flashcards"])
            for i, fc in enumerate(conteudo["flashcards"]):
                fc["id"] = f"{prefixo}_FC_{offset + i + 1:03d}"
                materia["flashcards"].append(fc)

            # Adiciona questões (com IDs únicos)
            offset_q = len(materia["questoes"])
            for i, q in enumerate(conteudo["questoes"]):
                q["id"] = f"{prefixo}_Q_{offset_q + i + 1:03d}"
                materia["questoes"].append(q)

            # Atualiza cache
            cache[chave] = {
                "hash": hash_atual,
                "processado_em": datetime.now().isoformat(),
                "titulo": conteudo["titulo"]
            }

            novos += 1

        except json.JSONDecodeError as e:
            log(f"Erro ao parsear resposta da IA em {caminho_pdf.name}: {e}", "ERRO")
        except Exception as e:
            log(f"Erro ao processar {caminho_pdf.name}: {e}", "ERRO")

    if novos > 0:
        salvar_banco(banco)
        salvar_cache(cache)
        log(f"Sincronização concluída — {novos} PDF(s) processado(s)", "OK")
    else:
        log("Tudo atualizado, nenhum PDF novo encontrado.", "OK")

def _cor_para_materia(nome: str) -> str:
    """Atribui uma cor fixa a cada matéria baseada no nome."""
    cores = [
        "#00d4ff", "#ff6b35", "#a855f7", "#00ff88",
        "#ffd700", "#ff3333", "#00bcd4", "#ff9800"
    ]
    idx = sum(ord(c) for c in nome) % len(cores)
    return cores[idx]

def modo_watch(intervalo=30):
    """Fica monitorando as pastas e processa automaticamente."""
    log(f"Modo watch ativo — verificando a cada {intervalo}s. Ctrl+C para parar.", "INFO")
    try:
        while True:
            sincronizar()
            time.sleep(intervalo)
    except KeyboardInterrupt:
        log("Watch encerrado pelo usuário.", "INFO")

# ─── SETUP INICIAL ────────────────────────────────────────────────────────────

def criar_estrutura():
    """Cria as pastas necessárias se não existirem."""
    MATERIAS_DIR.mkdir(exist_ok=True)
    DADOS_DIR.mkdir(exist_ok=True)

    # Cria pasta de exemplo
    exemplo = MATERIAS_DIR / "Inquerito Policial"
    exemplo.mkdir(exist_ok=True)

    # Cria README de instrução
    readme = BASE_DIR / "COMO_USAR.txt"
    if not readme.exists():
        readme.write_text("""Academia PF — Como usar
=======================

1. Coloque seus PDFs nas pastas dentro de "materias/"
   Exemplo:
     materias/
       Inquerito Policial/
         Capitulo_1.pdf
         Capitulo_2.pdf
       Inteligencia Policial/
         Modulo_1.pdf

2. Execute o processador:
   python processar_pdfs.py            → processa PDFs novos
   python processar_pdfs.py --watch    → monitora automaticamente
   python processar_pdfs.py --forcar   → reprocessa tudo

3. Abra plataforma.html no navegador.

A plataforma atualiza automaticamente ao recarregar a página.
""", encoding="utf-8")
    log("Estrutura de pastas criada.", "OK")

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Academia PF — Processador de PDFs")
    parser.add_argument("--watch", action="store_true", help="Monitora pastas continuamente")
    parser.add_argument("--forcar", action="store_true", help="Reprocessa todos os PDFs")
    parser.add_argument("--setup", action="store_true", help="Cria estrutura de pastas")
    parser.add_argument("--intervalo", type=int, default=30, help="Segundos entre verificações no modo watch")
    args = parser.parse_args()

    criar_estrutura()

    if args.setup:
        log("Setup concluído. Adicione PDFs em materias/ e execute novamente.", "OK")
    elif args.watch:
        modo_watch(args.intervalo)
    else:
        sincronizar(forcar=args.forcar)
