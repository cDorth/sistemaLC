"""Leaflet servido de DENTRO do HTML entregue, não de CDN.

Por que existe (2026-09-03)
---------------------------
Achado do `adversario`, e ele derruba a garantia inteira do mapa embutido: os
tiles podiam estar todos dentro do arquivo, com log dizendo "0 falhas", e o
cliente sem internet ainda via **página em branco** — porque
`<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js">` continuava lá.
Sem a biblioteca não há mapa, não há camada base e não há nem o fallback online:
não há nada. Embutir tile e deixar a lib no CDN é embutir o passageiro e deixar
o carro na rua.

⚠️ A minha própria verificação tinha o mesmo furo: o teste de "0 requisições"
liberava o unpkg **de propósito**, para o Leaflet carregar. Um teste que abre
exceção para a dependência que decide o resultado não está medindo a garantia.

Fronteira honesta: o **Plotly** (gráfico de histórico do mapa desktop) continua
vindo de `cdn.plot.ly` — são ~3,4 MB, e embutir dobraria o arquivo que vai por
WhatsApp. Consequência exata, que precisa ser dita ao usuário: **sem internet o
MAPA funciona inteiro; o GRÁFICO de histórico do desktop não desenha.** Os mapas
mobile não usam Plotly.

Licença: Leaflet 1.9.4, BSD-2-Clause, (c) 2010-2023 Vladimir Agafonkin, (c)
2010-2011 CloudMade. O cabeçalho `/* @preserve ... */` do arquivo original vai
junto no HTML — é o que a licença pede.
"""

from __future__ import annotations
import base64
import functools
import logging
import re
import sys
from pathlib import Path
logger = logging.getLogger(__name__)
LEAFLET_VERSAO = '1.9.4'

def _raiz_vendor():
    """Onde vive vendor/. Precisa achar tanto no repo quanto dentro do .exe
    (PyInstaller descompacta os datas em `sys._MEIPASS`)."""
    if getattr(sys, 'frozen', False):
        base = Path(getattr(sys, '_MEIPASS', '.'))
        for cand in ((base / 'vendor'), ((base / 'project_heatmaps') / 'vendor')):
            if cand.is_dir():
                return cand
            continue
    return (Path(__file__).resolve().parent / 'vendor')

@functools.lru_cache(maxsize=1)
def leaflet_css_inline():
    """`<style>` com o CSS do Leaflet e as imagens dele como data: URI.

    As `url(images/*.png)` do CSS são o detalhe que passa batido: sem elas o
    controle de camadas e o marcador ficam sem ícone — não quebra o mapa, mas
    é rede pedida por um arquivo que promete não pedir.
    """
    raiz = (_raiz_vendor() / 'leaflet')
    css_path = raiz / 'leaflet.css'
    if not css_path.is_file():
        logger.error('web_assets: %s não existe — o HTML vai buscar o Leaflet na REDE', css_path)
        return f"<link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@{LEAFLET_VERSAO}/dist/leaflet.css\" />"
    css = css_path.read_text(encoding='utf-8')
    def embutir(m):
        nome = m.group(1)
        img = ((raiz / 'images') / nome)
        if not img.is_file():
            return m.group(0)
        b64 = base64.b64encode(img.read_bytes()).decode('ascii')
        return f"url(data:image/png;base64,{b64})"

    css = re.sub('url\\(\\s*images/([^)\\s]+?)\\s*\\)', embutir, css)
    return f"<style>\n/* Leaflet {LEAFLET_VERSAO} — BSD-2-Clause */\n{css}\n</style>"

@functools.lru_cache(maxsize=1)
def leaflet_js_inline():
    """`<script>` com o Leaflet inteiro dentro."""
    js_path = ((_raiz_vendor() / 'leaflet') / 'leaflet.js')
    if not js_path.is_file():
        logger.error('web_assets: %s não existe — o HTML vai buscar o Leaflet na REDE', js_path)
        return f"<script src=\"https://unpkg.com/leaflet@{LEAFLET_VERSAO}/dist/leaflet.js\"></script>"
    js = js_path.read_text(encoding='utf-8')
    return (((("""<script>
""" + js) + """
""") + _js_icone_padrao()) + """
</script>""")

def _js_icone_padrao():
    """Fixa o ícone padrão de marcador em data: URI.

    Sem isto o Leaflet resolve `images/marker-icon.png` a partir do `src` da
    própria tag `<script>` (`_detectIconPath`) — e uma tag INLINE não tem `src`,
    então ele cai no diretório do documento e pede um arquivo que não existe.
    O CSS acima já cobre `.leaflet-default-icon-path`; isto cobre o caminho de
    JS, que é o que `L.marker()` usa de verdade.
    """
    raiz = ((_raiz_vendor() / 'leaflet') / 'images')
    def uri(nome):
        img = raiz / nome
        if not img.is_file():
            return ''
        return ('data:image/png;base64,' + base64.b64encode(img.read_bytes()).decode('ascii'))

    icone, icone2x, sombra = uri('marker-icon.png'), uri('marker-icon-2x.png'), uri('marker-shadow.png')
    if not icone:
        return ''
    return f"L.Icon.Default.mergeOptions({{iconUrl:'{icone}',iconRetinaUrl:'{(icone2x or icone)}',shadowUrl:'{sombra}'}});"

