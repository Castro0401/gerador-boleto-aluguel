import sqlite3
from datetime import date
import io

import streamlit as st

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors


# -----------------------------
# Utils
# -----------------------------
def brl(value: float) -> str:
    s = f"{value:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def mes_to_display(mes_aaaa_mm: str) -> str:
    """'2026-02' -> '02/2026'"""
    try:
        y, m = mes_aaaa_mm.split("-")
        return f"{int(m):02d}/{int(y):04d}"
    except Exception:
        return mes_aaaa_mm


def display_to_mes(display_mm_aaaa: str) -> str:
    """'02/2026' -> '2026-02' (se o usuário digitar assim)"""
    try:
        m, y = display_mm_aaaa.split("/")
        return f"{int(y):04d}-{int(m):02d}"
    except Exception:
        return display_mm_aaaa


# -----------------------------
# DB
# -----------------------------
DB_PATH = "boleto.db"


def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    with get_conn() as conn:
        cur = conn.cursor()

        # Tabela CONFIG (fixos)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                imovel TEXT,
                bairro TEXT,
                locador_nome TEXT,
                locador_doc TEXT,
                locatario_nome TEXT,
                locatario_doc TEXT,
                vencimento_dia INTEGER,
                banco TEXT,
                agencia TEXT,
                conta TEXT,
                tipo_conta TEXT,
                titular TEXT,
                titular_doc TEXT,
                pix TEXT,
                contato_comprovante TEXT
            )
        """)
        cur.execute("""
            INSERT OR IGNORE INTO config (id, vencimento_dia)
            VALUES (1, 5)
        """)

        # Migração CONFIG (caso seu DB seja antigo)
        cur.execute("PRAGMA table_info(config)")
        cfg_cols = [c[1] for c in cur.fetchall()]
        if "locatario_doc" not in cfg_cols:
            cur.execute("ALTER TABLE config ADD COLUMN locatario_doc TEXT")

        # Tabela LANCAMENTOS (mês a mês)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lancamentos (
                mes TEXT PRIMARY KEY,              -- "AAAA-MM"
                aluguel REAL DEFAULT 0,
                condominio REAL DEFAULT 0,
                iptu REAL DEFAULT 0,
                consumo_agua REAL DEFAULT 0,
                seguro_incendio REAL DEFAULT 0,
                outras_taxas REAL DEFAULT 0,
                outros_descontos REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Migração LANCAMENTOS (caso seu DB seja antigo)
        cur.execute("PRAGMA table_info(lancamentos)")
        lan_cols = [c[1] for c in cur.fetchall()]

        # Seu app antigo provavelmente tinha: taxa_admin e desconto
        # Vamos manter compatibilidade migrando dados, se existirem.
        if "consumo_agua" not in lan_cols and "taxa_admin" in lan_cols:
            cur.execute("ALTER TABLE lancamentos ADD COLUMN consumo_agua REAL DEFAULT 0")
            cur.execute("UPDATE lancamentos SET consumo_agua = IFNULL(taxa_admin, 0)")
        elif "consumo_agua" not in lan_cols:
            cur.execute("ALTER TABLE lancamentos ADD COLUMN consumo_agua REAL DEFAULT 0")

        if "outros_descontos" not in lan_cols and "desconto" in lan_cols:
            cur.execute("ALTER TABLE lancamentos ADD COLUMN outros_descontos REAL DEFAULT 0")
            cur.execute("UPDATE lancamentos SET outros_descontos = IFNULL(desconto, 0)")
        elif "outros_descontos" not in lan_cols:
            cur.execute("ALTER TABLE lancamentos ADD COLUMN outros_descontos REAL DEFAULT 0")

        if "seguro_incendio" not in lan_cols:
            cur.execute("ALTER TABLE lancamentos ADD COLUMN seguro_incendio REAL DEFAULT 0")

        # Se a tabela antiga não tinha consumo_agua/outros_descontos/seguro_incendio,
        # agora ela terá. (As colunas antigas ficam, mas não usamos mais.)

        conn.commit()


def load_config() -> dict:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM config WHERE id=1")
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def save_config(cfg: dict):
    fields = [
        "imovel", "bairro",
        "locador_nome", "locador_doc",
        "locatario_nome", "locatario_doc",
        "vencimento_dia",
        "banco", "agencia", "conta", "tipo_conta",
        "titular", "titular_doc",
        "pix", "contato_comprovante"
    ]
    values = [cfg.get(f) for f in fields]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE config SET
                imovel=?, bairro=?,
                locador_nome=?, locador_doc=?,
                locatario_nome=?, locatario_doc=?,
                vencimento_dia=?,
                banco=?, agencia=?, conta=?, tipo_conta=?,
                titular=?, titular_doc=?,
                pix=?, contato_comprovante=?
            WHERE id=1
        """, values)
        conn.commit()


def upsert_lancamento(mes: str, data: dict):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO lancamentos (
                mes, aluguel, condominio, iptu, consumo_agua, seguro_incendio, outras_taxas, outros_descontos
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mes) DO UPDATE SET
                aluguel=excluded.aluguel,
                condominio=excluded.condominio,
                iptu=excluded.iptu,
                consumo_agua=excluded.consumo_agua,
                seguro_incendio=excluded.seguro_incendio,
                outras_taxas=excluded.outras_taxas,
                outros_descontos=excluded.outros_descontos
        """, (
            mes,
            float(data.get("aluguel", 0)),
            float(data.get("condominio", 0)),
            float(data.get("iptu", 0)),
            float(data.get("consumo_agua", 0)),
            float(data.get("seguro_incendio", 0)),
            float(data.get("outras_taxas", 0)),
            float(data.get("outros_descontos", 0)),
        ))
        conn.commit()


def get_lancamento(mes: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT mes, aluguel, condominio, iptu, consumo_agua, seguro_incendio, outras_taxas, outros_descontos
            FROM lancamentos
            WHERE mes=?
        """, (mes,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "mes": row[0],
            "aluguel": row[1] or 0.0,
            "condominio": row[2] or 0.0,
            "iptu": row[3] or 0.0,
            "consumo_agua": row[4] or 0.0,
            "seguro_incendio": row[5] or 0.0,
            "outras_taxas": row[6] or 0.0,
            "outros_descontos": row[7] or 0.0,
        }


def list_lancamentos():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT mes, aluguel, condominio, iptu, consumo_agua, seguro_incendio, outras_taxas, outros_descontos
            FROM lancamentos
            ORDER BY mes DESC
        """)
        rows = cur.fetchall()
        out = []
        for r in rows:
            out.append({
                "mes": r[0],
                "aluguel": r[1] or 0.0,
                "condominio": r[2] or 0.0,
                "iptu": r[3] or 0.0,
                "consumo_agua": r[4] or 0.0,
                "seguro_incendio": r[5] or 0.0,
                "outras_taxas": r[6] or 0.0,
                "outros_descontos": r[7] or 0.0,
            })
        return out


# -----------------------------
# PDF Generator
# -----------------------------
def generate_pdf_bytes(cfg: dict, lanc: dict) -> bytes:
    mes = lanc["mes"]

    aluguel = float(lanc["aluguel"])
    condominio = float(lanc["condominio"])
    iptu = float(lanc["iptu"])
    consumo_agua = float(lanc["consumo_agua"])
    seguro_incendio = float(lanc["seguro_incendio"])
    outras = float(lanc["outras_taxas"])
    outros_descontos = float(lanc["outros_descontos"])

    subtotal = aluguel + condominio + iptu + consumo_agua + seguro_incendio + outras
    total = subtotal - outros_descontos

    # vencimento: dia fixo do config no mês selecionado
    y, m = map(int, mes.split("-"))
    dia = int(cfg.get("vencimento_dia") or 5)
    # Mantemos o dia (05), mas a referência principal fica em MÊS/ANO.
    venc = date(y, m, min(dia, 28))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCenter", parent=styles["Title"], alignment=1)

    elements = []
    elements.append(Paragraph("BOLETO / DEMONSTRATIVO DE COBRANÇA – ALUGUEL", title_style))
    elements.append(Spacer(1, 14))

    imovel = (cfg.get("imovel") or "").strip()
    bairro = (cfg.get("bairro") or "").strip()
    locador_nome = (cfg.get("locador_nome") or "").strip()
    locador_doc = (cfg.get("locador_doc") or "").strip()
    locatario = (cfg.get("locatario_nome") or "").strip()
    locatario_doc = (cfg.get("locatario_doc") or "").strip()

    elements.append(Paragraph(f"<b>Imóvel:</b> {imovel}<br/>{bairro}", styles["Normal"]))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"<b>Locador:</b> {locador_nome}<br/><b>CPF/CNPJ:</b> {locador_doc}", styles["Normal"]))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"<b>Locatário:</b> {locatario}<br/><b>CPF/CNPJ:</b> {locatario_doc}", styles["Normal"]))
    elements.append(Spacer(1, 8))

    # Formato MÊS/ANO
    ref_mm_aaaa = mes_to_display(mes)
    elements.append(Paragraph(f"<b>Referência:</b> Aluguel referente a {ref_mm_aaaa}", styles["Normal"]))
    elements.append(Spacer(1, 14))

    table_data = [
        ["Descrição", "Valor (R$)"],
        ["Aluguel Mensal", brl(aluguel)],
        ["Condomínio", brl(condominio)],
        ["IPTU (parcela mensal)", brl(iptu)],
        ["Consumo de água", brl(consumo_agua)],
        ["Seguro de incêndio", brl(seguro_incendio)],
        ["Outras taxas", brl(outras)],
        ["Subtotal", brl(subtotal)],
        ["Outros Descontos", brl(outros_descontos)],
        ["TOTAL A PAGAR", brl(total)],
    ]

    t = Table(table_data, colWidths=[320, 140])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 14))

    # Mantém o dia 05, mas você pediu "mês/ano" — então mostramos referência MM/AAAA
    elements.append(Paragraph(
        f"<b>Vencimento:</b> dia {dia:02d} / {ref_mm_aaaa}",
        styles["Normal"]
    ))
    elements.append(Spacer(1, 10))

    pagamento = f"""
    <b>Dados para Pagamento / Depósito</b><br/>
    Banco: {cfg.get("banco") or ""}<br/>
    Agência: {cfg.get("agencia") or ""}<br/>
    Conta: {cfg.get("conta") or ""}<br/>
    Tipo: {cfg.get("tipo_conta") or ""}<br/>
    Titular: {cfg.get("titular") or ""}<br/>
    CPF/CNPJ: {cfg.get("titular_doc") or ""}<br/>
    PIX (se houver): {cfg.get("pix") or ""}
    """
    elements.append(Paragraph(pagamento, styles["Normal"]))
    elements.append(Spacer(1, 10))

    contato = (cfg.get("contato_comprovante") or "").strip()
    if contato:
        elements.append(Paragraph(f"<b>Comprovante:</b> enviar para {contato}", styles["Normal"]))
    else:
        elements.append(Paragraph("Favor enviar o comprovante de pagamento após a quitação.", styles["Normal"]))

    doc.build(elements)
    return buffer.getvalue()


# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="Cobrança de Aluguel", page_icon="🧾", layout="centered")
init_db()

st.title("🧾 Gerador de Demonstrativo de Aluguel")

cfg = load_config()

tab1, tab2, tab3 = st.tabs(["⚙️ Fixos (Config)", "🗓️ Mês (Valores)", "📚 Histórico"])

with tab1:
    st.subheader("Informações fixas")
    with st.form("cfg_form"):
        imovel = st.text_input(
            "Imóvel (endereço)",
            value=cfg.get("imovel") or "Rua Abraham Palatnik, 100 – Apto 301 – Bloco 7"
        )
        bairro = st.text_input(
            "Bairro/Cidade/UF",
            value=cfg.get("bairro") or "Recreio dos Bandeirantes – Rio de Janeiro/RJ"
        )

        colA, colB = st.columns(2)
        with colA:
            locador_nome = st.text_input("Locador (nome)", value=cfg.get("locador_nome") or "")
        with colB:
            locador_doc = st.text_input("Locador (CPF/CNPJ)", value=cfg.get("locador_doc") or "")

        colC, colD = st.columns(2)
        with colC:
            locatario_nome = st.text_input("Locatário (nome)", value=cfg.get("locatario_nome") or "")
        with colD:
            locatario_doc = st.text_input("Locatário (CPF/CNPJ)", value=cfg.get("locatario_doc") or "")

        vencimento_dia = st.number_input(
            "Dia fixo de vencimento",
            min_value=1, max_value=28,
            value=int(cfg.get("vencimento_dia") or 5)
        )

        st.markdown("### Dados para pagamento")
        col1, col2 = st.columns(2)
        with col1:
            banco = st.text_input("Banco", value=cfg.get("banco") or "")
            agencia = st.text_input("Agência", value=cfg.get("agencia") or "")
            conta = st.text_input("Conta", value=cfg.get("conta") or "")
        with col2:
            tipo_conta = st.text_input("Tipo (Corrente/Poupança)", value=cfg.get("tipo_conta") or "")
            titular = st.text_input("Titular", value=cfg.get("titular") or "")
            titular_doc = st.text_input("CPF/CNPJ do titular", value=cfg.get("titular_doc") or "")

        pix = st.text_input("PIX (chave)", value=cfg.get("pix") or "")
        contato_comprovante = st.text_input(
            "Contato p/ comprovante (e-mail/WhatsApp)",
            value=cfg.get("contato_comprovante") or ""
        )

        saved = st.form_submit_button("Salvar configurações")
        if saved:
            save_config({
                "imovel": imovel,
                "bairro": bairro,
                "locador_nome": locador_nome,
                "locador_doc": locador_doc,
                "locatario_nome": locatario_nome,
                "locatario_doc": locatario_doc,
                "vencimento_dia": vencimento_dia,
                "banco": banco,
                "agencia": agencia,
                "conta": conta,
                "tipo_conta": tipo_conta,
                "titular": titular,
                "titular_doc": titular_doc,
                "pix": pix,
                "contato_comprovante": contato_comprovante,
            })
            st.success("Configurações salvas!")


with tab2:
    st.subheader("Valores do mês")

    # Você pediu data em "mês/ano". Aqui aceitamos tanto "AAAA-MM" quanto "MM/AAAA".
    today = date.today()
    default_mes = f"{today.year:04d}-{today.month:02d}"
    default_display = mes_to_display(default_mes)

    mes_input = st.text_input(
        "Mês (MM/AAAA)",
        value=default_display,
        help="Ex: 02/2026"
    )
    mes = display_to_mes(mes_input.strip())

    existing = get_lancamento(mes)

    aluguel = st.number_input("Aluguel (R$)", min_value=0.0, value=float(existing["aluguel"]) if existing else 0.0, step=50.0)
    condominio = st.number_input("Condomínio (R$)", min_value=0.0, value=float(existing["condominio"]) if existing else 0.0, step=10.0)
    iptu = st.number_input("IPTU (R$)", min_value=0.0, value=float(existing["iptu"]) if existing else 0.0, step=10.0)

    consumo_agua = st.number_input("Consumo de água (R$)", min_value=0.0, value=float(existing["consumo_agua"]) if existing else 0.0, step=10.0)
    seguro_incendio = st.number_input("Seguro de incêndio (R$)", min_value=0.0, value=float(existing["seguro_incendio"]) if existing else 0.0, step=10.0)

    outras_taxas = st.number_input("Outras taxas (R$)", min_value=0.0, value=float(existing["outras_taxas"]) if existing else 0.0, step=10.0)

    outros_descontos = st.number_input(
        "Outros Descontos (R$)",
        min_value=0.0,
        value=float(existing["outros_descontos"]) if existing else 0.0,
        step=10.0,
        help="Digite como valor positivo (ex: 80)."
    )

    subtotal = aluguel + condominio + iptu + consumo_agua + seguro_incendio + outras_taxas
    total = subtotal - outros_descontos

    st.info(
    f"**Subtotal:** {brl(subtotal)}\n\n"
    f"**Outros Descontos:** {brl(outros_descontos)}\n\n"
    f"**Total a pagar:** {brl(total)}"
)



    colX, colY = st.columns(2)
    with colX:
        if st.button("💾 Salvar mês"):
            upsert_lancamento(mes, {
                "aluguel": aluguel,
                "condominio": condominio,
                "iptu": iptu,
                "consumo_agua": consumo_agua,
                "seguro_incendio": seguro_incendio,
                "outras_taxas": outras_taxas,
                "outros_descontos": outros_descontos
            })
            st.success(f"Lançamento {mes_to_display(mes)} salvo!")

    with colY:
        if st.button("🧾 Gerar PDF do mês"):
            upsert_lancamento(mes, {
                "aluguel": aluguel,
                "condominio": condominio,
                "iptu": iptu,
                "consumo_agua": consumo_agua,
                "seguro_incendio": seguro_incendio,
                "outras_taxas": outras_taxas,
                "outros_descontos": outros_descontos
            })
            cfg = load_config()
            lanc = get_lancamento(mes)
            pdf_bytes = generate_pdf_bytes(cfg, lanc)

            st.download_button(
                "⬇️ Baixar PDF",
                data=pdf_bytes,
                file_name=f"Boleto_{mes}.pdf",
                mime="application/pdf"
            )


with tab3:
    st.subheader("Histórico de meses")
    rows = list_lancamentos()

    if not rows:
        st.warning("Ainda não há lançamentos. Vá na aba “Mês (Valores)”.")
    else:
        table_rows = []
        for r in rows:
            subtotal = (
                r["aluguel"] + r["condominio"] + r["iptu"]
                + r["consumo_agua"] + r["seguro_incendio"] + r["outras_taxas"]
            )
            total = subtotal - r["outros_descontos"]

            table_rows.append({
                "Mês": mes_to_display(r["mes"]),
                "Aluguel": brl(r["aluguel"]),
                "Condomínio": brl(r["condominio"]),
                "IPTU": brl(r["iptu"]),
                "Consumo de água": brl(r["consumo_agua"]),
                "Seguro de incêndio": brl(r["seguro_incendio"]),
                "Outras taxas": brl(r["outras_taxas"]),
                "Outros Descontos": brl(r["outros_descontos"]),
                "Total": brl(total),
            })

        st.dataframe(table_rows, use_container_width=True)

        st.markdown("### Gerar PDF de um mês já lançado")
        meses = [r["mes"] for r in rows]
        # Mostra bonito (MM/AAAA) mas mantém o valor interno como AAAA-MM
        mes_sel = st.selectbox(
            "Selecione o mês",
            options=meses,
            format_func=mes_to_display
        )

        if st.button("Gerar PDF do mês selecionado"):
            cfg = load_config()
            lanc = get_lancamento(mes_sel)
            pdf_bytes = generate_pdf_bytes(cfg, lanc)

            st.download_button(
                "⬇️ Baixar PDF",
                data=pdf_bytes,
                file_name=f"Boleto_{mes_sel}.pdf",
                mime="application/pdf"
            )
