"""
Onde escrever o código do setor ("SCPM 13") por cima do mapa.

Um setor pode ter várias áreas, e o rótulo vai em CADA área, nunca no meio
entre elas. Dois casos:

- Áreas soltas (MultiPolygon, ou arquivos do mesmo código em lugares diferentes):
  cada uma ganha o seu rótulo.
- Áreas ligadas por uma ou poucas ruas (ilha com ponte): o polígono é um só,
  mas o rótulo no centro dele cairia na ponte, ou em cima de OUTRO setor.

Por que não "abertura morfológica" (buffer negativo + positivo) para cortar as
pontes: o setor da Locar é desenhado como buffer das RUAS (~30 m de largura,
medido em Cuiabá). O setor inteiro é fino; a abertura que corta a ponte apaga o
bairro junto. O que separa bairro de ponte é a DENSIDADE: num quarteirão a malha
cobre ~50% de uma janela de 300 m; uma rua só, ~10%.

Algoritmo (só numpy; o .exe exclui scipy):
1. Rasteriza a área do setor numa grade de CELL_M metros (CRS UTM do lugar).
2. Densidade = fração de células ocupadas numa janela de ±DENSITY_RADIUS_M.
3. Núcleo = célula ocupada com densidade >= CORE_DENSITY. Núcleos a menos de
   LINK_GAP_M um do outro são ligados (dilatação). A rua que liga dois núcleos
   distantes não é núcleo: não ganha rótulo nem os une.
3b. Ponte curta ainda liga (a densidade "vaza" do bairro para ela): a área
   ligada é erodida por NECK_CUT_M, a faixa da ponte some, e cada miolo que
   sobra é um cluster com um rótulo.
4. Pedaço do setor sem núcleo nenhum (uma rua isolada, um trecho rural) é uma
   área solta: ganha rótulo se tiver ao menos MIN_LOOSE_AREA_M2.
5. O rótulo vai na célula de núcleo mais perto do centro do cluster: sempre em
   cima da área do próprio setor.
"""

from __future__ import annotations
import math
from collections import deque
from typing import Dict, List, Optional
import numpy as np
import shapely
from project_heatmaps.parsers import split_code_parts
CELL_M = 20.0
DENSITY_RADIUS_M = 150.0
CORE_DENSITY = 0.25
LINK_GAP_M = 120.0
NECK_CUT_M = 90.0
LOOSE_LINK_GAP_M = 200.0
LOOSE_NECK_CUT_M = 140.0
LOOSE_FROM_CORE_M = 300.0
MERGE_GAP_M = 400.0
MERGE_MAX_SPAN_M = 900.0
MERGE_INSET_M = 30.0
MERGE_OTHER_TOL_M2 = 20.0
GAP_CLOSE_M = 100.0
MIN_LOOSE_AREA_M2 = 3000.0
_MAX_CELLS = 4000000

def sector_label_code(sector_name):
    """'SCPM 13 SEG QUA SEX' -> 'SCPM 13' (sem o sufixo de agenda)."""
    prefix, number, _ = split_code_parts(str((sector_name or '')))
    return f"{prefix} {number}".strip() if number else prefix

def _box_sum(a, k):
    """Soma de `a` na janela (2k+1)x(2k+1) de cada célula (tabela de somas)."""
    p = np.pad(a.astype(np.int32), (k + 1))
    s = p.cumsum(0).cumsum(1)
    n = ((2 * k) + 1)
    h, w = a.shape
    return (s[n:n + h, n:n + w] - s[0:h, n:n + w] - s[n:n + h, 0:w] + s[0:h, 0:w])

def _components(mask):
    """Rótulo de componente 8-conexo por célula (0 = fora). BFS só nas ocupadas."""
    lab = np.zeros(mask.shape, dtype=np.int32)
    h, w = mask.shape
    nxt = 0
    for r0, c0 in zip(*np.nonzero(mask)):
        if lab[(r0, c0)]:
            continue
        nxt += 1
        lab[(r0, c0)] = nxt
        q = deque([(r0, c0)])
        while q:
            r, c = q.popleft()
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr:
                        if rr < h:
                            pass
                    else:
                        continue
                    if 0 <= cc:
                        if cc < w:
                            pass
                    else:
                        continue
                    if mask[(rr, cc)] and not lab[(rr, cc)]:
                        lab[(rr, cc)] = nxt
                        q.append((rr, cc))
    return lab

def _rasterize(geom, cell):
    """Grade de ocupação do polígono (centro da célula dentro) + origem."""
    x0, y0, x1, y1 = geom.bounds
    nx = max(1, int(math.ceil(((x1 - x0) / cell))))
    ny = max(1, int(math.ceil(((y1 - y0) / cell))))
    occ = np.zeros((ny, nx), dtype=bool)
    parts = getattr(geom, 'geoms', [geom])
    for part in parts:
        if part.is_empty or part.area <= 0:
            continue
        shapely.prepare(part)
        px0, py0, px1, py1 = part.bounds
        c0 = max(0, int(((px0 - x0) / cell)))
        c1 = min(nx, (int(math.ceil(((px1 - x0) / cell))) + 1))
        r0 = max(0, int(((py0 - y0) / cell)))
        r1 = min(ny, (int(math.ceil(((py1 - y0) / cell))) + 1))
        xs = (x0 + ((np.arange(c0, c1) + 0.5) * cell))
        ys = (y0 + ((np.arange(r0, r1) + 0.5) * cell))
        X, Y = np.meshgrid(xs, ys)
        inside = shapely.contains_xy(part, X, Y)
        if not inside.any():
            rp = part.representative_point()
            r = min((ny - 1), int(((rp.y - y0) / cell)))
            c = min((nx - 1), int(((rp.x - x0) / cell)))
            occ[(r, c)] = True
        else:
            occ[r0:r1, c0:c1] |= inside
    return (occ, x0, y0)

def _split_by_necks(member, g, e):
    """
    Clusters de `member`: células a até 2g uma da outra ficam ligadas
    (dilatação por g), e a área ligada é cortada nos gargalos.

    Ponte CURTA: o bairro dos dois lados chega até ela (a janela de densidade
    enxerga o bairro), e a dilatação vira uma faixa que liga os dois bairros
    num cluster só, com o rótulo no meio da ponte (achado do auditor-claims,
    pontes de 200–300 m). Erodir a área ligada por e células apaga a faixa
    (~2g + largura da rua) e mantém os bairros: cada miolo que sobra é um
    cluster. Área pequena demais para ter miolo vira um cluster inteira.

    Returns:
        [(rows, cols, anchor_rows, anchor_cols, tem_miolo)] — as células do
        cluster, as candidatas a receber o rótulo (as do miolo, longe da ponte)
        e se o cluster veio de um miolo (False = área inteira sem miolo).
    """
    h, w = member.shape
    linked = _box_sum(member, g) > 0
    linked_lab = _components(linked)
    interior_lab = _components(_box_sum(linked, e) == (((2 * e) + 1) ** 2))
    clusters = []
    for lab in range(1, (int(interior_lab.max()) + 1)):
        ir, ic = np.nonzero(interior_lab == lab)
        r0, r1 = max(0, (ir.min() - e)), min(h, ((ir.max() + e) + 1))
        c0, c1 = max(0, (ic.min() - e)), min(w, ((ic.max() + e) + 1))
        grown = _box_sum(interior_lab[r0:r1, c0:c1] == lab, e) > 0
        rr, cc = np.nonzero((grown & member[r0:r1, c0:c1]))
        if not len(rr):
            continue
        rr, cc = rr + r0, cc + c0
        inner = interior_lab[(rr, cc)] == lab
        clusters.append(True if inner.any() else True)
    with_interior = (set(np.unique(linked_lab[interior_lab > 0]).tolist()) - {0})
    for lab in range(1, (int(linked_lab.max()) + 1)):
        if lab in with_interior:
            continue
        rr, cc = np.nonzero((linked_lab == lab & member))
        if len(rr):
            clusters.append((rr, cc, rr, cc, False))
    return clusters

def _casco_livre(hull, others):
    """O casco (menos MERGE_INSET_M de borda) não cobre OUTRO setor além da tolerância."""
    miolo = hull.buffer((-MERGE_INSET_M))
    if miolo.is_empty:
        return True
    return all((o.intersection(miolo).area <= MERGE_OTHER_TOL_M2 for o in others if o.intersects(miolo)))

def _vao_livre(rr, cc, rot, ox, oy, cell, others):
    """
    Outro setor no VÃO entre os pedaços do cluster? O casco do cluster inteiro é
    largo demais para isso: o de uma estrada rural cobre bairros vizinhos que
    nem estão entre os pedaços, e partir por ele levou Cuiabá de 173 para 191
    rótulos. O vão de cada par de pedaços próximos é o casco das PONTAS que se
    olham (o trecho de cada um a até a distância entre eles + 50 m do outro),
    menos os próprios pedaços.
    """
    pedacos = []
    for u in np.unique(rot):
        m = rot == u
        pedacos.append(shapely.union_all(shapely.box((ox + (cc[m] * cell)), (oy + (rr[m] * cell)), (ox + ((cc[m] + 1) * cell)), (oy + ((rr[m] + 1) * cell)))))
    todos = shapely.union_all(pedacos).buffer(cell)
    for a in range(len(pedacos)):
        for b in range((a + 1), len(pedacos)):
            d = pedacos[a].distance(pedacos[b])
            if d > (2 * GAP_CLOSE_M):
                continue
            pa = pedacos[a].intersection(pedacos[b].buffer((d + 50)))
            pb = pedacos[b].intersection(pedacos[a].buffer((d + 50)))
            vao = shapely.union_all([pa, pb]).convex_hull.difference(todos)
            if vao.is_empty:
                continue
            if any((o.intersection(vao).area > MERGE_OTHER_TOL_M2 for o in others if o.intersects(vao))):
                return False
    return True

def label_anchors(geom, cell=CELL_M, others=()):
    """
    Pontos de rótulo de UM setor (geometria já unida, em CRS métrico).

    Returns:
        Lista de {"x", "y", "w", "h", "area"} — posição, largura/altura do
        cluster e área ocupada, em metros. Mais importante primeiro.
    """
    if geom is None or geom.is_empty or geom.area <= 0:
        return []
    x0, y0, x1, y1 = geom.bounds
    while (((x1 - x0) / cell) * ((y1 - y0) / cell)) > _MAX_CELLS:
        cell *= 2
    occ, ox, oy = _rasterize(geom, cell)
    k = max(1, int(round((DENSITY_RADIUS_M / cell))))
    density = (_box_sum(occ, k) / float((((2 * k) + 1) ** 2)))
    core = occ & density >= CORE_DENSITY
    g = max(1, int(round((LINK_GAP_M / (2 * cell)))))
    e = max(1, int(round((NECK_CUT_M / cell))))
    occ_lab = (_components(_box_sum(occ, g) > 0) * occ)
    gr = max(1, int(round((LOOSE_FROM_CORE_M / cell))))
    loose = (occ & (~(_box_sum(core, gr) > 0)))
    g_loose = max(1, int(round((LOOSE_LINK_GAP_M / (2 * cell)))))
    e_loose = max(1, int(round((LOOSE_NECK_CUT_M / cell))))
    clusters = _split_by_necks(core, g, e)
    soltos = [c for c in _split_by_necks(loose, g_loose, e_loose) if ((len(c[0]) * cell) * cell) >= MIN_LOOSE_AREA_M2]
    rotulados = (set(np.unique(occ_lab[core]).tolist()) | {int(occ_lab[(c[0][0], c[1][0])]) for c in soltos if c[4]})
    clusters += [c for c in soltos if c[4] or int(occ_lab[(c[0][0], c[1][0])]) not in rotulados]
    fisico = _components(occ)
    partidos = []
    for rr, cc, ar, ac, miolo in clusters:
        rot = fisico[(rr, cc)]
        unicos = np.unique(rot)
        if (len(unicos) == 1 or not others):
            partidos.append((rr, cc, ar, ac, miolo))
        else:
            passo = max(1, (len(rr) // 3000))
            casco = shapely.MultiPoint(np.column_stack([(ox + ((cc[::passo] + 0.5) * cell)), (oy + ((rr[::passo] + 0.5) * cell))])).convex_hull
            if (_casco_livre(casco, others) or _vao_livre(rr, cc, rot, ox, oy, cell, others)):
                partidos.append((rr, cc, ar, ac, miolo))
            else:
                pedacos = []
                for u in unicos:
                    m, am = rot == u, fisico[(ar, ac)] == u
                    if am.any():
                        pass
                    else:
                        pass
                    if am.any():
                        pass
                    else:
                        pass
                    rr[m](cc[m], (ar[am], rr[m], ac[am], cc[m], miolo))
                pedacos.sort(key=(lambda p: (-len(p[0]))))
                partidos += ([pedacos[0]] + [p for p in pedacos[1:] if ((len(p[0]) * cell) * cell) >= MIN_LOOSE_AREA_M2])
    clusters = partidos
    if not clusters:
        rp = geom.representative_point()
        bx0, by0, bx1, by1 = geom.bounds
        return [{'x': rp.x, 'y': rp.y, 'w': (bx1 - bx0), 'h': (by1 - by0), 'area': geom.area, 'bbox': geom.bounds}]
    out = []
    for rr, cc, ar, ac, _ in clusters:
        cy, cx = rr.mean(), cc.mean()
        i = int(np.argmin((((ar - cy) ** 2) + ((ac - cx) ** 2))))
        out.append({'x': (ox + ((ac[i] + 0.5) * cell)), 'y': (oy + ((ar[i] + 0.5) * cell)), 'w': (((cc.max() - cc.min()) + 1) * cell), 'h': (((rr.max() - rr.min()) + 1) * cell), 'area': ((len(rr) * cell) * cell), 'bbox': ((ox + (cc.min() * cell)), (oy + (rr.min() * cell)), (ox + ((cc.max() + 1) * cell)), (oy + ((rr.max() + 1) * cell)))})
    out.sort(key=(lambda a: -a['area']))
    return out

def _merge_fragments(anchors, others):
    """
    Junta rótulos do mesmo setor cujos fragmentos estão perto (aglomerativo:
    o par mais próximo primeiro, enquanto der).

    Um grupo só fecha se couber em MERGE_MAX_SPAN_M e se o casco convexo dele
    (menos MERGE_INSET_M de borda) não cobrir mais que MERGE_OTHER_TOL_M2 de
    outro setor. O rótulo vai no centro do casco — pode ficar entre os
    fragmentos, que é o pedido; nunca sobre outro setor.
    """
    from shapely.geometry import box
    grupos = [{'m': [a], 'caixa': box(*a['bbox'])} for a in anchors]
    def casco(g1, g2):
        return shapely.union_all([g1['caixa'], g2['caixa']]).convex_hull

    def livre(h):
        return _casco_livre(h, others)

    while True:
        melhor = None
        for i in range(len(grupos)):
            for j in range((i + 1), len(grupos)):
                gap = grupos[i]['caixa'].distance(grupos[j]['caixa'])
                if gap > MERGE_GAP_M or melhor and gap >= melhor[0]:
                    continue
                h = casco(grupos[i], grupos[j])
                x0, y0, x1, y1 = h.bounds
                if (max((x1 - x0), (y1 - y0)) > MERGE_MAX_SPAN_M or not livre(h)):
                    continue
                melhor = (gap, i, j, h)
        if melhor is None:
            break
        _, i, j, h = melhor
        grupos[i] = {'m': (grupos[i]['m'] + grupos[j]['m']), 'caixa': h}
        del grupos[j]
    out = []
    for g in grupos:
        if len(g['m']) == 1:
            out.append(g['m'][0])
        else:
            c = g['caixa'].centroid
            if any((o.buffer(MERGE_INSET_M).contains(c) for o in others)):
                out.extend(g['m'])
            else:
                x0, y0, x1, y1 = g['caixa'].bounds
                out.append({'x': c.x, 'y': c.y, 'w': (x1 - x0), 'h': (y1 - y0), 'area': sum((a['area'] for a in g['m'])), 'bbox': (x0, y0, x1, y1)})
    out.sort(key=(lambda a: -a['area']))
    return out

def sector_label_points(sectors, name_col='sector_name'):
    """
    Rótulos de todos os setores do GeoDataFrame, por código sem sufixo.

    Linhas com o mesmo código (agendas diferentes do mesmo setor) são unidas
    antes: o mesmo lugar não ganha dois rótulos iguais.

    Returns:
        {"SCPM 13": [[lat, lon, largura_m, area_m2], ...]} — WGS84, mais
        importante primeiro.
    """
    if (sectors is None or len(sectors) == 0 or name_col not in sectors.columns):
        return {}
    gdf = sectors[[name_col, 'geometry']].copy()
    gdf = gdf[(gdf.geometry.notna() & (~gdf.geometry.is_empty))]
    if gdf.empty:
        return {}
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    try:
        metric = gdf.estimate_utm_crs()
    except Exception:
        metric = 'EPSG:3857'
    gdf = gdf.to_crs(metric)
    gdf['_code'] = gdf[name_col].map(sector_label_code)
    gdf = gdf[gdf['_code'] != '']
    from pyproj import Transformer
    to_wgs = Transformer.from_crs(metric, 4326, always_xy=True)
    geoms = {}
    for code, grp in gdf.groupby('_code', sort=False):
        geom = shapely.make_valid(shapely.union_all(grp.geometry.values))
        polys = [g for g in getattr(geom, 'geoms', [geom]) if g.area > 0]
        if polys:
            geoms[code] = shapely.union_all(polys)
    codes = list(geoms)
    tree = shapely.STRtree([geoms[c] for c in codes])
    result = {}
    for code, geom in geoms.items():
        others = [geoms[codes[i]] for i in tree.query(geom.envelope.buffer(MERGE_MAX_SPAN_M)) if codes[i] != code]
        labels = []
        for a in _merge_fragments(label_anchors(geom, others=others), others):
            lon, lat = to_wgs.transform(a['x'], a['y'])
            labels.append([round(lat, 6), round(lon, 6), round(min(a['w'], (2.5 * a['h'])), 1), round(a['area'], 1)])
        result[code] = labels
    return result

def sector_stats(segments):
    """
    % de coleta de cada setor, para a barra lateral seguir o setor em foco.

    MESMAS funções da média geral do mapa (`compute_overall_collection_ratio`
    e `..._with_manual_ratio`: metro coletado / metro esperado), aplicadas às
    ruas do setor. Calculado aqui, não no navegador: o recálculo em JS do mapa
    não bate com o do Python nem sem filtro (ver STATE).

    Returns:
        {"SCPD 09": [coleta, coleta_com_falha_gps, com_manual, com_manual_gps]}
        (0–1; None quando o setor não tem rua esperada).
    """
    from project_heatmaps.stats_module import compute_overall_collection_ratio, compute_overall_with_manual_ratio
    if (segments is None or len(segments) == 0 or 'route_code_full' not in segments.columns):
        return {}
    from project_heatmaps.stats_module import missed_with_gps_gap
    segments = segments.copy()
    if 'missed_days' in segments.columns:
        segments['missed_days_with_gps_gap'] = missed_with_gps_gap(segments)
    chaves = segments['route_code_full'].fillna('').astype(str).map(sector_label_code)
    out = {}
    for code, df in segments.groupby(chaves):
        if not code:
            continue
        a = (compute_overall_collection_ratio(df) or (None, None))
        b = (compute_overall_with_manual_ratio(df) or (None, None))
        out[code] = [None if v is None else round(float(v), 5) for v in (*a, *b)]
    return out

def shared_sector_keys(segments):
    """
    Para cada rua, os OUTROS setores que têm a mesma rua (mesma geometria).

    O segmento é por rota (`merge_same_geometry=False`): a rua de divisa de dois
    setores vira duas linhas uma sobre a outra, e só a de cima recebe o mouse.
    Com a lista, o cartão da rua mostra os dois e o clique alterna entre eles.
    """
    if (segments is None or len(segments) == 0 or 'route_code_full' not in segments.columns):
        return []
    chaves = segments['route_code_full'].fillna('').astype(str).map(sector_label_code).tolist()
    geo = [shapely.normalize(g).wkb if not g.is_empty else None for g in segments.geometry if g is not None]
    por_geo = {}
    for g, k in zip(geo, chaves):
        if g is not None and k:
            por_geo.setdefault(g, set()).add(k)
    return [sorted((por_geo.get(g, set()) - k)) if g is not None else [] for g, k in zip(geo, chaves)]

def add_stats_to_features(features, stats):
    """Põe o % do setor (`sector_stats`) em todo setor do GeoJSON."""
    for f in features:
        props = f.setdefault('properties', {})
        props['stats'] = stats.get((props.get('code') or sector_label_code(props.get('name', ''))))

LABEL_PALETTE = ['#FFFFFF', '#FF8AD8', '#C9A8FF', '#FFC48A', '#E3CFA6']
NEIGHBOR_M = 1500.0

def label_colors(labels):
    """
    Uma cor por código, vizinhos com cores diferentes (coloração gulosa).

    A mesma cor em todas as áreas do setor: é a cor que diz "é o mesmo setor"
    quando o código aparece em dois lugares.
    """
    pts = {c: [(lat, lon) for lat, lon, *_ in v] for c, v in labels.items() if v}
    def perto(a, b):
        for la, lo in pts[a]:
            for lb, mb in pts[b]:
                dx = (((lo - mb) * 111320) * math.cos(math.radians(la)))
                dy = ((la - lb) * 110540)
                if ((dx * dx) + (dy * dy)) < (NEIGHBOR_M ** 2):
                    return True
        return False

    codes = sorted(pts)
    viz = {c: {o for o in codes if o != c and perto(c, o)} for c in codes}
    cor = {}
    for c in sorted(codes, key=(lambda c: ((-len(viz[c])), c))):
        usadas = [cor[o] for o in viz[c] if o in cor]
        livres = [p for p in LABEL_PALETTE if p not in usadas]
        if livres:
            pass
        else:
            pass
        cor[c] = min(LABEL_PALETTE, key=usadas.count)
    return cor

def add_labels_to_features(features, labels):
    """
    Põe `code` e `label_color` em todo setor do GeoJSON e `labels` no PRIMEIRO
    de cada código.

    Os pontos vão dentro do GeoJSON dos setores (e não numa variável própria)
    para seguir o mesmo caminho dele no modo seguro: criptografados junto.
    """
    pending = dict(labels)
    cores = label_colors(labels)
    for f in features:
        props = f.setdefault('properties', {})
        code = sector_label_code(props.get('name', ''))
        props['code'] = code
        props['label_color'] = cores.get(code, LABEL_PALETTE[0])
        props['labels'] = pending.pop(code, [])

SECTOR_LABELS_CSS = """
        .sector-label { background: none; border: none; }
        .sector-label span {
            position: absolute; left: 0; top: 0;
            transform: translate(-50%, -50%);
            white-space: nowrap; pointer-events: auto; cursor: pointer;
            font-family: 'Segoe UI', Roboto, Arial, sans-serif;
            font-weight: 800; letter-spacing: 0.06em;
            opacity: 0.95;
            text-shadow:
                -1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000, -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000,
                0 -1.5px 0 #000, 0 1.5px 0 #000, -1.5px 0 0 #000, 1.5px 0 0 #000,
                0 0 4px rgba(0, 0, 0, 0.6);
        }
"""
SECTOR_LABELS_JS = """
        function createSectorLabels(map, features, opts) {
            opts = opts || {};
            var MIN_PX = 11, MAX_PX = 30;
            if (!map.getPane('sectorLabelPane')) {
                map.createPane('sectorLabelPane');
                map.getPane('sectorLabelPane').style.zIndex = 425;
                map.getPane('sectorLabelPane').style.pointerEvents = 'none';
            }
            var layer = L.layerGroup();
            var items = [];
            var on = false;
            var focusKey = null;
            function esc(s) {
                var d = document.createElement('div');
                d.appendChild(document.createTextNode(s || ''));
                return d.innerHTML;
            }
            function dark(hex) {
                var h = String(hex || '#555555').replace('#', '');
                if (h.length !== 6) return '#333333';
                return '#' + [0, 2, 4].map(function(i) {
                    return Math.floor(parseInt(h.substr(i, 2), 16) * 0.55).toString(16).padStart(2, '0');
                }).join('');
            }
            (features || []).forEach(function(f) {
                var p = f.properties || {};
                (p.labels || []).forEach(function(l) {
                    var m = L.marker([l[0], l[1]], {
                        pane: 'sectorLabelPane', interactive: false, keyboard: false,
                        icon: L.divIcon({
                            className: 'sector-label', iconSize: null,
                            html: '<span style="color:' + (p.label_color || dark(p.color)) + '">' + esc(p.code) + '</span>'
                        })
                    });
                    // Código também foca o setor: mouse = prévia, clique = fixa. Sem
                    // deixar o evento chegar ao mapa (o clique do mapa solta o foco).
                    m.on('add', function() {
                        var el = m.getElement();
                        if (!el || el._focoLigado) return;
                        el._focoLigado = true;
                        L.DomEvent.disableClickPropagation(el);
                        L.DomEvent.on(el, 'mousemove', L.DomEvent.stopPropagation);
                        L.DomEvent.on(el, 'mouseenter', function() { if (opts.onHover) opts.onHover(p.code); });
                        L.DomEvent.on(el, 'mouseleave', function() { if (opts.onHover) opts.onHover(null); });
                        L.DomEvent.on(el, 'click', function(e) {
                            L.DomEvent.stop(e);
                            if (opts.onClick) opts.onClick(p.code);
                        });
                    });
                    m.addTo(layer);
                    items.push({ m: m, code: p.code, a: l[3], n: Math.max((p.code || '').length, 4) });
                });
            });
            items.sort(function(x, y) { return y.a - x.a; });

            // Tamanho SÓ pelo zoom (Jorge, 2026-10-28): todo código do mesmo zoom
            // tem o mesmo tamanho, seja de bairro grande ou de fragmento.
            function sizeForZoom(z) {
                return Math.max(MIN_PX, Math.min(MAX_PX, 11 + (z - 12) * 3));
            }

            function layout() {
                if (!on) return;
                var base = sizeForZoom(map.getZoom());
                var kept = [];
                // O setor em foco entra primeiro: a colisão nunca esconde o código dele.
                var ordem = focusKey === null ? items : items.filter(function(it) {
                    return it.code === focusKey;
                }).concat(items.filter(function(it) { return it.code !== focusKey; }));
                ordem.forEach(function(it) {
                    var el = it.m.getElement();
                    if (!el || !el.firstChild) return;
                    var foco = focusKey !== null && it.code === focusKey;
                    var fs = foco ? Math.min(MAX_PX * 1.25, base * 1.25) : base;
                    el.style.opacity = (focusKey === null || foco) ? '' : '0.15';
                    var pt = map.latLngToLayerPoint(it.m.getLatLng());
                    var hw = (0.62 * fs * it.n) / 2 + 4, hh = fs * 0.55 + 2;
                    var r = { x0: pt.x - hw, x1: pt.x + hw, y0: pt.y - hh, y1: pt.y + hh };
                    var hit = kept.some(function(k) {
                        return !(r.x1 < k.x0 || r.x0 > k.x1 || r.y1 < k.y0 || r.y0 > k.y1);
                    });
                    el.firstChild.style.fontSize = fs.toFixed(1) + 'px';
                    el.style.display = hit ? 'none' : '';
                    if (!hit) kept.push(r);
                });
            }
            map.on('zoomend', layout);
            return {
                count: items.length,
                // Foco num setor: os outros códigos esmaecem e o dele cresce.
                setFocus: function(key) {
                    focusKey = key || null;
                    layout();
                },
                setVisible: function(v) {
                    on = !!v;
                    if (on) { layer.addTo(map); layout(); }
                    else if (map.hasLayer(layer)) { map.removeLayer(layer); }
                }
            };
        }
"""

