import streamlit as st
import pandas as pd
import pdfplumber
import plotly.express as px
import os
import re
from datetime import datetime
import base64
import requests
import threading

st.set_page_config(page_title="Shareable 智能财务管家", page_icon="🌍", layout="wide")

# ==========================================
# 常量与全局设定
# ==========================================
GLOBAL_KNOWLEDGE_FILE = "shared_knowledge.csv"

PERSONAL_BLACKLIST = ["zelle", "venmo", "transfer", "online banking", "payment", "epay", "check", "deposit", "payroll", "ach"]

STOP_WORDS = {
    "the", "and", "store", "shop", "cafe", "restaurant", "market",
    "inc", "llc", "com", "www", "st", "rd", "ave", "san", "jose",
    "francisco", "ca", "ny", "tx", "pay", "payment", "bill", "sq",
    "tst", "pos", "terminal", "valley", "fair", "center", "city",
    "santa", "clara", "diego", "monica", "sunnyvale", "los", "angeles",
    "purchase", "refund", "return", "debit", "credit", "card",
    "auth", "authorized", "transaction", "fee", "transfer", "direct",
    "dep", "deposit", "withdrawal", "atm", "online", "banking",
}

CATEGORIES = [
    "☕️ 咖啡奶茶", "🍱 餐饮外卖", "🛍️ 购物超市", "🛒 宠物消费", "🚗 交通油费",
    "✈️ 旅行住宿", "🧘🏻‍♀️ 运动健身", "🎿 娱乐票务", "🏥 医疗健康", "🏠 房租水电",
    "📦 生活杂项", "🏦 银行手续费", "💳 信用卡还款", "💰 内部转账", "其他", "其他收入",
]

KEYWORD_MAPPING = {
    "☕️ 咖啡奶茶": ["boba", "milk tea", "roaster", "voyager", "coffee", "moon tea", "umetea", "dr.ink", "molly tea", "matcha town", "naisnow", "shuyi", "chicha", "taningca", "minglewood", "little bear cafe", "tea", "starbucks", "peets"],
    "🍱 餐饮外卖": ["porridge", "noodle", "bbq", "grill", "bakery", "cake", "pho", "bafang", "dumpling", "chipotle", "doordash", "dd *", "fantuan", "seamless", "hunan mifen", "malatang", "sweetgreen", "lee's sandwiches", "snack*", "uep*", "restaurant", "dining", "mcdonald", "wendy", "popeyes", "kfc", "kitchen", "sushi", "bistro", "cafe", "pizza", "waiter.com"],
    "🛍️ 购物超市": ["market", "mart", "grocery", "plaza", "amazon", "amzn", "sephora", "lancome", "sports basement", "parallel mountian", "target", "walmart", "costco", "safeway", "99 ranch", "weee", "wholefds", "whole foods", "trader joe"],
    "🛒 宠物消费": ["vet", "veterinary", "petsmart", "chewy", "petco"],
    "🚗 交通油费": ["auto", "car wash", "repair", "gas", "chevron", "shell", "exxon", "uber", "lyft", "caltrain", "parking", "fastrak", "toll", "transit", "bart"],
    "✈️ 旅行住宿": ["alaska air", "united", "delta", "american air", "southwest", "hotel", "resort", "airbnb", "marriott", "hilton", "hyatt", "motel", "expedia", "booking.com", "outrigger"],
    "🧘🏻‍♀️ 运动健身": ["pilates", "glowlab", "yoga", "gym", "golf", "golfnow", "golf cour"],
    "🎿 娱乐票务": ["palisades", "tahoe", "ski", "movie", "steam games", "tm *", "ticketmaster", "livenation", "amc", "cinemark", "stubhub", "concert"],
    "🏥 医疗健康": ["dental", "dentist", "clinic", "doctor", "vision", "quest diagnostics", "qdi", "cvs", "pharmacy", "walgreens", "hospital", "pets best", "pet insurance", "kaiser", "sutter"],
    "🏠 房租水电": ["jpmorgan-bzb312", "jpmorgan-bzo4312", "yardi service", "ladwp", "pgande", "rent", "water", "trash", "sewer"],
    "📦 生活杂项": ["usps", "comcast", "utilities", "apple", "google", "openai"],
    "🏦 银行手续费": ["annual membership fee", "fee", "interest"],
    "💳 信用卡还款": ["payment thank you", "autopay", "payment to", "chase card", "credit card bill payment", "chase credit crd", "american express des:ach", "epay", "online banking payment to crd"],
    "💰 内部转账": ["online banking transfer", "zelle payment", "venmo"],
}

# 🔧 FIX: 预计算免疫关键词集合（还款/转账/手续费里的所有词）
IMMUNE_CATEGORIES = {"💳 信用卡还款", "💰 内部转账", "🏦 银行手续费"}
IMMUNE_KEYWORDS = []
for _cat in IMMUNE_CATEGORIES:
    IMMUNE_KEYWORDS.extend(KEYWORD_MAPPING[_cat])


def _match_keyword(desc_lower, keyword):
    """检查 desc_lower 是否包含 keyword（支持去空格模糊）"""
    return keyword in desc_lower or keyword.replace(" ", "") in desc_lower.replace(" ", "")


def is_immune(desc):
    """🔧 FIX: 判断一条交易描述是否命中了还款/转账/手续费关键词"""
    d = str(desc).lower()
    return any(_match_keyword(d, kw) for kw in IMMUNE_KEYWORDS)


# ==========================================
# 会话状态管理
# ==========================================
if "my_df" not in st.session_state:
    st.session_state["my_df"] = pd.DataFrame(columns=["日期", "交易描述", "金额", "类别"])
# 🔧 FIX: 新增本地私有记忆库（存放敏感转账的手动分类）
if "local_memory" not in st.session_state:
    st.session_state["local_memory"] = pd.DataFrame(columns=["交易描述", "类别"])

# ==========================================
# 全局大脑逻辑
# ==========================================
@st.cache_data(ttl=60)
def load_global_knowledge():
    if os.path.exists(GLOBAL_KNOWLEDGE_FILE):
        return pd.read_csv(GLOBAL_KNOWLEDGE_FILE)
    return pd.DataFrame(columns=["交易描述", "类别", "贡献次数"])


def save_global_knowledge(df):
    df.to_csv(GLOBAL_KNOWLEDGE_FILE, index=False)
    load_global_knowledge.clear()

    def push_to_github():
        if "GITHUB_TOKEN" in st.secrets and "GITHUB_REPO" in st.secrets:
            token = st.secrets["GITHUB_TOKEN"]
            repo = st.secrets["GITHUB_REPO"]
            url = f"https://api.github.com/repos/{repo}/contents/{GLOBAL_KNOWLEDGE_FILE}"
            headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
            try:
                get_resp = requests.get(url, headers=headers)
                sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None
                csv_content = df.to_csv(index=False)
                encoded_content = base64.b64encode(csv_content.encode("utf-8")).decode("utf-8")
                payload = {"message": "🤖 Auto-update Shared Knowledge Brain", "content": encoded_content}
                if sha:
                    payload["sha"] = sha
                requests.put(url, headers=headers, json=payload)
            except Exception:
                pass

    threading.Thread(target=push_to_github).start()


def update_knowledge(description, category):
    """🔧 FIX: 智能分发——敏感词存本地 session，普通商家存 GitHub"""
    desc_lower = str(description).lower()
    is_private = any(bw in desc_lower for bw in PERSONAL_BLACKLIST)

    if is_private:
        # 存入本地私有记忆
        lm = st.session_state["local_memory"]
        match_idx = lm[lm["交易描述"].str.lower() == desc_lower].index
        if not match_idx.empty:
            lm.at[match_idx[0], "类别"] = category
        else:
            st.session_state["local_memory"] = pd.concat(
                [lm, pd.DataFrame([{"交易描述": description, "类别": category}])], ignore_index=True
            )
        return False  # 私有
    else:
        global_df = load_global_knowledge()
        match_idx = global_df[global_df["交易描述"].str.lower() == desc_lower].index
        if not match_idx.empty:
            idx = match_idx[0]
            global_df.at[idx, "类别"] = category
            global_df.at[idx, "贡献次数"] += 1
        else:
            global_df = pd.concat(
                [global_df, pd.DataFrame([{"交易描述": description, "类别": category, "贡献次数": 1}])],
                ignore_index=True,
            )
        save_global_knowledge(global_df)
        return True  # 全局


# ==========================================
# 特征提取 & 相似度
# ==========================================
def extract_core_features(text):
    text = str(text).lower()
    # 去收银机前缀
    text = re.sub(r"^(sq\s*\*|tst\s*\*|sp\s*\*|paypal\s*\*|poy\s*\*|dd\s+doordash\s*)", "", text)
    # 🔧 FIX: 一刀斩断银行系统描述——DES: / ID: / INDN: 后面全是废话和人名
    text = re.split(r"\b(des:|id:|indn:|co\s*id:|web\b)", text)[0]
    # 去日期和电话
    text = re.sub(r"\b\d{2}/\d{2}\b", " ", text)
    text = re.sub(r"\b\d{3}-\d{3}-\d{4}\b", " ", text)

    words = []
    for w in re.split(r"[^a-z0-9]", text):
        if len(w) >= 3 and not w.isnumeric() and w not in STOP_WORDS:
            words.append(w)
    return words


def are_names_similar(name1, name2):
    """🔧 FIX: 只认主店名（第一个核心词），不搞任何交集匹配"""
    f1 = extract_core_features(name1)
    f2 = extract_core_features(name2)
    if not f1 or not f2:
        return False
    # 唯一条件：第一个特征词完全一样，且长度>=4
    if f1[0] == f2[0] and len(f1[0]) >= 4:
        return True
    # 完全一样也行
    if f1 == f2:
        return True
    return False


# ==========================================
# 🔧 FIX: 分类引擎（彻底重写优先级）
# ==========================================
def auto_categorize(description, amount):
    desc = str(description).strip()
    desc_lower = desc.lower()

    # ━━━ 第 0 层：绝对最高优先级 ━━━
    # 还款 / 转账 / 手续费 关键词一旦命中，直接锁死，不再查询任何数据库！
    for cat in IMMUNE_CATEGORIES:
        for kw in KEYWORD_MAPPING[cat]:
            if _match_keyword(desc_lower, kw):
                return cat

    # ━━━ 第 1 层：本地私有记忆（存放 USCIS 等敏感项） ━━━
    lm = st.session_state["local_memory"]
    if not lm.empty:
        exact = lm[lm["交易描述"].str.lower() == desc_lower]
        if not exact.empty:
            return exact.iloc[-1]["类别"]
        desc_feat = extract_core_features(desc)
        if desc_feat:
            for _, row in lm.iterrows():
                hf = extract_core_features(str(row["交易描述"]))
                if hf and desc_feat[0] == hf[0] and len(desc_feat[0]) >= 4:
                    return row["类别"]

    # ━━━ 第 2 层：全局 GitHub 大脑 ━━━
    global_df = load_global_knowledge()
    if not global_df.empty:
        valid = global_df[global_df["类别"].isin(CATEGORIES)].copy()
        exact = valid[valid["交易描述"].str.lower() == desc_lower]
        if not exact.empty:
            return exact.iloc[-1]["类别"]
        desc_feat = extract_core_features(desc)
        if desc_feat:
            for _, row in valid.iterrows():
                hf = extract_core_features(str(row["交易描述"]))
                if hf and desc_feat[0] == hf[0] and len(desc_feat[0]) >= 4:
                    return row["类别"]

    # ━━━ 第 3 层：内置字典兜底（跳过已在第0层处理的类别） ━━━
    for cat, keywords in KEYWORD_MAPPING.items():
        if cat in IMMUNE_CATEGORIES:
            continue
        for kw in keywords:
            if _match_keyword(desc_lower, kw):
                return cat

    # ━━━ 第 4 层：正数=收入，其余=其他 ━━━
    try:
        if float(amount) > 0:
            return "其他收入"
    except Exception:
        pass
    return "其他"


# ==========================================
# 🔧 FIX: 退款抵消（只保留一个，删除重复定义）
# ==========================================
def apply_refund_cancellation(df):
    if df.empty:
        return df, 0
    refund_candidates = df[
        (df["金额"] > 0) & (~df["类别"].isin(["💳 信用卡还款", "💰 内部转账", "其他收入"]))
    ].copy()
    expense_candidates = df[df["金额"] < 0].copy()
    drop_indices = set()

    for r_idx, refund in refund_candidates.iterrows():
        r_amount = refund["金额"]
        r_desc = str(refund["交易描述"]).strip().lower()
        clean_r = re.sub(r"^(sq\s*\*|tst\s*\*|sp\s*\*|paypal\s*\*|poy\s*\*|dd\s+doordash\s*)", "", r_desc).strip()
        r_words = [w for w in re.split(r"[^a-z0-9]", clean_r) if len(w) > 2]

        possible = expense_candidates[
            (abs(expense_candidates["金额"] + r_amount) < 0.01) & (~expense_candidates.index.isin(drop_indices))
        ]
        if possible.empty:
            continue

        found = False
        for e_idx, expense in possible.iterrows():
            e_desc = str(expense["交易描述"]).strip().lower()
            clean_e = re.sub(r"^(sq\s*\*|tst\s*\*|sp\s*\*|paypal\s*\*|poy\s*\*|dd\s+doordash\s*)", "", e_desc).strip()
            if clean_r == clean_e or clean_r in clean_e or clean_e in clean_r:
                drop_indices.add(r_idx)
                drop_indices.add(e_idx)
                found = True
                break

        if not found and len(r_words) >= 2:
            for e_idx, expense in possible.iterrows():
                e_desc = str(expense["交易描述"]).strip().lower()
                clean_e = re.sub(r"^(sq\s*\*|tst\s*\*|sp\s*\*|paypal\s*\*|poy\s*\*|dd\s+doordash\s*)", "", e_desc).strip()
                e_words = [w for w in re.split(r"[^a-z0-9]", clean_e) if len(w) > 2]
                if len(e_words) >= 2 and r_words[0] == e_words[0] and r_words[1] == e_words[1]:
                    drop_indices.add(r_idx)
                    drop_indices.add(e_idx)
                    break

    if drop_indices:
        df = df.drop(index=list(drop_indices)).reset_index(drop=True)
        return df, len(drop_indices) // 2
    return df, 0


# ==========================================
# 解析器
# ==========================================
def parse_chase_pdf(uploaded_file):
    transactions = []
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            text = ""
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
        year = "2026"
        year_match = re.search(r"Opening/Closing Date.*?(\d{2})$", text, re.MULTILINE)
        if year_match:
            year = "20" + year_match.group(1)
        pattern = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+(-?[\d,]+\.\d{2})$", re.MULTILINE)
        for m in pattern.findall(text):
            date_str, desc, amount_str = m
            amount = float(amount_str.replace(",", "")) * -1
            transactions.append({"日期": f"{year}/{date_str}", "交易描述": desc.strip(), "金额": amount})
    except Exception as e:
        st.error(f"解析 PDF 失败: {e}")
        return pd.DataFrame()
    df = pd.DataFrame(transactions)
    if not df.empty:
        df["日期"] = pd.to_datetime(df["日期"]).dt.date
        df["类别"] = df.apply(lambda x: auto_categorize(x["交易描述"], x["金额"]), axis=1)
    return df


def parse_csv(uploaded_file):
    try:
        raw_lines = uploaded_file.getvalue().decode("utf-8").splitlines()
        header_row_index = 0
        is_boa = is_chase = False
        for i, line in enumerate(raw_lines[:20]):
            if "Transaction Date" in line and "Description" in line and "Amount" in line:
                header_row_index = i; is_chase = True; break
            elif "Date" in line and "Description" in line and "Amount" in line and "Running Bal." in line:
                header_row_index = i; is_boa = True; break
        uploaded_file.seek(0)
        df = pd.read_csv(uploaded_file, skiprows=header_row_index)
        if is_chase:
            extracted_df = pd.DataFrame({"日期": df["Transaction Date"], "交易描述": df["Description"], "金额": df["Amount"]})
        elif is_boa:
            extracted_df = pd.DataFrame({"日期": df["Date"], "交易描述": df["Description"], "金额": df["Amount"]})
            extracted_df = extracted_df.dropna(subset=["金额"])
        else:
            st.error("未能识别的 CSV 格式"); return pd.DataFrame()
        extracted_df["日期"] = pd.to_datetime(extracted_df["日期"], errors="coerce").dt.date
        extracted_df = extracted_df.dropna(subset=["日期"])
        if extracted_df["金额"].dtype == "O":
            extracted_df["金额"] = extracted_df["金额"].str.replace(",", "").astype(float)
        else:
            extracted_df["金额"] = extracted_df["金额"].astype(float)
        extracted_df["类别"] = extracted_df.apply(lambda x: auto_categorize(x["交易描述"], x["金额"]), axis=1)
        return extracted_df
    except Exception as e:
        st.error(f"解析 CSV 失败: {e}"); return pd.DataFrame()


# ==========================================
# UI 布局
# ==========================================
with st.sidebar:
    st.markdown("### 🔒 隐私与数据安全")
    st.info("您的账单数据**仅存在于本次浏览器会话中**，关闭即销毁。")
    st.markdown("---")
    st.markdown("### 📂 恢复历史记忆")
    history_file = st.file_uploader("导入 personal_history.csv", type="csv")
    if history_file is not None:
        try:
            hist_df = pd.read_csv(history_file); hist_df["日期"] = pd.to_datetime(hist_df["日期"]).dt.date
            st.session_state["my_df"] = hist_df; st.success("账单导入成功！")
        except Exception:
            st.error("导入失败，文件格式不正确。")

    # 🔧 FIX: 新增私有记忆导入
    memory_file = st.file_uploader("导入 local_memory.csv (私有敏感词库)", type="csv")
    if memory_file is not None:
        try:
            st.session_state["local_memory"] = pd.read_csv(memory_file)
            st.success("私有敏感记忆导入成功！")
        except Exception:
            pass

    st.markdown("---")
    if not st.session_state["my_df"].empty:
        st.write(f"当前记录：**{len(st.session_state['my_df'])}** 条")
        st.download_button("💾 下载最新账单数据", st.session_state["my_df"].to_csv(index=False).encode("utf-8"), "personal_history.csv", "text/csv")
    # 🔧 FIX: 新增私有记忆下载
    if not st.session_state["local_memory"].empty:
        st.download_button("💾 下载私有敏感词库", st.session_state["local_memory"].to_csv(index=False).encode("utf-8"), "local_memory.csv", "text/csv",
                           help="保存了 USCIS / 保险 等敏感转账的手动分类记忆")
    if st.button("🗑️ 清空当前面板"):
        st.session_state["my_df"] = pd.DataFrame(columns=["日期", "交易描述", "金额", "类别"]); st.rerun()


st.title("🌍 智能财务管家 (Cloud & Crowdsourced)")
st.markdown("每一次人工修正都会让 AI 大脑变得更聪明！*(Zelle等隐私转账将被自动拦截，不会上传全局)*")

tab_import, tab_dashboard, tab_trends, tab_export = st.tabs(["📥 账单导入与修正", "📊 月度消费概览", "📈 历史支出趋势追踪", "📥 自定义多月导出"])

with tab_import:
    st.header("1. 上传新账单 (PDF或CSV)")
    uploaded_file = st.file_uploader("支持 Chase PDF, Chase CSV, BoA CSV", type=["pdf", "csv"], key="new_statement")
    if uploaded_file is not None:
        with st.spinner("正在呼叫全局大脑进行智能分类..."):
            fn = uploaded_file.name.lower()
            new_df = parse_chase_pdf(uploaded_file) if fn.endswith(".pdf") else parse_csv(uploaded_file) if fn.endswith(".csv") else pd.DataFrame()
            if new_df.empty:
                st.warning("未能提取到记录。")
            else:
                st.success(f"成功提取 {len(new_df)} 条交易记录！")
                st.subheader("2. 人工修正窗口 (双击类别修改)")
                edited_df = st.data_editor(new_df, column_config={
                    "类别": st.column_config.SelectboxColumn("消费类别", options=CATEGORIES, required=True),
                    "金额": st.column_config.NumberColumn("金额", format="%.2f"),
                    "日期": st.column_config.DateColumn("交易日期"),
                }, hide_index=True, num_rows="dynamic", use_container_width=True)

                if st.button("💾 确认无误，并入我的看板", type="primary"):
                    combined = pd.concat([st.session_state["my_df"], edited_df]).drop_duplicates(subset=["日期", "交易描述", "金额"])
                    cleaned, cnt = apply_refund_cancellation(combined)
                    st.session_state["my_df"] = cleaned
                    if cnt > 0: st.toast(f"🧹 成功抵消了 {cnt} 对退款！")

                    diff = edited_df["类别"] != new_df["类别"]
                    if diff.any():
                        shared_c = private_c = 0
                        for _, row in edited_df[diff].iterrows():
                            if update_knowledge(row["交易描述"], row["类别"]): shared_c += 1
                            else: private_c += 1
                        if shared_c: st.toast(f"🌍 {shared_c} 条已上传至公共大脑")
                        if private_c: st.toast(f"🔒 {private_c} 条已存入本地私有记忆")
                    st.balloons(); st.success("数据已入库！")

global_df = st.session_state["my_df"].copy()

with tab_dashboard:
    if global_df.empty:
        st.info("暂无数据，请先上传账单或导入历史记录。")
    else:
        global_df["年月"] = pd.to_datetime(global_df["日期"]).dt.to_period("M")
        available_months = sorted([str(m) for m in global_df["年月"].unique()], reverse=True)
        selected_month = st.selectbox("📅 选择要分析的月份", available_months)
        current_month_df = global_df[global_df["年月"] == pd.Period(selected_month, freq="M")]

        valid_expense_df = current_month_df[(current_month_df["金额"] < 0) & (~current_month_df["类别"].isin(["💳 信用卡还款", "💰 内部转账", "其他收入"]))]
        total_expense = valid_expense_df["金额"].sum()
        st.metric(f"💸 {selected_month} 真实总支出", f"$ {abs(total_expense):.2f}")
        st.markdown("---")

        st.subheader(f"🏆 {selected_month} 高额消费 Top 5")
        if not valid_expense_df.empty:
            top_5 = valid_expense_df.nsmallest(5, "金额")[["日期", "交易描述", "类别", "金额"]]
            top_5["金额"] = top_5["金额"].abs()
            for i, row in enumerate(top_5.itertuples(), 1):
                c1, c2, c3, c4 = st.columns([1, 4, 3, 2])
                c1.markdown(f"**#{i}**"); c2.text(row.交易描述); c3.text(row.类别); c4.markdown(f"**$ {row.金额:.2f}**")

        st.markdown("---")
        st.subheader(f"日常支出构成图 ({selected_month}) - *不含房租*")
        pie_expense_df = valid_expense_df[valid_expense_df["类别"] != "🏠 房租水电"].copy()
        pie_expense_df["绝对金额"] = pie_expense_df["金额"].abs()

        if not pie_expense_df.empty:
            idx_max = pie_expense_df.groupby("类别")["绝对金额"].idxmax()
            top_items = pie_expense_df.loc[idx_max][["类别", "交易描述", "绝对金额"]].rename(columns={"交易描述": "最大单笔", "绝对金额": "最大单笔金额"})
            pie_data = pie_expense_df.groupby("类别")["绝对金额"].sum().reset_index()
            pie_data = pd.merge(pie_data, top_items, on="类别")
            pie_data["hover_text"] = pie_data.apply(lambda x: f"最大单笔: {x['最大单笔']} (${x['最大单笔金额']:.2f})", axis=1)
            fig_pie = px.pie(pie_data, values="绝对金额", names="类别", hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel, custom_data=["hover_text"])
            fig_pie.update_traces(hovertemplate="<b>%{label}</b><br>总计: $%{value:.2f}<br>占比: %{percent}<br><br>%{customdata[0]}<extra></extra>")
            st.plotly_chart(fig_pie, use_container_width=True)

            st.markdown("#### 📝 本月所有消费明细")
            for category in sorted(current_month_df["类别"].unique()):
                cat_df = current_month_df[current_month_df["类别"] == category].sort_values(by="金额")
                # 🔧 FIX: 非消费项不计入金额
                if category in ["💳 信用卡还款", "💰 内部转账", "其他收入"]:
                    exp_title = f"{category} (非消费项，共 {len(cat_df)} 笔)"
                else:
                    expense_only = cat_df[cat_df["金额"] < 0]["金额"].sum()
                    exp_title = f"{category} (总支出: $ {abs(expense_only):.2f})"

                with st.expander(exp_title):
                    detail_df = cat_df[["日期", "交易描述", "金额", "类别"]].copy().reset_index(drop=True)
                    with st.form(key=f"form_{category}_{selected_month}"):
                        edited_df = st.data_editor(detail_df, column_config={
                            "日期": st.column_config.DateColumn("交易日期", disabled=True),
                            "交易描述": st.column_config.TextColumn("交易描述", disabled=True),
                            "金额": st.column_config.NumberColumn("金额", format="%.2f", disabled=True),
                            "类别": st.column_config.SelectboxColumn("分类 (双击修改 ✍️)", options=CATEGORIES, required=True),
                        }, hide_index=True, use_container_width=True)
                        submit_edits = st.form_submit_button("💾 批量保存修改")

                    if submit_edits:
                        diff = edited_df["类别"] != detail_df["类别"]
                        if diff.any():
                            my_df = st.session_state["my_df"]
                            total_updated = 0
                            for _, row in edited_df[diff].iterrows():
                                target_desc = row["交易描述"]
                                new_cat = row["类别"]

                                # 🔧 FIX: 绝对防御结界
                                # 条件1: 精确匹配（用户直接点击的那一行）永远允许修改
                                # 条件2: 相似名称连坐——但如果目标行含有免疫关键词（还款/转账），拒绝连坐！
                                mask = my_df["交易描述"].apply(
                                    lambda x: x == target_desc or (are_names_similar(x, target_desc) and not is_immune(x))
                                )
                                total_updated += mask.sum()
                                my_df.loc[mask, "类别"] = new_cat

                                is_global = update_knowledge(target_desc, new_cat)
                                if is_global:
                                    st.toast(f"🌍 '{target_desc}' 已全网同步")
                                else:
                                    st.toast(f"🔒 '{target_desc}' 已存入本地私有记忆")

                            st.session_state["my_df"] = my_df
                            st.success(f"🪄 关联更新：波及了 {total_updated} 条记录！")
                            st.rerun()


with tab_trends:
    if not global_df.empty:
        st.header("历史支出趋势追踪")
        trend_df = global_df[(global_df["金额"] < 0) & (~global_df["类别"].isin(["💳 信用卡还款", "💰 内部转账", "其他收入"]))].copy()
        if not trend_df.empty:
            trend_df["绝对金额"] = trend_df["金额"].abs()
            trend_df["年月"] = pd.to_datetime(trend_df["日期"]).dt.to_period("M").astype(str)
            monthly_summary = trend_df.groupby(["年月", "类别"])["绝对金额"].sum().reset_index()
            if not monthly_summary.empty:
                st.subheader("📊 总体消费趋势 (按月堆叠)")
                fig_bar = px.bar(monthly_summary, x="年月", y="绝对金额", color="类别", barmode="stack", color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig_bar, use_container_width=True)
                st.markdown("---")
                st.subheader("📈 单项分类趋势追踪")
                avail_cats = sorted(monthly_summary["类别"].unique())
                if avail_cats:
                    sel_cats = st.multiselect("🔍 选择消费类别", options=avail_cats, default=[avail_cats[0]])
                    if sel_cats:
                        filt = monthly_summary[monthly_summary["类别"].isin(sel_cats)]
                        fig_line = px.line(filt, x="年月", y="绝对金额", color="类别", markers=True, color_discrete_sequence=px.colors.qualitative.Pastel)
                        fig_line.update_layout(yaxis=dict(rangemode="tozero"))
                        st.plotly_chart(fig_line, use_container_width=True)

with tab_export:
    st.header("📥 自定义多月组合导出")
    if not global_df.empty:
        export_df = global_df.copy()
        export_df["Month"] = pd.to_datetime(export_df["日期"]).dt.strftime("%Y-%m")
        avail_months = sorted(export_df["Month"].unique(), reverse=True)
        sel_months = st.multiselect("📅 请选择月份（支持多选）：", options=avail_months, default=avail_months[:1] if avail_months else [])
        if sel_months:
            filt_df = export_df[export_df["Month"].isin(sel_months)].copy()
            exp_df = filt_df[(filt_df["金额"] < 0) & (~filt_df["类别"].isin(["💳 信用卡还款", "💰 内部转账", "其他收入"]))].copy()
            exp_df["绝对金额"] = exp_df["金额"].abs()
            if not exp_df.empty:
                c1, c2 = st.columns(2)
                pie_d = exp_df[exp_df["类别"] != "🏠 房租水电"]
                cat_sum = pie_d.groupby("类别")["绝对金额"].sum().reset_index()
                fig_ep = px.pie(cat_sum, values="绝对金额", names="类别", hole=0.4, title="合并支出占比(无房租)", color_discrete_sequence=px.colors.qualitative.Pastel)
                with c1: st.plotly_chart(fig_ep, use_container_width=True)
                fig_et = None
                with c2:
                    if len(sel_months) > 1:
                        td = exp_df.groupby(["Month", "类别"])["绝对金额"].sum().reset_index().sort_values("Month")
                        fig_et = px.line(td, x="Month", y="绝对金额", color="类别", markers=True, color_discrete_sequence=px.colors.qualitative.Pastel)
                        st.plotly_chart(fig_et, use_container_width=True)
                    else:
                        st.markdown(f"**🏆 {sel_months[0]} 支出分类排行**")
                        st.dataframe(cat_sum.sort_values("绝对金额", ascending=False).style.format({"绝对金额": "${:.2f}"}), hide_index=True, use_container_width=True)

                st.markdown(f"#### 📝 所选月份交易明细 ({len(filt_df)} 笔)")
                st.dataframe(filt_df.drop(columns=["Month", "年月", "绝对金额"], errors="ignore"), use_container_width=True)
                st.markdown("### 📥 导出报告")
                bc1, bc2 = st.columns(2)
                with bc1:
                    csv_d = filt_df.drop(columns=["Month", "年月", "绝对金额"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
                    st.download_button("📄 导出明细 (CSV)", csv_d, f"finance_{'_'.join(sel_months)}.csv", "text/csv")
                with bc2:
                    html = f"<html><head><meta charset='utf-8'><title>Report</title></head><body style='font-family:sans-serif;padding:20px'><h1>财务分析报告</h1><p>月份: {', '.join(sel_months)}</p><p>生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p><hr><h2>支出占比</h2>{fig_ep.to_html(full_html=False, include_plotlyjs='cdn')}"
                    if fig_et: html += f"<h2>支出趋势</h2>{fig_et.to_html(full_html=False, include_plotlyjs='cdn')}"
                    html += "</body></html>"
                    st.download_button("📈 导出图表报告 (HTML)", html, f"report_{'_'.join(sel_months)}.html", "text/html")
            else:
                st.warning("所选月份没有有效支出记录。")
