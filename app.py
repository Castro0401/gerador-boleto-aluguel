import sqlite3
from datetime import date
import io

import streamlit as st

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors


# -----------------------------
# Segurança (gate)
# -----------------------------
ALLOWED_CODES = {"133", "735", "169"}

def security_gate():
    if "auth_ok" not in st.session_state:
        st.session_state["auth_ok"] = False

    if st.session_state["auth_ok"]:
        return

    st.set_page_config(page_title="Acesso restrito", page_icon="🔒", layout="centered")
    st.title("🔒 Acesso restrito")
    st.write("Digite os **3 primeiros dígitos do CPF** para acessar.")

    code = st.text_input("Senha (3 dígitos)", max_chars=3, type="password", placeholder="Ex: 133")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Entrar", use_container_width=True):
            if code in ALLOWED_CODES:
                st.session_state["auth_ok"] = True
                st.success("Acesso liberado.")
                st.rerun()
            else:
                st.session_state["auth_ok"] = False
                st.error("Senha inválida.")

    with col2:
        if st.button("Limpar", use_container_width=True):
            st.session_state["auth_ok"] = False
            st.rerun()

    st.stop()


# Chame o gate antes de qualquer coisa do app:
security_gate()


# -----------------------------
# Utils
# -----------------------------
def brl(value: float) -> str:
    try:
        v = float(value)
    except Exception:
        v = 0.0
    s = f"{v:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def mes_to_display(mes_aaaa_mm: str) -> str:
    """'2026-02' -> '02/2026'"""
    try:
        y, m = mes_aaaa_mm.split("-")
        return f"{int(m):02d}/{int(y):04d}"
    except Exception:
        return mes_aaaa_mm


def display_to_mes(display_mm_aaaa: str) -> str:
    """'02/2026' -> '2026-02'"""
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


def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def init_db():
    """
    Novo modelo:
      - apartamentos (N imóveis)
      - configs (1 por apartamento)
      - lancamentos (por apartamento + mês) com observações
    Migração automática:
      - Se existirem tabelas antigas: config (id=1) e lancamentos (mes PK),
        e as novas estiverem vazias -> migra para um apartamento inicial.
    """
    with get_conn() as conn:
        cur = conn.cursor()

        # --- NOVAS TABELAS ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS apartamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                apelido TEXT NOT NULL,
                imovel TEXT NOT NULL,
                bairro TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS configs (
                apartamento_id INTEGER PRIMARY KEY,
                locador_nome TEXT,
                locador_doc TEXT,
                locatario_nome TEXT,
                locatario_doc TEXT,
                vencimento_dia INTEGER DEFAULT 5,
                banco TEXT,
                agencia TEXT,
                conta TEXT,
                tipo_conta TEXT,
                titular TEXT,
                titular_doc TEXT,
                pix TEXT,
                contato_comprovante TEXT,
                FOREIGN KEY(apartamento_id) REFERENCES apartamentos(id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS lancamentos (
                apartamento_id INTEGER NOT NULL,
                mes TEXT NOT NULL, -- AAAA-MM

                aluguel REAL DEFAULT 0,
                aluguel_obs TEXT DEFAULT '',

                condominio REAL DEFAULT 0,
                condominio_obs TEXT DEFAULT '',

                iptu REAL DEFAULT 0,
                iptu_obs TEXT DEFAULT '',

                consumo_agua REAL DEFAULT 0,
                consumo_agua_obs TEXT DEFAULT '',

                seguro_incendio REAL DEFAULT 0,
                seguro_incendio_obs TEXT DEFAULT '',

                outras_taxas REAL DEFAULT 0,
                outras_taxas_obs TEXT DEFAULT '',

                outros_descontos REAL DEFAULT 0,
                outros_descontos_obs TEXT DEFAULT '',

                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),

                PRIMARY KEY (apartamento_id, mes),
                FOREIGN KEY(apartamento_id) REFERENCES apartamentos(id)
            )
        """)

        # --- MIGRAÇÃO (se aplicável) ---
        cur.execute("SELECT COUNT(*) FROM apartamentos")
        apts_count = cur.fetchone()[0]

        old_config_exists = table_exists(cur, "config")   # tabela antiga singular
        old_lanc_exists = table_exists(cur, "lancamentos") # tabela antiga singular

        if apts_count == 0:
            # Se tem dado antigo, cria apt com base no config antigo; senão, cria um padrão.
            if old_config_exists:
                cur.execute("SELECT * FROM config WHERE id=1")
                row = cur.fetchone()
                if row:
                    cols = [d[0] for d in cur.description]
                    old_cfg = dict(zip(cols, row))

                    imovel = (old_cfg.get("imovel") or "Rua Abraham Palatnik, 100 – Apto 301 – Bloco 7").strip()
                    bairro = (old_cfg.get("bairro") or "Recreio dos Bandeirantes – Rio de Janeiro/RJ").strip()
                    apelido = "Imóvel 1"

                    cur.execute(
                        "INSERT INTO apartamentos (apelido, imovel, bairro) VALUES (?, ?, ?)",
                        (apelido, imovel, bairro)
                    )
                    apt_id = cur.lastrowid

                    cur.execute("""
                        INSERT INTO configs (
                            apartamento_id, locador_nome, locador_doc,
                            locatario_nome, locatario_doc, vencimento_dia,
                            banco, agencia, conta, tipo_conta, titular, titular_doc, pix, contato_comprovante
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        apt_id,
                        old_cfg.get("locador_nome", ""),
                        old_cfg.get("locador_doc", ""),
                        old_cfg.get("locatario_nome", ""),
                        old_cfg.get("locatario_doc", ""),
                        int(old_cfg.get("vencimento_dia") or 5),
                        old_cfg.get("banco", ""),
                        old_cfg.get("agencia", ""),
                        old_cfg.get("conta", ""),
                        old_cfg.get("tipo_conta", ""),
                        old_cfg.get("titular", ""),
                        old_cfg.get("titular_doc", ""),
                        old_cfg.get("pix", ""),
                        old_cfg.get("contato_comprovante", ""),
                    ))
                else:
                    old_config_exists = False  # não tinha linha, cai no padrão

            if not old_config_exists:
                cur.execute("""
                    INSERT INTO apartamentos (apelido, imovel, bairro)
                    VALUES (?, ?, ?)
                """, (
                    "Abraham Palatnik 100/301/7",
                    "Rua Abraham Palatnik, 100 – Apto 301 – Bloco 7",
                    "Recreio dos Bandeirantes – Rio de Janeiro/RJ"
                ))
                apt_id = cur.lastrowid
                cur.execute("INSERT INTO configs (apartamento_id, vencimento_dia) VALUES (?, 5)", (apt_id,))

            conn.commit()

            # Migra lançamentos antigos, se existirem (para o apt criado)
            if old_lanc_exists:
                # descobrir colunas antigas
                cur.execute("PRAGMA table_info(lancamentos)")
                old_cols = [c[1] for c in cur.fetchall()]

                # pega todos os lançamentos antigos
                cur.execute("SELECT * FROM lancamentos")
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]

                for r in rows:
                    old = dict(zip(cols, r))

                    # compat: seu app antigo tinha taxa_admin e desconto em algum momento
                    consumo_agua = float(old.get("consumo_agua") or old.get("taxa_admin") or 0.0)
                    outros_descontos = float(old.get("outros_descontos") or old.get("desconto") or 0.0)

                    cur.execute("""
                        INSERT OR IGNORE INTO lancamentos (
                            apartamento_id, mes,
                            aluguel, condominio, iptu, consumo_agua, seguro_incendio, outras_taxas, outros_descontos,
                            aluguel_obs, condominio_obs, iptu_obs, consumo_agua_obs, seguro_incendio_obs, outras_taxas_obs, outros_descontos_obs
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '', '', '', '', '')
                    """, (
                        apt_id,
                        old.get("mes"),
                        float(old.get("aluguel") or 0.0),
                        float(old.get("condominio") or 0.0),
                        float(old.get("iptu") or 0.0),
                        float(consumo_agua),
                        float(old.get("seguro_incendio") or 0.0),
                        float(old.get("outras_taxas") or 0.0),
                        float(outros_descontos),
                    ))

                conn.commit()


def list_apartamentos():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, apelido, imovel, bairro FROM apartamentos ORDER BY id ASC")
        rows = cur.fetchall()
        return [{"id": r[0], "apelido": r[1], "imovel": r[2], "bairro": r[3]} for r in rows]


def create_apartamento(apelido: str, imovel: str, bairro: str) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO apartamentos (apelido, imovel, bairro) VALUES (?, ?, ?)",
            (apelido.strip(), imovel.strip(), bairro.strip())
        )
        apt_id = cur.lastrowid
        cur.execute("INSERT INTO configs (apartamento_id, vencimento_dia) VALUES (?, 5)", (apt_id,))
        conn.commit()
        return apt_id


def update_apartamento(apt_id: int, apelido: str, imovel: str, bairro: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE apartamentos SET apelido=?, imovel=?, bairro=? WHERE id=?",
            (apelido.strip(), imovel.strip(), bairro.strip(), apt_id)
        )
        conn.commit()


def load_config(apt_id: int) -> dict:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT locador_nome, locador_doc,
                   locatario_nome, locatario_doc,
                   vencimento_dia, banco, agencia, conta, tipo_conta,
                   titular, titular_doc, pix, contato_comprovante
            FROM configs WHERE apartamento_id=?
        """, (apt_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO configs (apartamento_id, vencimento_dia) VALUES (?, 5)", (apt_id,))
            conn.commit()
            return load_config(apt_id)

        keys = [
            "locador_nome", "locador_doc",
            "locatario_nome", "locatario_doc",
            "vencimento_dia", "banco", "agencia", "conta", "tipo_conta",
            "titular", "titular_doc", "pix", "contato_comprovante"
        ]
        return dict(zip(keys, row))


def save_config(apt_id: int, cfg: dict):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE configs SET
                locador_nome=?,
                locador_doc=?,
                locatario_nome=?,
                locatario_doc=?,
                vencimento_dia=?,
                banco=?,
                agencia=?,
                conta=?,
                tipo_conta=?,
                titular=?,
                titular_doc=?,
                pix=?,
                contato_comprovante=?
            WHERE apartamento_id=?
        """, (
            cfg.get("locador_nome", ""),
            cfg.get("locador_doc", ""),
            cfg.get("locatario_nome", ""),
            cfg.get("locatario_doc", ""),
            int(cfg.get("vencimento_dia") or 5),
            cfg.get("banco", ""),
            cfg.get("agencia", ""),
            cfg.get("conta", ""),
            cfg.get("tipo_conta", ""),
            cfg.get("titular", ""),
            cfg.get("titular_doc", ""),
            cfg.get("pix", ""),
            cfg.get("contato_comprovante", ""),
            apt_id
        ))
        conn.commit()


def get_lancamento(apt_id: int, mes: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                mes,
                aluguel, aluguel_obs,
                condominio, condominio_obs,
                iptu, iptu_obs,
                consumo_agua, consumo_agua_obs,
                seguro_incendio, seguro_incendio_obs,
                outras_taxas, outras_taxas_obs,
                outros_descontos, outros_descontos_obs
            FROM lancamentos
            WHERE apartamento_id=? AND mes=?
        """, (apt_id, mes))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "mes": row[0],
            "aluguel": row[1] or 0.0, "aluguel_obs": row[2] or "",
            "condominio": row[3] or 0.0, "condominio_obs": row[4] or "",
            "iptu": row[5] or 0.0, "iptu_obs": row[6] or "",
            "consumo_agua": row[7] or 0.0, "consumo_agua_obs": row[8] or "",
            "seguro_incendio": row[9] or 0.0, "seguro_incendio_obs": row[10] or "",
            "outras_taxas": row[11] or 0.0, "outras_taxas_obs": row[12] or "",
            "outros_descontos": row[13] or 0.0, "outros_descontos_obs": row[14] or "",
        }


def get_latest_lancamento(apt_id: int):
    """Último lançamento salvo desse apartamento (para pré-preencher novos meses)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                mes,
                aluguel, aluguel_obs,
                condominio, condominio_obs,
                iptu, iptu_obs,
                consumo_agua, consumo_agua_obs,
                seguro_incendio, seguro_incendio_obs,
                outras_taxas, outras_taxas_obs,
                outros_descontos, outros_descontos_obs
            FROM lancamentos
            WHERE apartamento_id=?
            ORDER BY mes DESC
            LIMIT 1
        """, (apt_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "mes": row[0],
            "aluguel": row[1] or 0.0, "aluguel_obs": row[2] or "",
            "condominio": row[3] or 0.0, "condominio_obs": row[4] or "",
            "iptu": row[5] or 0.0, "iptu_obs": row[6] or "",
            "consumo_agua": row[7] or 0.0, "consumo_agua_obs": row[8] or "",
            "seguro_incendio": row[9] or 0.0, "seguro_incendio_obs": row[10] or "",
            "outras_taxas": row[11] or 0.0, "outras_taxas_obs": row[12] or "",
            "outros_descontos": row[13] or 0.0, "outros_descontos_obs": row[14] or "",
        }


def upsert_lancamento(apt_id: int, mes: str, data: dict):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO lancamentos (
                apartamento_id, mes,

                aluguel, aluguel_obs,
                condominio, condominio_obs,
                iptu, iptu_obs,
                consumo_agua, consumo_agua_obs,
                seguro_incendio, seguro_incendio_obs,
                outras_taxas, outras_taxas_obs,
                outros_descontos, outros_descontos_obs,
                updated_at
            )
            VALUES (?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                datetime('now')
            )
            ON CONFLICT(apartamento_id, mes) DO UPDATE SET
                aluguel=excluded.aluguel,
                aluguel_obs=excluded.aluguel_obs,

                condominio=excluded.condominio,
                condominio_obs=excluded.condominio_obs,

                iptu=excluded.iptu,
                iptu_obs=excluded.iptu_obs,

                consumo_agua=excluded.consumo_agua,
                consumo_agua_obs=excluded.consumo_agua_obs,

                seguro_incendio=excluded.seguro_incendio,
                seguro_incendio_obs=excluded.seguro_incendio_obs,

                outras_taxas=excluded.outras_taxas,
                outras_taxas_obs=excluded.outras_taxas_obs,

                outros_descontos=excluded.outros_descontos,
                outros_descontos_obs=excluded.outros_descontos_obs,

                updated_at=datetime('now')
        """, (
            apt_id, mes,

            float(data.get("aluguel", 0.0)), data.get("aluguel_obs", "") or "",
            float(data.get("condominio", 0.0)), data.get("condominio_obs", "") or "",
            float(data.get("iptu", 0.0)), data.get("iptu_obs", "") or "",
            float(data.get("consumo_agua", 0.0)), data.get("consumo_agua_obs", "") or "",
            float(data.get("seguro_incendio", 0.0)), data.get("seguro_incendio_obs", "") or "",
            float(data.get("outras_taxas", 0.0)), data.get("outras_taxas_obs", "") or "",
            float(data.get("outros_descontos", 0.0)), data.get("outros_descontos_obs", "") or "",
        ))
        conn.commit()


def list_lancamentos(apt_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT mes,
                   aluguel, condominio, iptu, consumo_agua, seguro_incendio, outras_taxas, outros_descontos
            FROM lancamentos
            WHERE apartamento_id=?
            ORDER BY mes DESC
        """, (apt_id,))
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
def generate_pdf_bytes(apto: dict, cfg: dict, lanc: dict) -> bytes:
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

    dia = int(cfg.get("vencimento_dia") or 5)
    ref_mm_aaaa = mes_to_display(mes)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCenter", parent=styles["Title"], alignment=1)

    elements = []
    elements.append(Paragraph("BOLETO / DEMONSTRATIVO DE COBRANÇA – ALUGUEL", title_style))
    elements.append(Spacer(1, 14))

    imovel = (apto.get("imovel") or "").strip()
    bairro = (apto.get("bairro") or "").strip()

    locador_nome = (cfg.get("locador_nome") or "").strip()
    locador_doc = (cfg.get("locador_doc") or "").strip()
    locatario_nome = (cfg.get("locatario_nome") or "").strip()
    locatario_doc = (cfg.get("locatario_doc") or "").strip()

    elements.append(Paragraph(f"<b>Imóvel:</b> {imovel}<br/>{bairro}", styles["Normal"]))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"<b>Locador:</b> {locador_nome}<br/><b>CPF/CNPJ:</b> {locador_doc}", styles["Normal"]))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"<b>Locatário:</b> {locatario_nome}<br/><b>CPF/CNPJ:</b> {locatario_doc}", styles["Normal"]))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph(f"<b>Referência:</b> Aluguel referente a {ref_mm_aaaa}", styles["Normal"]))
    elements.append(Spacer(1, 14))

    # Tabela com observações
    table_data = [
        ["Descrição", "Observação", "Valor (R$)"],
        ["Aluguel Mensal", lanc.get("aluguel_obs", ""), brl(aluguel)],
        ["Condomínio", lanc.get("condominio_obs", ""), brl(condominio)],
        ["IPTU (parcela mensal)", lanc.get("iptu_obs", ""), brl(iptu)],
        ["Consumo de água", lanc.get("consumo_agua_obs", ""), brl(consumo_agua)],
        ["Seguro de incêndio", lanc.get("seguro_incendio_obs", ""), brl(seguro_incendio)],
        ["Outras taxas", lanc.get("outras_taxas_obs", ""), brl(outras)],
        ["Subtotal", "", brl(subtotal)],
        ["Outros Descontos", lanc.get("outros_descontos_obs", ""), brl(outros_descontos)],
        ["TOTAL A PAGAR", "", brl(total)],
    ]

    t = Table(table_data, colWidths=[170, 200, 90])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 14))

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

# Sidebar: selecionar imóvel
st.sidebar.header("🏠 Imóvel")

apts = list_apartamentos()
if not apts:
    st.error("Nenhum imóvel encontrado. Reinicie o app.")
    st.stop()

labels = {a["id"]: a["apelido"] for a in apts}

if "apt_id" not in st.session_state:
    st.session_state["apt_id"] = apts[0]["id"]

apt_ids = [a["id"] for a in apts]
current_index = apt_ids.index(st.session_state["apt_id"]) if st.session_state["apt_id"] in apt_ids else 0

apt_id = st.sidebar.selectbox(
    "Selecione o imóvel",
    options=apt_ids,
    index=current_index,
    format_func=lambda i: labels.get(i, f"Imóvel {i}")
)
st.session_state["apt_id"] = apt_id

apto = next(a for a in apts if a["id"] == apt_id)
cfg = load_config(apt_id)

with st.sidebar.expander("➕ Cadastrar novo imóvel"):
    apelido_novo = st.text_input("Apelido (ex: Barra 1201)", value="")
    imovel_novo = st.text_input("Imóvel (endereço)", value="")
    bairro_novo = st.text_input("Bairro/Cidade/UF", value="")
    if st.button("Criar imóvel"):
        if not apelido_novo.strip() or not imovel_novo.strip() or not bairro_novo.strip():
            st.error("Preencha apelido, imóvel e bairro.")
        else:
            new_id = create_apartamento(apelido_novo, imovel_novo, bairro_novo)
            st.success("Imóvel criado!")
            st.session_state["apt_id"] = new_id
            st.rerun()

with st.sidebar.expander("✏️ Editar imóvel selecionado"):
    apelido_edit = st.text_input("Apelido", value=apto["apelido"])
    imovel_edit = st.text_input("Imóvel (endereço)", value=apto["imovel"])
    bairro_edit = st.text_input("Bairro/Cidade/UF", value=apto["bairro"])
    if st.button("Salvar imóvel"):
        update_apartamento(apt_id, apelido_edit, imovel_edit, bairro_edit)
        st.success("Imóvel atualizado!")
        st.rerun()

if st.sidebar.button("🚪 Sair"):
    st.session_state["auth_ok"] = False
    st.rerun()

st.caption(f"Imóvel ativo: **{apto['apelido']}**")


tab1, tab2, tab3 = st.tabs(["⚙️ Fixos (Config)", "🗓️ Mês (Valores)", "📚 Histórico"])


# -------- Tab 1: Fixos
with tab1:
    st.subheader("Informações fixas (por imóvel)")

    with st.form("cfg_form"):
        st.write(f"**Imóvel:** {apto['imovel']}")
        st.write(f"**Bairro/Cidade/UF:** {apto['bairro']}")

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
            save_config(apt_id, {
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


# -------- Tab 2: Mês (Valores)
with tab2:
    st.subheader("Valores do mês")

    today = date.today()
    default_mes = f"{today.year:04d}-{today.month:02d}"
    default_display = mes_to_display(default_mes)

    mes_input = st.text_input(
        "Mês (MM/AAAA)",
        value=default_display,
        help="Ex: 02/2026"
    )
    mes = display_to_mes(mes_input.strip())

    existing = get_lancamento(apt_id, mes)

    # memória: se não existe esse mês, puxa o último do mesmo imóvel
    base = existing if existing else get_latest_lancamento(apt_id)

    def val_obs_row(label, key_val, key_obs, step=10.0):
        c1, c2 = st.columns([1, 1])
        with c1:
            v = st.number_input(
                label,
                min_value=0.0,
                value=float(base.get(key_val, 0.0)) if base else 0.0,
                step=step
            )
        with c2:
            o = st.text_input(
                "Obs.",
                value=(base.get(key_obs, "") if base else ""),
                key=f"{apt_id}_{mes}_{key_obs}"
            )
        return v, o

    aluguel, aluguel_obs = val_obs_row("Aluguel (R$)", "aluguel", "aluguel_obs", step=50.0)
    condominio, condominio_obs = val_obs_row("Condomínio (R$)", "condominio", "condominio_obs", step=10.0)
    iptu, iptu_obs = val_obs_row("IPTU (R$)", "iptu", "iptu_obs", step=10.0)
    consumo_agua, consumo_agua_obs = val_obs_row("Consumo de água (R$)", "consumo_agua", "consumo_agua_obs", step=10.0)
    seguro_incendio, seguro_incendio_obs = val_obs_row("Seguro de incêndio (R$)", "seguro_incendio", "seguro_incendio_obs", step=10.0)
    outras_taxas, outras_taxas_obs = val_obs_row("Outras taxas (R$)", "outras_taxas", "outras_taxas_obs", step=10.0)

    outros_descontos, outros_descontos_obs = val_obs_row(
        "Outros Descontos (R$)",
        "outros_descontos",
        "outros_descontos_obs",
        step=10.0
    )

    subtotal = aluguel + condominio + iptu + consumo_agua + seguro_incendio + outras_taxas
    total = subtotal - outros_descontos

    st.info(
        f"**Subtotal:** {brl(subtotal)}\n\n"
        f"**Outros Descontos:** {brl(outros_descontos)}\n\n"
        f"**Total a pagar:** {brl(total)}"
    )

    payload = {
        "aluguel": aluguel, "aluguel_obs": aluguel_obs,
        "condominio": condominio, "condominio_obs": condominio_obs,
        "iptu": iptu, "iptu_obs": iptu_obs,
        "consumo_agua": consumo_agua, "consumo_agua_obs": consumo_agua_obs,
        "seguro_incendio": seguro_incendio, "seguro_incendio_obs": seguro_incendio_obs,
        "outras_taxas": outras_taxas, "outras_taxas_obs": outras_taxas_obs,
        "outros_descontos": outros_descontos, "outros_descontos_obs": outros_descontos_obs,
    }

    colX, colY = st.columns(2)
    with colX:
        if st.button("💾 Salvar mês"):
            upsert_lancamento(apt_id, mes, payload)
            st.success(f"Lançamento {mes_to_display(mes)} salvo!")

    with colY:
        if st.button("🧾 Gerar PDF do mês"):
            upsert_lancamento(apt_id, mes, payload)
            cfg_now = load_config(apt_id)
            lanc_now = get_lancamento(apt_id, mes)
            pdf_bytes = generate_pdf_bytes(apto, cfg_now, lanc_now)

            st.download_button(
                "⬇️ Baixar PDF",
                data=pdf_bytes,
                file_name=f"Boleto_{apto['apelido'].replace(' ', '_')}_{mes}.pdf",
                mime="application/pdf"
            )


# -------- Tab 3: Histórico
with tab3:
    st.subheader("Histórico de meses (por imóvel)")
    rows = list_lancamentos(apt_id)

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
        mes_sel = st.selectbox(
            "Selecione o mês",
            options=meses,
            format_func=mes_to_display
        )

        if st.button("Gerar PDF do mês selecionado"):
            cfg_now = load_config(apt_id)
            lanc_now = get_lancamento(apt_id, mes_sel)
            pdf_bytes = generate_pdf_bytes(apto, cfg_now, lanc_now)

            st.download_button(
                "⬇️ Baixar PDF",
                data=pdf_bytes,
                file_name=f"Boleto_{apto['apelido'].replace(' ', '_')}_{mes_sel}.pdf",
                mime="application/pdf"
            )
