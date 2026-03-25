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

# 页面配置
st.set_page_config(page_title="Shareable 智能财务管家", page_icon="🌍", layout="wide")

# ==========================================
# 常量与全局设定
# ==========================================
GLOBAL_KNOWLEDGE_FILE = "shared_knowledge.csv"
LOCAL_PRIVATE_KNOWLEDGE_FILE = "local_private_knowledge.csv" # 专门存敏感转账类记忆

# 【个人隐私黑名单】：包含这些关键词的交易，绝对不会被上传到公共大脑，而是存在本地
PERSONAL_BLACKLIST = ["zelle", "venmo", "transfer", "online banking", "payment", "epay", "check", "deposit", "payroll", "ach"]

STOP_WORDS = {
    "the", "and", "store", "shop", "cafe", "restaurant", "market", 
    "inc", "llc", "com", "www", "st", "rd", "ave", "san", "jose", 
    "francisco", "ca", "ny", "tx", "pay", "payment", "bill", "sq", 
    "tst", "pos", "terminal", "valley", "fair", "center", "city",
    "santa", "clara", "diego", "monica", "sunnyvale", "los", "angeles",
    "purchase", "refund", "return", "debit", "credit", "card",
    "auth", "authorized", "transaction", "fee", "transfer", "direct",
    "dep", "deposit", "withdrawal", "atm", "online", "banking"
}

CATEGORIES = [
    "☕️ 咖啡奶茶", "🍱 餐饮外卖", "🛍️ 购物超市", "🛒 宠物消费", "🚗 交通油费",
    "✈️ 旅行住宿", "🧘🏻‍♀️ 运动健身", "🎿 娱乐票务", "🏥 医疗健康", "🏠 房租水电",
    "📦 生活杂项", "🏦 银行手续费", "💳 信用卡还款", "💰 内部转账", "其他", "其他收入"
]

# 初始内置字典 (最高优先级)
KEYWORD_MAPPING = {
    "💳 信用卡还款": ["payment thank you", "autopay", "payment to", "chase card", "credit card bill payment", "chase credit crd", "american express des:ach", "epay", "online banking payment to crd"],
    "💰 内部转账": ["online banking transfer", "zelle payment", "venmo"],
    "🏦 银行手续费": ["annual membership fee", "fee", "interest"],
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
    "📦 生活杂项": ["usps", "comcast", "utilities", "apple", "google", "openai"]
}

# 提取出所有需要“绝对免疫连坐”的关键词
IMMUNE_KEYWORDS = set()
for kw_list in [KEYWORD_MAPPING["💳 信用卡还款"], KEYWORD_MAPPING["💰 内部转账"], KEYWORD_MAPPING["🏦 银行手续费"]]:
    IMMUNE_KEYWORDS.update(kw_list)

def is_immune(desc):
    """判断一条交易描述是否属于绝对免疫项 (含有还款/转账/手续费等铁词)"""
    desc_lower = str(desc).lower()
    return any(kw in desc_lower or kw.replace(' ', '') in desc_lower.replace(' ', '') for kw in IMMUNE_KEYWORDS)


# ==========================================
# 会话状态管理 
# ==========================================
if 'my_df' not in st.session_state:
    st.session_state['my_df'] = pd.DataFrame(columns=["日期", "交易描述", "金额", "类别"])
if 'local_memory' not in st.session_state:
    st.session_state['local_memory'] = pd.DataFrame(columns=["交易描述", "类别", "贡献次数"])

# ==========================================
# 大脑逻辑 (Public + Private)
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
                encoded_content = base64.b64encode(csv_content.encode('utf-8')).decode('utf-8')
                payload = {"message": "🤖 Auto-update Shared Knowledge", "content": encoded_content}
                if sha: payload["sha"] = sha
                requests.put(url, headers=headers, json=payload)
            except: pass
    threading.Thread(target=push_to_github).start()

def update_knowledge(description, category):
    """
    智能分配：敏感转账存本地 Session，正常消费存 GitHub
    """
    desc_lower = str(description).lower()
    is_private = any(bw in desc_lower for bw in PERSONAL_BLACKLIST)
    
    if is_private:
        # 存入本地记忆池
        local_df = st.session_state['local_memory']
        match_idx = local_df[local_df['交易描述'].str.lower() == desc_lower].index
        if not match_idx.empty:
            local_df.at[match_idx[0], '类别'] = category
        else:
            new_row = pd.DataFrame([{"交易描述": description, "类别": category, "贡献次数": 1}])
            st.session_state['local_memory'] = pd.concat([local_df, new_row], ignore_index=True)
        return False # False 代表私有
    else:
        # 存入全局 GitHub
        global_df = load_global_knowledge()
        match_idx = global_df[global_df['交易描述'].str.lower() == desc_lower].index
        if not match_idx.empty:
            global_df.at[match_idx[0], '类别'] = category
            global_df.at[match_idx[0], '贡献次数'] += 1
        else:
            new_row = pd.DataFrame([{"交易描述": description, "类别": category, "贡献次数": 1}])
            global_df = pd.concat([global_df, new_row], ignore_index=True)
        save_global_knowledge(global_df)
        return True # True 代表全局

# ==========================================
# 匹配算法
# ==========================================
def extract_core_features(text):
    text = str(text).lower()
    text = re.sub(r'^(sq\s*\*|tst\s*\*|sp\s*\*|paypal\s*\*|poy\s*\*|dd\s+doordash\s*)', '', text)
    # 最强斩断器：一切系统描述直接抛弃
    text = re.split(r'\b(des:|id:|indn:|co id:|auth:|web)\b', text)[0]
    text = re.sub(r'\b\d{2}/\d{2}\b', ' ', text)
    text = re.sub(r'\b\d{3}-\d{3}-\d{4}\b', ' ', text)
    
    words = []
    for w in re.split(r'[^a-z0-9]', text):
        if len(w) >= 3 and not w.isnumeric() and w not in STOP_WORDS:
            words.append(w)
    return words

def are_names_similar(name1, name2):
    features1 = extract_core_features(name1)
    features2 = extract_core_features(name2)
    if not features1 or not features2: return False
    
    # 唯一连坐条件：前置第一个特征词完全一样，且不是特别短的通用词
    if features1[0] == features2[0] and len(features1[0]) >= 4:
        return True
    if features1 == features2:
        return True
    return False

# ==========================================
# 分类引擎
# ==========================================
def auto_categorize(description, amount):
    desc = str(description).strip()
    desc_lower = desc.lower()
    
    # 【最高优先级 0】: 强制拦截！只要触发了还款、转账等关键词，神仙也拦不住，直接归类
    for category in ["💳 信用卡还款", "💰 内部转账", "🏦 银行手续费"]:
        for keyword in KEYWORD_MAPPING[category]:
            if keyword in desc_lower or keyword.replace(' ', '') in desc_lower.replace(' ', ''):
                return category
                
    # 【次高优先级 1】: 用户私有敏感记忆库 (解决 USCIS 这种不想上云但又想自动识别的)
    local_df = st.session_state['local_memory']
    if not local_df.empty:
        exact_match = local_df[local_df['交易描述'].str.lower() == desc_lower]
        if not exact_match.empty:
            return exact_match.iloc[-1]['类别']
            
        desc_features = extract_core_features(desc)
        if desc_features:
            for _, row in local_df.iterrows():
                hist_features = extract_core_features(str(row['交易描述']))
                if hist_features and desc_features[0] == hist_features[0]:
                    return row['类别']
    
    # 【常规优先级 2】: 全局 GitHub 大脑
    global_df = load_global_knowledge()
    if not global_df.empty:
        valid_history = global_df[global_df['类别'].isin(CATEGORIES)].copy()
        
        exact_match = valid_history[valid_history['交易描述'].str.lower() == desc_lower]
        if not exact_match.empty:
            return exact_match.iloc[-1]['类别']
            
        desc_features = extract_core_features(desc)
        if desc_features: 
            for _, row in valid_history.iterrows():
                hist_features = extract_core_features(str(row['交易描述']))
                if hist_features and desc_features[0] == hist_features[0]:
                    return row['类别']
    
    # 【最后兜底 3】: 常规内置字典
    for category, keywords in KEYWORD_MAPPING.items():
        if category in ["💳 信用卡还款", "💰 内部转账", "🏦 银行手续费"]: continue
        for keyword in keywords:
            if keyword in desc_lower or keyword.replace(' ', '') in desc_lower.replace(' ', ''):
                return category
            
    try:
        if float(amount) > 0: return "其他收入"
    except: pass
    return "其他"


def apply_refund_cancellation(df):
    if df.empty: return df, 0
    refund_candidates = df[(df['金额'] > 0) & (~df['类别'].isin(['💳 信用卡还款', '💰 内部转账', '其他收入']))].copy()
    expense_candidates = df[df['金额'] < 0].copy()
    drop_indices = set()
    
    for r_idx, refund in refund_candidates.iterrows():
        r_amount = refund['金额']
        r_desc = str(refund['交易描述']).strip().lower()
        
        clean_r_desc = re.sub(r'^(sq\s*\*|tst\s*\*|sp\s*\*|paypal\s*\*|poy\s*\*|dd\s+doordash\s*)', '', r_desc).strip()
        r_words = [w for w in re.split(r'[^a-z0-9]', clean_r_desc) if len(w) > 2]
        
        possible_matches = expense_candidates[(abs(expense_candidates['金额'] + r_amount) < 0.01) & (~expense_candidates.index.isin(drop_indices))]
        if possible_matches.empty: continue
            
        match_found = False
        for e_idx, expense in possible_matches.iterrows():
            e_desc = str(expense['交易描述']).strip().lower()
            clean_e_desc = re.sub(r'^(sq\s*\*|tst\s*\*|sp\s*\*|paypal\s*\*|poy\s*\*|dd\s+doordash\s*)', '', e_desc).strip()
            if clean_r_desc == clean_e_desc or clean_r_desc in clean_e_desc or clean_e_desc in clean_r_desc:
                drop_indices.add(r_idx)
                drop_indices.add(e_idx)
                match_found = True
                break
                
        if not match_found and len(r_words) >= 2:
            for e_idx, expense in possible_matches.iterrows():
                e_desc = str(expense['交易描述']).strip().lower()
                clean_e_desc = re.sub(r'^(sq\s*\*|tst\s*\*|sp\s*\*|paypal\s*\*|poy\s*\*|dd\s+doordash\s*)', '', e_desc).strip()
                e_words = [w for w in re.split(r'[^a-z0-9]', clean_e_desc) if len(w) > 2]
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
                if extracted: text += extracted + "\n"
        
        year = "2026"
        year_match = re.search(r'Opening/Closing Date.*?(\d{2})$', text, re.MULTILINE)
        if year_match: year = "20" + year_match.group(1)

        pattern = re.compile(r'^(\d{2}/\d{2})\s+(.+?)\s+(-?[\d,]+\.\d{2})$', re.MULTILINE)
        matches = pattern.findall(text)
        
        for match in matches:
            date_str, desc, amount_str = match
            amount = float(amount_str.replace(',', '')) * -1
            full_date = f"{year}/{date_str}"
            transactions.append({"日期": full_date, "交易描述": desc.strip(), "金额": amount})
    except Exception as e:
        st.error(f"解析 PDF 失败: {str(e)}")
        return pd.DataFrame()
        
    df = pd.DataFrame(transactions)
    if not df.empty:
        df['日期'] = pd.to_datetime(df['日期']).dt.date
        df['类别'] = df.apply(lambda x: auto_categorize(x['交易描述'], x['金额']), axis=1)
    return df

def parse_csv(uploaded_file):
    try:
        raw_lines = uploaded_file.getvalue().decode('utf-8').splitlines()
        header_row_index = 0
        is_boa = False
        is_chase = False
        
        for i, line in enumerate(raw_lines[:20]):
            if 'Transaction Date' in line and 'Description' in line and 'Amount' in line:
                header_row_index = i; is_chase = True; break
            elif 'Date' in line and 'Description' in line and 'Amount' in line and 'Running Bal.' in line:
                header_row_index = i; is_boa = True; break
        
        uploaded_file.seek(0)
        df = pd.read_csv(uploaded_file, skiprows=header_row_index)
        extracted_df = pd.DataFrame()
        
        if is_chase:
            extracted_df = pd.DataFrame({"日期": df['Transaction Date'], "交易描述": df['Description'], "金额": df['Amount']})
        elif is_boa:
            extracted_df = pd.DataFrame({"日期": df['Date'], "交易描述": df['Description'], "金额": df['Amount']})
            extracted_df = extracted_df.dropna(subset=['金额'])
        else:
            st.error("未能识别的 CSV 格式")
            return pd.DataFrame()
            
        extracted_df['日期'] = pd.to_datetime(extracted_df['日期'], errors='coerce').dt.date
        extracted_df = extracted_df.dropna(subset=['日期'])
        if extracted_df['金额'].dtype == 'O': 
            extracted_df['金额'] = extracted_df['金额'].str.replace(',', '').astype(float)
        else:
            extracted_df['金额'] = extracted_df['金额'].astype(float)
            
        extracted_df['类别'] = extracted_df.apply(lambda x: auto_categorize(x['交易描述'], x['金额']), axis=1)
        return extracted_df
    except Exception as e:
        st.error(f"解析 CSV 失败: {str(e)}")
        return pd.DataFrame()

# ==========================================
# UI 布局
# ==========================================
with st.sidebar:
    st.markdown("### 🔒 隐私与数据安全")
    st.info("您的账单数据**仅存在于本次浏览器会话中**，关闭网页即销毁，绝不上传服务器。")
    
    st.markdown("---")
    st.markdown("### 📂 恢复历史记忆")
    st.write("导入您之前下载的压缩包 (包含了您的账单和私有敏感分类)。")
    history_file = st.file_uploader("导入 personal_history.csv", type="csv")
    
    if history_file is not None:
        try:
            hist_df = pd.read_csv(history_file)
            hist_df['日期'] = pd.to_datetime(hist_df['日期']).dt.date
            st.session_state['my_df'] = hist_df
            st.success("账单导入成功！")
        except:
            pass

    memory_file = st.file_uploader("导入 local_memory.csv", type="csv")
    if memory_file is not None:
        try:
            mem_df = pd.read_csv(memory_file)
            st.session_state['local_memory'] = mem_df
            st.success("私有敏感记忆导入成功！")
        except: pass
            
    st.markdown("---")
    if not st.session_state['my_df'].empty or not st.session_state['local_memory'].empty:
        st.write("离开前请务必下载保存以下数据：")
        
        if not st.session_state['my_df'].empty:
            st.download_button(
                label="💾 下载最新账单数据",
                data=st.session_state['my_df'].to_csv(index=False).encode('utf-8'),
                file_name="personal_history.csv", mime="text/csv"
            )
        
        if not st.session_state['local_memory'].empty:
            st.download_button(
                label="💾 下载私有敏感词库 (USCIS等)",
                data=st.session_state['local_memory'].to_csv(index=False).encode('utf-8'),
                file_name="local_memory.csv", mime="text/csv",
                help="这里保存了触发了隐私保护的转账、还款等手动修改记录"
            )

    if st.button("🗑️ 清空当前面板"):
        st.session_state['my_df'] = pd.DataFrame(columns=["日期", "交易描述", "金额", "类别"])
        st.rerun()

st.title("🌍 智能财务管家 (Cloud & Crowdsourced)")

tab_import, tab_dashboard, tab_trends, tab_export = st.tabs(["📥 账单导入与修正", "📊 月度消费概览", "📈 历史支出趋势追踪", "📥 自定义多月导出"])

with tab_import:
    st.header("1. 上传新账单 (PDF或CSV)")
    uploaded_file = st.file_uploader("支持 Chase PDF, Chase CSV, 以及 BoA CSV 格式", type=["pdf", "csv"], key="new_statement")
    
    if uploaded_file is not None:
        with st.spinner("正在呼叫全局大脑进行智能分类..."):
            filename_lower = uploaded_file.name.lower()
            if filename_lower.endswith('.pdf'): new_df = parse_chase_pdf(uploaded_file)
            elif filename_lower.endswith('.csv'): new_df = parse_csv(uploaded_file)
            else: new_df = pd.DataFrame()
            
            if new_df.empty:
                st.warning("未能提取到记录。")
            else:
                st.success(f"成功提取 {len(new_df)} 条交易记录！")
                st.subheader("2. 人工修正窗口 (双击类别修改，将同步至云端大脑 🧠)")
                
                edited_df = st.data_editor(
                    new_df,
                    column_config={
                        "类别": st.column_config.SelectboxColumn("消费类别", options=CATEGORIES, required=True),
                        "金额": st.column_config.NumberColumn("金额 (负数表示支出)", format="%.2f"),
                        "日期": st.column_config.DateColumn("交易日期")
                    },
                    hide_index=True, num_rows="dynamic", use_container_width=True
                )
                
                if st.button("💾 确认无误，并入我的看板", type="primary"):
                    combined_df = pd.concat([st.session_state['my_df'], edited_df]).drop_duplicates(subset=['日期', '交易描述', '金额'])
                    cleaned_df, cleaned_count = apply_refund_cancellation(combined_df)
                    st.session_state['my_df'] = cleaned_df
                    
                    if cleaned_count > 0: st.toast(f"🧹 自动清理魔法：成功抵消了 {cleaned_count} 对退款与消费记录！")
                    
                    diff = edited_df['类别'] != new_df['类别']
                    if diff.any():
                        changed_rows = edited_df[diff]
                        shared_count = 0
                        private_count = 0
                        for _, row in changed_rows.iterrows():
                            is_global = update_knowledge(row['交易描述'], row['类别'])
                            if is_global: shared_count += 1
                            else: private_count += 1
                        
                        if shared_count > 0: st.toast(f"🌍 感谢贡献！您更正的 {shared_count} 条商户信息已上传至公共大脑。")
                        if private_count > 0: st.toast(f"🔒 {private_count} 条转账信息已存入本地私有记忆，请记得去侧边栏下载！")

                    st.balloons()
                    st.success("数据已入库！请前往「月度概览」查看。")

global_df = st.session_state['my_df'].copy()

with tab_dashboard:
    if global_df.empty:
        st.info("暂无数据，请先上传账单或从左侧边栏导入历史记录。")
    else:
        global_df['年月'] = pd.to_datetime(global_df['日期']).dt.to_period('M')
        available_months = sorted([str(m) for m in global_df['年月'].unique()], reverse=True)
        selected_month = st.selectbox("📅 选择要分析的月份", available_months)
        current_month_df = global_df[global_df['年月'] == pd.Period(selected_month, freq='M')]
        
        valid_expense_df = current_month_df[(current_month_df['金额'] < 0) & 
                                            (~current_month_df['类别'].isin(['💳 信用卡还款', '💰 内部转账', '其他收入']))]
        total_expense = valid_expense_df['金额'].sum()
        
        st.metric(f"💸 {selected_month} 真实总支出", f"$ {abs(total_expense):.2f}")
        st.markdown("---")
        
        st.subheader(f"🏆 {selected_month} 高额消费 Top 5")
        if not valid_expense_df.empty:
            top_5 = valid_expense_df.nsmallest(5, '金额')[['日期', '交易描述', '类别', '金额']]
            top_5['金额'] = top_5['金额'].abs()
            st.dataframe(top_5.style.format({'金额': '${:.2f}'}), hide_index=True, use_container_width=True)
            
        st.markdown("---")
        st.subheader(f"日常支出构成图 ({selected_month}) - *不含房租*")
        
        pie_expense_df = valid_expense_df[valid_expense_df['类别'] != '🏠 房租水电'].copy()
        pie_expense_df['绝对金额'] = pie_expense_df['金额'].abs()
        
        if not pie_expense_df.empty:
            idx_max = pie_expense_df.groupby('类别')['绝对金额'].idxmax()
            top_items = pie_expense_df.loc[idx_max][['类别', '交易描述', '绝对金额']].rename(columns={'交易描述': '最大单笔', '绝对金额': '最大单笔金额'})
            pie_data = pie_expense_df.groupby('类别')['绝对金额'].sum().reset_index()
            pie_data = pd.merge(pie_data, top_items, on='类别')
            pie_data['hover_text'] = pie_data.apply(lambda x: f"最大单笔: {x['最大单笔']} (${x['最大单笔金额']:.2f})", axis=1)
            
            fig_pie = px.pie(pie_data, values='绝对金额', names='类别', hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel, custom_data=['hover_text'])
            fig_pie.update_traces(hovertemplate="<b>%{label}</b><br>总计: $%{value:.2f}<br>占比: %{percent}<br><br>%{customdata[0]}<extra></extra>")
            st.plotly_chart(fig_pie, use_container_width=True)
            
            st.markdown("#### 📝 本月所有消费明细 (💡 神奇修正：双击修改类别，即刻更新全域图表！)")
            for category in sorted(current_month_df['类别'].unique()):
                cat_df = current_month_df[current_month_df['类别'] == category].sort_values(by='金额')
                expense_only = 0 if category in ['💳 信用卡还款', '💰 内部转账', '其他收入'] else cat_df[cat_df['金额'] < 0]['金额'].sum()
                
                exp_title = f"{category} (非消费项，共 {len(cat_df)} 笔)" if category in ['💳 信用卡还款', '💰 内部转账', '其他收入'] else f"{category} (总支出: $ {abs(expense_only):.2f})"
                
                with st.expander(exp_title):
                    detail_df = cat_df[['日期', '交易描述', '金额', '类别']].copy().reset_index(drop=True)
                    with st.form(key=f"form_{category}_{selected_month}"):
                        edited_df = st.data_editor(
                            detail_df,
                            column_config={
                                "日期": st.column_config.DateColumn("交易日期", disabled=True),
                                "交易描述": st.column_config.TextColumn("交易描述", disabled=True),
                                "金额": st.column_config.NumberColumn("金额", format="%.2f", disabled=True),
                                "类别": st.column_config.SelectboxColumn("分类 (双击修改)", options=CATEGORIES, required=True)
                            },
                            hide_index=True, use_container_width=True
                        )
                        submit_edits = st.form_submit_button("💾 批量保存修改")
                        
                    if submit_edits:
                        diff = edited_df['类别'] != detail_df['类别']
                        if diff.any():
                            changed_rows = edited_df[diff]
                            my_df = st.session_state['my_df']
                            total_updated = 0
                            
                            for _, row in changed_rows.iterrows():
                                target_desc = row['交易描述']
                                new_cat = row['类别']
                                
                                # 【免疫结界】：只允许连坐那些不包含敏感词的普通商户
                                mask = my_df['交易描述'].apply(lambda x: x == target_desc or (are_names_similar(x, target_desc) and not is_immune(x)))
                                
                                affected_count = mask.sum()
                                total_updated += affected_count
                                my_df.loc[mask, '类别'] = new_cat
                                
                                # 存入记忆 (智能分发)
                                is_global = update_knowledge(target_desc, new_cat)
                                if is_global: st.toast(f"🌍 感谢贡献！'{target_desc}' 已全网同步")
                                else: st.toast(f"🔒 隐私保护：'{target_desc}' 已存入本地私有记忆库")
                                    
                            st.session_state['my_df'] = my_df
                            st.success(f"🪄 关联更新：本次修改自动波及了 {total_updated} 条相似记录！")
                            st.rerun()

with tab_trends:
    if not global_df.empty:
        trend_df = global_df[(global_df['金额'] < 0) & (~global_df['类别'].isin(['💳 信用卡还款', '💰 内部转账', '其他收入']))].copy()
        if not trend_df.empty:
            trend_df['绝对金额'] = trend_df['金额'].abs()
            trend_df['年月'] = pd.to_datetime(trend_df['日期']).dt.to_period('M').astype(str)
            monthly_summary = trend_df.groupby(['年月', '类别'])['绝对金额'].sum().reset_index()
            
            st.subheader("📊 总体消费趋势 (按月堆叠)")
            fig_bar = px.bar(monthly_summary, x='年月', y='绝对金额', color='类别', barmode='stack', color_discrete_sequence=px.colors.qualitative.Pastel)
            st.plotly_chart(fig_bar, use_container_width=True)

with tab_export:
    st.header("📥 自定义多月组合导出")
    if not global_df.empty:
        export_df = global_df.copy()
        export_df['Month'] = pd.to_datetime(export_df['日期']).dt.strftime('%Y-%m')
        available_export_months = sorted(export_df['Month'].unique(), reverse=True)
        selected_export_months = st.multiselect("📅 请选择月份（支持多选）：", options=available_export_months, default=available_export_months[:1])
        
        if selected_export_months:
            filtered_export_df = export_df[export_df['Month'].isin(selected_export_months)].copy()
            expenses_df = filtered_export_df[(filtered_export_df['金额'] < 0) & (~filtered_export_df['类别'].isin(['💳 信用卡还款', '💰 内部转账', '其他收入']))].copy()
            expenses_df['绝对金额'] = expenses_df['金额'].abs()

            if not expenses_df.empty:
                col1, col2 = st.columns(2)
                pie_df = expenses_df[expenses_df['类别'] != '🏠 房租水电']
                fig_export_pie = px.pie(pie_df.groupby('类别')['绝对金额'].sum().reset_index(), values='绝对金额', names='类别', hole=0.4, title="合并支出占比(无房租)", color_discrete_sequence=px.colors.qualitative.Pastel)
                with col1: st.plotly_chart(fig_export_pie, use_container_width=True)
                
                with col2:
                    if len(selected_export_months) > 1:
                        trend_df = expenses_df.groupby(['Month', '类别'])['绝对金额'].sum().reset_index().sort_values(by='Month') 
                        fig_export_trend = px.line(trend_df, x='Month', y='绝对金额', color='类别', markers=True, title="各类支出趋势", color_discrete_sequence=px.colors.qualitative.Pastel)
                        st.plotly_chart(fig_export_trend, use_container_width=True)
                        
                csv_data = filtered_export_df.drop(columns=['Month', '绝对金额'], errors='ignore').to_csv(index=False).encode('utf-8-sig')
                st.download_button(label="📄 导出所选月份明细 (CSV)", data=csv_data, file_name=f"finance_tracker_{'_'.join(selected_export_months)}.csv", mime="text/csv")