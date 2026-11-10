"""
Módulo de marca d'água e assinatura digital.

Fornece:
  1. Marca d'água visual para HTML (mapas) — canto + diagonal
  2. Marca d'água visual para PDF (reportlab) — diagonal transparente
  3. Certificado auto-assinado (cryptography) — gerado 1x, reutilizado
  4. Assinatura digital de PDF (endesive) — PKCS#7 visível no Adobe Reader

Dependências:
    cryptography (já existente)
    endesive (nova)
"""

import datetime
import logging
import os
from pathlib import Path
from typing import Optional
from project_heatmaps.config import PATHS
logger = logging.getLogger(__name__)
WATERMARK_LINE_1 = 'Produção e Distribuição Exclusiva do'
WATERMARK_LINE_2 = 'Departamento de Inteligência Estratégica - DIE'
WATERMARK_SHORT = 'DIE'
WATERMARK_FULL = f"{WATERMARK_LINE_1} {WATERMARK_LINE_2}"
CERT_DIR = Path('CERTIFICADO')
CERT_PFX_PATH = CERT_DIR / 'die_cert.pfx'
CERT_PASSWORD = b'die_internal_2025'

def get_html_watermark_overlay():
    """
    Retorna bloco HTML+CSS para injetar nos mapas.
    Inclui canto inferior direito + texto diagonal cobrindo o mapa.
    Usa pointer-events:none para não bloquear interação com o mapa.
    """
    return f"\n<!-- ===== MARCA D'ÁGUA DIE ===== -->\n<style>\n.die-watermark-corner {{\n    position: fixed;\n    bottom: 12px;\n    right: 12px;\n    z-index: 99999;\n    background: rgba(0, 0, 0, 0.65);\n    color: #ffffff;\n    padding: 8px 14px;\n    border-radius: 6px;\n    font-family: 'Segoe UI', Arial, sans-serif;\n    font-size: 9px;\n    line-height: 1.4;\n    text-align: right;\n    pointer-events: none;\n    user-select: none;\n    -webkit-user-select: none;\n    backdrop-filter: blur(4px);\n    -webkit-backdrop-filter: blur(4px);\n    box-shadow: 0 2px 8px rgba(0,0,0,0.3);\n    letter-spacing: 0.3px;\n}}\n.die-watermark-corner .die-line1 {{\n    opacity: 0.85;\n    font-size: 8px;\n}}\n.die-watermark-corner .die-line2 {{\n    font-weight: 700;\n    font-size: 10px;\n    letter-spacing: 0.5px;\n}}\n.die-watermark-diagonal {{\n    position: fixed;\n    top: 0; left: 0; width: 100%; height: 100%;\n    z-index: 99998;\n    pointer-events: none;\n    user-select: none;\n    -webkit-user-select: none;\n    overflow: hidden;\n    display: flex;\n    align-items: center;\n    justify-content: center;\n}}\n.die-watermark-diagonal-text {{\n    transform: rotate(-35deg);\n    white-space: nowrap;\n    font-family: 'Segoe UI', Arial, sans-serif;\n    font-weight: 800;\n    font-size: 3.2vw;\n    color: rgba(120, 120, 120, 0.07);\n    letter-spacing: 6px;\n    text-transform: uppercase;\n}}\n@media (max-width: 768px) {{\n    .die-watermark-corner {{\n        bottom: 8px;\n        right: 8px;\n        padding: 6px 10px;\n        font-size: 7px;\n    }}\n    .die-watermark-corner .die-line2 {{\n        font-size: 8px;\n    }}\n    .die-watermark-diagonal-text {{\n        font-size: 5vw;\n    }}\n}}\n</style>\n\n<div class=\"die-watermark-corner\">\n    <div class=\"die-line1\">{WATERMARK_LINE_1}</div>\n    <div class=\"die-line2\">{WATERMARK_LINE_2}</div>\n</div>\n\n<div class=\"die-watermark-diagonal\">\n    <div class=\"die-watermark-diagonal-text\">\n        {WATERMARK_SHORT} &bull; CONFIDENCIAL &bull; {WATERMARK_SHORT} &bull; CONFIDENCIAL &bull; {WATERMARK_SHORT}\n    </div>\n</div>\n<!-- ===== FIM MARCA D'ÁGUA ===== -->\n"

def inject_watermark_into_html(filepath):
    """
    Injeta marca d'água em um arquivo HTML existente.

    Insere antes de </body> se existir, senão antes de </html>,
    senão concatena ao final.

    Returns:
        True se injetou com sucesso.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()
        overlay = get_html_watermark_overlay()
        if 'die-watermark-corner' in html:
            return True
        if '</body>' in html.lower():
            idx = html.lower().rfind('</body>')
            html = (((html[:idx] + overlay) + """
""") + html[idx:])
        else:
            if '</html>' in html.lower():
                idx = html.lower().rfind('</html>')
                html = (((html[:idx] + overlay) + """
""") + html[idx:])
            else:
                html += ("""
""" + overlay)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
    except Exception as e:
        logger.error(f"Falha ao injetar watermark em {filepath}: {e}")
        return False
    else:
        return True

def draw_pdf_watermark(canvas, page_width, page_height):
    """
    Desenha marca d'água diagonal semi-transparente em uma página PDF.

    Chamar dentro de um onPage/onPageEnd callback do reportlab.

    Args:
        canvas: reportlab canvas
        page_width: largura da página em pontos
        page_height: altura da página em pontos
    """
    canvas.saveState()
    canvas.setFont('Helvetica-Bold', 38)
    canvas.setFillColorRGB(0.5, 0.5, 0.5, alpha=0.06)
    canvas.translate((page_width / 2), (page_height / 2))
    canvas.rotate(35)
    canvas.drawCentredString(0, 0, f"{WATERMARK_SHORT}  •  CONFIDENCIAL  •  {WATERMARK_SHORT}")
    canvas.restoreState()
    canvas.saveState()
    canvas.setFont('Helvetica', 6)
    canvas.setFillColorRGB(0.45, 0.45, 0.45, alpha=0.5)
    canvas.drawCentredString((page_width / 2), 18, WATERMARK_FULL)
    canvas.restoreState()

def generate_self_signed_certificate(org_name='Departamento de Inteligência Estratégica - DIE', common_name='DIE Sistema de Monitoramento', validity_days=1095, output_pfx=None):
    """
    Gera um certificado auto-assinado e salva como .pfx (PKCS#12).

    Se o certificado já existir e ainda for válido, reutiliza.

    Returns:
        Caminho do arquivo .pfx gerado
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates as pkcs12_load, serialize_key_and_certificates as pkcs12_serialize
    pfx_path = Path(output_pfx) if output_pfx else CERT_PFX_PATH
    pfx_path.parent.mkdir(parents=True, exist_ok=True)
    if pfx_path.exists():
        try:
            with open(pfx_path, 'rb') as f:
                pfx_data = f.read()
            key, cert, chain = pkcs12_load(pfx_data, CERT_PASSWORD)
            if cert.not_valid_after_utc > datetime.datetime.now(datetime.timezone.utc):
                logger.info(f"Certificado existente válido: {pfx_path}")
                return str(pfx_path)
            logger.info('Certificado expirado, gerando novo...')
        except Exception:
            logger.info('Certificado inválido, gerando novo...')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, 'BR'), x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, 'PE'), x509.NameAttribute(NameOID.ORGANIZATION_NAME, org_name), x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after((now + datetime.timedelta(days=validity_days))).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=False, encipher_only=False, decipher_only=False), critical=True).sign(key, hashes.SHA256())
    pfx_data = pkcs12_serialize(name=b'DIE', key=key, cert=cert, cas=None, encryption_algorithm=serialization.BestAvailableEncryption(CERT_PASSWORD))
    with open(pfx_path, 'wb') as f:
        f.write(pfx_data)
    logger.info(f"Certificado gerado: {pfx_path} (válido por {validity_days} dias)")
    return str(pfx_path)

def sign_pdf(pdf_path, pfx_path=None):
    """
    Assina digitalmente um PDF com o certificado auto-assinado.

    A assinatura fica visível no Adobe Reader como
    "Assinado por DIE Sistema de Monitoramento".

    O arquivo é modificado in-place (assinatura anexada ao final).

    Returns:
        True se assinou com sucesso.
    """
    try:
        from endesive.pdf import cms
        from cryptography.hazmat.primitives.serialization import pkcs12
    except ImportError:
        logger.warning('endesive não instalado — assinatura digital não aplicada. Instale com: pip install endesive')
        return False
    if pfx_path is None:
        pfx_path = str(CERT_PFX_PATH)
    if not os.path.exists(pfx_path):
        logger.warning(f"Certificado não encontrado: {pfx_path}")
        return False
    try:
        with open(pfx_path, 'rb') as f:
            pfx_data = f.read()
        key, cert, chain = pkcs12.load_key_and_certificates(pfx_data, CERT_PASSWORD)
        now = datetime.datetime.now(datetime.timezone.utc)
        date_str = now.strftime("D:%Y%m%d%H%M%S+00'00'")
        dct = {'sigflags': 3, 'contact': 'die@monitoramento.local', 'location': 'Brasil', 'signingdate': date_str, 'reason': 'Documento gerado e autenticado pelo sistema DIE'}
        with open(pdf_path, 'rb') as f:
            datau = f.read()
        datas = cms.sign(datau, dct, key, cert, (chain or []), 'sha256')
        with open(pdf_path, 'wb') as f:
            f.write(datau)
            f.write(datas)
        logger.info(f"✍️  PDF assinado digitalmente: {pdf_path}")
    except Exception as e:
        logger.error(f"Falha ao assinar PDF: {e}", exc_info=True)
        return False
    else:
        return True

