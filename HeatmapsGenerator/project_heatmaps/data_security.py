"""
Módulo de segurança para arquivos HTML de mapas de calor.

Fornece:
1. Criptografia AES-256-GCM dos dados embutidos no HTML
2. Derivação de chave ofuscada em JavaScript
3. Proteções anti-DevTools (F12, Ctrl+Shift+I, right-click, debugger)
4. Encapsulamento em closure (sem exposição via window.*)
5. Tile layer sem nomes de ruas

Uso:
    from project_heatmaps.data_security import generate_secure_map_script

    payload = {
        "SEGMENTS": segments_geojson,
        "SECTORS": sectors_geojson,
        "PASSAGE_DATA": passage_dict,
        ...
    }
    secure_js = generate_secure_map_script(payload, map_init_js)

Compatibilidade:
    - Python: cryptography (AES-256-GCM)
    - Browser: Web Crypto API (Chrome 37+, Firefox 34+, Edge 12+)
    - Compressão: gzip (Python) ↔ DecompressionStream (browsers modernos)
"""

import json
import base64
import gzip
import secrets
import random
import string
import logging
from typing import Dict, Any, List, Tuple, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
logger = logging.getLogger(__name__)
from project_heatmaps.config import BASEMAP_TILE_URL, BASEMAP_ATTRIBUTION
TILE_URL_NO_LABELS = BASEMAP_TILE_URL
TILE_ATTRIBUTION = BASEMAP_ATTRIBUTION
_VAR_NAME_POOL = ['_mapCfg', '_tileCfg', '_projCfg', '_gridCfg', '_viewCfg', '_renderOpts', '_layerOpts', '_zoomOpts', '_panOpts', '_drawOpts', '_geoMeta', '_coordMeta', '_boundsMeta', '_cacheMeta', '_bufMeta', '_uiState', '_panelState', '_filterState', '_tooltipState', '_frameCfg', '_canvasBuf', '_crsParams', '_bboxState', '_tileGrid', '_styleRef', '_overlayOpts', '_vectorCfg', '_clusterMeta', '_snapGrid', '_hitTest']

def _encrypt_aes_gcm(plaintext):
    """
    Criptografa com AES-256-GCM.

    Returns:
        (key_32_bytes, nonce_12_bytes + ciphertext + tag_16_bytes)
    """
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ct_and_tag = aesgcm.encrypt(nonce, plaintext, None)
    return (key, (nonce + ct_and_tag))

def encrypt_payload(payload):
    """
    Serializa, comprime e criptografa um dicionário de dados do mapa.

    Args:
        payload: Dict com chaves = nomes de variáveis JS,
                 valores = dados serializáveis (GeoJSON, dicts, etc.)

    Returns:
        (encrypted_base64, key_base64)
    """
    json_bytes = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    compressed = gzip.compress(json_bytes, compresslevel=6)
    key_bytes, encrypted_bytes = _encrypt_aes_gcm(compressed)
    encrypted_b64 = base64.b64encode(encrypted_bytes).decode('ascii')
    key_b64 = base64.b64encode(key_bytes).decode('ascii')
    logger.info(f"Payload criptografado: {len(json_bytes)} bytes JSON → {len(compressed)} comprimido → {len(encrypted_bytes)} criptografado")
    return (encrypted_b64, key_b64)

def _pick_var_name(used):
    """Escolhe um nome de variável não utilizado do pool."""
    available = [n for n in _VAR_NAME_POOL if n not in used]
    if available:
        name = secrets.choice(available)
    else:
        name = ('_' + ''.join((secrets.choice(string.ascii_lowercase) for _ in range(7))))
    used.add(name)
    return name

def _generate_noise(length):
    """Gera string de ruído com aparência de base64."""
    chars = ((string.ascii_letters + string.digits) + '+/')
    return ''.join((secrets.choice(chars) for _ in range(length)))

def _xor_encode(text, salt):
    """XOR de cada caractere com um salt, retorna como hex pairs."""
    return ''.join((f"{(ord(c) ^ ord(salt[(i % len(salt))])):02x}" for i, c in enumerate(text)))

def _generate_obfuscated_key_js(key_b64):
    """
    Gera código JS que declara fragmentos ofuscados da chave
    e uma função de reconstrução.

    Estratégia:
    - Divide a chave em 6 fragmentos
    - Cada fragmento é XOR'd com um salt aleatório
    - Fragmentos são armazenados em arrays misturados com ruído
    - Uma função un-XOR e reconstrói a chave

    Returns:
        (declarations_js, reconstruct_function_call)
    """
    used = set()
    n_frags = 6
    frag_len = (len(key_b64) // n_frags)
    fragments = []
    for i in range(n_frags):
        start = i * frag_len
        end = (start + frag_len) if i < (n_frags - 1) else len(key_b64)
        fragments.append(key_b64[start:end])
    salt = secrets.token_hex(4)
    xored_fragments = [_xor_encode(f, salt) for f in fragments]
    declarations = []
    extract_steps = []
    for pair_idx in range(0, n_frags, 2):
        var_name = _pick_var_name(used)
        frag_a = xored_fragments[pair_idx]
        frag_b = xored_fragments[(pair_idx + 1)] if (pair_idx + 1) < n_frags else ''
        rng = random.Random(secrets.token_hex(4))
        noise_before = [f"\"{_generate_noise(len(frag_a))}\"" for _ in range(rng.randint(1, 3))]
        noise_middle = [f"\"{_generate_noise(len(frag_a))}\"" for _ in range(rng.randint(1, 2))]
        noise_after = [f"\"{_generate_noise(len(frag_a))}\"" for _ in range(rng.randint(1, 3))]
        idx_a = len(noise_before)
        items = noise_before + [f"\"{frag_a}\""]
        items += noise_middle
        idx_b = len(items) if frag_b else -1
        items += f"\"{frag_b}\"" if frag_b else noise_after
        declarations.append(f"var {var_name}=[{','.join(items)}];")
        extract_steps.append(f"{var_name}[{idx_a}]")
        if frag_b:
            extract_steps.append(f"{var_name}[{idx_b}]")
    salt_name = _pick_var_name(used)
    declarations.append(f"var {salt_name}=\"{salt}\";")
    unxor_name = _pick_var_name(used)
    declarations.append(f"function {unxor_name}(h,s){{var r='';for(var i=0;i<h.length;i+=2){{r+=String.fromCharCode(parseInt(h.substr(i,2),16)^s.charCodeAt((i/2)%s.length));}}return r;}}")
    recon_name = _pick_var_name(used)
    join_parts = '+'.join((f"{unxor_name}({step},{salt_name})" for step in extract_steps))
    declarations.append(f"function {recon_name}(){{return {join_parts};}}")
    return ("""
        """.join(declarations), f"{recon_name}()")

def _get_anti_devtools_js():
    """
    Gera JavaScript com proteções anti-inspeção.

    Inclui:
    - Bloqueio de atalhos de teclado (F12, Ctrl+Shift+I/J/C, Ctrl+U, Ctrl+S)
    - Desabilitação do menu de contexto (right-click)
    - Detecção de DevTools via timing do debugger
    - Detecção via diferença de tamanho da janela
    - Sobrescrita de métodos do console
    - Limpeza de dados se DevTools detectado
    - Bloqueio de arrastar elementos (anti-drag)
    - Desabilitação de seleção de texto no mapa
    """
    return """
        // ===== PROTEÇÕES ANTI-INSPEÇÃO =====
        var _dtk=false,_dtn=0;

        // Bloqueia atalhos de teclado
        document.addEventListener('keydown',function(e){
            if(e.keyCode===123){e.preventDefault();return false;}
            if(e.ctrlKey&&e.shiftKey&&[73,74,67].indexOf(e.keyCode)!==-1){e.preventDefault();return false;}
            if(e.ctrlKey&&[85,83].indexOf(e.keyCode)!==-1){e.preventDefault();return false;}
        },true);

        // Bloqueia menu de contexto
        document.addEventListener('contextmenu',function(e){e.preventDefault();return false;},true);

        // Bloqueia arrastar
        document.addEventListener('dragstart',function(e){e.preventDefault();return false;},true);

        // Desabilita seleção de texto no body (exceto inputs)
        document.addEventListener('selectstart',function(e){
            if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return true;
            e.preventDefault();return false;
        },true);

        // Detecção via debugger timing
        var _dtI1=setInterval(function(){
            var t0=performance.now();
            (function(){}).constructor('debugger')();
            if(performance.now()-t0>80){
                _dtk=true;_dtn++;
                _wipePayload();
            }
        },4000);

        // Detecção via tamanho da janela (DevTools dockado altera dimensões)
        var _dtI2=setInterval(function(){
            var wD=window.outerWidth-window.innerWidth>160;
            var hD=window.outerHeight-window.innerHeight>160;
            if(wD||hD){
                if(!_dtk){_dtk=true;_wipePayload();}
            }
        },3000);

        // Sobrescrita do console
        var _noOp=function(){};
        try{
            var _fakeConsole={
                log:_noOp,warn:_noOp,error:_noOp,info:_noOp,
                debug:_noOp,dir:_noOp,table:_noOp,trace:_noOp,
                group:_noOp,groupEnd:_noOp,clear:_noOp,
                assert:_noOp,count:_noOp,time:_noOp,timeEnd:_noOp
            };
            Object.defineProperty(window,'console',{
                get:function(){return _fakeConsole;},
                set:_noOp,configurable:false
            });
        }catch(e){}

        // Wipe: limpa dados descriptografados da memória
        var _wipeFns=[];
        function _wipePayload(){
            for(var i=0;i<_wipeFns.length;i++){
                try{_wipeFns[i]();}catch(e){}
            }
        }
    """

def _get_decryption_engine_js():
    """
    Gera a função JS assíncrona que:
    1. Decodifica base64 → bytes
    2. Importa a chave AES-256-GCM via Web Crypto API
    3. Extrai nonce (12 bytes) e descriptografa
    4. Descomprime gzip via DecompressionStream
    5. Parseia JSON
    """
    return """
        async function _decPayload(encB64,keyB64){
            var eR=atob(encB64);
            var eB=new Uint8Array(eR.length);
            for(var i=0;i<eR.length;i++)eB[i]=eR.charCodeAt(i);

            var kR=atob(keyB64);
            var kB=new Uint8Array(kR.length);
            for(var i=0;i<kR.length;i++)kB[i]=kR.charCodeAt(i);

            var ck=await crypto.subtle.importKey(
                'raw',kB,{name:'AES-GCM'},false,['decrypt']
            );

            var nonce=eB.slice(0,12);
            var ct=eB.slice(12);

            var dec=await crypto.subtle.decrypt(
                {name:'AES-GCM',iv:nonce},ck,ct
            );

            var ds=new DecompressionStream('gzip');
            var wr=ds.writable.getWriter();
            wr.write(new Uint8Array(dec));
            wr.close();
            var txt=await new Response(ds.readable).text();

            return JSON.parse(txt);
        }
    """

def generate_secure_map_script(payload, map_init_js, leaflet_html=None, plotly_src='https://cdn.plot.ly/plotly-2.27.0.min.js'):
    """
    Gera o bloco <script> completo e seguro para um mapa HTML.

    Fluxo:
    1. Anti-DevTools ativa imediatamente
    2. Dados criptografados declarados como string base64
    3. Chave reconstruída de fragmentos ofuscados
    4. Descriptografia assíncrona (AES-256-GCM + gzip)
    5. Dados extraídos como variáveis locais (closure)
    6. Código de inicialização do mapa executa com dados locais
    7. Monitoramento contínuo limpa dados se DevTools detectado

    Args:
        payload: Dict {nome_variavel: dados} a criptografar.
                 Ex: {"SEGMENTS": geojson, "SECTORS": geojson, ...}
        map_init_js: Código JS de inicialização do mapa.
                     Deve referenciar variáveis do payload como locais
                     (ex: SEGMENTS em vez de window.SEGMENTS).
        leaflet_html: Bloco HTML que traz o Leaflet. Padrão: a lib EMBUTIDA
                      (web_assets.leaflet_js_inline). Passar uma tag com `src`
                      remoto devolve o HTML entregue à dependência de rede —
                      foi assim que o mapa ficava em branco offline (03/09).
        plotly_src: URL do Plotly JS. None = não carregar Plotly.

    Returns:
        HTML com tags <script> completas (libs externas + código seguro).
    """
    var_names = list(payload.keys())
    encrypted_b64, key_b64 = encrypt_payload(payload)
    key_decl_js, key_call_js = _generate_obfuscated_key_js(key_b64)
    extract_lines = """
            """.join((f"var {name}=_d['{name}'];" for name in var_names))
    if leaflet_html is None:
        from project_heatmaps.web_assets import leaflet_js_inline
        leaflet_html = leaflet_js_inline()
    ext_scripts = leaflet_html
    if plotly_src:
        ext_scripts += f"\n    <script src=\"{plotly_src}\"></script>"
    return f"""\n    {ext_scripts}\n\n    <script>\n    (function(){{\n        'use strict';\n\n        {_get_anti_devtools_js()}\n\n        // ===== PAYLOAD CRIPTOGRAFADO =====\n        var _ep=\"{encrypted_b64}\";\n\n        // ===== COMPONENTES DA CHAVE (ofuscados) =====\n        {key_decl_js}\n\n        {_get_decryption_engine_js()}\n\n        // ===== INICIALIZAÇÃO =====\n        async function _boot(){{\n            if(_dtk)return;\n\n            try{{\n                var _k={key_call_js};\n                var _d=await _decPayload(_ep,_k);\n\n                // Limpa fontes criptografadas\n                _ep=null;_k=null;\n\n                // Extrai datasets como variáveis locais\n                {extract_lines}\n                _d=null;\n\n                // Registra função de limpeza\n                _wipeFns.push(function(){{\n                    {'; '.join((f"{n}=null" for n in var_names))};\n                }});\n\n                // ===== CÓDIGO DO MAPA =====\n                {map_init_js}\n\n            }}catch(e){{\n                var _m=e&&e.message?e.message:'Desconhecido';\n                var _s=e&&e.stack?e.stack.split('\n').slice(0,3).join(' | '):'';\n                document.body.innerHTML=\n                    '<div style=\"display:flex;align-items:center;justify-content:center;'\n                    +'height:100vh;font-family:Arial;color:#666;\">'\n                    +'<div style=\"text-align:center;\">'\n                    +'<h2>Mapa Indisponível</h2>'\n                    +'<p>Não foi possível carregar os dados.</p>'\n                    +'<p style=\"font-size:11px;color:#aaa;max-width:500px;word-break:break-all;\">'+_m+'</p>'\n                    +'</div></div>';\n            }}\n        }}\n\n        _boot();\n    }})();\n    </script>\n    """

def prepare_passage_data(segments):
    """
    Extrai passage_details de um GeoDataFrame de segmentos
    em formato dict {fid: details}.

    Substitui o _compress_passage_data() do plotting.py original.
    Ao invés de comprimir separadamente, os dados vão dentro
    do payload criptografado.
    """
    data = {}
    for idx_num, (idx, row) in enumerate(segments.iterrows()):
        pd_val = row.get('passage_details')
        if isinstance(pd_val, list) and pd_val:
            data[str(idx_num)] = pd_val
    return data

_WINDOW_REPLACEMENTS = [('window.HEATMAP_OVERALL_MANUAL_GPS', 'HEATMAP_OVERALL_MANUAL_GPS'), ('window.HEATMAP_OVERALL_MANUAL', 'HEATMAP_OVERALL_MANUAL'), ('window.HEATMAP_OVERALL_GPS', 'HEATMAP_OVERALL_GPS'), ('window.HEATMAP_OVERALL', 'HEATMAP_OVERALL'), ('window.HEATMAP_HISTORY', 'HEATMAP_HISTORY'), ('window.PASSAGE_DATA', 'PASSAGE_DATA'), ('window.PASSAGE_B64', 'PASSAGE_B64'), ('window.SEGMENTS_DATA', 'SEGMENTS_DATA'), ('window.SECTORS_DATA', 'SECTORS_DATA'), ('window.FILTER_CONFIG', 'FILTER_CONFIG'), ('window.HISTORY_DATA', 'HISTORY_DATA'), ('window.VEHICLE_DATA', 'VEHICLE_DATA'), ('window.VEHICLE_B64', 'VEHICLE_B64'), ('window.STOPS_HISTORY', 'STOPS_HISTORY'), ('window.STOPS_OVERALL_PCT', 'STOPS_OVERALL_PCT'), ('window.STOPS_OVERALL', 'STOPS_OVERALL'), ('window.PONTOS_VICIADOS', 'PONTOS_VICIADOS'), ('window.SINGLE_DAY_MODE', 'SINGLE_DAY_MODE'), ('window.SEGMENTS', 'SEGMENTS'), ('window.SECTORS', 'SECTORS'), ('window.FILTERS', 'FILTERS')]

def localize_window_references(js_code):
    """
    Substitui referências window.VARNAME por VARNAME (variáveis locais).

    Mantém intactas referências legítimas ao window como:
    window.addEventListener, window.Plotly, window.MutationObserver, etc.
    """
    result = js_code
    for old, new in _WINDOW_REPLACEMENTS:
        result = result.replace(old, new)
    return result

def secure_inline_html(raw_html, payload, tile_nolabels=True):
    """
    Recebe um HTML completo com scripts inline (como os gerados por
    _build_mobile_html ou _build_stops_mobile_html), extrai os blocos
    de script, criptografa os dados e gera HTML seguro.

    Processo:
    1. Localiza todos os blocos <script> inline (não src=)
    2. Remove o bloco de dados (que contém window.* = ...)
    3. Extrai o código do app (IIFE), remove IIFE wrapper
    4. Substitui window.* por referências locais
    5. Remove console.log/warn
    6. Opcionalmente troca tiles para versão sem labels
    7. Gera o envelope criptografado com anti-DevTools
    8. Substitui os <script> originais pelo seguro

    Args:
        raw_html: HTML completo com scripts inline
        payload: Dict {nome_variavel: dados} a criptografar
        tile_nolabels: Se True, troca tiles para versão sem labels

    Returns:
        HTML com scripts protegidos
    """
    import re
    inline_pattern = re.compile('<script>(.*?)</script>', re.DOTALL)
    inline_blocks = list(inline_pattern.finditer(raw_html))
    if len(inline_blocks) < 1:
        logger.warning('Nenhum bloco <script> inline encontrado')
        return raw_html
    app_block = inline_blocks[-1]
    app_code = app_block.group(1).strip()
    strict_pos = app_code.find("'use strict'")
    if strict_pos == -1:
        iife_open = app_code.find('(function()')
        if iife_open != -1:
            brace_start = app_code.find('{', iife_open)
            brace_end = app_code.rfind('})();')
            if brace_end == -1:
                brace_end = app_code.rfind('})()')
            if brace_start != -1 and brace_end != -1:
                app_code = app_code[(brace_start + 1):brace_end].strip()
    else:
        semicolon_after = app_code.find(';', strict_pos)
        inner_start = (semicolon_after + 1) if semicolon_after != -1 else (strict_pos + len("'use strict';"))
        inner_end = app_code.rfind('})();')
        if inner_end == -1:
            inner_end = app_code.rfind('})()')
    app_code = app_code[inner_start:inner_end].strip() if inner_end != -1 else localize_window_references(app_code)
    if tile_nolabels:
        pass
    lines = app_code.split("""
""")
    clean_lines = [line for line in lines if 'console.log' not in line and 'console.warn' not in line]
    app_code = """
""".join(clean_lines)
    src_pattern = re.compile('<script\\s+src="[^"]+"></script>')
    src_tags = src_pattern.findall(raw_html)
    secure_script = generate_secure_map_script(payload=payload, map_init_js=app_code, leaflet_src='', plotly_src='')
    secure_script = secure_script.replace('<script src=""></script>', '')
    secure_script = re.sub('\\s*<script src=""></script>\\s*', """
""", secure_script)
    first_inline_start = inline_blocks[0].start()
    last_inline_end = inline_blocks[-1].end()
    html_before = raw_html[:first_inline_start]
    html_after = raw_html[last_inline_end:]
    preserved_tags = []
    region = raw_html[first_inline_start:last_inline_end]
    for tag in src_tags:
        if tag in region:
            preserved_tags.append(tag)
    preserved_html = """
    """.join(preserved_tags) if preserved_tags else ''
    return ((((html_before + preserved_html) + """
""") + secure_script) + html_after)

