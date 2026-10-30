"""真实制度文档语料来源清单（只收公开、免费、无个人信息的文档）。

字段：id, url, title, issuer, date, lang, kind(法规/金融监管/企业制度/模板手册)
文件格式由下载后的扩展名决定。
"""

# 已剔除：CISA ChemLock Security Roles and Responsibilities Template —— 实为联系人表单，没有条款结构。
C = "https://www.csrc.gov.cn/csrc/c106256"
SANS = "https://raw.githubusercontent.com/deepanshusood/SANS-Security-Policy-Templates/HEAD/"
CISA = "https://www.cisa.gov/sites/default/files/"

SOURCES = [
    # ---- 证监会规章（文字版 docx/doc 与 PDF 版）----
    ("csrc-qhdjg", f"{C}/c1653888/1653888/files/664ac3e073ac49948dd7679fe46d84e5.docx", "期货公司董事、监事和高级管理人员任职管理办法", "中国证监会", "2021-01-15", "zh", "financial_regulation"),
    ("csrc-qfii", f"{C}/c1653823/1653823/files/87af08cdc8d44c979ce0a372838fc5c8.docx", "合格境外机构投资者和人民币合格境外机构投资者境内证券期货投资管理办法", "中国证监会/人民银行/外汇局", "2020-09-25", "zh", "financial_regulation"),
    ("csrc-zrt", f"{C}/c1653885/1653885/files/a7650b7dc10740829f071881bdf13bc9.pdf", "转融通业务监督管理试行办法", "中国证监会", "2017-12-07", "zh", "financial_regulation"),
    ("csrc-xzmm", f"{C}/c1653880/1653880/files/3e7958d18b444d3891976707fb1b1c24.docx", "中国证券监督管理委员会限制证券买卖实施办法", "中国证监会", "2020-10-30", "zh", "financial_regulation"),
    ("csrc-jjpj", f"{C}/c1653866/1653866/files/b8e27963b62f40e7a34eb5aaec1fafae.pdf", "证券投资基金评价业务管理暂行办法", "中国证监会", "2020-10-30", "zh", "financial_regulation"),
    ("csrc-kcbipo", f"{C}/c1653777/1653777/files/55712f20664c4750b88134f606275b13.docx", "科创板首次公开发行股票注册管理办法（试行）", "中国证监会", "2020-07-10", "zh", "financial_regulation"),
    ("csrc-jjtg", f"{C}/c1653705/1653705/files/3aff82bca6a042eeb39a0e411c861166.pdf", "证券投资基金托管业务管理办法", "中国证监会/原银监会", "2020-03-20", "zh", "financial_regulation"),
    ("csrc-cybcx", f"{C}/c1654012/1654012/files/ee7425e650c74fb99da309968539aff5.docx", "创业板上市公司持续监管办法（试行）", "中国证监会", "2020-06-12", "zh", "financial_regulation"),
    ("csrc-bjyw", f"{C}/c1653903/1653903/files/18d0ac5356994487a7bb3426ba0de2c7.docx", "证券发行上市保荐业务管理办法", "中国证监会", "2020-06-12", "zh", "financial_regulation"),
    ("csrc-djcf", f"{C}/c1653994/1653994/files/9446abf177a74183a23392fe546dc047.pdf", "中国证券监督管理委员会冻结、查封实施办法", "中国证监会", "2020-03-20", "zh", "financial_regulation"),
    ("csrc-wstz", f"{C}/c1653966/1653966/files/6790b82464ab4e7b9db8953b26d7a519.docx", "外商投资证券公司管理办法", "中国证监会", "2020-03-20", "zh", "financial_regulation"),
    ("csrc-fkzb", f"{C}/c1653957/1653957/files/4b16ce5d503247a98b230015225a1925.docx", "证券公司风险控制指标管理办法", "中国证监会", "2020-03-20", "zh", "financial_regulation"),
    ("csrc-hggl", f"{C}/c1653949/1653949/files/f2b8e901a9f142adb3a48504f23685fa.docx", "证券公司和证券投资基金管理公司合规管理办法", "中国证监会", "2020-03-20", "zh", "financial_regulation"),
    ("csrc-cxjd", f"{C}/c1653941/1653941/files/d33e4f9f482e41fea723babe6853b3f0.pdf", "证券期货市场诚信监督管理办法", "中国证监会", "2020-03-20", "zh", "financial_regulation"),
    ("csrc-xxpl", f"{C}/c1653985/1653985/files/e2075a1e8c494bce93060f6ba58743b3.docx", "公开募集证券投资基金信息披露管理办法", "中国证监会", "2020-03-20", "zh", "financial_regulation"),
    ("csrc-gzzd", f"{C}/c1653993/1653993/files/2b9ccfa4191b444cbec6c849d49be710.docx", "证券期货规章制定程序规定", "中国证监会", "2020-03-13", "zh", "financial_regulation"),
    ("csrc-jwdb", f"{C}/c1654007/1654007/files/6584e0cc56684b5dac0339bfd00c694d.pdf", "境外证券期货交易所驻华代表机构管理办法", "中国证监会", "2019-07-25", "zh", "financial_regulation"),
    ("csrc-qhjd", f"{C}/c1654011/1654011/files/ecbab85ccedb4a79ae15edc35c91047f.docx", "期货公司监督管理办法", "中国证监会", "2019-06-04", "zh", "financial_regulation"),
    ("csrc-gqjl", f"{C}/c1654022/1654022/files/7d8f062ccf30469e809a02314dabbf77.docx", "上市公司股权激励管理办法", "中国证监会", "2018-08-15", "zh", "financial_regulation"),
    ("csrc-ljcy", f"{C}/c1654026/1654026/files/7c6a65feaead46e2b57d4f4cb3882777.pdf", "证券期货经营机构及其工作人员廉洁从业规定", "中国证监会", "2018-06-27", "zh", "financial_regulation"),
    ("csrc-ctpz", f"{C}/c1654029/1654029/files/b900b96956bb42bb99380db78f2cf7bf.doc", "存托凭证发行与交易管理办法（试行）", "中国证监会", "2018-06-06", "zh", "financial_regulation"),
    ("csrc-xzxk", f"{C}/c1654027/1654027/files/8ec66647b1ec430a8b3e4de0c78b92a9.doc", "中国证券监督管理委员会行政许可实施程序规定", "中国证监会", "2018-03-08", "zh", "financial_regulation"),
    # ---- 银行监管 / 地方政府 ----
    ("nfra-nknk", "https://www.nfra.gov.cn/chinese/OFFICE/PDF/1040649.pdf", "商业银行内部控制指引", "原中国银监会", "2014-09-12", "zh", "financial_regulation"),
    ("xa-gfxwj", "https://www.xa.gov.cn/web_files/xian/file/2025/08/11/202508111630300791714.pdf", "西安市行政规范性文件管理办法", "西安市人民政府", "2025-08-11", "zh", "law_regulation"),
    # ---- 上市公司内部制度（巨潮资讯）----
    ("cn-jsyh-nkpj", "https://static.cninfo.com.cn/finalpage/2016-11-30/1202849547.PDF", "江苏银行内部控制评价管理办法", "江苏银行", "2016-11-30", "zh", "corporate_policy"),
    ("cn-nkjc", "https://static.cninfo.com.cn/finalpage/2025-09-30/1224690361.PDF", "内部控制管理及检查监督办法", "上市公司（巨潮）", "2025-09-30", "zh", "corporate_policy"),
    ("cn-nkzd", "https://static.cninfo.com.cn/finalpage/2025-04-29/1223375665.PDF", "内部控制制度", "上市公司（巨潮）", "2025-04-29", "zh", "corporate_policy"),
    ("cn-zqgf-xxpl", "https://static.cninfo.com.cn/finalpage/2025-07-25/1224283030.PDF", "山东省章丘鼓风机股份有限公司信息披露管理制度", "章丘鼓风机", "2025-07-25", "zh", "corporate_policy"),
    ("cn-bfhx-nk", "https://static.cninfo.com.cn/finalpage/2025-12-19/1224886340.PDF", "北方化学工业股份有限公司内部控制管理制度", "北化股份", "2025-12-19", "zh", "corporate_policy"),
    ("cn-ty-nk", "https://static.cninfo.com.cn/finalpage/2025-12-15/1224876677.PDF", "天音通信控股股份有限公司内部控制管理制度", "天音控股", "2025-12-15", "zh", "corporate_policy"),
    ("cn-zgrj-gljy", "https://static.cninfo.com.cn/finalpage/2025-12-30/1224904195.PDF", "中国软件与技术服务股份有限公司关联交易管理制度", "中国软件", "2025-12-30", "zh", "corporate_policy"),
    ("cn-dfhk-gljy", "https://static.cninfo.com.cn/finalpage/2025-10-31/1224776268.PDF", "中国东方航空股份有限公司关联交易管理制度", "东方航空", "2025-10-31", "zh", "corporate_policy"),
    ("cn-lc-gljy", "https://static.cninfo.com.cn/finalpage/2025-10-31/1224777785.PDF", "浪潮电子信息产业股份有限公司关联交易管理制度", "浪潮信息", "2025-10-31", "zh", "corporate_policy"),
    ("cn-sg-mjzj", "https://static.cninfo.com.cn/finalpage/2026-08-26/1225507616.PDF", "曙光信息产业股份有限公司募集资金管理办法", "中科曙光", "2026-08-26", "zh", "corporate_policy"),
    ("cn-dwdb", "https://static.cninfo.com.cn/finalpage/2026-05-12/1225290643.PDF", "对外担保管理办法", "上市公司（巨潮）", "2026-05-12", "zh", "corporate_policy"),
    # ---- English: templates / corporate / regulation ----
    ("en-cisa-vdp", CISA + "2023-02/bod-20-01-vdp-template.docx", "Vulnerability Disclosure Policy Template (BOD 20-01)", "CISA", "2023-02", "en", "template_manual"),
    ("en-cisa-bgcheck", CISA + "2024-01/chemlock-background-checks-policy-template-508.docx", "ChemLock Personnel Background Checks Policy Template", "CISA", "2024-01", "en", "template_manual"),
    ("en-cisa-suspicious", CISA + "2024-08/chemlock-reporting-suspicious-activity-template-508.docx", "ChemLock Reporting Suspicious Activity Policy Template", "CISA", "2024-08", "en", "template_manual"),
    ("en-cisa-securechems", CISA + "publications/chemlock-secure-chems-template-nov21.docx", "ChemLock Secure Chemicals Template", "CISA", "2021-11", "en", "template_manual"),
    ("en-sans-aup", "https://assets.contentstack.io/v3/assets/blt36c2e63521272fdc/blt207beda4b7c14d22/636f1a30e3836b0c88e8f0a8/Acceptable_Use_Policy.pdf", "Acceptable Use Policy", "SANS Institute", "2022-10", "en", "template_manual"),
    ("en-sans-pwd", SANS + "44.%20password_protection_policy.pdf", "Password Protection Policy", "SANS Institute", "2022", "en", "template_manual"),
    ("en-sans-breach", SANS + "10.%20data_breach_response.pdf", "Data Breach Response Policy", "SANS Institute", "2022", "en", "template_manual"),
    ("en-sans-email", SANS + "16.%20email_policy.pdf", "Email Policy", "SANS Institute", "2022", "en", "template_manual"),
    ("en-sans-pandemic", SANS + "42.%20pandemic_response_planning_policy.pdf", "Pandemic Response Planning Policy", "SANS Institute", "2022", "en", "template_manual"),
    ("en-sans-ethics", SANS + "20.%20ethics_policy.pdf", "Ethics Policy", "SANS Institute", "2022", "en", "template_manual"),
    ("en-sussex-ig", "https://www.sussex.ics.nhs.uk/wp-content/uploads/2022/10/SXIG02-Information-Governance-Policy-FINAL.pdf", "Information Governance Policy", "NHS Sussex", "2022-10", "en", "corporate_policy"),
    ("en-uk-dpcharges", "https://www.legislation.gov.uk/uksi/2018/480/pdfs/uksi_20180480_en.pdf", "The Data Protection (Charges and Information) Regulations 2018", "UK Parliament", "2018", "en", "law_regulation"),
    ("en-uk-mlr", "https://www.legislation.gov.uk/uksi/2017/692/pdfs/uksi_20170692_en.pdf", "The Money Laundering, Terrorist Financing and Transfer of Funds Regulations 2017", "UK Parliament", "2017", "en", "law_regulation"),
    ("en-nist-csf2", "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf", "The NIST Cybersecurity Framework (CSF) 2.0", "NIST", "2024-02-26", "en", "template_manual"),
    ("en-eu-gdpr", "https://www.legislation.gov.uk/eur/2016/679/pdfs/eur_20160679_adopted_en.pdf", "Regulation (EU) 2016/679 (GDPR), as adopted", "European Union (via legislation.gov.uk)", "2016-04-27", "en", "law_regulation"),
]
