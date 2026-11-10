# pycrefine: 1 place could not be reconstructed reliably; treat the source below as unverified there:
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in PacoteBasemap at SETUP_ANNOTATIONS (offset 10); Python 3.11]
"""Mapa de fundo EMBUTIDO no HTML entregue (tiles pré-baixados).

Por que existe (2026-09-03)
---------------------------
Os HTMLs são entregues prontos (WhatsApp) e abertos do disco pelo cliente.
Uma página `file://` **não manda header `Referer`** — verificado nos headers da
requisição, com Chrome de verdade — e a tile usage policy do OSM exige Referer
OU User-Agent que identifique a aplicação. Do lado da página não há conserto:
não existe `<meta name="referrer">` que invente origem para `file://`.

Pior: quando o bloqueio dispara, o servidor devolve **200 com um PNG válido**
que desenha o aviso "Access blocked". Para o Leaflet o tile CARREGOU — nenhuma
guarda por `tileerror` veria isso. O cliente vê o aviso, nós vemos verde.

A saída é não depender do servidor de ninguém na hora em que o cliente abre o
arquivo: os tiles vão DENTRO do HTML, como `data:` URI, restritos à área que o
mapa realmente desenha. Quem baixa é o gerador — uma vez, aqui, com
User-Agent identificando a aplicação e cache em disco — e não o cliente, N
vezes, sem identificação nenhuma. O tráfego que o OSM recebe CAI: hoje é cada
cliente, a cada abertura, a cada arrasto de mapa.

Dimensionamento medido (mapa real Jaboatão Noturno 16.03, 4.642 segmentos,
tiles do OSM Standard recodificados em WebP q65, com anel de 1 tile de folga):

    zoom      tiles   acrescenta ao HTML (base64)
    z13          19      0,29 MB
    z14          33      0,47 MB
    z15          60      0,94 MB
    z16         123      1,61 MB
    z17         299      3,24 MB

    z13..z16 = 3,3 MB  -> HTML de 2,2 MB vira ~5,5 MB
    z13..z17 = 6,6 MB  -> HTML de 2,2 MB vira ~8,8 MB

Recortar pela MALHA que o mapa desenha (e não pela bbox da cidade) é o que
segura o número: a bbox da cidade inteira de Jaboatão daria ~11 MB só até z16.

Direção do fail-safe
--------------------
Se o cache falhar (sem rede, provedor fora, orçamento estourado), a camada
volta a ser ONLINE. Um mapa que talvez apareça é melhor que um mapa em branco
garantido. A falha é RUIDOSA (log + contadores no retorno), nunca silenciosa.
"""

from __future__ import annotations
import base64
import functools
import io
import json
import logging
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence
from project_heatmaps.config import BASEMAP_TILE_URL, BASEMAP_ATTRIBUTION, BASEMAP_MAX_ZOOM
logger = logging.getLogger(__name__)
_AVISOU_SEM_PILLOW = False
USER_AGENT = 'HeatmapsGenerator/1.0 (Locar; gerador de heatmap de coleta)'
INTERVALO_MIN_S = 0.5
VERSAO_INDICE = 2
TILE_VAZIO = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNgYGBgAAAABQABeqhXUAAAAABJRU5ErkJggg=='
@dataclass
class PacoteBasemap:
    """O que o gerador precisa para escrever a camada base no HTML."""
    tiles: dict[str, str] = field(default_factory=dict)
    zoom_min: int = 0
    zoom_max: int = 0
    bounds: Optional[tuple[float, float, float, float]] = None
    bytes_embutidos: int = 0
    baixados: int = 0
    do_cache: int = 0
    falhas: int = 0
    pedidos: int = 0
    @property
    def solicitados(self):
        return max(self.pedidos, (len(self.tiles) + self.falhas))

    @property
    def pct_falhas(self):
        return ((100.0 * self.falhas) / self.solicitados) if self.solicitados else 0.0

def _tile_de(lat, lon, z):
    n = 2 ** z
    lat = max(min(lat, 85.0511), -85.0511)
    rad = math.radians(lat)
    x = int((((lon + 180.0) / 360.0) * n))
    y = int((((1.0 - (math.log((math.tan(rad) + (1.0 / math.cos(rad)))) / math.pi)) / 2.0) * n))
    return (max(0, min((n - 1), x)), max(0, min((n - 1), y)))

def _cantos_do_tile(x, y, z):
    n = 2 ** z
    oeste = (((x / n) * 360.0) - 180.0)
    leste = ((((x + 1) / n) * 360.0) - 180.0)
    norte = math.degrees(math.atan(math.sinh((math.pi * (1 - ((2 * y) / n))))))
    sul = math.degrees(math.atan(math.sinh((math.pi * (1 - ((2 * (y + 1)) / n))))))
    return (sul, oeste, norte, leste)

def pontos_de_geojson(*fontes):
    """Extrai (lat, lon) de GeoJSON — dict ou string, FeatureCollection ou não.

    Aceita o que os geradores já têm na mão, para não pedir a geometria duas
    vezes. Entrada inválida devolve lista vazia (o chamador cai no online).
    """
    pontos = []
    def coords(c):
        if (not isinstance(c, (list, tuple)) or not c):
            return None
        if isinstance(c[0], (int, float)):
            if len(c) >= 2:
                pontos.append((float(c[1]), float(c[0])))
            return None
        for filho in c:
            coords(filho)

    for fonte in fontes:
        if fonte in (None, '', 'null'):
            continue
        obj = fonte
        if isinstance(obj, (str, bytes)):
            try:
                obj = json.loads(obj)
            except (ValueError, TypeError):
                continue
        if isinstance(obj, dict):
            feicoes = obj.get('features')
            if feicoes is None:
                feicoes = [obj]
            for f in feicoes:
                geom = ((f or {}).get('geometry') or {})
                coords(geom.get('coordinates'))
                continue
    return pontos

def tiles_de_area(bbox, zoom_min, zoom_max, folga_km=15.0, teto_por_zoom=400):
    """Preenche TODOS os tiles de uma caixa (a bbox do município + folga).

    Existe porque recortar pela malha viária deixa BURACO onde não passa rua —
    mar, mata, área rural — e buraco não é fundo branco só na borda: dentro de
    uma camada o Leaflet mostra UM nível nativo por vez, então o tile que falta
    não cai para o nível de baixo, ele some. Foi o bloco cinza que apareceu no
    mar a sudeste de Jaboatão.

    Só vale a pena nos zooms de contexto: em z13 um tile cobre 4,9 km, então a
    região metropolitana inteira cabe em ~80 tiles. `teto_por_zoom` protege o
    município grande e rural (Caruaru, Cuiabá), onde a caixa é enorme: acima do
    teto o zoom é descartado e quem cobre é o recorte pela malha.
    """
    oeste, sul, leste, norte = bbox
    graus_lat = folga_km / 111.0
    meio = math.radians(((sul + norte) / 2.0))
    graus_lon = (folga_km / (111.0 * max(0.2, math.cos(meio))))
    sul -= graus_lat
    norte += graus_lat
    oeste -= graus_lon
    leste += graus_lon
    por_zoom = {}
    for z in range(zoom_min, (zoom_max + 1)):
        x1, y1 = _tile_de(norte, oeste, z)
        x2, y2 = _tile_de(sul, leste, z)
        largura, altura = abs((x2 - x1)) + 1, abs((y2 - y1)) + 1
        if (largura * altura) > teto_por_zoom:
            logger.info('basemap: área de contexto em z%d daria %d tiles (teto %d) — esse zoom fica só com o recorte pela malha', z, (largura * altura), teto_por_zoom)
        else:
            por_zoom[z] = {(x, y) for x in range(min(x1, x2), (max(x1, x2) + 1)) for y in range(min(y1, y2), (max(y1, y2) + 1))}
    return por_zoom

def anel_para_zoom(z, anel_base):
    """Folga em tiles, por zoom.

    Nos zooms de contexto (z<=13) o recorte pela malha viária deixa BURACO onde
    não passa rua — orla, mata, área rural — e o buraco aparece como bloco
    branco no meio do mapa. Um tile a mais de folga lá custa pouco (o tile é
    grande: 4,9 km em z13, 39 km em z10) e fecha o furo. Nos zooms de rua a
    folga fica em 1: dobrar ali multiplicaria o arquivo.
    """
    return (anel_base + 1) if z <= 13 else anel_base

def tiles_necessarios(pontos, zoom_min, zoom_max, anel=1):
    """Tiles que cobrem a geometria desenhada, com `anel` tiles de folga.

    A folga existe para o cliente poder arrastar um pouco o mapa sem cair no
    vazio. Ela é o que faz a conta subir: no medido, anel=1 mais que dobra a
    contagem em z13 e ainda soma ~45% em z16.
    """
    por_zoom = {}
    for z in range(zoom_min, (zoom_max + 1)):
        n = 2 ** z
        folga = anel_para_zoom(z, anel)
        base = set()
        for lat, lon in pontos:
            base.add(_tile_de(lat, lon, z))
        com_folga = set()
        for x, y in base:
            for dx in range((-folga), (folga + 1)):
                for dy in range((-folga), (folga + 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx:
                        if nx < n:
                            pass
                    else:
                        continue
                    if 0 <= ny:
                        if ny < n:
                            pass
                    else:
                        continue
                    com_folga.add((nx, ny))
        por_zoom[z] = com_folga
    return por_zoom

def _para_webp(png, qualidade):
    """Recodifica o PNG do provedor em WebP. Medido: ~21 KB -> ~10 KB.

    Se o Pillow não estiver disponível ou a imagem não abrir, devolve o PNG
    original — perde tamanho, não perde o mapa.
    """
    global _AVISOU_SEM_PILLOW
    try:
        from PIL import Image
    except ImportError:
        if not _AVISOU_SEM_PILLOW:
            _AVISOU_SEM_PILLOW = True
            logger.warning("basemap: Pillow indisponível — os tiles vão como PNG e cada HTML entregue fica ~2x maior. No .exe isso significa que 'PIL' voltou aos excludes do heatmaps.spec.")
        return (png, 'image/png')
    try:
        img = Image.open(io.BytesIO(png)).convert('RGB')
    except Exception:
        return (png, 'image/png')
    buf = io.BytesIO()
    img.save(buf, 'WEBP', quality=qualidade, method=4)
    return (buf.getvalue(), 'image/webp')

class _Baixador:
    def __init__(self, cache_dir, qualidade_webp):
        self.cache_dir = cache_dir
        self.qualidade = qualidade_webp
        self._ultimo = 0.0

    def _caminho(self, z, x, y):
        return (((self.cache_dir / str(z)) / str(x)) / f"{y}.webp")

    def obter(self, z, x, y):
        """Devolve (bytes, mimetype, veio_do_cache)."""
        destino = self._caminho(z, x, y)
        if destino.is_file() and destino.stat().st_size > 0:
            return (destino.read_bytes(), 'image/webp', True)
        espera = (INTERVALO_MIN_S - (time.monotonic() - self._ultimo))
        if espera > 0:
            time.sleep(espera)
        url = BASEMAP_TILE_URL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                bruto = resp.read()
        finally:
            pass
        self._ultimo = time.monotonic()
        dados, mime = _para_webp(bruto, self.qualidade)
        if mime == 'image/webp':
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(dados)
        return (dados, mime, False)

def montar_pacote(por_zoom, *, cache_dir, orcamento_bytes, qualidade_webp=65, rotulo=''):
    """Baixa (ou lê do cache) o conjunto de tiles pedido e devolve o pacote.

    `orcamento_bytes` é o teto do que pode ser embutido no HTML, já em base64.
    Ao estourar, os zooms mais fundos são DESCARTADOS (o mapa perde
    aproximação, não perde o fundo) e o pacote informa até onde foi.
    """
    pacote = PacoteBasemap()
    if not por_zoom:
        return pacote
    pacote.pedidos = sum((len(v) for v in por_zoom.values()))
    baixador = _Baixador(cache_dir, qualidade_webp)
    for z in sorted(por_zoom):
        alvo = sorted(por_zoom[z])
        if not alvo:
            continue
        parcial = {}
        bytes_parcial = 0
        falhas = baixados = do_cache_n = 0
        for x, y in alvo:
            dados, mime, do_cache = baixador.obter(z, x, y)
            if dados is None:
                falhas += 1
            else:
                uri = f"data:{mime};base64,{base64.b64encode(dados).decode('ascii')}"
                parcial[f"{z}/{x}/{y}"] = uri
                bytes_parcial += len(uri)
                if do_cache:
                    do_cache_n += 1
                else:
                    baixados += 1
        if pacote.tiles and (pacote.bytes_embutidos + bytes_parcial) > orcamento_bytes:
            if rotulo:
                pass
            else:
                pass
            logger.warning('basemap%s: orçamento estourado em z%d — parando em z%d', f" [{rotulo}]", '', z, pacote.zoom_max)
            __SENTINEL_NULL__
            break
        pacote.falhas += falhas
        pacote.baixados += baixados
        pacote.do_cache += do_cache_n
        if not parcial:
            continue
        pacote.tiles.update(parcial)
        pacote.bytes_embutidos += bytes_parcial
        if not pacote.zoom_max:
            pacote.zoom_min = z
        pacote.zoom_max = z
    return pacote

def _dir_malhas():
    from project_heatmaps.config import BASE_DIR, BASEMAP_MALHAS_SUBDIR
    if BASE_DIR is None:
        return None
    d = (Path(BASE_DIR) / BASEMAP_MALHAS_SUBDIR)
    return d if d.is_dir() else None

@functools.lru_cache(maxsize=1)
def _malhas_com_bbox():
    """(caminho, oeste, sul, leste, norte) de cada malha, em WGS84.

    Lê só o cabeçalho do GeoPackage (`pyogrio.read_info`) — as 13 malhas saem
    em ~0,2 s, contra segundos por arquivo se fosse abrir a geometria.
    """
    d = _dir_malhas()
    if d is None:
        return ()
    try:
        import pyogrio
        from pyproj import Transformer
    except ImportError:
        logger.warning('basemap: pyogrio/pyproj indisponíveis — sem camada de cidade')
        return ()
    saida = []
    for arq in sorted(d.glob('*.gpkg')):
        try:
            info = pyogrio.read_info(arq)
            oeste, sul, leste, norte = info['total_bounds']
            crs = (info.get('crs') or 'EPSG:4326')
            if str(crs).upper() not in ('EPSG:4326', 'OGC:CRS84'):
                tr = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
                oeste, sul = tr.transform(oeste, sul)
                leste, norte = tr.transform(leste, norte)
        except Exception as exc:
            logger.warning('basemap: não li a bbox de %s (%s)', arq.name, exc)
            continue
        saida.append((str(arq), float(oeste), float(sul), float(leste), float(norte)))
    return tuple(saida)

def resolver_malha(pontos):
    """Qual malha municipal contém o que este mapa desenha.

    Decide pela GEOMETRIA, não pelo nome do contrato: o nome chega em formatos
    diferentes em cada gerador ("CABO - DIURNO [SELETIVA]", "Cabo Diurno") e
    casar string seria mais um lugar para apodrecer. Exige maioria folgada
    (>80% dos pontos dentro) para não pegar município vizinho por acaso.

    ⚠️ Fronteira honesta: algumas malhas estão gravadas com CRS que não bate
    com a posição real (as de fora do Nordeste, em EPSG:31985). Para essas a
    bbox cai em outro lugar do mundo, nenhum ponto casa, e o mapa segue pelo
    caminho de uma camada só — perde contexto, não quebra.
    """
    malhas = _malhas_com_bbox()
    if not malhas or not pontos:
        return None
    amostra = pontos if len(pontos) <= 2000 else pontos[::max(1, (len(pontos) // 2000))]
    melhor, melhor_frac = (None, 0.0)
    for caminho, oeste, sul, leste, norte in malhas:
        dentro = sum((1 for lat, lon in amostra if sul <= lat if lat <= norte if oeste <= lon if lon <= leste))
        frac = (dentro / len(amostra))
        if frac > melhor_frac:
            melhor, melhor_frac = caminho, frac
    if melhor is None or melhor_frac < 0.8:
        logger.info('basemap: nenhuma malha municipal cobre este mapa (melhor: %.0f%%) — camada de cidade desligada', (melhor_frac * 100))
        return None
    return Path(melhor)

def bbox_da_malha(malha):
    """(oeste, sul, leste, norte) em WGS84, ou None se a malha não estiver listada."""
    alvo = str(malha)
    for caminho, oeste, sul, leste, norte in _malhas_com_bbox():
        if caminho == alvo:
            return (oeste, sul, leste, norte)

def tiles_da_cidade(malha, zoom_min, zoom_max, anel, cache_dir):
    """Tiles que cobrem a malha viária inteira do município.

    O resultado é gravado em JSON: abrir o GeoPackage custa segundos (o de
    Caruaru tem 56 MB) e o conjunto é o MESMO para todo mapa daquela cidade.
    A chave inclui o mtime do arquivo, então editar a malha invalida sozinho.
    """
    try:
        chave = f"{malha.stem}_z{zoom_min}-{zoom_max}_a{anel}_v{VERSAO_INDICE}_{int(malha.stat().st_mtime)}"
    except OSError:
        return {}
    destino = ((cache_dir / '_malhas') / f"{chave}.json")
    if destino.is_file():
        try:
            bruto = json.loads(destino.read_text(encoding='utf-8'))
            return {int(z): {tuple(t) for t in lista} for z, lista in bruto.items()}
        except (ValueError, TypeError):
            logger.warning('basemap: índice de tiles corrompido em %s — refazendo', destino)
    try:
        import geopandas as gpd
        from shapely import get_coordinates, segmentize
    except ImportError:
        return {}
    try:
        gdf = gpd.read_file(malha, columns=[]).to_crs(4326)
        co = get_coordinates(segmentize(gdf.geometry.values, 0.0004))
    except Exception as exc:
        logger.warning('basemap: falhei ao ler a malha %s (%s)', malha.name, exc)
        return {}
    pontos = [(float(lat), float(lon)) for lon, lat in co]
    por_zoom = tiles_necessarios(pontos, zoom_min, zoom_max, anel=anel)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({str(z): sorted(v) for z, v in por_zoom.items()}, separators=(',', ':')), encoding='utf-8')
    logger.info('basemap: malha %s indexada — %s', malha.stem, ', '.join((f"z{z}:{len(v)}" for z, v in sorted(por_zoom.items()))))
    return por_zoom

def _js_online(nome_mapa):
    return f"L.tileLayer({json.dumps(BASEMAP_TILE_URL)}, {{\n            attribution: {json.dumps(BASEMAP_ATTRIBUTION)},\n            maxZoom: {BASEMAP_MAX_ZOOM}\n        }}).addTo({nome_mapa});"

def js_camada_base(pacotes, nome_mapa='map', max_falhas_pct=2.0):
    """JS que instala a camada base — embutida se der, ONLINE se não der.

    Recebe as camadas em ordem de desenho: a primeira é o fundo (cidade
    inteira, zoom de contexto), as seguintes vão por cima (detalhe da área
    desenhada). Onde a camada de cima não tem tile ela é transparente, e o que
    aparece é a de baixo aproximada — nunca buraco.

    ⚠️ `max_falhas_pct` existe por achado do `adversario` (2026-09-03): com o
    provedor fora, um pacote saía com 31 tiles faltando de 71 e era embarcado
    assim mesmo. "Tem algum tile" não é o teste; o teste é "tem tile o bastante
    para o fundo existir".
    """
    uteis = [pc for pc in pacotes if pc and pc.tiles]
    if not uteis:
        return _js_online(nome_mapa)
    falhas = sum((pc.falhas for pc in uteis))
    solicitados = sum((pc.solicitados for pc in uteis))
    pct = ((100.0 * falhas) / solicitados) if solicitados else 0.0
    if pct > max_falhas_pct:
        logger.warning('basemap: %d de %d tiles faltaram (%.0f%%) — pacote DESCARTADO, camada base fica ONLINE', falhas, solicitados, pct)
        return _js_online(nome_mapa)
    partes = []
    for i, pc in enumerate(uteis):
        de_cima = i > 0
        partes.append(''.join(["""
            // camada """, f"{(i + 1)}", '/', f"{len(uteis)}", ': ', f"{len(pc.tiles)}", ' tiles z', f"{pc.zoom_min}", '-z', f"{pc.zoom_max}", """,
            // """, f"{(pc.bytes_embutidos / 1048576):.1f}", """ MB
            var TILES_EMBUTIDOS_""", f"{i}", ' = ', f"{json.dumps(pc.tiles, separators=(',', ':'))}", """;
            new CamadaEmbutida('', {
                attribution: """, f"""{json.dumps(BASEMAP_ATTRIBUTION) if i == 0 else '""'}""", """,
                dicionario: TILES_EMBUTIDOS_""", f"{i}", """,
                // `maxZoom` da CAMADA é o que o Leaflet usa para calcular o
                // `maxZoom` do MAPA — por isso vale """, f"{BASEMAP_MAX_ZOOM}", """ em todas,
                // igual ao caminho online. Quem marca o que existe de tile é
                // `min/maxNativeZoom`: fora dessa faixa o Leaflet reaproveita o
                // tile mais próximo em vez de recusar o nível.
                minZoom: """, f"{pc.zoom_min if de_cima else 0}", """,
                maxZoom: """, f"{BASEMAP_MAX_ZOOM}", """,
                minNativeZoom: """, f"{pc.zoom_min}", """,
                maxNativeZoom: """, f"{pc.zoom_max}", """,
                zIndex: """, f"{(1 + i)}", """
            }).addTo(""", f"{nome_mapa}", ');']))
    return f"(function() {{\n            // Mapa de fundo EMBUTIDO: {sum((len(pc.tiles) for pc in uteis))} tiles em {len(uteis)} camada(s),\n            // {(sum((pc.bytes_embutidos for pc in uteis)) / 1048576):.1f} MB. Não faz nenhuma requisição de\n            // rede — é o que impede o \"Access blocked\" do provedor na tela do cliente.\n            //\n            // Fundo BRANCO fora da área coberta: o cinza padrão do Leaflet\n            // desenhava a borda do que foi embutido e denunciava o recorte.\n            var _est = document.createElement('style');\n            _est.textContent = '.leaflet-container{{background:#ffffff}}';\n            document.head.appendChild(_est);\n\n            var CamadaEmbutida = L.TileLayer.extend({{\n                getTileUrl: function(coords) {{\n                    return this.options.dicionario[coords.z + '/' + coords.x + '/' + coords.y]\n                        || {json.dumps(TILE_VAZIO)};\n                }}\n            }});\n{''.join(partes)}\n            // Sem `setMaxBounds`: prender o pan impedia o enquadramento inicial\n            // de caber o mapa inteiro ao lado da sidebar (o Leaflet reclampava a\n            // vista) e dava a sensação de mapa recortado. Fora da área coberta\n            // aparece branco, que é o combinado.\n        }})();"

def bloco_camada_base(*fontes_geojson, nome_mapa='map', orcamento_mb=None):
    """Atalho para os geradores: recebe o(s) GeoJSON que o mapa desenha e
    devolve o bloco JS pronto da camada base.

    É o único ponto que os geradores chamam. Se o embutido estiver desligado
    ou falhar, sai o bloco ONLINE — o gerador não decide nada e não sabe qual
    saiu (ver "Direção do fail-safe" no topo).

    `orcamento_mb` troca o `BASEMAP_OFFLINE_ORCAMENTO_MB` nesta chamada — é por
    onde `caber_no_teto` encolhe o mobile.
    """
    from project_heatmaps.config import BASEMAP_OFFLINE_ENABLED, BASEMAP_OFFLINE_CONTEXTO_ZOOM_MIN, BASEMAP_OFFLINE_CONTEXTO_ZOOM_MAX, BASEMAP_OFFLINE_CONTEXTO_FOLGA_KM, BASEMAP_OFFLINE_CONTEXTO_TETO_TILES, BASEMAP_OFFLINE_CIDADE_ZOOM_MIN, BASEMAP_OFFLINE_CIDADE_ZOOM_MAX, BASEMAP_OFFLINE_DETALHE_ZOOM_MIN, BASEMAP_OFFLINE_DETALHE_ZOOM_MAX, BASEMAP_OFFLINE_ANEL_TILES, BASEMAP_OFFLINE_ORCAMENTO_MB, BASEMAP_OFFLINE_WEBP_QUALIDADE, BASEMAP_MAX_ZOOM, PATHS
    if not BASEMAP_OFFLINE_ENABLED:
        logger.warning('basemap: BASEMAP_OFFLINE_ENABLED desligado — camada base ONLINE, o HTML entregue vai pedir tile ao provedor')
        return js_camada_base([], nome_mapa)
    pontos = pontos_de_geojson(*fontes_geojson)
    if not pontos:
        logger.warning('basemap: nenhuma coordenada nas %d fonte(s) recebidas — camada base ONLINE. Quem chama precisa passar a geometria REAL que o mapa desenha (ver `basemap_geojson` nos geradores)', len(fontes_geojson))
        return js_camada_base([], nome_mapa)
    cache_dir = Path(PATHS.basemap_cache_dir)
    if orcamento_mb is None:
        orcamento_mb = BASEMAP_OFFLINE_ORCAMENTO_MB
    orcamento = int(((orcamento_mb * 1024) * 1024))
    anel = BASEMAP_OFFLINE_ANEL_TILES
    qual = BASEMAP_OFFLINE_WEBP_QUALIDADE
    def _empacotar(por_zoom, rotulo, gasto):
        sobra = orcamento - gasto
        if sobra <= 0 or not por_zoom:
            return None
        return montar_pacote(por_zoom, cache_dir=cache_dir, orcamento_bytes=sobra, qualidade_webp=qual, rotulo=rotulo)

    try:
        pacotes = []
        malha = resolver_malha(pontos)
        if malha is not None:
            bbox = bbox_da_malha(malha)
            if bbox is not None:
                pc = _empacotar(tiles_de_area(bbox, BASEMAP_OFFLINE_CONTEXTO_ZOOM_MIN, min(BASEMAP_OFFLINE_CONTEXTO_ZOOM_MAX, BASEMAP_MAX_ZOOM), folga_km=BASEMAP_OFFLINE_CONTEXTO_FOLGA_KM, teto_por_zoom=BASEMAP_OFFLINE_CONTEXTO_TETO_TILES), f"contexto {malha.stem}", 0)
                if pc and pc.tiles:
                    pacotes.append(pc)
            pc = _empacotar(tiles_da_cidade(malha, BASEMAP_OFFLINE_CIDADE_ZOOM_MIN, min(BASEMAP_OFFLINE_CIDADE_ZOOM_MAX, BASEMAP_MAX_ZOOM), anel, cache_dir), f"cidade {malha.stem}", sum((p.bytes_embutidos for p in pacotes)))
            if pc and pc.tiles:
                pacotes.append(pc)
        zdet_min = BASEMAP_OFFLINE_DETALHE_ZOOM_MIN
        zdet_max = min(BASEMAP_OFFLINE_DETALHE_ZOOM_MAX, BASEMAP_MAX_ZOOM)
        if not pacotes:
            zdet_min = min(BASEMAP_OFFLINE_CONTEXTO_ZOOM_MIN, zdet_min)
        pc = _empacotar(tiles_necessarios(pontos, zdet_min, zdet_max, anel=anel), 'detalhe', sum((p.bytes_embutidos for p in pacotes)))
        if pc and pc.tiles:
            pacotes.append(pc)
    except Exception:
        logger.exception('basemap: montagem do pacote falhou — fica ONLINE')
        return js_camada_base([], nome_mapa)
    js = js_camada_base(pacotes, nome_mapa)
    embutido = 'TILES_EMBUTIDOS' in js
    if embutido:
        pass
    else:
        pass
    if embutido:
        pass
    else:
        pass
    logger.warning('basemap %s: %d tiles em %d camada(s), %.1f MB — %d baixados, %d do cache, %d falhas', 'EMBUTIDO', 'ONLINE (pacote descartado)', sum((len(pc.tiles) for pc in pacotes)), len([pc for pc in pacotes if pc.tiles]), (sum((pc.bytes_embutidos for pc in pacotes)) / 1048576), sum((pc.baixados for pc in pacotes)), sum((pc.do_cache for pc in pacotes)), sum((pc.falhas for pc in pacotes)))
    return js

_RX_TILE_EMBUTIDO = re.compile('"\\d+/\\d+/\\d+":"(data:[^"]+)"')
_PISO_ORCAMENTO_MB = 0.5
_FOLGA_BYTES = 262144

def bytes_de_tiles(html):
    """Quanto do HTML é tile embutido (base64)."""
    return sum((len(u) for u in _RX_TILE_EMBUTIDO.findall(html)))

def caber_no_teto(montar, teto_mb, rotulo=''):
    'Monta o HTML FINAL e, se passar de `teto_mb`, monta de novo com menos tile.\n\n    `montar(orcamento_mb)` devolve o arquivo exatamente como vai para o disco\n    (modo seguro, ai_poison e marca d\'água incluídos) — por isso a medida é do\n    arquivo entregue, não de uma estimativa do gerador.\n\n    O corte reaproveita a regra de `montar_pacote`: as camadas gastam o\n    orçamento de baixo para cima (contexto → cidade → detalhe) e, ao estourar,\n    caem os zooms MAIS FUNDOS. O mapa perde aproximação (z17 vira z16\n    ampliado), não perde o fundo, e nunca cai no ONLINE — que no celular é o\n    "Access blocked" (ver o topo deste módulo).\n\n    Exceção, e ela é ruidosa (log ERROR): quando o DADO + o fundo MÍNIMO não\n    cabem. O mínimo é o 1º zoom de cada camada (`montar_pacote` sempre o aceita;\n    o contexto pode chegar a 400 tiles) com orçamento de no mínimo 0,5 MB. Abaixo\n    disso cortar mais tiraria o fundo, ou seja, ONLINE, ou seja, "Access blocked".\n    A escolha é um arquivo grande com fundo, em vez de um que abre quebrado.\n\n    ⚠️ Mede a str em UTF-8 com "\n". Quem grava tem que usar `newline=""`: no\n    Windows o modo texto troca cada "\n" por "\r\n" e o arquivo passa do teto\n    (adversário, 2026-11-03).\n    '
    def _tamanho(s):
        return len(s.encode('utf-8', errors='xmlcharrefreplace'))

    teto = int(((teto_mb * 1024) * 1024))
    orcamento = teto_mb
    html = montar(orcamento)
    for _ in range(4):
        tamanho = _tamanho(html)
        if tamanho <= teto:
            return html
        tiles = bytes_de_tiles(html)
        alvo = max((((tiles - (tamanho - teto)) - _FOLGA_BYTES) / 1048576), _PISO_ORCAMENTO_MB)
        if tiles == 0 or alvo >= orcamento:
            break
        if rotulo:
            pass
        else:
            pass
        logger.info('basemap%s: %.1f MB passa do teto de %.1f MB — tiles de %.1f para %.1f MB', f" [{rotulo}]", '', (tamanho / 1048576), teto_mb, (tiles / 1048576), alvo)
        orcamento = alvo
        html = montar(orcamento)
    tamanho = _tamanho(html)
    if tamanho > teto:
        if rotulo:
            pass
        else:
            pass
        logger.error('basemap%s: arquivo com %.1f MB, acima do teto de %.1f MB mesmo com o mínimo de tile (%.1f MB) — o WhatsApp não vai abrir', f" [{rotulo}]", '', (tamanho / 1048576), teto_mb, (bytes_de_tiles(html) / 1048576))
    return html

