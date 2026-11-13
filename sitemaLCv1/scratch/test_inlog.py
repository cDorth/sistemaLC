import requests, json

r = requests.post(
    'https://locar.inlog.com.br/autenticacao-services/connect/token',
    data={'client_id':'Locar.Client.Api.Coleta','client_secret':'TG9jYXIuQ2xpZW50LkFwaS5Db2xldGE=',
          'grant_type':'client_credentials','scope':'Inlog.Integration.Coleta.Api'},
    headers={'Content-Type':'application/x-www-form-urlencoded'}, timeout=15
)
token = r.json()['access_token']
hdrs = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
base = 'https://locar.inlog.com.br/IntegrationColeta'

r2 = requests.get(f'{base}/api/Veiculos/TempoReal', headers=hdrs, timeout=15)
veiculos = r2.json()['data']

# Mapeamento completo cidade -> tipo -> placas
mapa = {}
for v in veiculos:
    for g in v.get('gruposControle', []):
        desc = g.get('descricao','')
        partes = desc.split(' - ')
        if len(partes) >= 2:
            cidade = partes[0].strip().lower()
            tipo = ' - '.join(partes[1:]).strip().lower()
            if cidade not in mapa:
                mapa[cidade] = {}
            if tipo not in mapa[cidade]:
                mapa[cidade][tipo] = []
            mapa[cidade][tipo].append(v['placaVeiculo'])

print('=== MAPA CIDADE -> TIPO -> PLACAS:')
for cidade in sorted(mapa.keys()):
    for tipo in sorted(mapa[cidade].keys()):
        print(f'  [{cidade}][{tipo}]: {mapa[cidade][tipo]}')

# Verificar GPS para Jaboatao
print()
print('=== HISTORICO JABOATAO ===')
for placa in ['LVE-5J16', 'DGG-8F39', 'IZR-9B32']:
    encontrado = False
    for data in ['2026-11-12','2026-11-11','2026-11-10','2026-11-07','2026-11-06','2026-11-05']:
        r3 = requests.get(f'{base}/api/Historico',
            params={'dataInicio': f'{data}T00:00:00', 'dataFim': f'{data}T23:59:59', 'placa': placa},
            headers=hdrs, timeout=20)
        pts = r3.json().get('data',[]) if r3.status_code==200 else []
        if pts:
            lat0 = pts[0]['latitude']
            lon0 = pts[0]['longitude']
            print(f'  {placa} em {data}: {len(pts)} pontos. Inicio: lat={lat0}, lon={lon0}')
            encontrado = True
            break
    if not encontrado:
        print(f'  {placa}: SEM DADOS em nenhuma data testada')

# Teste com placa Cuiaba que tinha velocidade > 0
print()
print('=== VEICULOS CUIABA ===')
cuiaba = [v for v in veiculos if any('cuiaba' in str(g.get('descricao','')).lower() for g in v.get('gruposControle',[]))]
print('Placas Cuiaba:', [v['placaVeiculo'] for v in cuiaba])
for v in cuiaba[:2]:
    placa = v['placaVeiculo']
    r3 = requests.get(f'{base}/api/Historico',
        params={'dataInicio': '2026-11-12T00:00:00', 'dataFim': '2026-11-12T23:59:59', 'placa': placa},
        headers=hdrs, timeout=20)
    pts = r3.json().get('data',[]) if r3.status_code==200 else []
    if pts:
        print(f'  {placa}: {len(pts)} pontos GPS. Lat={pts[0]["latitude"]}, Lon={pts[0]["longitude"]}')
    else:
        print(f'  {placa}: 0 pontos')
