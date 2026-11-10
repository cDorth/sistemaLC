"""
O mapa montado do MySQL (Job 4, sessão 4 — `locar_mysql/COLLECTOR_DESIGN.md`).

O coletor grava, por (contrato, turno, dia operacional), a prova de cada trecho
(`trecho_dia`) e as passagens por placa (`trecho_dia_veiculo`); este módulo é o
leitor do HeatmapsGenerator. O período sai daí sem refazer geometria:

- **segmentos** = os do registro de rotas (`rota_trecho`), da versão MAIS NOVA
  de cada rota entre as que vigoram em algum dia do período (o mapa desenha a
  geometria nova; os arquivos `rotas/` não entram);
- **linhas** = as gravadas de cada dia, com a versão que vigorava NAQUELE dia; o
  último dia lê a linha `ultimo=1` quando a janela do contrato não é de 24 h;
- linha de uma versão antiga vai para o trecho da versão nova: o mesmo
  (trecho_id, sentido) se a peça ficou, senão `trecho_equivalencia` (o
  casamento estrito de 3 m da sessão 3), passo a passo pela cadeia de versões.
  Peça antiga sem herdeiro sai do mapa. Uma rua = uma linha, uma contagem: dois
  dias de origem caindo no mesmo (dia, trecho novo) é `MapaInconsistente`.

Setores continuam dos arquivos (`setor_versao` guarda o polígono, não o nome
com a agenda que o filtro de agenda lê). Quem não tem linha gravada para TODOS
os dias (JABOATAO: GPS só em shapefile), ou foi gravado com outra regra ou outra
janela, fica no caminho dos arquivos: `motivo_arquivos` diz por quê.

A forma compacta de `trecho_dia_veiculo` (corte de espaço, Jorge 2026-11-04)
volta ao texto do mapa em `tdv_do_banco`; quem grava (`locar_mysql/heatmap_dia`)
confere a ida e a volta antes de gravar.
"""

from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Optional
import geopandas as gpd
import pandas as pd
from project_heatmaps.config import GEO
from project_heatmaps.parsers import make_schedule_label, parse_full_schedule_from_code
from project_heatmaps import stats_module as sm
logger = logging.getLogger(__name__)
TABELAS = ('heatmap_dia', 'trecho_dia', 'trecho_dia_veiculo', 'heatmap_placa', 'rota_versao', 'rota_trecho', 'trecho', 'rota_versao_par', 'trecho_equivalencia')
TD_COLS = ['n_pontos_8m', 'interior_8m', 'dwell1_detectado', 'dwell1_s', 'vic1_s', 'cobertura_trajeto', 'tem_15m', 'interior_15m', 'dwell2_detectado', 'dwell2_lento', 'dwell2_s', 'vic2_s', 'cobertura_faixa', 'parada_esquina']
TD_BOOL = {'dwell1_detectado', 'dwell2_detectado', 'dwell2_lento', 'interior_15m', 'interior_8m', 'parada_esquina', 'tem_15m'}
_CHAVE_FMT = '%d/%m/%Y %H:%M:%S'
TDV_SELECT = 'hp.placa, x.n_minutos, x.soma_vel, x.soma_vel_resto, x.n_vel, x.prim_em, x.prim_vel, x.ult_em, x.ult_vel FROM trecho_dia_veiculo x JOIN heatmap_placa hp ON hp.placa_id = x.placa_id'

class MapaInconsistente(RuntimeError):
    """O que está gravado não dá uma linha por rua: o mapa não sai do banco."""

def texto_passagem(em, dd):
    """(chave, data, hora) do mapa a partir do DATETIME (None = passagem sem hora: a chave é a data)."""
    return '' if em is None else em.strftime('%H:%M')

def tdv_do_banco(dia, placa, n_minutos, soma_vel, soma_vel_resto, n_vel, prim_em, prim_vel, ult_em, ult_vel):
    """Uma linha do banco → passagem nas colunas de stats_module._TRECHO_DIA_VEICULO_COLS."""
    dd = dia.strftime('%d/%m/%Y')
    if n_minutos == 1:
        ult_em, ult_vel = prim_em, prim_vel
    pc, pd_, ph = texto_passagem(prim_em, dd)
    uc, ud, uh = texto_passagem(ult_em, dd)
    return dict(placa=placa, n_minutos=int(n_minutos), soma_vel=float(soma_vel), soma_vel_resto=0.0 if soma_vel_resto is None else float(soma_vel_resto), n_vel=int(n_vel), prim_chave=pc, prim_data=pd_, prim_hora=ph, prim_vel=prim_vel, ult_chave=uc, ult_data=ud, ult_hora=uh, ult_vel=ult_vel)

def tipar(td, tdv, dias):
    """Listas de dicts → SegmentDayRows com os tipos de segment_day_rows."""
    a = pd.DataFrame(td, columns=sm._TRECHO_DIA_COLS)
    for c in TD_COLS:
        if c in TD_BOOL:
            a[c] = a[c].astype(bool)
        elif c == 'n_pontos_8m':
            a[c] = a[c].astype('int64')
        else:
            a[c] = a[c].astype('float64')
    b = pd.DataFrame(tdv, columns=sm._TRECHO_DIA_VEICULO_COLS)
    for c in ('n_minutos', 'n_vel'):
        b[c] = b[c].astype('int64')
    for c in ('soma_vel', 'soma_vel_resto', 'prim_vel', 'ult_vel'):
        b[c] = b[c].astype('float64')
    for x in (a, b):
        x['sentido'] = x['sentido'].astype('int64')
        x['segment_id'] = x['segment_id'].astype('int64')
    return sm.SegmentDayRows(a, b, sorted(dias))

def invertida(geom):
    """A mesma de locar_mysql/registro_rotas.invertida (o .exe não leva locar_mysql)."""
    from shapely.geometry import LineString, MultiLineString
    if geom.geom_type == 'LineString':
        return LineString(list(geom.coords)[::-1])
    return MultiLineString([list(p.coords)[::-1] for p in list(geom.geoms)[::-1]])

def _ordem(seg, i):
    """A ordem de build_route_segments (groupby da chave (aresta, rota)): (u, v) ordenado quando a rota
    tem os nós, senão o WKB da geometria. As somas ponderadas do mapa (razão do contrato, XML) somam
    nessa ordem, e somar em outra muda o último bit."""
    r = seg.iloc[i]
    if seg['u'].notna().all() and seg['v'].notna().all():
        return (tuple(sorted((r['u'], r['v']))), str(r['code_full']))
    return (r.geometry.wkb_hex, str(r['code_full']))

def _dias(d1, d2):
    return [(d1 + timedelta(days=i)) for i in range(((d2 - d1).days + 1))]

class FonteMapaMySQL:
    """Uma conexão; `preparar` por mapa. Sem `preparar` OK, `motivo_arquivos` diz por que vai pelos arquivos."""
    def __init__(self, cur):
        self.cur = cur
        self.motivo_arquivos = None
        self.segmentos = None
        self.linhas = None

    @classmethod
    def abrir(cls):
        from inlog_api_project.core.gps_store_mysql import MySQLGPSStore
        st = MySQLGPSStore()
        f = cls(st._cursor())
        f._store = st
        return f

    def _q(self, sql, args=()):
        self.cur.execute(sql, args)
        return self.cur.fetchall()

    def _recusa(self, motivo):
        self.motivo_arquivos, self.segmentos, self.linhas = motivo, None, None
        return False

    def preparar(self, pastas, d1, d2, janela):
        """pastas = [(contrato, turno)]; janela = (ini_min, fim_min) de hoje. True = o mapa sai do banco."""
        ph = ','.join((['%s'] * len(TABELAS)))
        (n,), = self._q(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ({ph})", TABELAS)
        if n != len(TABELAS):
            return self._recusa('tabelas do job 4 ausentes (ou na forma antiga)')
        ultimo_d2 = int(janela[0] != janela[1])
        segs, td, tdv, dias = ([], [], [], set())
        for contrato, turno in pastas:
            feitos = {(d, u): (g, r, i, f, vs) for d, u, g, r, i, f, vs in self._q('SELECT data, ultimo, tem_gps, regra_versao, ini_min, fim_min, versoes FROM heatmap_dia WHERE contrato=%s AND turno=%s AND data BETWEEN %s AND %s', (contrato, turno, d1, d2))}
            escolha = [(ultimo_d2, 0) for d in _dias(d1, d2) if d == d2]
            for d, u in escolha:
                x = feitos.get((d, u))
                if x is None:
                    return self._recusa(f"{contrato} - {turno} {d} ultimo={u}: dia não gravado")
                if (x[1] != sm.REGRA_VERSAO or (x[2], x[3]) != tuple(janela)):
                    return self._recusa(f"{contrato} - {turno} {d}: gravado com regra {x[1]} janela {x[2:]}, hoje regra {sm.REGRA_VERSAO} janela {tuple(janela)}")
                if x[0]:
                    dias.add(pd.Timestamp(d))
            r = self._pasta(contrato, turno, d1, d2, escolha, feitos)
            if isinstance(r, str):
                return self._recusa(r)
            segs.append(r[0])
            td += r[1]
            tdv += r[2]
        seg = pd.concat(segs, ignore_index=True)
        seg = seg.iloc[sorted(range(len(seg)), key=(lambda i: _ordem(seg, i)))].reset_index(drop=True)
        seg['segment_id'] = range(len(seg))
        self.segmentos = gpd.GeoDataFrame(seg, geometry='geometry', crs=GEO.projected_crs)
        self.linhas = tipar(td, tdv, dias)
        self.motivo_arquivos = None
        return True

    def _pasta(self, contrato, turno, d1, d2, escolha, feitos):
        versoes = self._q('SELECT rota_versao_id, rota_codigo, valido_de, hash_trechos, valido_ate FROM rota_versao WHERE contrato=%s AND turno=%s AND valido_de<=%s AND (valido_ate IS NULL OR valido_ate>%s) ORDER BY rota_codigo, valido_de, rota_versao_id', (contrato, turno, d2, d1))
        if not versoes:
            return f"{contrato} - {turno}: nenhuma versão de rota vigente em {d1}→{d2}"
        cadeia = {}
        for vid, cod, _, h, _ in versoes:
            cadeia.setdefault(cod.strip(), []).append(vid)
        nova = {c: vs[-1] for c, vs in cadeia.items()}
        sem = [c for c, vid in nova.items() if next((h for v, _, _, h, _ in versoes if v == vid)) is None]
        if sem:
            return f"{contrato} - {turno}: versão nova sem trechos lidos: {', '.join(sorted(sem))}"
        prox = {a: b for vs in cadeia.values() for a, b in zip(vs, vs[1:])}
        vids = [v for v, *_ in versoes]
        vigentes = {}
        for d, u in escolha:
            vigentes[d] = sorted((v for v, _, de, _, ate in versoes if de <= d and ate is None or ate > d))
            gravadas = sorted((int(x) for x in (feitos[(d, u)][4] or '').split(',') if x))
            if gravadas != vigentes[d]:
                return f"{contrato} - {turno} {d}: gravado com as versões {gravadas}, em vigor hoje {vigentes[d]} (o job ainda não refez o dia)"
        ph = ','.join((['%s'] * len(vids)))
        membros = {}
        for vid, tid, s in self._q(f"SELECT rota_versao_id, trecho_id, sentido FROM rota_trecho WHERE rota_versao_id IN ({ph})", vids):
            membros.setdefault(vid, set()).add((tid, s))
        casados = {(a, b) for a, b in self._q(f"SELECT versao_antiga, versao_nova FROM rota_versao_par WHERE versao_antiga IN ({ph})", vids)}
        faltam = sorted(((a, b) for a, b in prox.items() if (a, b) not in casados))
        if faltam:
            return f"{contrato} - {turno}: pares de versão ainda não casados: {faltam}"
        herda = {}
        for a, b, ta, sa, tn, sn in self._q(f"SELECT versao_antiga, versao_nova, trecho_antigo, sentido_antigo, trecho_novo, sentido_novo FROM trecho_equivalencia WHERE versao_antiga IN ({ph})", vids):
            herda.setdefault((a, b, ta, sa), []).append((tn, sn))
        def destino(vid, tid, s):
            """(vid, tid, s) gravado → peças da versão nova que o herdam (0, 1 ou várias num corte)."""
            atual = [(tid, s)]
            while vid in prox:
                b = prox[vid]
                atual = [y for t, x in atual if (t, x) in membros.get(b, ()) for y in herda.get((vid, b, t, x), [])]
                vid = b
            return [(vid, t, x) for t, x in atual]

        seg, chave = self._segmentos(list(nova.values()), d1, d2)
        td, tdv = [], []
        for d, u in escolha:
            for sql, dest in ((f"SELECT x.rota_versao_id, x.trecho_id, x.sentido, {', '.join((('x.' + c) for c in TD_COLS))} FROM trecho_dia x", td), (f"SELECT x.rota_versao_id, x.trecho_id, x.sentido, {TDV_SELECT}", tdv)):
                vistos = set()
                vd = vigentes[d]
                if not vd:
                    continue
                for r in self._q((sql + f" WHERE x.data=%s AND x.ultimo=%s AND x.rota_versao_id IN ({','.join((['%s'] * len(vd)))})"), (d, u, *vd)):
                    vals = tdv_do_banco(d, *r[3:]) if dest is tdv else dict(zip(TD_COLS, r[3:]))
                    for k in destino(*r[:3]):
                        if k not in chave:
                            continue
                        um = vals['placa'] if dest is tdv else k
                        if um in vistos:
                            raise MapaInconsistente(f"{contrato} - {turno} {d}: duas linhas para o trecho {k}")
                        vistos.add(um)
                        cod, gch = chave[k]
                        dest.append(dict(data=pd.Timestamp(d), segment_id=-1, rota_codigo=cod, geom_chave=gch, sentido=int(k[2]), **vals))
        return (seg, td, tdv)

    def _segmentos(self, vids, d1, d2):
        """Os trechos das versões `vids` no formato de build_route_segments, com o mesmo filtro de agenda."""
        from shapely import wkb
        ph = ','.join((['%s'] * len(vids)))
        linhas = self._q(f"SELECT rt.rota_versao_id, rv.rota_codigo, rt.trecho_id, rt.sentido, t.geom_chave, COALESCE(rt.geom_propria, t.geom), rt.classificacao, rt.rua, rt.u, rt.v, rt.seq FROM rota_trecho rt JOIN rota_versao rv ON rv.rota_versao_id = rt.rota_versao_id JOIN trecho t ON t.trecho_id = rt.trecho_id WHERE rt.rota_versao_id IN ({ph}) ORDER BY rv.rota_codigo, rt.seq, rt.trecho_id, rt.sentido", vids)
        datas = _dias(d1, d2)
        agenda = {}
        out, geoms, chave = [], [], {}
        for vid, cod, tid, s, gch, g, cls, rua, u, v, seq in linhas:
            cod = cod.strip()
            if cod not in agenda:
                sch = parse_full_schedule_from_code(cod)
                agenda[cod] = (sch, (hasattr(sch, 'matches_date') and any((sch.matches_date(x) for x in datas))))
            sch, tem_dia = agenda[cod]
            if not tem_dia:
                continue
            geom = wkb.loads(bytes(g))
            chave[(vid, tid, s)] = (cod, gch)
            geoms.append(invertida(geom) if s else geom)
            out.append(dict(u=u, v=v, classification=cls, name=rua, seq_id=seq, route_name=cod, code_full=cod, _schedule=sch, weekday_set=sch.weekday_set, schedule_label=make_schedule_label(sch), route_codes_all=cod, route_code_individual=cod, rota_versao_id=vid, trecho_id=tid, sentido=s, geom_chave=gch))
        return (gpd.GeoDataFrame(out, geometry=geoms, crs=GEO.projected_crs), chave)

