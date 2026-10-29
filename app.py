"""
Radar de Notícias - efeito das notícias no mini índice (WIN) e no mini dólar (WDO)

Servidor local que:
  1. Busca notícias em feeds RSS públicos (InfoMoney, Valor, Money Times, Investing, Google Notícias)
  2. Classifica cada notícia pelo impacto provável no mini índice (WIN) e no mini dólar (WDO)
     e pela DIREÇÃO mais comum (dólar/índice tendem a subir ou cair), com o motivo
  3. Serve a interface web (pasta /web) em http://127.0.0.1:8765

Não precisa instalar nada além do Python 3. Para rodar: python app.py  (ou dê dois cliques em Iniciar.bat)
"""

import html
import sys
import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTA = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--porta=")), 8765)
PASTA_WEB = Path(__file__).parent / "web"
CACHE_SEGUNDOS = 300          # busca novas notícias no máximo a cada 5 minutos
HORAS_MAXIMAS = 48            # ignora notícias mais velhas que isso


def google_news(consulta):
    q = urllib.parse.quote(consulta + " when:2d")
    return f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


FEEDS = [
    ("InfoMoney", "https://www.infomoney.com.br/feed/"),
    ("Valor", "https://valor.globo.com/rss/valor"),
    ("Money Times", "https://www.moneytimes.com.br/feed/"),
    ("Investing", "https://br.investing.com/rss/news.rss"),
    ("Investing - Indicadores", "https://br.investing.com/rss/news_95.rss"),
    ("Google Notícias", google_news("dólar hoje OR Ibovespa hoje")),
    ("Google Notícias", google_news("Copom OR Selic OR IPCA")),
    ("Google Notícias", google_news("Fed OR Powell OR payroll OR \"inflação nos EUA\"")),
    ("Google Notícias", google_news("fiscal Haddad OR arcabouço OR \"contas públicas\"")),
    ("Google Notícias", google_news("pesquisa segundo turno mercado")),
]

# ---------------------------------------------------------------------------
# Regras de classificação.
# Cada regra: palavras-chave (regex, sem acento, minúsculas), peso (1 a 3),
# ativos afetados e a explicação didática de COMO isso costuma mexer no preço.
# ---------------------------------------------------------------------------
REGRAS = [
    {
        "id": "fed",
        "nome": "Juros nos EUA (Fed)",
        "peso": 4,
        "ativos": ["WDO", "WIN"],
        "padroes": [r"\bfed\b", r"federal reserve", r"\bpowell\b", r"\bfomc\b", r"juros (nos|dos) eua",
                    r"treasur", r"\byields?\b", r"titulos americanos", r"banco central americano"],
        "efeito": "Juros americanos mais altos atraem o dinheiro do mundo para os EUA. "
                  "Resultado típico: dólar SOBE (WDO ↑) e bolsa brasileira CAI (WIN ↓). "
                  "Sinal de corte de juros nos EUA costuma fazer o contrário.",
    },
    {
        "id": "dados_eua",
        "nome": "Dados econômicos dos EUA",
        "peso": 4,
        "ativos": ["WDO", "WIN"],
        "padroes": [r"payroll", r"\bcpi\b", r"\bpce\b", r"\bppi\b", r"inflacao (nos|dos|americana|ao consumidor nos) eua",
                    r"inflacao americana", r"pedidos de seguro.desemprego", r"(desemprego|emprego) (nos|dos) eua",
                    r"vagas de trabalho", r"\bjolts\b", r"\bpib (dos|nos) eua", r"varejo (dos|nos) eua",
                    r"precos ao produtor", r"\bism\b", r"\bpmi\b"],
        "efeito": "Dado americano MAIS FORTE que o esperado (mais emprego, mais inflação) = juros nos EUA "
                  "podem ficar altos por mais tempo → dólar ↑ (WDO ↑) e índice ↓ (WIN ↓). "
                  "Dado mais FRACO que o esperado costuma fazer o oposto. O que importa é a SURPRESA "
                  "em relação à expectativa, não o número em si.",
    },
    {
        "id": "copom",
        "nome": "Juros no Brasil (Copom/Selic)",
        "peso": 4,
        "ativos": ["WDO", "WIN"],
        "padroes": [r"copom", r"\bselic\b", r"galipolo", r"banco central", r"\bbc\b", r"juros futuros",
                    r"\bdi\b", r"curva de juros", r"ata do copom", r"politica monetaria"],
        "efeito": "Selic mais alta (ou sinal de alta) torna a renda fixa mais atraente que a bolsa → WIN tende a cair. "
                  "Para o dólar, juros altos no Brasil atraem dólares → WDO tende a cair. "
                  "Sinal de corte de juros costuma ajudar a bolsa (WIN ↑).",
    },
    {
        "id": "inflacao_br",
        "nome": "Inflação no Brasil",
        "peso": 4,
        "ativos": ["WIN", "WDO"],
        "padroes": [r"\bipca\b", r"ipca-15", r"\bigp-?m\b", r"\binpc\b", r"inflacao (no brasil|brasileira)",
                    r"boletim focus", r"\bfocus\b", r"expectativas? de inflacao"],
        "efeito": "Inflação acima do esperado = chance de juros altos por mais tempo → WIN tende a cair. "
                  "Inflação abaixo do esperado abre espaço para cortes de juros → WIN tende a subir.",
    },
    {
        "id": "fiscal",
        "nome": "Contas do governo (fiscal)",
        "peso": 3,
        "ativos": ["WDO", "WIN"],
        "padroes": [r"fiscal", r"arcabouco", r"\bhaddad\b", r"deficit", r"superavit", r"meta fiscal",
                    r"contas publicas", r"gastos? publicos?", r"divida publica", r"orcamento",
                    r"tesouro nacional", r"isencao do ir"],
        "efeito": "Medo de o governo gastar demais = investidores pedem mais prêmio para ficar no Brasil → "
                  "dólar ↑ (WDO ↑) e bolsa ↓ (WIN ↓). Notícias de corte de gastos/responsabilidade fiscal "
                  "costumam ter o efeito contrário.",
    },
    {
        "id": "eleicao",
        "nome": "Política e eleições",
        "peso": 3,
        "ativos": ["WDO", "WIN"],
        "padroes": [r"eleic", r"segundo turno", r"\b2o turno", r"pesquisa (datafolha|quaest|atlas|ipec|cnt|eleitoral)",
                    r"datafolha", r"quaest", r"atlasintel", r"\bipec\b", r"intencao de voto"],
        "efeito": "Em período eleitoral o mercado reage às pesquisas conforme enxerga o risco fiscal de cada "
                  "candidato. Pesquisa vista como 'ruim para as contas públicas' → dólar ↑ / índice ↓. "
                  "Vista como 'boa' → dólar ↓ / índice ↑. Reações podem ser bruscas na divulgação.",
    },
    {
        "id": "dolar",
        "nome": "Câmbio / dólar",
        "peso": 2,
        "ativos": ["WDO"],
        "padroes": [r"\bdolar\b", r"cambio", r"\bdxy\b", r"real se (valoriza|desvaloriza)", r"moedas emergentes",
                    r"fluxo cambial", r"intervencao", r"leilao de (linha|swap)", r"\bswap cambial"],
        "efeito": "Notícia direta sobre o câmbio. Lembre: WDO acompanha o dólar contra o real. "
                  "Leilões do BC (swap/linha) costumam segurar a alta do dólar.",
    },
    {
        "id": "ibov",
        "nome": "Bolsa / Ibovespa",
        "peso": 2,
        "ativos": ["WIN"],
        "padroes": [r"ibovespa", r"\bibov\b", r"\bb3\b", r"bolsa brasileira", r"fluxo estrangeiro",
                    r"investidor estrangeiro", r"wall street", r"s&p 500", r"\bnasdaq\b", r"dow jones",
                    r"futuros (de|em) nova york", r"bolsas (de|em) nova york"],
        "efeito": "Notícia sobre a própria bolsa ou bolsas americanas. O Ibovespa costuma seguir o humor de "
                  "Nova York: S&P 500 subindo forte tende a puxar o WIN para cima.",
    },
    {
        "id": "commodities",
        "nome": "Commodities e China",
        "peso": 2,
        "ativos": ["WIN", "WDO"],
        "padroes": [r"petroleo", r"\bbrent\b", r"\bwti\b", r"\bopep", r"minerio", r"\bchina\b", r"chines",
                    r"\bvale\b(?! a pena)", r"petrobras", r"\bpetr4\b", r"\bvale3\b", r"commodit"],
        "efeito": "Vale e Petrobras pesam muito no Ibovespa. Minério/petróleo em alta ajudam o WIN ↑ "
                  "e trazem dólares para o Brasil (WDO tende a ↓). China fraca derruba o minério → WIN ↓.",
    },
    {
        "id": "geopolitica",
        "nome": "Geopolítica e tarifas",
        "peso": 2,
        "ativos": ["WDO", "WIN"],
        "padroes": [r"tarifa", r"guerra comercial", r"\bguerra\b", r"sancao", r"sancoes", r"conflito armado",
                    r"ataque (militar|aereo)", r"oriente medio", r"\bisrael\b", r"\b(o|do|no|ao) ira\b",
                    r"\birani", r"russia", r"ucrania", r"aversao (a|ao) risco"],
        "efeito": "Medo no mundo = investidores fogem para o dólar (aversão a risco) → WDO ↑ e WIN ↓. "
                  "Acordos e alívio de tensões costumam fazer o contrário.",
    },
    {
        "id": "politica",
        "nome": "Política em Brasília",
        "peso": 1,
        "ativos": ["WDO", "WIN"],
        "padroes": [r"\blula\b", r"flavio bolsonaro", r"congresso", r"\bcamara\b", r"senado", r"planalto",
                    r"\bstf\b", r"\btse\b", r"candidat"],
        "efeito": "Decisões em Brasília mexem no mercado quando mudam a expectativa sobre gastos do governo. "
                  "Sozinha, a notícia política costuma ter impacto menor; ganha força quando envolve dinheiro público.",
    },
    {
        "id": "atividade_br",
        "nome": "Atividade econômica no Brasil",
        "peso": 1,
        "ativos": ["WIN"],
        "padroes": [r"\bpib\b", r"\bibc-br\b", r"desemprego", r"\bcaged\b", r"\bpnad\b", r"vendas no varejo",
                    r"producao industrial", r"setor de servicos"],
        "efeito": "Economia forte ajuda o lucro das empresas (bom para WIN), mas economia forte demais pode "
                  "pressionar a inflação e os juros. Normalmente impacto menor que juros e inflação.",
    },
]

# Palavras que reduzem a relevância (notícia de empresa isolada, esportes etc.)
RUIDO = [r"futebol", r"novela", r"\bbbb\b", r"receita de", r"horoscopo", r"celebridade", r"loteria", r"mega-sena"]


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


REGRAS_COMPILADAS = [(r, [re.compile(p) for p in r["padroes"]]) for r in REGRAS]
RUIDO_COMPILADO = [re.compile(p) for p in RUIDO]


def classificar(titulo, resumo):
    texto_titulo = normalizar(titulo)
    texto_resumo = normalizar(resumo)
    pontos = 0
    categorias = []
    ativos = set()
    for regra, padroes in REGRAS_COMPILADAS:
        no_titulo = any(p.search(texto_titulo) for p in padroes)
        no_resumo = any(p.search(texto_resumo) for p in padroes)
        if no_titulo or no_resumo:
            # palavra no título vale o dobro do que só no resumo
            pontos += regra["peso"] * (2 if no_titulo else 1)
            categorias.append({"id": regra["id"], "nome": regra["nome"], "efeito": regra["efeito"]})
            ativos.update(regra["ativos"])
    if any(p.search(texto_titulo) for p in RUIDO_COMPILADO):
        pontos = 0
    if pontos >= 10:
        impacto = "alto"
    elif pontos >= 6:
        impacto = "medio"
    elif pontos >= 3:
        impacto = "baixo"
    else:
        impacto = None
    return pontos, impacto, categorias, sorted(ativos)


# ---------------------------------------------------------------------------
# Direção provável: regras de economia que dizem para que lado o dólar (WDO) e o
# índice (WIN) COSTUMAM ir com cada tipo de manchete. Não é previsão: é a reação mais comum.
# (wdo, win) = força do empurrão: + sobe, - cai. "relato" = a manchete conta o que o preço JÁ fez.
# ---------------------------------------------------------------------------
_SOBE = r"(sobe|sobem|subiu|subiram|avanca|avancam|avancou|dispara|disparam|disparou|ganha|ganham|salta|saltou|valoriza|renova maxima|fecha em alta|opera em alta|em alta|tem alta|tem forte alta|forte alta)"
_CAI = r"(cai|caem|caiu|cairam|recua|recuam|recuou|despenca|despencou|perde|perdem|perdeu|cede|cedeu|tomba|afunda|desvaloriza|fecha em queda|opera em queda|em queda|em baixa|tem queda|tem forte queda|forte queda)"


_VERBOS = (r"(?:sobe|sobem|subiu|subiram|avanca|avancam|avancou|dispara|disparam|disparou|ganha|ganham|salta|saltou|"
           r"valoriza|renova|cai|caem|caiu|cairam|recua|recuam|recuou|despenca|despencou|perde|perdem|perdeu|cede|cedeu|"
           r"tomba|afunda|desvaloriza|fecha|fica|opera|tem|aliviam)")


def _perto(sujeito, verbo):
    # o assunto se liga ao PRIMEIRO verbo que vem depois dele (até 5 palavras):
    # "Bolsa cai e dólar sobe" → bolsa=cai, dólar=sobe · "Dólar e juros disparam" → dólar=disparam
    return rf"{sujeito}(?:\s+(?!{_VERBOS}\b)[\w$%.,]+){{0,5}}?\s+{verbo}"


DIRECAO = [
    (_perto(r"(dolar|cambio)", _SOBE), 2, 0, True, "o dólar já está subindo"),
    (_perto(r"(dolar|cambio)", _CAI), -2, 0, True, "o dólar já está caindo"),
    (_perto(r"(ibovespa|ibov|bolsa)", _SOBE), 0, 2, True, "a bolsa já está subindo"),
    (_perto(r"(ibovespa|ibov|bolsa)", _CAI), 0, -2, True, "a bolsa já está caindo"),
    (r"real se (valoriza|fortalece)", -2, 0, True, "o real está se valorizando (dólar caindo)"),
    # "estável" conta como relato: o mercado não está seguindo a leitura (o 0,001 só marca o ativo)
    (_perto(r"(dolar|cambio)", r"(fica estavel|estavel|opera de lado|sem direcao|perto da estabilidade)"), 0.001, 0, True, "o dólar está estável por enquanto"),
    (_perto(r"(ibovespa|ibov|bolsa)", r"(fica estavel|estavel|opera de lado|sem direcao|perto da estabilidade)"), 0, 0.001, True, "a bolsa está estável por enquanto"),
    (r"real se (desvaloriza|enfraquece)", 2, 0, True, "o real está se desvalorizando (dólar subindo)"),
    (r"\b(cpi|payroll|pce|ppi)\b[^.]{0,50}(acima|supera|forte|acelera|surpreende|sobe)|(inflacao|emprego|vagas|varejo|producao industrial|pib)[^.]{0,40}(eua|americ)[^.]{0,40}(acima|supera|forte|acelera)|(inflacao|preco)[^.]{0,20}(americana|nos eua)[^.]{0,30}(acima|maior)",
     1.5, -1.5, False, "dado americano forte: os juros nos EUA podem ficar altos por mais tempo"),
    (r"\b(cpi|payroll|pce)\b[^.]{0,50}(abaixo|fraco|fraca|desacelera|esfria)|(inflacao|emprego|vagas)[^.]{0,40}(eua|americ)[^.]{0,40}(abaixo|fraco|desacelera|esfria)",
     -1.5, 1.5, False, "dado americano fraco: abre espaço para o Fed cortar juros"),
    (r"(fed|powell|fomc)[^.]{0,60}(alta de juros|aumentar|elevar|eleva|aperto|duro|juros altos|rigido|mais rigido|manter.{0,20}alt)",
     1.5, -1.5, False, "sinal de juros altos nos EUA (Fed mais duro)"),
    (r"(fed|powell|fomc)[^.]{0,60}(corte|cortar|corta|reduzir|reducao|afrouxa|brando)",
     -1.5, 1.5, False, "sinal de corte de juros nos EUA (Fed mais brando)"),
    (_perto(r"(treasur\w*|yields?|titulos americanos)", r"(sobe|sobem|dispara|disparam|avanca|avancam)"), 1, -1, False, "juros dos títulos americanos subindo"),
    (r"(copom|selic)[^.]{0,50}(eleva|alta|aumenta|aumento|subir|manter.{0,15}alt)", -0.5, -1.5, False, "juros mais altos no Brasil pesam na bolsa"),
    (r"(copom|selic)[^.]{0,50}(corta|corte|reduz|reducao|queda)", 0, 1.5, False, "juros menores no Brasil ajudam a bolsa"),
    (r"^(?!.*(eua|americ)).*(ipca|inflacao)[^.]{0,50}(acima|supera|fura|estoura|acelera|dispara|sobe)", 0.5, -1.5, False, "inflação alta no Brasil: juros altos por mais tempo"),
    (r"^(?!.*(eua|americ)).*(ipca|inflacao)[^.]{0,50}(abaixo|desacelera|recua|alivio)", -0.5, 1.5, False, "inflação menor no Brasil: espaço para juros caírem"),
    (_perto(r"(juros futuros|di futuro|taxas futuras|curva de juros)", r"(sobe|sobem|dispara|disparam|avanca|avancam|tem firme alta|em alta)"), 1, -1.5, False, "juros futuros subindo mostram medo (fiscal ou inflação)"),
    (_perto(r"(juros futuros|di futuro|taxas futuras)", r"(cai|caem|recua|recuam|em queda|aliviam)"), -1, 1.5, False, "juros futuros caindo mostram alívio"),
    (r"(deficit|rombo|gasto|gastos|despesa|divida)[^.]{0,50}(sobe|aument|piora|maior|supera|estoura|amplia|dispara|superior)|(amplia|aumenta|novos?) (gastos|despesas)|temor fiscal|risco fiscal|afrouxa.{0,15}meta",
     1.5, -1.5, False, "piora nas contas do governo: investidor pede mais prêmio para ficar no Brasil"),
    (r"corte de gastos|ajuste fiscal|superavit|meta fiscal (cumprida|mantida)|compromisso (com a meta|fiscal)|alivio fiscal", -1, 1, False, "sinal de disciplina nas contas do governo"),
    (r"(rebaixa|corta) (a )?nota|rebaixamento", 1.5, -1.5, False, "nota de crédito do Brasil rebaixada"),
    (r"leilao de (swap|linha|dolar)|intervencao no cambio", -1, 0, False, "intervenção do Banco Central costuma segurar o dólar"),
    (_perto(r"(wall street|s&p 500|nasdaq|dow jones|nova york|bolsas americanas|bolsas de ny)", _SOBE + r"|recorde"), -0.5, 1, False, "bolsas americanas em alta costumam puxar o Ibovespa"),
    (_perto(r"(wall street|s&p 500|nasdaq|dow jones|nova york|bolsas americanas|bolsas de ny)", _CAI), 0.5, -1, False, "bolsas americanas em queda costumam pesar no Ibovespa"),
    (_perto(r"(petroleo|brent|minerio( de ferro)?|commodities)", _SOBE), -0.5, 1, False, "commodities em alta ajudam Vale e Petrobras e trazem dólares"),
    (_perto(r"(petroleo|brent|minerio( de ferro)?|commodities)", _CAI), 0.5, -1, False, "commodities em queda pesam em Vale e Petrobras"),
    (r"(china|chines)[^.]{0,40}(estimulo|acelera|cresce)", -0.5, 1, False, "China forte ajuda o minério e a Vale"),
    (r"(china|chines)[^.]{0,40}(desacelera|fraco|fraca|crise)", 0.5, -1, False, "China fraca derruba o minério e a Vale"),
    (r"guerra|ataque (militar|aereo)|conflito armado|sancao|sancoes|novas tarifas|tarifaco|aversao a risco|temor de recessao",
     1.5, -1.5, False, "medo no mundo: investidores correm para o dólar"),
    (r"acordo comercial|alivio (das|nas) tensoes|cessar.fogo|tregua", -1, 1, False, "alívio de tensões favorece a bolsa"),
    (r"(entrada|fluxo positivo) de (capital |investidores? )?estrangeir", -1, 1.5, False, "entrada de dinheiro estrangeiro"),
    (r"saida de (capital |investidores? )?estrangeir", 1, -1.5, False, "saída de dinheiro estrangeiro"),
    (r"(empate tecnico|disputa (acirrada|apertada)|segundo turno (mais )?apertado|cenario eleitoral (mais )?apertado)", 1, -1, False,
     "eleição mais incerta: o mercado fica mais cauteloso"),
]
DIRECAO_COMPILADA = [(re.compile(r), wdo, win, relato, porque) for r, wdo, win, relato, porque in DIRECAO]


def ler_direcao(titulo):
    t = normalizar(titulo)
    achados = [d for d in DIRECAO_COMPILADA if d[0].search(t)]
    relato_wdo = any(d[3] and d[1] for d in achados)
    relato_win = any(d[3] and d[2] for d in achados)
    wdo = sum(d[1] * (1 if d[3] or not relato_wdo else 0.5) for d in achados)
    win = sum(d[2] * (1 if d[3] or not relato_win else 0.5) for d in achados)
    sentido = lambda x: "sobe" if x >= 1 else "cai" if x <= -1 else "neutro"
    motivos = list(dict.fromkeys(d[4] for d in achados))
    return {"wdo": sentido(wdo), "win": sentido(win), "forca_wdo": round(wdo, 1), "forca_win": round(win, 1),
            "ja_aconteceu": any(d[3] for d in achados), "motivos": motivos}


def limpar_html(texto):
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    return re.sub(r"\s+", " ", html.unescape(texto)).strip()


def ler_data(texto):
    if not texto:
        return None
    try:
        dt = parsedate_to_datetime(texto)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def baixar_feed(fonte, url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (RadarDayTrade)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            dados = resp.read()
        raiz = ET.fromstring(dados)
    except Exception as erro:  # feed fora do ar não deve derrubar o app
        print(f"[aviso] falha ao ler {fonte}: {erro}")
        return []
    itens = []
    for item in raiz.iter("item"):
        titulo = limpar_html(item.findtext("title", ""))
        link = (item.findtext("link") or "").strip()
        resumo = limpar_html(item.findtext("description", ""))
        data = ler_data(item.findtext("pubDate"))
        nome_fonte = fonte
        # Google Notícias coloca o veículo no fim do título: "Título - Estadão"
        if fonte == "Google Notícias" and " - " in titulo:
            titulo, veiculo = titulo.rsplit(" - ", 1)
            nome_fonte = veiculo.strip()
            resumo = ""
        if titulo and link:
            itens.append({"titulo": titulo, "link": link, "resumo": resumo[:400],
                          "fonte": nome_fonte, "data": data})
    return itens


_cache = {"quando": 0.0, "dados": None}
_trava = threading.Lock()


def chave_titulo(titulo):
    return re.sub(r"[^a-z0-9]", "", normalizar(titulo))[:70]


def coletar_noticias(forcar=False):
    with _trava:
        if not forcar and _cache["dados"] and time.time() - _cache["quando"] < CACHE_SEGUNDOS:
            return _cache["dados"]
        with ThreadPoolExecutor(max_workers=len(FEEDS)) as ex:
            resultados = list(ex.map(lambda f: baixar_feed(*f), FEEDS))
        agora = datetime.now(timezone.utc)
        limite = agora - timedelta(hours=HORAS_MAXIMAS)
        vistas = set()
        noticias = []
        for lista in resultados:
            for n in lista:
                if n["data"] and n["data"] < limite:
                    continue
                chave = chave_titulo(n["titulo"])
                if chave in vistas:
                    continue
                vistas.add(chave)
                pontos, impacto, categorias, ativos = classificar(n["titulo"], n["resumo"])
                if not impacto:
                    continue
                noticias.append({
                    **n,
                    "data": n["data"].isoformat() if n["data"] else None,
                    "pontos": pontos,
                    "impacto": impacto,
                    "categorias": categorias,
                    "ativos": ativos,
                    "direcao": ler_direcao(n["titulo"]),
                })
        noticias.sort(key=lambda n: n["data"] or "", reverse=True)
        dados = {
            "atualizado_em": agora.isoformat(),
            "total": len(noticias),
            "fontes_ok": sum(1 for r in resultados if r),
            "fontes_total": len(FEEDS),
            "noticias": noticias,
        }
        _cache.update(quando=time.time(), dados=dados)
        return dados


class Manipulador(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PASTA_WEB), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # sempre carrega a versão mais nova da interface
        super().end_headers()

    def responder_json(self, dados, codigo=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = urllib.parse.urlparse(self.path)
        if caminho.path == "/api/noticias":
            forcar = "forcar=1" in (caminho.query or "")
            self.responder_json(coletar_noticias(forcar))
            return
        super().do_GET()

    def log_message(self, formato, *args):
        pass  # mantém o terminal limpo


def main():
    servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), Manipulador)
    url = f"http://127.0.0.1:{PORTA}"
    print(f"Radar de Noticias rodando em {url}")
    print("Deixe esta janela aberta enquanto usa o app. Para fechar: Ctrl+C")
    threading.Thread(target=coletar_noticias, daemon=True).start()  # aquece o cache
    if "--sem-navegador" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    main()
