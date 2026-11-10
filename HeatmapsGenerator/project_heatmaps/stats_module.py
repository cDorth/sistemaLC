# pycrefine: 2 places could not be reconstructed reliably; treat the source below as unverified there:
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in SegmentDayRows at SETUP_ANNOTATIONS (offset 10); Python 3.11]
#   - stack_underflow: an operand is missing from the stack [in segment_day_rows at CALL (offset 3180); Python 3.11]
"""
Funções de cálculo de estatísticas de coleta.

VERSÃO CORRIGIDA:
- Suporta o novo campo route_code_individual
- Melhor fallback para encontrar código da rota
- Validação de cobertura paralela (coverage ratio)
- Detecção de permanência em endpoints (dwell time) para coleta manual
- Novo status "possible_manual" para segmentos collection com dwell
"""

import hashlib
import math
from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional, Tuple
import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
from shapely.geometry import Point
from project_heatmaps.config import GEO
from project_heatmaps.parsers import compute_schedule_days_for_code, extract_plate_key, format_plate

def _display_plate(vid_str):
    """
    Placa para exibição a partir do `vehicle_id`, seja ele o nome do shapefile
    ("16.03.2026_DGG8F39_JABOATAO") ou a placa crua do DuckDB ("DGG-8F39").

    Como as duas fontes podem coexistir no mesmo mapa (união DuckDB +
    shapefiles), a placa passa pela chave canônica antes de ser formatada —
    senão a mesma carreta apareceria como dois veículos distintos nos popups
    e nas estatísticas por veículo.
    """
    if (vid_str is None or str(vid_str) in ('', 'nan')):
        return 'Desconhecido'
    text = str(vid_str)
    try:
        text.encode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        text = text.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
    return format_plate(extract_plate_key(text))

def _compute_coverage_ratio(gps_geometries, segment_geom, n_bins=10):
    """
    Calcula que fração do segmento é coberta por pontos GPS projetados.
    
    Divide o segmento em n_bins partes iguais. Para cada ponto GPS,
    projeta-o sobre a linha do segmento e marca o bin correspondente.
    Retorna a fração de bins cobertos.
    
    Uma passagem perpendicular na esquina cobre apenas 1-2 bins (~10-20%).
    Uma passagem paralela ao longo do segmento cobre a maioria dos bins.
    
    Args:
        gps_geometries: Série ou lista de geometrias Point (CRS projetado)
        segment_geom: LineString original do segmento (CRS projetado, sem buffer)
        n_bins: Número de bins para discretizar o segmento
        
    Returns:
        Fração de bins cobertos (0.0 a 1.0)
    """
    if segment_geom is None or segment_geom.is_empty:
        return 0.0
    seg_len = segment_geom.length
    if seg_len == 0:
        return 0.0
    bins_hit = set()
    for pt_geom in gps_geometries:
        if pt_geom is None or pt_geom.is_empty:
            continue
        try:
            dist = segment_geom.project(pt_geom)
            bin_idx = min(int(((dist / seg_len) * n_bins)), (n_bins - 1))
            bins_hit.add(bin_idx)
        except Exception:
            continue
    return (len(bins_hit) / n_bins)

def _check_dwell_at_endpoints(gps_group, segment_geom, dwell_radius=20.0, min_seconds=120.0, has_datahora=True):
    """
    Verifica permanência de veículo perto de um endpoint do segmento.
    
    Returns:
        Dict com:
        - detected: bool — se houve dwell suficiente
        - dwell_seconds: float — tempo total parado (velocidade ≤ 3 km/h) nos arredores
        - vicinity_seconds: float — tempo total em que o veículo ficou nos arredores
        Retorna {'detected': False, 'dwell_seconds': 0, 'vicinity_seconds': 0} se não detectado.
    """
    result = {'detected': False, 'dwell_seconds': 0.0, 'vicinity_seconds': 0.0, 'max_stop_seconds': 0.0, 'slow': False}
    if segment_geom is None or segment_geom.is_empty:
        return result
    if not has_datahora or '_dt_parsed' not in gps_group.columns:
        return result
    has_vehicle = 'vehicle_id' in gps_group.columns
    has_speed = 'VELOCIDADE' in gps_group.columns
    coords = list(segment_geom.coords)
    if len(coords) < 2:
        return result
    endpoints = [Point(coords[0][:2]), Point(coords[-1][:2])]
    best_vicinity = 0.0
    best_dwell = 0.0
    max_stop = 0.0
    slow = False
    detected = False
    for _item in endpoints:
        endpoint = _item
        dists = gps_group.geometry.apply((lambda g: g.distance(endpoint) if not g.is_empty else float('inf')))
        near_mask = dists <= dwell_radius
        near = gps_group[near_mask]
        if (near.empty or len(near) < 2):
            continue
        groups = near.groupby('vehicle_id') if has_vehicle else near
        for vid, vgrp in groups:
            times = vgrp['_dt_parsed'].dropna().sort_values()
            if len(times) < 2:
                continue
            vicinity_sec = (times.iloc[-1] - times.iloc[0]).total_seconds()
            dwell_sec = 0.0
            if has_speed:
                speeds = pd.to_numeric(vgrp.loc[(times.index, 'VELOCIDADE')], errors='coerce')
                sorted_times = times.values
                sorted_speeds = speeds.reindex(times.index).values
                for k in range((len(sorted_times) - 1)):
                    interval = (pd.Timestamp(sorted_times[(k + 1)]) - pd.Timestamp(sorted_times[k])).total_seconds()
                    if interval > 300:
                        continue
                    spd = sorted_speeds[k]
                    if pd.notna(spd) and spd <= 3.0:
                        dwell_sec += interval
                    continue
                finite = sorted_speeds[np.isfinite(sorted_speeds)]
                if len(finite) >= 2 and finite.max() <= GEO.corner_slow_kmh:
                    slow = True
            else:
                dwell_sec = vicinity_sec
            max_stop = max(max_stop, dwell_sec)
            if vicinity_sec >= min_seconds:
                detected = True
            if vicinity_sec > best_vicinity:
                best_vicinity = vicinity_sec
                best_dwell = dwell_sec
    result['detected'] = detected
    result['vicinity_seconds'] = best_vicinity
    result['dwell_seconds'] = best_dwell
    result['max_stop_seconds'] = max_stop
    result['slow'] = slow
    return result

def _tracks_as_fixes(t):
    """
    Reduz cada registro de track ao seu FIX (um ponto).

    As duas fontes chegam em formas diferentes: o DuckDB traz um Point por
    fix; o shapefile do Inlog traz uma LineString de 2 vértices por registro
    (a perna entre um fix e o seguinte, encadeadas sem folga). Com a perna
    pronta, o shapefile ganhava continuidade de graça — e sem guarda nenhuma
    contra atalho —, enquanto o DuckDB, que virou a fonte primária, só marcava
    o segmento onde um ponto caía dentro do buffer. Reduzir as duas ao fix
    põe as duas na MESMA regra: ponto no buffer, ou perna reconstruída por
    `_build_track_legs` (com guardas) cobrindo o segmento.
    """
    if t.empty:
        return t
    gt = t.geometry.geom_type
    if (gt == 'Point').all():
        return t.reset_index(drop=True)
    def _fix(g):
        if g is None or g.is_empty or g.geom_type == 'Point':
            return g
        if g.geom_type == 'LineString':
            return shapely.get_point(g, 0)
        return g.representative_point()

    t = t.copy()
    orig_geom = t.geometry.copy()
    t['geometry'] = [_fix(g) for g in t.geometry]
    is_line = (gt == 'LineString').to_numpy()
    if is_line.any() and 'DATAHORA' in t.columns:
        s = t[is_line].copy()
        s['_dt'] = pd.to_datetime(s['DATAHORA'], errors='coerce', dayfirst=True)
        s = s[s['_dt'].notna()]
        if not s.empty:
            keys = [s['vehicle_id'].astype(str)] if 'vehicle_id' in s.columns else []
            keys.append(pd.to_datetime(s['day']).dt.normalize())
            s = s.sort_values('_dt', kind='mergesort')
            keys = [k.loc[s.index] for k in keys]
            prev_dur = s.groupby(keys)['_dt'].diff().dt.total_seconds()
            last = s.loc[s.groupby(keys)['_dt'].idxmax()].copy()
            end_geom = orig_geom.loc[last.index]
            length = end_geom.length.to_numpy()
            kmh = pd.to_numeric(last['VELOCIDADE'], errors='coerce').fillna(5.0).to_numpy() if 'VELOCIDADE' in last.columns else np.full(len(last), 5.0)
            by_speed = (length / (np.maximum(kmh, 5.0) / 3.6))
            by_prev = prev_dur.reindex(last.index).to_numpy(dtype=float)
            secs = np.where((np.isfinite(by_prev) & by_prev > 0), by_prev, by_speed)
            secs = np.clip(secs, 1.0, GEO.leg_max_seconds)
            new_dt = (last['_dt'] + pd.to_timedelta(secs, unit='s'))
            if pd.api.types.is_datetime64_any_dtype(t['DATAHORA']):
                last['DATAHORA'] = new_dt.to_numpy()
            else:
                last['DATAHORA'] = new_dt.dt.strftime('%d/%m/%Y %H:%M:%S').to_numpy()
            last['geometry'] = [shapely.get_point(g, -1) for g in end_geom]
            last = last.drop(columns=['_dt'])
            t['_est_time'] = False
            last['_est_time'] = True
            t = pd.concat([t, last], ignore_index=True)
            t = gpd.GeoDataFrame(t, geometry='geometry', crs=orig_geom.crs)
    return t.reset_index(drop=True)

def _build_track_legs(fixes):
    """
    Liga fixes consecutivos do MESMO veículo no MESMO dia em pernas retas.

    A perna é a hipótese "o caminhão foi em linha reta de um fix ao outro".
    Ela só é aceita quando essa hipótese é plausível:

    - ``dt ≤ GEO.leg_max_seconds`` e ``dist ≤ GEO.leg_max_meters`` — acima
      disso a reta entre os fixes não diz nada sobre por onde ele passou;
    - **guarda de direção**: o veículo tem de ter CHEGADO ao fix inicial ou
      SAÍDO do fix final andando no sentido da perna (≤
      ``GEO.leg_max_turn_deg``). O carro que passa numa esquina, dá a volta
      no quarteirão e reaparece na outra esquina da mesma rua chega e sai na
      perpendicular — a perna entre as duas esquinas é descartada, senão
      ela "fecharia" a rua do meio. Não depende de velocidade reportada.

      Com UM lado só alinhado (o veículo virou na outra ponta), a perna tem
      ainda de andar a ≥ ``GEO.leg_turn_speed_ratio`` × a velocidade da perna
      vizinha alinhada: quem dá a volta no quarteirão e depois SEGUE pela
      mesma rua tem um lado alinhado, mas a reta entre as esquinas sai lenta
      demais para o tempo gasto (achado do `adversario`, 2ª rodada).

      Custo conhecido: quem ENTRA na rua por uma esquina e SAI pela outra com
      um fix só em cada esquina é indistinguível de quem deu a volta, e
      também é descartado. Com fix a cada ~12 s em movimento (medido), rua
      percorrida de verdade quase sempre tem fix no meio.

    Sem DATAHORA não há ordem dos fixes e nenhuma perna é criada.

    Returns:
        (legs, info, fix_heads) — ``legs`` com colunas ``day`` + geometria
        LineString; ``info`` com contagens para o log; ``fix_heads`` com a
        direção de chegada (``hin``) e de saída (``hout``) de cada fix, em
        radianos, indexado como ``fixes`` (NaN = sem perna em movimento).
    """
    info = {'candidatas': 0, 'aceitas': 0, 'sem_direcao': 0, 'longas': 0}
    empty = gpd.GeoDataFrame({'day': pd.Series(dtype='datetime64[ns]')}, geometry=gpd.GeoSeries([], crs=fixes.crs), crs=fixes.crs)
    no_heads = pd.DataFrame({'hin': pd.Series(dtype=float), 'hout': pd.Series(dtype=float)})
    if fixes.empty or 'DATAHORA' not in fixes.columns:
        return (empty, info, no_heads)
    f = fixes[(fixes.geometry.notna() & (~fixes.geometry.is_empty))].copy()
    f = f[f.geometry.geom_type == 'Point']
    f['_dt'] = pd.to_datetime(f['DATAHORA'], errors='coerce', dayfirst=True)
    f = f[f['_dt'].notna()]
    if len(f) < 2:
        return (empty, info, no_heads)
    if 'vehicle_id' in f.columns:
        f['_veh'] = f['vehicle_id'].astype(str)
    else:
        f['_veh'] = '_all'
    f = f.sort_values(['_veh', '_dt'], kind='mergesort')
    x = f.geometry.x.to_numpy()
    y = f.geometry.y.to_numpy()
    veh = f['_veh'].to_numpy()
    day = pd.to_datetime(f['day']).dt.normalize().to_numpy()
    same = veh[1:] == veh[:-1] & day[1:] == day[:-1]
    dt_s = (np.diff(f['_dt'].to_numpy()).astype('timedelta64[ms]').astype(float) / 1000.0)
    dist = np.hypot(np.diff(x), np.diff(y))
    cand = ((same & dt_s > 0) & dist >= 0.5)
    info['candidatas'] = int(cand.sum())
    longas = (cand & (dt_s > GEO.leg_max_seconds | dist > GEO.leg_max_meters))
    keep = (cand & (~longas))
    info['longas'] = int(longas.sum())
    ang = np.arctan2(np.diff(y), np.diff(x))
    moving = ((((same & dt_s > 0) & dt_s <= GEO.leg_max_seconds) & dist >= GEO.leg_heading_min_meters) & dist <= GEO.leg_max_meters)
    grp = pd.Series(np.cumsum(np.r_[(True, (~same))])[:-1])
    head = pd.Series(np.where(moving, ang, np.nan))
    est = f['_est_time'].fillna(False).to_numpy(dtype=bool) if '_est_time' in f.columns else np.zeros(len(f), dtype=bool)
    vel_known = (~(est[:-1] | est[1:]))
    with np.errstate(divide='ignore', invalid='ignore'):
        vel = pd.Series(np.where((moving & vel_known), (dist / dt_s), np.nan))
    prev_dir = head.groupby(grp).shift(1).groupby(grp).ffill().to_numpy()
    next_dir = head.groupby(grp).shift(-1).groupby(grp).bfill().to_numpy()
    prev_vel = vel.groupby(grp).shift(1).groupby(grp).ffill().to_numpy()
    next_vel = vel.groupby(grp).shift(-1).groupby(grp).bfill().to_numpy()
    def _turn(a, b):
        return np.abs(((((a - b) + np.pi) % (2 * np.pi)) - np.pi))

    max_turn = np.radians(GEO.leg_max_turn_deg)
    with np.errstate(divide='ignore', invalid='ignore'):
        v_leg = np.where(vel_known, (dist / dt_s), np.nan)
        al_prev = _turn(prev_dir, ang) <= max_turn
        al_next = _turn(ang, next_dir) <= max_turn
        r = GEO.leg_turn_speed_ratio
        prev_unknown = (np.isnan(prev_vel) | np.isnan(v_leg))
        next_unknown = (np.isnan(next_vel) | np.isnan(v_leg))
        ok_prev = np.where(prev_unknown, np.isnan(next_dir), v_leg >= (r * prev_vel))
        ok_next = np.where(next_unknown, np.isnan(prev_dir), v_leg >= (r * next_vel))
        aligned = (((al_prev & al_next) | ((al_prev & (~al_next)) & ok_prev)) | ((al_next & (~al_prev)) & ok_next))
    sem_direcao = (keep & (~aligned))
    info['sem_direcao'] = int(sem_direcao.sum())
    keep &= aligned
    head_ff = head.groupby(grp).ffill().to_numpy()
    head_bf = head.groupby(grp).bfill().to_numpy()
    n_fix = len(f)
    hin = np.full(n_fix, np.nan)
    hout = np.full(n_fix, np.nan)
    hin[1:] = np.where(same, head_ff, np.nan)
    hout[:-1] = np.where(same, head_bf, np.nan)
    fix_heads = pd.DataFrame({'hin': hin, 'hout': hout}, index=f.index)
    idx = np.nonzero(keep)[0]
    info['aceitas'] = int(len(idx))
    if len(idx) == 0:
        return (empty, info, fix_heads)
    coords = np.stack([x[idx], y[idx], x[(idx + 1)], y[(idx + 1)]], axis=1).reshape(-1, 2, 2)
    legs = gpd.GeoDataFrame({'day': day[idx]}, geometry=shapely.linestrings(coords), crs=fixes.crs)
    return (legs, info, fix_heads)

def _stop_sessions(fixes):
    """
    Fixes PARADOS (≤ 3 km/h), cada um com a duração total da sua sessão de
    parada (fixes parados consecutivos do mesmo veículo no mesmo dia).

    A duração soma o intervalo até o fix seguinte (limitado a 300 s, como no
    dwell) de cada fix parado. Sem DATAHORA ou VELOCIDADE: vazio.
    """
    empty = gpd.GeoDataFrame({'day': pd.Series(dtype='datetime64[ns]'), 'sess': pd.Series(dtype=int), 'dur': pd.Series(dtype=float)}, geometry=gpd.GeoSeries([], crs=fixes.crs), crs=fixes.crs)
    if fixes.empty or 'DATAHORA' not in fixes.columns or 'VELOCIDADE' not in fixes.columns:
        return empty
    f = fixes[(fixes.geometry.notna() & fixes.geometry.geom_type == 'Point')].copy()
    f['_dt'] = pd.to_datetime(f['DATAHORA'], errors='coerce', dayfirst=True)
    f = f[f['_dt'].notna()]
    if f.empty:
        return empty
    if 'vehicle_id' in f.columns:
        pass
    else:
        pass
    f['_veh'] = '_all'
    f = f.sort_values(['_veh', '_dt'], kind='mergesort')
    v = pd.to_numeric(f['VELOCIDADE'], errors='coerce').to_numpy(dtype=float)
    veh = f['_veh'].to_numpy()
    day = pd.to_datetime(f['day']).dt.normalize().to_numpy()
    same_next = np.r_[((veh[1:] == veh[:-1] & day[1:] == day[:-1]), False)]
    nxt = np.r_[((np.diff(f['_dt'].to_numpy()).astype('timedelta64[ms]').astype(float) / 1000.0), 0.0)]
    nxt = np.where(same_next, np.minimum(nxt, 300.0), 0.0)
    stopped = v <= 3.0
    cont = np.r_[(False, ((stopped[1:] & stopped[:-1]) & same_next[:-1]))]
    sess = np.cumsum((~cont))
    s = f.loc[(stopped, ['day'])].copy()
    s['sess'] = sess[stopped]
    s['_nxt'] = nxt[stopped]
    s['dur'] = s.groupby('sess')['_nxt'].transform('sum')
    s['day'] = pd.to_datetime(s['day']).dt.normalize()
    return gpd.GeoDataFrame(s[['day', 'sess', 'dur']], geometry=f.geometry[stopped].values, crs=fixes.crs)

def _trajectory_coverage(legs, seg_lines, buffer_meters, min_parallel):
    """
    Fração de cada segmento percorrida por pernas PARALELAS a ele, por dia.

    Para cada perna que cruza o buffer do segmento, pega o trecho da perna
    dentro do buffer e projeta suas pontas no segmento (linear referencing).
    O trecho só conta se for paralelo: extensão projetada / comprimento do
    trecho ≥ ``min_parallel``. Travessia perpendicular numa esquina projeta
    em ~0 e é descartada — é o que impede a perna que só CRUZA a rua de
    marcá-la como coletada.

    O buffer tem ponta RETA (``cap_style="flat"``): com ponta redonda, a
    perna que atravessa um segmento curto de avenida em linha reta ganharia
    8 m de cada lado dentro do buffer, projetados no mesmo ponto final, e um
    segmento de 5 m daria 5/21 de paralelismo — reprovado com o carro
    passando por cima dele.

    Returns:
        {(segment_id, day): fração coberta (0–1)} — união dos intervalos,
        então ida e volta sobre o mesmo trecho não contam em dobro.
    """
    if legs.empty or seg_lines.empty:
        return {}
    lines = seg_lines[['segment_id', 'geometry']].copy()
    lines = lines[(lines.geometry.notna() & (~lines.geometry.is_empty))]
    lines = lines[lines.geometry.length > 0]
    if lines.empty:
        return {}
    buf = lines.copy()
    buf['geometry'] = buf.geometry.buffer(buffer_meters, cap_style='flat')
    j = gpd.sjoin(legs[['day', 'geometry']], buf, how='inner', predicate='intersects')
    if j.empty:
        return {}
    line_map = dict(zip(lines['segment_id'], lines.geometry))
    buf_map = dict(zip(buf['segment_id'], buf.geometry))
    len_map = {sid: g.length for sid, g in line_map.items()}
    sid_arr = j['segment_id'].to_numpy()
    day_arr = pd.to_datetime(j['day']).dt.normalize().to_numpy()
    leg_arr = np.array(list(j.geometry), dtype=object)
    line_arr = np.array([line_map[s] for s in sid_arr], dtype=object)
    buf_arr = np.array([buf_map[s] for s in sid_arr], dtype=object)
    pieces = shapely.intersection(leg_arr, buf_arr)
    plen = shapely.length(pieces)
    coords, cidx = shapely.get_coordinates(pieces, return_index=True)
    if len(coords) == 0:
        return {}
    d = shapely.line_locate_point(line_arr[cidx], shapely.points(coords))
    n = len(pieces)
    dmin = np.full(n, np.inf)
    dmax = np.full(n, (-np.inf))
    np.minimum.at(dmin, cidx, d)
    np.maximum.at(dmax, cidx, d)
    spread = dmax - dmin
    ok = ((plen > 0 & np.isfinite(spread)) & spread >= (min_parallel * plen))
    if not ok.any():
        return {}
    iv = pd.DataFrame({'sid': sid_arr[ok], 'day': day_arr[ok], 'a': dmin[ok], 'b': dmax[ok]}).sort_values(['sid', 'day', 'a'])
    out = {}
    for (sid, day), g in iv.groupby(['sid', 'day'], sort=False):
        covered = 0.0
        cur_a = cur_b = None
        for a, b in zip(g['a'].to_numpy(), g['b'].to_numpy()):
            if cur_b is None or a > cur_b:
                if cur_b is not None:
                    covered += cur_b - cur_a
                cur_a, cur_b = a, b
            else:
                cur_b = max(cur_b, b)
        covered += cur_b - cur_a
        seg_len = len_map.get(sid, 0.0)
        if seg_len > 0:
            out[(sid, pd.Timestamp(day))] = min(1.0, (covered / seg_len))
    return out

def _fill_gps_gaps(seg, visited_by_seg, visit_row_counts, projected_crs=GEO.projected_crs, endpoint_tolerance=2.0, max_gap_size=1):
    """
    Preenche falhas de GPS em sequencias de segmentos contiguos.

    Detecta padroes tipo "Coletado -> Nao Coletado -> Coletado" dentro
    da mesma rota e marca o(s) segmento(s) intermediario(s) como coletado(s),
    pois a passagem continua do veiculo indica que o GPS falhou momentaneamente.

    Opera por rota e por dia: para cada dia esperado de cada rota, monta a
    cadeia de segmentos e identifica lacunas isoladas de ate ``max_gap_size``
    segmentos entre segmentos visitados.

    Args:
        seg: GeoDataFrame de segmentos (geometria original, sem buffer).
        visited_by_seg: dict {segment_id: set(days_visited)}.
                        Modificado in-place com os preenchimentos.
        visit_row_counts: dict {(segment_id, day): count}.
                          Modificado in-place com os preenchimentos.
        projected_crs: CRS projetado (metros) para calculo de distancias.
        endpoint_tolerance: Tolerancia em metros para considerar dois
                           endpoints como conectados.
        max_gap_size: Numero maximo de segmentos nao-visitados consecutivos
                      que serao preenchidos (padrao: 1).

    Returns:
        Quantidade total de preenchimentos realizados (segmento x dia).
    """
    route_col = 'route_code_used'
    if route_col not in seg.columns or seg.empty:
        return 0
    seg_proj = seg.to_crs(projected_crs) if seg.crs != projected_crs else seg.copy()
    total_fills = 0
    for route_code, group in seg_proj.groupby(route_col):
        if len(group) < 3:
            continue
        seg_endpoints = {}
        for _, row in group.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            coords = list(geom.coords)
            if len(coords) >= 2:
                seg_endpoints[row['segment_id']] = (np.array(coords[0][:2]), np.array(coords[-1][:2]))
        if len(seg_endpoints) < 3:
            continue
        sids = list(seg_endpoints.keys())
        adj = {sid: set() for sid in sids}
        tol2 = endpoint_tolerance ** 2
        for i in range(len(sids)):
            ep_a = seg_endpoints[sids[i]]
            for j in range((i + 1), len(sids)):
                ep_b = seg_endpoints[sids[j]]
                connected = False
                for pa in ep_a:
                    for pb in ep_b:
                        if np.sum(((pa - pb) ** 2)) <= tol2:
                            connected = True
                            break
                    if connected:
                        break
                if connected:
                    adj[sids[i]].add(sids[j])
                    adj[sids[j]].add(sids[i])
        all_days = set()
        for sid in sids:
            all_days.update(visited_by_seg.get(sid, set()))
        for _, row in group.iterrows():
            exp = row.get('expected_dates', [])
            if isinstance(exp, (list, set)):
                all_days.update((pd.to_datetime(d).normalize() for d in exp))
        for _item in all_days:
            day = _item
            visited_today = {sid for sid in sids if day in visited_by_seg.get(sid, set())}
            if len(visited_today) < 2:
                continue
            for _pass in range(max_gap_size):
                fills_this_pass = []
                for sid in sids:
                    if sid in visited_today:
                        continue
                    neighbors = adj.get(sid, set())
                    if len(neighbors) < 2:
                        continue
                    visited_neighbors = neighbors & visited_today
                    if len(visited_neighbors) >= 2:
                        fills_this_pass.append(sid)
                if not fills_this_pass:
                    break
                for sid in fills_this_pass:
                    visited_by_seg.setdefault(sid, set()).add(day)
                    visit_row_counts.setdefault((sid, day), 0)
                    visit_row_counts[(sid, day)] += 1
                    visited_today.add(sid)
                    total_fills += 1
    return total_fills

def _aggregate_passage_details(raw_passages):
    """
    Agrega passagens brutas em resumo por veículo.
    
    Retorna lista de dicts, um por veículo, com:
    - vehicle: identificador do veículo
    - total_passages: número de registros
    - avg_speed: velocidade média (ou None)
    - passages: lista de {date, time, speed} ordenada por data/hora
    - first: primeira passagem
    - last: última passagem
    """
    from collections import defaultdict
    by_vehicle = defaultdict(list)
    for p in raw_passages:
        vid = p.get('vehicle', 'Desconhecido')
        by_vehicle[vid].append(p)
    result = []
    for vid, passages in sorted(by_vehicle.items()):
        def sort_key(p):
            dt = p.get('datetime', '')
            if dt:
                return dt
            return p.get('date', '')

        sorted_passages = sorted(passages, key=sort_key)
        speeds = [p['speed'] for p in sorted_passages if p.get('speed') is not None]
        avg_speed = round((sum(speeds) / len(speeds)), 1) if speeds else None
        seen = set()
        compact = []
        for p in sorted_passages:
            key = (p.get('date', ''), p.get('time', ''))
            if key not in seen:
                seen.add(key)
                compact.append({'date': p.get('date', ''), 'time': p.get('time', ''), 'speed': p.get('speed')})
        first_p = compact[0] if compact else {}
        last_p = compact[-1] if compact else {}
        result.append({'v': vid, 'n': len(compact), 's': avg_speed, 'f': [first_p.get('date', ''), first_p.get('time', ''), first_p.get('speed')], 'l': [last_p.get('date', ''), last_p.get('time', ''), last_p.get('speed')]})
    return result

REGRA_VERSAO = 1
_TRECHO_DIA_COLS = ['data', 'segment_id', 'rota_codigo', 'geom_chave', 'sentido', 'n_pontos_8m', 'interior_8m', 'dwell1_detectado', 'dwell1_s', 'vic1_s', 'cobertura_trajeto', 'tem_15m', 'interior_15m', 'dwell2_detectado', 'dwell2_lento', 'dwell2_s', 'vic2_s', 'cobertura_faixa', 'parada_esquina']
_TRECHO_DIA_VEICULO_COLS = ['data', 'segment_id', 'rota_codigo', 'geom_chave', 'sentido', 'placa', 'n_minutos', 'soma_vel', 'soma_vel_resto', 'n_vel', 'prim_chave', 'prim_data', 'prim_hora', 'prim_vel', 'ult_chave', 'ult_data', 'ult_hora', 'ult_vel']
@dataclass
class SegmentDayRows:
    """
    Linhas de "um contrato-dia → linhas por segmento" (forma das tabelas
    `trecho_dia` / `trecho_dia_veiculo` da sessão 3).

    Chave de cada linha: (`data`, `rota_codigo`, `geom_chave`, `sentido`) — a rota
    e a chave estável do trecho do registro de rotas (a mesma rua desenhada nos
    dois sentidos na mesma rota são dois segmentos: `sentido` os separa, como em
    `rota_trecho`). `segment_id` vai junto só
    para quem agrega no MESMO GeoDataFrame (o caminho de hoje); ele muda com o
    período e não pode ser gravado.

    - `trecho_dia`: uma linha por (segmento, dia) com QUALQUER prova (fix a ≤ 15 m,
      perna paralela, parada na esquina). Sem linha = nenhuma prova.
    - `trecho_dia_veiculo`: passagens a ≤ 8 m por placa de exibição.
    - `dias_com_gps`: dias com algum ponto (a data de referência do "dias sem visita").
    """
    trecho_dia: pd.DataFrame
    trecho_dia_veiculo: pd.DataFrame
    dias_com_gps: list
    @staticmethod
    def concat(parts):
        parts = list(parts)
        return SegmentDayRows(pd.concat(([p.trecho_dia for p in parts] or [pd.DataFrame(columns=_TRECHO_DIA_COLS)]), ignore_index=True), pd.concat(([p.trecho_dia_veiculo for p in parts] or [pd.DataFrame(columns=_TRECHO_DIA_VEICULO_COLS)]), ignore_index=True), sorted({d for p in parts for d in p.dias_com_gps}))

def segment_geom_key(geom):
    return _segment_key(geom)[0]

def _segment_key(geom):
    """
    `geom_chave` do registro de rotas (`locar_mysql/registro_rotas.chave_trecho`):
    SHA-256 dos vértices arredondados a 1 m, sentido normalizado. Copiada porque o
    .exe não leva `locar_mysql`; `test_paridade_dia.py` confere que as duas dão a
    mesma chave nos segmentos reais.
    """
    partes = list(geom.geoms) if hasattr(geom, 'geoms') else geom
    ida = [[(round(x), round(y)) for x, y, *_ in p.coords] for p in partes]
    volta = [p[::-1] for p in ida[::-1]]
    sentido = 1 if volta < ida else 0
    canon = volta if sentido else ida
    txt = '|'.join((';'.join((f"{x},{y}" for x, y in p)) for p in canon))
    return (hashlib.sha256((b'trecho-1m\x00' + txt.encode('ascii'))).hexdigest(), sentido)

def _find_route_code(row):
    priority_cols = ['route_code_individual', 'code_full', 'route_code', 'route_code_used', 'route_name', 'route_prefix', 'route']
    for c in priority_cols:
        if c in row and pd.notna(row[c]):
            val = str(row[c]).strip()
            if val and '; ' not in val:
                return val
    for c in ('route_codes_all', 'code_full'):
        if c in row and pd.notna(row[c]):
            val = str(row[c]).strip()
            if val:
                return val.split('; ')[0]
    return ''

def _with_route_code(segments):
    seg = segments.copy()
    if 'segment_id' not in seg.columns:
        seg = seg.reset_index().rename(columns={'index': 'segment_id'})
    seg['route_code_used'] = seg.apply(_find_route_code, axis=1)
    return seg

def _expected_dates_for_row(row, date_from, date_to):
    code = row.get('route_code_used', '')
    dates = []
    if code:
        try:
            dates = compute_schedule_days_for_code(code, date_from, date_to)
        except Exception:
            dates = []
    if not dates:
        wset = row.get('weekday_set', set())
        if isinstance(wset, set) and wset:
            rng = pd.date_range(start=pd.to_datetime(date_from), end=pd.to_datetime(date_to), freq='D')
            dates = [d.normalize() for d in rng if d.weekday() in wset]
    return sorted(set((pd.to_datetime(d).normalize() for d in dates)))

def _segments_in_period(segments, date_from, date_to):
    """Segmentos com ao menos um dia esperado no período, com `expected_dates`."""
    seg = _with_route_code(segments)
    seg['expected_dates'] = seg.apply((lambda r: _expected_dates_for_row(r, date_from, date_to)), axis=1)
    seg['total_days'] = seg['expected_dates'].apply(len)
    return seg[seg['total_days'] > 0].copy()

def _segment_class_map(seg):
    if 'classification' not in seg.columns:
        return {}
    return dict(zip(seg['segment_id'], seg['classification'].fillna('').str.lower().str.strip()))

def _mark_interior(jj, seg_orig_geom_map, fix_heads):
    """
    `_interior` = o fix vale para o segmento: projeta a ≥
    GEO.point_endpoint_margin_meters das duas PONTAS (fix na esquina está
    no buffer da rua cruzada tanto quanto no da percorrida) **e** o
    veículo chegou ou saiu dele andando AO LONGO do segmento (≤
    point_max_axis_deg, sem sentido). Quem cruza a rua no meio da quadra
    tem fix no buffer, mas anda na perpendicular. Fix sem direção nenhuma
    (parado o dia todo, fix isolado) vale — não há como dizer.
    """
    if jj.empty:
        jj['_interior'] = pd.Series(dtype=bool)
        return jj
    margin = GEO.point_endpoint_margin_meters
    lines_arr = np.array([seg_orig_geom_map.get(s) for s in jj['segment_id']], dtype=object)
    proj = shapely.line_locate_point(lines_arr, np.array(list(jj.geometry), dtype=object))
    seglen = shapely.length(lines_arr)
    margin = np.minimum(margin, (GEO.point_endpoint_margin_frac * seglen))
    interior = (proj >= margin & proj <= (seglen - margin))
    p1 = shapely.line_interpolate_point(lines_arr, np.clip((proj - 1.0), 0.0, seglen))
    p2 = shapely.line_interpolate_point(lines_arr, np.clip((proj + 1.0), 0.0, seglen))
    seg_ang = np.arctan2((shapely.get_y(p2) - shapely.get_y(p1)), (shapely.get_x(p2) - shapely.get_x(p1)))
    h = fix_heads.reindex(jj.index)
    hin = h['hin'].to_numpy(dtype=float)
    hout = h['hout'].to_numpy(dtype=float)
    def _axis_turn(a, b):
        return np.abs(((((a - b) + (np.pi / 2)) % np.pi) - (np.pi / 2)))

    max_turn = np.radians(GEO.point_max_axis_deg)
    with np.errstate(invalid='ignore'):
        along = ((_axis_turn(hin, seg_ang) <= max_turn | _axis_turn(hout, seg_ang) <= max_turn) | (np.isnan(hin) & np.isnan(hout)))
    jj['_interior'] = (interior & along)
    return jj

def _passage_sort_key(p):
    dt = p.get('datetime', '')
    if dt:
        return dt
    return p.get('date', '')

def _day_vehicle_passages(raw_passages):
    """
    As passagens de UM segmento num dia, por placa, reduzidas ao que
    `_aggregate_passage_details` precisa para refazer o período: minutos
    distintos, soma das velocidades, primeira e última passagem (com a chave de
    ordenação, que é o TEXTO "dd/mm/AAAA HH:MM:SS" — como hoje).
    """
    from collections import defaultdict
    by_vehicle = defaultdict(list)
    for p in raw_passages:
        by_vehicle[p.get('vehicle', 'Desconhecido')].append(p)
    out = []
    for vid, passages in by_vehicle.items():
        sorted_passages = sorted(passages, key=_passage_sort_key)
        speeds = [p['speed'] for p in sorted_passages if p.get('speed') is not None]
        seen = set()
        compact = []
        for p in sorted_passages:
            key = (p.get('date', ''), p.get('time', ''))
            if key not in seen:
                seen.add(key)
                compact.append(p)
        first_p, last_p = compact[0], compact[-1]
        out.append({'placa': vid, 'n_minutos': len(compact), 'soma_vel': sum(speeds), 'soma_vel_resto': math.fsum((speeds + [(-sum(speeds))])), 'n_vel': len(speeds), 'prim_chave': _passage_sort_key(first_p), 'prim_data': first_p.get('date', ''), 'prim_hora': first_p.get('time', ''), 'prim_vel': first_p.get('speed'), 'ult_chave': _passage_sort_key(last_p), 'ult_data': last_p.get('date', ''), 'ult_hora': last_p.get('time', ''), 'ult_vel': last_p.get('speed')})
    return out

def _period_passage_details(day_rows):
    """`passage_details` do período a partir das linhas por (dia, placa) — mesmo
    resultado de `_aggregate_passage_details` sobre todas as passagens juntas."""
    by_vehicle = {}
    for r in day_rows:
        by_vehicle.setdefault(r['placa'], []).append(r)
    result = []
    for vid, rs in sorted(by_vehicle.items()):
        rs = sorted(rs, key=(lambda r: r['data']))
        n_vel = sum((int(r['n_vel']) for r in rs))
        soma = sum(([float(r['soma_vel']) for r in rs] + [float(r['soma_vel_resto']) for r in rs]))
        first = min(rs, key=(lambda r: r['prim_chave']))
        last = max(rs, key=(lambda r: r['ult_chave']))
        def _vel(v):
            if v is not None:
                if isinstance(v, float):
                    pass
            return None if np.isnan(v) else float(v)

        if n_vel:
            pass
        else:
            pass
        result.append((vid, {
            'v': sum((int(r['n_minutos']) for r in rs)),
            'n': round((soma / n_vel), 1) if n_vel else None,
            's': None,
            'f': [first['prim_data'], first['prim_hora'], _vel(first['prim_vel'])],
            'l': [last['ult_data'], last['ult_hora'], _vel(last['ult_vel'])]
        }))
    return result

def segment_day_rows(tracks, segments, projected_crs=GEO.projected_crs, buffer_meters=GEO.buffer_meters):
    """
    Prova de coleta por (segmento × dia operacional), SEM decidir status.

    `tracks` já com `day` = dia operacional (`main._filter_tracks_by_contract_time`);
    pode ter um dia ou vários — cada prova é de um veículo num dia, então as
    linhas de um período são a união das linhas de cada dia. `segments` pode ser
    o conjunto de qualquer período (ou a pasta inteira): a prova de um segmento
    não depende dos outros.
    """
    t = tracks.copy()
    if 'day' not in t.columns:
        raise RuntimeError("tracks must contain a 'day' column")
    t['day'] = pd.to_datetime(t['day']).dt.normalize()
    dias_com_gps = sorted(t['day'].unique()) if not t.empty else []
    seg = _with_route_code(segments)
    seg_proj = seg.to_crs(projected_crs)
    seg_orig_geom_map = dict(zip(seg_proj['segment_id'], seg_proj.geometry))
    seg_cls_map = _segment_class_map(seg)
    seg_key = {sid: (code, *(_segment_key(g) if (g is not None and not g.is_empty) else ('', 0))) for sid, code, g in zip(seg_proj['segment_id'], seg['route_code_used'], seg_proj.geometry)}
    primeiro = {}
    for sid, k in seg_key.items():
        primeiro.setdefault(k, sid)
    grava = {sid for sid, k in seg_key.items() if primeiro[k] == sid}
    seg_buf = seg_proj[['segment_id', 'geometry']].copy()
    seg_buf['geometry'] = seg_buf.geometry.buffer(buffer_meters)
    t_proj = _tracks_as_fixes(t.to_crs(projected_crs)) if not t.empty else gpd.GeoDataFrame(columns=t.columns, geometry='geometry', crs=projected_crs)
    track_cols = ['geometry', 'day']
    has_vehicle_id = 'vehicle_id' in t_proj.columns
    has_datahora = 'DATAHORA' in t_proj.columns
    has_velocidade = 'VELOCIDADE' in t_proj.columns
    if has_vehicle_id:
        track_cols.append('vehicle_id')
    if has_datahora:
        track_cols.append('DATAHORA')
    if has_velocidade:
        track_cols.append('VELOCIDADE')
    if has_datahora and not t_proj.empty:
        legs, leg_info, fix_heads = _build_track_legs(t_proj)
    else:
        legs, leg_info = (None, None)
        fix_heads = pd.DataFrame({'hin': pd.Series(dtype=float), 'hout': pd.Series(dtype=float)})
    ev = {}
    def _row(sid, day):
        r = ev.get((sid, day))
        if r is None:
            ev[(sid, day)] = (r := {'n_pontos_8m': 0, 'interior_8m': False, 'dwell1_detectado': False, 'dwell1_s': np.nan, 'vic1_s': np.nan, 'cobertura_trajeto': np.nan, 'tem_15m': False, 'interior_15m': False, 'dwell2_detectado': False, 'dwell2_lento': False, 'dwell2_s': np.nan, 'vic2_s': np.nan, 'cobertura_faixa': np.nan, 'parada_esquina': False})
        return r

    passages = {}
    if not t_proj.empty and not seg_buf.empty:
        left = t_proj[[c for c in track_cols if c in t_proj.columns]].copy()
        j = gpd.sjoin(left, seg_buf, how='inner', predicate='intersects')
        if not j.empty:
            j['day'] = pd.to_datetime(j['day']).dt.normalize()
            j = _mark_interior(j, seg_orig_geom_map, fix_heads)
    else:
        j = gpd.GeoDataFrame(columns=['geometry', 'day', 'segment_id'])
    if not j.empty:
        if has_datahora:
            j['_dt_parsed'] = pd.to_datetime(j['DATAHORA'], errors='coerce', dayfirst=True)
        sids_arr = j['segment_id'].values
        days_arr = pd.to_datetime(j['day']).values
        vehicles_arr = j['vehicle_id'].astype(str).map(_display_plate).values if has_vehicle_id else np.full(len(j), 'Desconhecido')
        dates_str_arr = pd.to_datetime(j['day']).dt.strftime('%d/%m/%Y').values
        if has_datahora:
            dt_series = pd.to_datetime(j['DATAHORA'], errors='coerce', dayfirst=True)
            datetimes_str_arr = dt_series.dt.strftime('%d/%m/%Y %H:%M:%S').fillna('').values
            times_str_arr = dt_series.dt.strftime('%H:%M').fillna('').values
        else:
            datetimes_str_arr = np.full(len(j), '')
            times_str_arr = np.full(len(j), '')
        speeds_arr = pd.to_numeric(j['VELOCIDADE'], errors='coerce').values if has_velocidade else np.full(len(j), np.nan)
        for i in range(len(sids_arr)):
            spd = speeds_arr[i]
            if not np.isnan(spd):
                pass
            else:
                pass
            passages.setdefault((sids_arr[i], pd.Timestamp(days_arr[i])), []).append(vehicles_arr[i], {'vehicle': dates_str_arr[i], 'date': datetimes_str_arr[i], 'datetime': times_str_arr[i], 'time': round(float(spd), 1), 'speed': None})
            continue
        for (sid, day_val), grp in j.groupby(['segment_id', 'day']):
            r = _row(sid, pd.to_datetime(day_val).normalize())
            r['n_pontos_8m'] = len(grp)
            r['interior_8m'] = bool(grp['_interior'].any())
            if seg_cls_map.get(sid, '') == 'manual':
                d = _check_dwell_at_endpoints(grp, seg_orig_geom_map.get(sid), dwell_radius=GEO.dwell_radius_meters, min_seconds=GEO.dwell_min_seconds, has_datahora=has_datahora)
                r['dwell1_detectado'] = bool(d['detected'])
                r['dwell1_s'] = d['dwell_seconds']
                r['vic1_s'] = d['vicinity_seconds']
            continue
    if legs is not None:
        traj_sids = [sid for sid in seg_orig_geom_map if seg_cls_map.get(sid, '') != 'manual']
        seg_lines = gpd.GeoDataFrame({'segment_id': traj_sids}, geometry=[seg_orig_geom_map[s] for s in traj_sids], crs=projected_crs)
        for (sid, day_norm), frac in _trajectory_coverage(legs, seg_lines, buffer_meters, GEO.leg_min_parallel).items():
            _row(sid, day_norm)['cobertura_trajeto'] = frac
            continue
        print(f"  [trajeto] {leg_info['aceitas']:,} perna(s) de {leg_info['candidatas']:,} (descartadas: {leg_info['longas']:,} longas, {leg_info['sem_direcao']:,} sem direção)")
    recheck_buffer = GEO.gps_recheck_buffer_meters
    if recheck_buffer > buffer_meters or not t_proj.empty or not seg.empty:
        right_wide = seg_proj[['segment_id', 'geometry']].copy()
        right_wide['geometry'] = right_wide.geometry.buffer(recheck_buffer)
        left_wide = t_proj[[c for c in track_cols if c in t_proj.columns]].copy()
        j_wide = gpd.sjoin(left_wide, right_wide, how='inner', predicate='intersects')
        if not j_wide.empty:
            j_wide['day'] = pd.to_datetime(j_wide['day']).dt.normalize()
            j_wide = _mark_interior(j_wide, seg_orig_geom_map, fix_heads)
            if has_datahora:
                j_wide['_dt_parsed'] = pd.to_datetime(j_wide['DATAHORA'], errors='coerce', dayfirst=True)
            for (sid, day_val), grp_wide in j_wide.groupby(['segment_id', 'day']):
                r = _row(sid, pd.to_datetime(day_val).normalize())
                d = _check_dwell_at_endpoints(grp_wide, seg_orig_geom_map.get(sid), dwell_radius=GEO.dwell_radius_meters, min_seconds=GEO.dwell_min_seconds, has_datahora=has_datahora)
                r['tem_15m'] = True
                r['interior_15m'] = bool(grp_wide['_interior'].any())
                r['dwell2_detectado'] = bool(d['detected'])
                r['dwell2_lento'] = bool(d['slow'])
                r['dwell2_s'] = d['dwell_seconds']
                r['vic2_s'] = d['vicinity_seconds']
                continue
    if legs is not None and not legs.empty:
        band_sids = [sid for sid, g in seg_orig_geom_map.items() if seg_cls_map.get(sid, '') != 'manual' and g is not None and not g.is_empty]
        band_lines = gpd.GeoDataFrame({'segment_id': band_sids}, geometry=[seg_orig_geom_map[s] for s in band_sids], crs=projected_crs)
        for (sid, day_norm), frac in _trajectory_coverage(legs, band_lines, GEO.gps_parallel_band_meters, GEO.leg_min_parallel).items():
            _row(sid, day_norm)['cobertura_faixa'] = frac
            continue
    if has_datahora and has_velocidade or not t_proj.empty:
        stops = _stop_sessions(t_proj)
        stops = stops[stops['dur'] >= GEO.corner_stop_min_seconds]
        cand = [sid for sid, g in seg_orig_geom_map.items() if seg_cls_map.get(sid, '') != 'manual' and g is not None and not g.is_empty]
        if not stops.empty and cand:
            ep_sid, ep_geom = [], []
            for sid in cand:
                cs = list(seg_orig_geom_map[sid].coords)
                for c in (cs[0], cs[-1]):
                    ep_sid.append(sid)
                    ep_geom.append(Point(c[:2]).buffer(GEO.corner_radius_meters))
                    continue
                continue
            eps = gpd.GeoDataFrame({'segment_id': ep_sid}, geometry=ep_geom, crs=projected_crs)
            jc = gpd.sjoin(stops, eps, how='inner', predicate='intersects')
            for sid, day_val in set(zip(jc['segment_id'], jc['day'])):
                _row(sid, pd.Timestamp(day_val).normalize())['parada_esquina'] = True
                continue
    def _k(sid):
        return {'rota_codigo': seg_key[sid][0], 'geom_chave': seg_key[sid][1], 'sentido': seg_key[sid][2]}

    td = pd.DataFrame([{'data': day, 'segment_id': sid, **_k(sid), **r} for (sid, day), r in sorted(ev.items(), key=(lambda kv: (kv[0][1], str(kv[0][0])))) if sid in grava], columns=_TRECHO_DIA_COLS)
    tv = pd.DataFrame([{'data': day, 'segment_id': sid, **_k(sid), **v} for (sid, day), ps in passages.items() if sid in grava for v in _day_vehicle_passages(ps)], columns=_TRECHO_DIA_VEICULO_COLS)
    return SegmentDayRows(td, tv, [pd.Timestamp(d) for d in dias_com_gps])

def aggregate_segment_days(segments, rows, date_from, date_to, projected_crs=GEO.projected_crs, join_on='chave'):
    """
    Estatísticas de coleta por segmento do período, a partir das linhas por dia.

    `segments` é o conjunto do período (o mesmo que o mapa de hoje recebe);
    só entram linhas de dias do período e de segmentos dele. A decisão é a de
    sempre, na mesma ordem: fix a 8 m → propagação manual → trajeto → 15 m →
    faixa paralela → parada na esquina. `join_on="chave"` liga as linhas pelo
    (rota, geom_chave) — o caminho das linhas gravadas; `"segment_id"` só serve
    quando as linhas saíram deste mesmo GeoDataFrame.
    """
    seg = _segments_in_period(segments, date_from, date_to)
    seg_proj = seg.to_crs(projected_crs)
    seg_orig_geom_map = dict(zip(seg_proj['segment_id'], seg_proj.geometry))
    seg_len_map = {sid: float(g.length) if not g.is_empty else 0.0 for sid, g in seg_orig_geom_map.items() if g is not None}
    seg_cls_map = _segment_class_map(seg)
    d0, d1 = pd.to_datetime(date_from).normalize(), pd.to_datetime(date_to).normalize()
    dias = [d for d in rows.dias_com_gps if d0 <= d if d <= d1]
    if dias:
        pass
    else:
        pass
    reference_date = d1 if date_to else None
    def _in_universe(df):
        df = df[(pd.to_datetime(df['data']) >= d0 & pd.to_datetime(df['data']) <= d1)]
        if join_on == 'segment_id':
            return df[df['segment_id'].isin(set(seg_orig_geom_map))].copy()
        k2s = {}
        for sid, code, g in zip(seg['segment_id'], seg['route_code_used'], seg_proj.geometry):
            if g is not None:
                pass
            k = _segment_key(g) if not g.is_empty else tuple(('', 0))
            k2s.setdefault(k, []).append(sid)
        df = df.assign(segment_id=[k2s.get((c, g, int(sn)), []) for c, g, sn in zip(df['rota_codigo'], df['geom_chave'], df['sentido'])])
        return df.explode('segment_id').dropna(subset=['segment_id']).reset_index(drop=True)

    td = _in_universe(rows.trecho_dia)
    td['data'] = pd.to_datetime(td['data']).dt.normalize()
    td = td.sort_values(['segment_id', 'data'], kind='mergesort')
    recs = td.to_dict('records')
    visited_by_seg = {}
    visit_row_counts = {}
    visit_type_by_seg_day = {}
    dwell_info_by_seg = {}
    def _keep_best_dwell(sid, dwell_s, vic_s):
        prev = dwell_info_by_seg.get(sid)
        if (prev is None or vic_s > prev.get('vicinity_seconds', 0)):
            dwell_info_by_seg[sid] = {'dwell_seconds': dwell_s, 'vicinity_seconds': vic_s}
            return None

    for r in recs:
        if r['n_pontos_8m'] <= 0:
            continue
        sid, day = r['segment_id'], r['data']
        visit_row_counts[(sid, day)] = int(r['n_pontos_8m'])
        if seg_cls_map.get(sid, '') == 'manual':
            _keep_best_dwell(sid, r['dwell1_s'], r['vic1_s'])
            if r['dwell1_detectado']:
                visited_by_seg.setdefault(sid, set()).add(day)
                visit_type_by_seg_day[(sid, day)] = 'manual_collected'
        elif r['interior_8m']:
            visited_by_seg.setdefault(sid, set()).add(day)
            visit_type_by_seg_day[(sid, day)] = 'collected'
    manual_sids = [sid for sid, cls in seg_cls_map.items() if cls == 'manual']
    if len(manual_sids) >= 2:
        endpoint_tol = GEO.gps_gap_endpoint_tolerance
        tol2 = endpoint_tol ** 2
        manual_endpoints = {}
        for _item in manual_sids:
            sid = _item
            geom = seg_orig_geom_map.get(sid)
            if geom is None or geom.is_empty:
                continue
            coords = list(geom.coords)
            if len(coords) >= 2:
                manual_endpoints[sid] = (np.array(coords[0][:2]), np.array(coords[-1][:2]))
            continue
        adj_manual = {sid: set() for sid in manual_endpoints}
        sids_list = list(manual_endpoints.keys())
        for i in range(len(sids_list)):
            ep_a = manual_endpoints[sids_list[i]]
            for jj in range((i + 1), len(sids_list)):
                ep_b = manual_endpoints[sids_list[jj]]
                connected = False
                for pa in ep_a:
                    for pb in ep_b:
                        if np.sum(((pa - pb) ** 2)) <= tol2:
                            connected = True
                            max(dias)
                            break
                        continue
                    if connected:
                        break
                    continue
                if connected:
                    adj_manual[sids_list[i]].add(sids_list[jj])
                    adj_manual[sids_list[jj]].add(sids_list[i])
                continue
            continue
        all_manual_days = set()
        for _item in manual_sids:
            sid = _item
            all_manual_days.update(visited_by_seg.get(sid, set()))
            row_data = seg[seg['segment_id'] == sid]
            if not row_data.empty:
                exp = row_data.iloc[0].get('expected_dates', [])
                if isinstance(exp, (list, set)):
                    all_manual_days.update((pd.to_datetime(d).normalize() for d in exp))
            continue
        for _item in all_manual_days:
            day = _item
            collected_today = {sid for sid in manual_sids if day in visited_by_seg.get(sid, set())}
            if not collected_today:
                continue
            queue = list(collected_today)
            while queue:
                current = queue.pop(0)
                for neighbor in adj_manual.get(current, set()):
                    if neighbor not in collected_today:
                        collected_today.add(neighbor)
                        visited_by_seg.setdefault(neighbor, set()).add(day)
                        visit_type_by_seg_day[(neighbor, day)] = 'manual_collected'
                        queue.append(neighbor)
                    continue
            continue
    n_traj = 0
    for r in recs:
        sid, day = r['segment_id'], r['data']
        if (seg_cls_map.get(sid, '') == 'manual' or not r['cobertura_trajeto'] >= GEO.coverage_min_ratio):
            continue
        if day in visited_by_seg.get(sid, set()):
            continue
        visited_by_seg.setdefault(sid, set()).add(day)
        visit_type_by_seg_day[(sid, day)] = 'collected'
        n_traj += 1
    print(f"  [trajeto] +{n_traj:,} segmento-dia(s) coletado(s) sem fix no buffer")
    gps_gap_by_seg = {}
    possible_manual_by_seg = {}
    for r in recs:
        if not r['tem_15m']:
            continue
        sid, day = r['segment_id'], r['data']
        if day in visited_by_seg.get(sid, set()):
            continue
        is_manual = seg_cls_map.get(sid, '') == 'manual'
        _keep_best_dwell(sid, r['dwell2_s'], r['vic2_s'])
        corner_service = r['dwell2_detectado'] or r['dwell2_lento']
        if corner_service and not is_manual:
            possible_manual_by_seg.setdefault(sid, set()).add(day)
        elif r['dwell2_detectado'] and is_manual:
            visited_by_seg.setdefault(sid, set()).add(day)
            visit_type_by_seg_day[(sid, day)] = 'manual_collected'
        elif r['interior_15m']:
            gps_gap_by_seg.setdefault(sid, set()).add(day)
    n_band = 0
    for r in recs:
        sid, day = r['segment_id'], r['data']
        g = seg_orig_geom_map.get(sid)
        if (seg_cls_map.get(sid, '') == 'manual' or g is None or g.is_empty):
            continue
        if (not r['cobertura_faixa'] >= GEO.coverage_min_ratio or day in visited_by_seg.get(sid, set())):
            continue
        gps_gap_by_seg.setdefault(sid, set()).add(day)
        possible_manual_by_seg.get(sid, set()).discard(day)
        n_band += 1
    print(f"  [paralelo] +{n_band:,} segmento-dia(s) como possível falha de GPS (trajeto paralelo a ≤ {GEO.gps_parallel_band_meters:.0f} m)")
    n_corner = 0
    for r in recs:
        if not r['parada_esquina']:
            continue
        sid, day = r['segment_id'], r['data']
        g = seg_orig_geom_map.get(sid)
        if (seg_cls_map.get(sid, '') == 'manual' or g is None or g.is_empty):
            continue
        if (day in visited_by_seg.get(sid, set()) or day in gps_gap_by_seg.get(sid, set()) or day in possible_manual_by_seg.get(sid, set())):
            continue
        possible_manual_by_seg.setdefault(sid, set()).add(day)
        n_corner += 1
    print(f"  [esquina] +{n_corner:,} segmento-dia(s) como possível coleta manual (parada ≥ {GEO.corner_stop_min_seconds:.0f} s a ≤ {GEO.corner_radius_meters:.0f} m da ponta)")
    passage_rows = {}
    for v in _in_universe(rows.trecho_dia_veiculo).to_dict('records'):
        passage_rows.setdefault(v['segment_id'], []).append(v)
    out_rows = []
    for _, r in seg.iterrows():
        sid = r['segment_id']
        exp_dates = (set(r.get('expected_dates', [])) or set())
        total = len(exp_dates)
        cls = seg_cls_map.get(sid, '')
        visited_dates = visited_by_seg.get(sid, set())
        valid_visited = {d for d in visited_dates if d in exp_dates}
        n_days_visited = len(valid_visited)
        visit_count = sum((visit_row_counts.get((sid, d), 0) for d in valid_visited))
        n_days_manual_collected = 0
        for d in valid_visited:
            vt = visit_type_by_seg_day.get((sid, d), 'collected')
            if vt == 'manual_collected':
                n_days_manual_collected += 1
        pm_dates_raw = possible_manual_by_seg.get(sid, set())
        pm_valid = {d for d in pm_dates_raw if d in exp_dates and d not in valid_visited}
        n_days_possible_manual = len(pm_valid)
        gps_gap_dates_raw = gps_gap_by_seg.get(sid, set())
        gps_gap_valid = {d for d in gps_gap_dates_raw if d in exp_dates and d not in valid_visited and d not in pm_valid}
        n_gps_gap_days = len(gps_gap_valid)
        if n_days_visited > 0:
            first_visit = min(valid_visited)
            last_visit = max(valid_visited)
        else:
            first_visit = pd.NaT
            last_visit = pd.NaT
        missed = max(0, (total - n_days_visited))
        missed_with_gps_gap = max(0, ((missed - n_gps_gap_days) - n_days_possible_manual))
        never = n_days_visited == 0 and n_gps_gap_days == 0 and n_days_possible_manual == 0
        days_since = None
        if reference_date is not None and not pd.isna(last_visit):
            days_since = int((reference_date - last_visit).days)
        base = r.drop(labels=[c for c in r.index if c in ('geometry', 'expected_dates', 'total_days')], errors='ignore').to_dict()
        stats = {'segment_id': sid, 'route_code_used': r.get('route_code_used', ''), 'classification': cls, 'segment_length_m': round(seg_len_map.get(sid, 0.0), 2), 'total_days': int(total), 'n_days_visited': int(n_days_visited), 'visit_count': int(visit_count), 'missed_days': int(missed), 'missed_days_with_gps_gap': int(missed_with_gps_gap), 'n_gps_gap_days': int(n_gps_gap_days), 'n_days_possible_manual': int(n_days_possible_manual), 'n_days_manual_collected': int(n_days_manual_collected), 'never_visited': bool(never), 'first_visit_day': first_visit if not pd.isna(first_visit) else pd.NaT, 'last_visit_day': last_visit if not pd.isna(last_visit) else pd.NaT, 'days_since_visit': days_since, 'expected_dates': sorted(list(exp_dates)), 'visited_dates': sorted(list(valid_visited))}
        stats['passage_details'] = _period_passage_details(passage_rows.get(sid, []))
        dinfo = dwell_info_by_seg.get(sid, {})
        stats['dwell_seconds'] = dinfo.get('dwell_seconds', 0.0)
        stats['vicinity_seconds'] = dinfo.get('vicinity_seconds', 0.0)
        if 'geometry' in r and r['geometry'] is not None:
            stats['geometry'] = r['geometry']
        for k, v in base.items():
            if k not in stats:
                stats[k] = v
        out_rows.append(stats)
    if out_rows:
        df = pd.DataFrame(out_rows)
        gdf_stats = gpd.GeoDataFrame(df, geometry='geometry', crs=segments.crs)
    else:
        cols = ['segment_id', 'route_code_used', 'total_days', 'n_days_visited', 'visit_count', 'missed_days', 'missed_days_with_gps_gap', 'n_gps_gap_days', 'never_visited', 'first_visit_day', 'last_visit_day', 'days_since_visit', 'expected_dates', 'geometry']
        gdf_stats = gpd.GeoDataFrame(columns=cols, geometry='geometry', crs=segments.crs)
    for col in ('first_visit_day', 'last_visit_day'):
        if col in gdf_stats.columns:
            gdf_stats[col] = pd.to_datetime(gdf_stats[col], errors='coerce')
    ref_date = pd.to_datetime(reference_date).normalize() if reference_date is not None else None
    return (gdf_stats, ref_date)

def compute_segment_missed_days(tracks, segments, date_from, date_to, projected_crs=GEO.projected_crs, buffer_meters=GEO.buffer_meters):
    """
    Calcula estatísticas de coleta por segmento.

    Considera apenas dias esperados conforme agenda da rota. = prova por dia
    (`segment_day_rows`) + decisão do período (`aggregate_segment_days`).
    """
    t = tracks.copy()
    if 'day' not in t.columns:
        raise RuntimeError("tracks must contain a 'day' column")
    t['day'] = pd.to_datetime(t['day']).dt.normalize()
    t = t[(t['day'] >= pd.to_datetime(date_from) & t['day'] <= pd.to_datetime(date_to))].copy()
    seg = _segments_in_period(segments, date_from, date_to)
    rows = segment_day_rows(t, seg.drop(columns=['expected_dates', 'total_days']), projected_crs, buffer_meters)
    return aggregate_segment_days(segments, rows, date_from, date_to, projected_crs, join_on='segment_id')

def segment_length_weights(df):
    """
    Peso de cada segmento nas porcentagens agregadas: o comprimento (m).

    Dono único da régua "metro coletado / metro esperado" — usada pelo
    cabeçalho do mapa, pelo histórico, pelo relatório e pelo XML. Até
    2026-09-10 a média era POR SEGMENTO: um trecho de 100 m sem coleta pesava
    o mesmo que um toco de 0,5 m numa esquina.

    Lê ``segment_length_m`` (gravada por ``compute_segment_missed_days`` no
    CRS projetado); sem ela, cai para a geometria se o CRS for projetado; sem
    nenhuma das duas, peso 1 (média por segmento, o comportamento antigo).
    """
    if 'segment_length_m' in df.columns:
        w = pd.to_numeric(df['segment_length_m'], errors='coerce').fillna(0.0)
        if w.sum() > 0:
            return w
    if isinstance(df, gpd.GeoDataFrame) and 'geometry' in df.columns:
        try:
            g = df.geometry
            if g.crs is not None and not g.crs.is_projected:
                g = g.to_crs(GEO.projected_crs)
            if g.crs is not None:
                w = g.length.fillna(0.0)
                if w.sum() > 0:
                    return w
        except Exception:
            pass
    return pd.Series(1.0, index=df.index)

def missed_with_gps_gap(df):
    """
    Dias perdidos contando falha de GPS e possível coleta manual como cobertos —
    a MESMA regra de `compute_segment_missed_days` (dono dela).

    Existe porque a coluna `missed_days_with_gps_gap` das ruas do MAPA fica
    velha: passos depois da estatística (ex.: "[paralelo]", falha de GPS por
    trajeto paralelo) sobem `n_gps_gap_days`/`n_days_possible_manual` sem
    refazê-la. Medido em Cuiabá 21/10/2026: no mapa ela era igual a
    `missed_days` em todas as 8.170 ruas, e o "~98,2%" do cabeçalho virava 96,1%.
    """
    missed = pd.to_numeric(df['missed_days'], errors='coerce').fillna(0)
    gap = pd.to_numeric(df.get('n_gps_gap_days', 0), errors='coerce')
    poss = pd.to_numeric(df.get('n_days_possible_manual', 0), errors='coerce')
    gap = gap.fillna(0) if isinstance(gap, pd.Series) else gap
    poss = poss.fillna(0) if isinstance(poss, pd.Series) else poss
    return ((missed - gap) - poss).clip(lower=0)

def length_weighted_ratio(df, missed_col='missed_days'):
    """
    Σ comprimento × dias coletados / Σ comprimento × dias esperados (0–1).

    Em dia único é "metros coletados / metros esperados". Em período, cada
    segmento pesa comprimento × dias em que era esperado.
    """
    if df.empty:
        return None
    w = segment_length_weights(df)
    total = pd.to_numeric(df['total_days'], errors='coerce').fillna(0.0)
    missed = pd.to_numeric(df[missed_col], errors='coerce').fillna(total)
    den = float((w * total).sum())
    if den <= 0:
        return None
    return (float((w * (total - missed).clip(lower=0.0)).sum()) / den)

def _overall_ratio_pair(df):
    """(ratio_strict, ratio_with_gps_gap) ponderados por comprimento."""
    if 'total_days' not in df.columns or 'missed_days' not in df.columns:
        return None
    df = df[pd.to_numeric(df['total_days'], errors='coerce').fillna(0) > 0]
    ratio_strict = length_weighted_ratio(df, 'missed_days')
    if ratio_strict is None:
        return None
    ratio_with_gps_gap = length_weighted_ratio(df, 'missed_days_with_gps_gap') if 'missed_days_with_gps_gap' in df.columns else ratio_strict
    return (ratio_strict, ratio_with_gps_gap)

def compute_overall_collection_ratio(segments_stats):
    """
    Calcula media global de coleta DOMICILIAR (0-1), ponderada por
    comprimento de segmento (ver ``segment_length_weights``).

    EXCLUI segmentos manuais do cálculo.
    possible_manual já NÃO está em n_days_visited, então o ratio
    reflete apenas passagens reais.

    Retorna tupla (ratio_strict, ratio_with_gps_gap).
    """
    if segments_stats is None or segments_stats.empty:
        return None
    df = segments_stats
    if 'classification' in df.columns:
        cls = df['classification'].fillna('').astype(str).str.lower().str.strip()
        df = df[cls != 'manual']
    return _overall_ratio_pair(df)

def compute_overall_with_manual_ratio(segments_stats):
    """
    Calcula media global de coleta INCLUINDO manual (0-1), ponderada por
    comprimento de segmento.

    Inclui TODOS os segmentos (collection + manual) no denominador.

    Retorna tupla (ratio_strict, ratio_with_gps_gap).
    """
    if segments_stats is None or segments_stats.empty:
        return None
    return _overall_ratio_pair(segments_stats)

def route_sector_join(seg, sec):
    """
    Ruas do período com o setor de cada uma = o da sua ROTA (Jorge, 2026-10-28).

    Dono único da regra para o mapa de linhas (`build_segments_for_map`) e o de
    paradas (`stops_stats.build_stops_for_map`). Entra a rua cuja rota tem dia
    esperado no período (`total_days` > 0); o polígono do setor não decide nada.
    Devolve as colunas com os nomes que o sjoin antigo produzia
    (`code_full_route`, `weekday_set_route`, `schedule_label_route`,
    `sector_name`, `schedule_label_sector`) mais `rota_codigo`.
    """
    from project_heatmaps.sector_labels import sector_label_code
    if 'total_days' in seg.columns:
        seg = seg[pd.to_numeric(seg['total_days'], errors='coerce').fillna(0) > 0]
    join = seg.rename(columns={'code_full': 'code_full_route', 'weekday_set': 'weekday_set_route', 'schedule_label': 'schedule_label_route'}).reset_index(drop=True)
    if join.empty:
        return join
    rota = join.get('route_code_individual')
    rota = rota if rota is not None else pd.Series(index=join.index, dtype=object).astype(object)
    rota = rota.where((rota.notna() & rota.astype(str).str.strip() != ''), join['code_full_route'])
    join['rota_codigo'] = rota.fillna('').astype(str)
    join['sector_name'] = join['rota_codigo'].map(sector_label_code)
    agenda_setor = {}
    for r in sec.itertuples(index=False):
        nome = (getattr(r, 'sector_name', None) or getattr(r, 'code_full', ''))
        agenda_setor.setdefault(sector_label_code(str(nome)), (getattr(r, 'schedule_label', '') or ''))
    join['schedule_label_sector'] = join['sector_name'].map(agenda_setor).fillna(join['schedule_label_route'] if 'schedule_label_route' in join.columns else '')
    return join

def build_segments_for_map(segments_ideal, segments_stats, sectors):
    """
    Produz segmentos prontos para visualização no mapa.
    
    IMPORTANTE: Esta função espera receber setores JÁ FILTRADOS pela agenda
    do período selecionado (feito em main.py). Ela NÃO faz filtragem de agenda.
    
    O setor de cada segmento é o da sua ROTA (código sem sufixo de agenda) —
    não o polígono por onde a rua passa (ver o bloco "O SETOR É A ROTA").
    A geometria do segmento é mantida intacta (sem recorte/clip).
    """
    required_seg = {'code_full', 'first_visit_day', 'geometry', 'last_visit_day', 'missed_days', 'n_days_visited', 'schedule_label', 'total_days', 'weekday_set'}
    missing = (required_seg - set(segments_stats.columns))
    if missing:
        raise RuntimeError(f"segments_stats missing columns: {missing}")
    required_sec = {'code_full', 'geometry', 'weekday_set'}
    missing = (required_sec - set(sectors.columns))
    if missing:
        raise RuntimeError(f"sectors missing columns: {missing}")
    seg = segments_ideal.copy()
    sec = sectors.copy()
    stats_cols = ['segment_id', 'total_days', 'missed_days', 'missed_days_with_gps_gap', 'n_gps_gap_days', 'n_days_visited', 'first_visit_day', 'last_visit_day']
    for extra_col in ('classification', 'n_days_possible_manual', 'n_days_manual_collected', 'dwell_seconds', 'vicinity_seconds', 'segment_length_m'):
        if extra_col in segments_stats.columns:
            stats_cols.append(extra_col)
    if 'passage_details' in segments_stats.columns:
        stats_cols.append('passage_details')
    stats_cols = [c for c in stats_cols if c in segments_stats.columns]
    overlap_cols = [c for c in stats_cols if c in seg.columns and c != 'segment_id']
    seg = seg.drop(columns=overlap_cols, errors='ignore') if overlap_cols else seg.merge(segments_stats[stats_cols], on='segment_id', how='left')
    seg['n_days_visited'] = seg['n_days_visited'].fillna(0)
    seg['missed_days'] = seg['missed_days'].fillna(seg['total_days'])
    seg['missed_days_with_gps_gap'] = seg['missed_days_with_gps_gap'].fillna(seg['missed_days'])
    seg['n_gps_gap_days'] = seg['n_gps_gap_days'].fillna(0)
    if seg.crs != sec.crs:
        sec = sec.to_crs(seg.crs)
    join = route_sector_join(seg, sec)
    if join.empty:
        return gpd.GeoDataFrame(columns=(list(seg.columns) + ['sector_code']), geometry='geometry', crs=seg.crs)
    has_passage_details = 'passage_details' in join.columns
    rows = []
    for r in join.itertuples(index=False):
        route_code = r.rota_codigo
        if has_passage_details:
            pd_val = r.passage_details
            pd_list = pd_val if isinstance(pd_val, list) else []
        else:
            pd_list = []
        row_data = {'segment_id': r.segment_id, 'geometry': r.geometry, 'route_code_full': route_code, 'sector_code': r.sector_name, 'weekday_set': r.weekday_set_route, 'route_schedule_label': r.schedule_label_route, 'sector_schedule_label': r.schedule_label_sector, 'classification': getattr(r, 'classification', ''), 'segment_length_m': getattr(r, 'segment_length_m', 0.0), 'total_days': r.total_days, 'missed_days': r.missed_days, 'missed_days_with_gps_gap': getattr(r, 'missed_days_with_gps_gap', r.missed_days), 'n_gps_gap_days': getattr(r, 'n_gps_gap_days', 0), 'n_days_visited': r.n_days_visited, 'n_days_possible_manual': getattr(r, 'n_days_possible_manual', 0), 'n_days_manual_collected': getattr(r, 'n_days_manual_collected', 0), 'first_visit_day': r.first_visit_day, 'last_visit_day': r.last_visit_day, 'dwell_seconds': getattr(r, 'dwell_seconds', 0.0), 'vicinity_seconds': getattr(r, 'vicinity_seconds', 0.0), 'passage_details': pd_list}
        _extra_cols = ('name', 'osmid', 'osm_id', 'NOME', 'nome', 'NM_LOG', 'nm_log', 'NM_TIP_LOG', 'nm_tip_log', 'nome_logra', 'NOME_LOGRA', 'street_name', 'logradouro', 'LOGRADOURO', 'highway', 'ref', 'length', 'u', 'v')
        for _ec in _extra_cols:
            val = getattr(r, _ec, None)
            if val is not None and _ec not in row_data:
                row_data[_ec] = val
        rows.append(row_data)
    if not rows:
        return gpd.GeoDataFrame(columns=[], geometry='geometry', crs=seg.crs)
    return gpd.GeoDataFrame(rows, geometry='geometry', crs=seg.crs)

