#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
職安法規觀測站 — 合併版儀表板

三個功能合一：
  - 最新動態：從官方新聞列表頁抓取，跨來源去重
  - 現行法規總覽：38 筆職安子法規清單，支援分類篩選、近期修正標示
  - 快速情境查詢：依作業情境索引相關法規
  - 職安人員實用區：人員配置門檻、教育訓練時數、裁罰基準

使用方式：
    python osh_dashboard.py                          # 只更新新聞
    python osh_dashboard.py --law-xml law.xml         # 新聞 + 用官方 XML 更新法規總覽
    python osh_dashboard.py --debug                   # 印出新聞來源實際抓到的內容
    python osh_dashboard.py -o docs/index.html        # 輸出到指定路徑

法規 XML 取得方式（請勿寫程式對查詢頁面爬取）：
    全國法規資料庫「公開資料下載」→ 法律資料檔下載（XML）
    或政府資料開放平台 https://data.gov.tw/dataset/18289
"""

import argparse
import json
import os
import re
import sys
import urllib.robotparser

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("缺少套件，請先執行：pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)


# ============================================================
# 共用設定
# ============================================================

OSH_KEYWORDS = [
    "職業安全衛生", "職安法", "職安署", "職安卡", "勞工安全衛生", "職業災害",
    "勞工健康", "母性健康", "勞動檢查", "危險性工作場所", "危險性機械",
    "職場霸凌", "缺氧症", "有機溶劑中毒", "鉛中毒", "特定化學物質",
    "粉塵危害", "危害性化學品", "營造安全衛生", "高架作業", "高溫作業",
    "重體力勞動", "精密作業", "高壓氣體勞工", "異常氣壓", "碼頭裝卸",
    "礦場安全", "礦場職業衛生", "船舶清艙", "鍋爐及壓力容器", "起重升降機具",
    "作業環境監測", "容許暴露標準", "局限空間", "熱危害", "工程安全",
    # 職安署新聞常見詞
    "作業安全", "工安", "職場健康", "安全衛生管理", "職災", "風險評估",
    "排氣裝置", "防護具", "安全訓練", "勞動檢查機構", "職場心理健康",
]
EXCLUDE_STATUS_KEYWORDS = ["廢止", "停止適用"]
USER_AGENT = "OSHDashboardBot/1.0 (+local personal use script)"
DATE_PATTERN = re.compile(r"(20\d{2})[.\-/年](\d{1,2})[.\-/月](\d{1,2})")

SOURCES = [
    # type="rss"：解析 RSS 2.0 / Atom feed；source_type：news/notice/event 決定前端預設顯示
    {"name": "勞動部新聞稿",    "org": "勞動部",              "url": "https://www.mol.gov.tw/1607/1632/1633/RssList",                          "type": "rss", "source_type": "news"},
    {"name": "職安署新聞稿",    "org": "勞動部職業安全衛生署", "url": "https://www.osha.gov.tw/48110/48417/48419/RssList",                     "type": "rss", "source_type": "news"},
    {"name": "職安署公布欄",    "org": "勞動部職業安全衛生署", "url": "https://www.osha.gov.tw/48110/48417/48423/RssList",                     "type": "rss", "source_type": "notice"},
    {"name": "職安署活動訊息",  "org": "勞動部職業安全衛生署", "url": "https://www.osha.gov.tw/48110/48417/48425/RssList",                     "type": "rss", "source_type": "event"},
    {"name": "勞動部法規公告",  "org": "勞動部",              "url": "https://www.mol.gov.tw/1607/1632/1634/RssList",                          "type": "rss", "source_type": "notice"},
    {"name": "行政院電子公報",  "org": "行政院",              "url": "https://gazette.nat.gov.tw/rss?agencyId=A22000000E",                     "type": "rss", "source_type": "notice"},
]

LAW_XML_URLS = [
    ("https://sendlaw.moj.gov.tw/PublicData/GetFile.ashx?DType=XML&AuData=CM", "law_data_cmd.xml"),   # 命令（規則/辦法/標準）
    ("https://sendlaw.moj.gov.tw/PublicData/GetFile.ashx?DType=XML&AuData=CF", "law_data_act.xml"),   # 法律（職業安全衛生法等）
]

REG_SOURCE = "https://law.moj.gov.tw/"
REG_SOURCE_EN = "https://law.moj.gov.tw/Eng/LawClass/LawAll.aspx"

_MOL = "勞動部"
_OSHA = "勞動部職業安全衛生署"
STATIC_REGISTRY = [
    {"name": "職業安全衛生法", "tier": "act", "cat": "管理制度", "date": "2025-12-19", "source": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=N0060001", "note": "全國法規資料庫已驗證", "authority": _MOL},
    {"name": "職業安全衛生法施行細則", "tier": "reg", "cat": "管理制度", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "職業安全衛生設施規則", "tier": "reg", "cat": "作業環境", "date": "2026-06-30", "source": REG_SOURCE, "note": "全國法規資料庫鏡像尚顯示舊版，資料同步中", "authority": _OSHA},
    {"name": "職業安全衛生管理辦法", "tier": "reg", "cat": "管理制度", "date": "2026-06-30", "source": REG_SOURCE, "note": "修正條文已見流通，正式生效日待官方公告確認", "authority": _OSHA},
    {"name": "職業安全衛生教育訓練規則", "tier": "reg", "cat": "管理制度", "date": "2026-06-25", "source": REG_SOURCE_EN, "note": "附表一、附表二時數修正", "authority": _OSHA},
    {"name": "勞工健康保護規則", "tier": "reg", "cat": "職業衛生", "date": None, "source": REG_SOURCE, "note": None, "authority": _MOL},
    {"name": "女性勞工母性健康保護實施辦法", "tier": "reg", "cat": "職業衛生", "date": None, "source": REG_SOURCE, "note": None, "authority": _MOL},
    {"name": "勞工作業環境監測實施辦法", "tier": "reg", "cat": "作業環境", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "職業安全衛生標示設置準則", "tier": "reg", "cat": "作業環境", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "妊娠與分娩後女性及未滿十八歲勞工禁止從事危險性或有害性工作認定標準", "tier": "reg", "cat": "職業衛生", "date": "2025-11-20", "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "異常氣壓危害預防標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "高架作業勞工保護措施標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "高溫作業勞工作息時間標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "精密作業勞工視機能保護設施標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "重體力勞動作業勞工保護措施標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "高壓氣體勞工安全規則", "tier": "reg", "cat": "機械設備", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "缺氧症預防規則", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "營造安全衛生設施標準", "tier": "reg", "cat": "營造工程", "date": "2026-06-30", "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "工程安全設計及整體工程統合管理辦法", "tier": "reg", "cat": "營造工程", "date": "2026-06-30", "source": REG_SOURCE, "note": "新訂定", "authority": _OSHA},
    {"name": "林場安全衛生設施規則", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "船舶清艙解體勞工安全規則", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "碼頭裝卸安全衛生設施標準", "tier": "reg", "cat": "特殊作業", "date": "2025-09-10", "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "礦場職業衛生設施標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "鍋爐及壓力容器安全規則", "tier": "reg", "cat": "機械設備", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "起重升降機具安全規則", "tier": "reg", "cat": "機械設備", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "危險性機械及設備安全檢查規則", "tier": "reg", "cat": "機械設備", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "危害性化學品標示及通識規則", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "危害性化學品評估及分級管理辦法", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "特定化學物質危害預防標準", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "有機溶劑中毒預防規則", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "鉛中毒預防規則", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "粉塵危害預防標準", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None, "authority": _OSHA},
    {"name": "勞工作業場所容許暴露標準", "tier": "reg", "cat": "化學品安全", "date": "2025-04-11", "source": REG_SOURCE_EN, "note": None, "authority": _OSHA},
    {"name": "新化學物質登記管理辦法", "tier": "reg", "cat": "化學品安全", "date": "2025-08-08", "source": REG_SOURCE_EN, "note": "新訂定", "authority": _OSHA},
    {"name": "優先管理化學品之指定及運作管理辦法", "tier": "reg", "cat": "化學品安全", "date": "2024-06-06", "source": REG_SOURCE_EN, "note": None, "authority": _OSHA},
    {"name": "職業災害預防及職業災害勞工重建補助辦法", "tier": "reg", "cat": "職業災害", "date": "2024-12-12", "source": REG_SOURCE_EN, "note": None, "authority": _MOL},
    {"name": "職業傷病診治醫療機構認可管理補助及職業傷病通報辦法", "tier": "reg", "cat": "職業災害", "date": "2024-11-18", "source": REG_SOURCE_EN, "note": None, "authority": _MOL},
    {"name": "職業災害勞工職能復健專業機構認可管理及補助辦法", "tier": "reg", "cat": "職業災害", "date": "2024-01-30", "source": REG_SOURCE_EN, "note": None, "authority": _MOL},
]

STATIC_DIRECTIVES = [
    {"name": "勞動部補助授權勞動檢查機構督促事業單位遵守職業安全衛生法令計畫", "tier": "dir", "date": "2026-07-07", "source": "https://law.moj.gov.tw/", "note": None},
    {"name": "適用職業安全衛生法部分規定之事業範圍", "tier": "notice", "date": "2026-07-01", "source": "https://law.moj.gov.tw/", "note": None},
    {"name": "職業安全衛生法第24條第1項規定之其他特定機械之種類及應具之容量", "tier": "notice", "date": "2026-07-01", "source": "https://law.moj.gov.tw/", "note": None},
    {"name": "勞動檢查機構執行職業安全衛生法第46條第2項講習實施要點", "tier": "dir", "date": "2026-07-01", "source": "https://law.moj.gov.tw/", "note": "訂定"},
    {"name": "職業安全衛生管理系統績效審查及績效良好選拔作業要點", "tier": "dir", "date": "2026-07-01", "source": "https://law.moj.gov.tw/", "note": "修正並改名"},
    {"name": "違反職業安全衛生法及勞動檢查法案件處理要點", "tier": "dir", "date": "2026-06-30", "source": "https://law.moj.gov.tw/", "note": None},
]

TIER_LABEL = {"act": "法律", "reg": "法規命令", "dir": "行政規則", "notice": "公告"}

# 法規分類（決定顯示順序）
CATEGORIES = ["管理制度", "作業環境", "職業衛生", "化學品安全", "機械設備", "特殊作業", "營造工程", "職業災害"]

# 合規稽核 Checklist（每筆法規的可勾選稽核項目）
CHECKLISTS = {
    "職業安全衛生法": [
        "已設置職業安全衛生委員會（§13，50人以上）",
        "已置業務主管及管理員/師（§22）",
        "已訂定安全衛生工作守則並報請備查（§23）",
        "已辦理一般安全衛生教育訓練（§32）",
        "已辦理特殊作業安全衛生教育訓練（§32）",
        "已依規定施行體格及健康檢查（§21）",
        "危險性工作場所已完成審查或檢查（§26）",
        "已訂定承攬管理計畫並告知危害（§26）",
        "共同作業已設協議組織（§27）",
    ],
    "職業安全衛生管理辦法": [
        "已建立職業安全衛生管理系統（§12之1）",
        "已制定安全衛生管理計畫（§12之2）",
        "已依行業別設置安全衛生組織（附表一、二）",
        "承攬管理：原事業單位已告知承攬人危害（§26）",
        "已訂定承攬管理計畫（§27）",
        "已舉辦安全衛生協議組織定期會議（§27）",
        "已實施自動檢查（設備每年至少一次）（§14）",
        "已訂定作業程序書（高風險作業）",
        "職業安全衛生委員會每季召開（§11）",
    ],
    "職業安全衛生設施規則": [
        "危險機械設備已取得合格證書（§23）",
        "局限空間作業前已測定含氧量及有害氣體（§29之1）",
        "高架作業已裝設安全防護設施（§224）",
        "電氣設備有接地及漏電斷路器（§248）",
        "個人防護具已依規提供且確認使用（§277）",
        "緊急應變計畫已訂定並定期演練（§23）",
        "防火設備定期維護（§164）",
        "有害物質儲存區已設置緊急沖洗設備（§301）",
    ],
    "職業安全衛生管理辦法": [
        "已建立職業安全衛生管理系統",
        "已制定安全衛生管理計畫",
        "已依行業別設置安全衛生組織",
        "已訂定承攬管理計畫",
        "已舉辦協議組織定期會議",
        "已實施自動檢查",
    ],
    "危害性化學品標示及通識規則": [
        "化學品已完成 GHS 分類（§5）",
        "容器已貼附符合規定的 GHS 標示（§7）",
        "已製作並更新安全資料表 SDS（§10）",
        "SDS 已告知勞工並放置於作業現場（§13）",
        "已建立危害性化學品清單（§16）",
        "勞工已接受化學品危害 SDS 教育訓練（§17）",
        "外文 SDS 已翻譯為中文版本（§12）",
    ],
    "勞工健康保護規則": [
        "已完成一般體格/健康檢查（§10）",
        "已完成特殊作業健康檢查（§14）",
        "健康異常勞工已進行健康追蹤（§20）",
        "50人以上已設置或委託健康服務機構（§4）",
        "健康資料已建立並保存10年（§21）",
        "女性勞工已實施母性健康保護評估（§31之1）",
    ],
    "勞工作業環境監測實施辦法": [
        "高風險作業場所已辦理作業環境監測（§7）",
        "已委託認可測定機構執行監測（§3）",
        "監測結果已告知勞工（§12）",
        "超標場所已採取改善措施（§13）",
        "監測紀錄保存至少3年（§14）",
    ],
    "缺氧症預防規則": [
        "已認定並標示缺氧危險場所（§4）",
        "已選任缺氧作業主管（§5）",
        "已訂定缺氧危險作業許可制度（§26之1）",
        "進入前已測定氧氣及有害氣體濃度（§14）",
        "已備置測氧儀、通風設備及防護具（§14）",
        "已訂定緊急避難及搶救方法（§26之2）",
        "勞工已接受缺氧症預防教育訓練（§22）",
    ],
    "營造安全衛生設施標準": [
        "已訂定施工安全計畫（§5之1）",
        "開挖作業已有擋土設施（§62）",
        "高架作業已設置護欄或安全網（§19）",
        "起重機械已取得合格證書（§155）",
        "職安卡制度已落實（進場訓練記錄）",
        "墜落防護設施符合標準（§17之1）",
        "已指定工地負責人（§5之1）",
    ],
    "高溫作業勞工作息時間標準": [
        "已量測作業場所綜合溫度熱指數（WBGT）（§3）",
        "已依 WBGT 值調整作息時間（附表）",
        "已提供防暑飲水及休息設施（§7）",
        "勞工已接受熱危害預防教育訓練（§8）",
        "高溫作業已辦理特殊健康檢查",
    ],
}


# 情境快速查詢
QUICK_SCENARIOS = [
    {
        "icon": "🚧",
        "name": "局限空間作業",
        "desc": "非常規作業、通風不良、有缺氧或有害氣體風險之受限空間（如隧道、桶槽、溝渠、坑道）",
        "laws": ["職業安全衛生設施規則", "缺氧症預防規則", "危害性化學品標示及通識規則", "勞工作業場所容許暴露標準"],
    },
    {
        "icon": "🏗️",
        "name": "高架作業",
        "desc": "離地面或樓板 2 公尺以上之高處從事作業，含施工架、鷹架、外牆等場合",
        "laws": ["高架作業勞工保護措施標準", "職業安全衛生設施規則", "營造安全衛生設施標準"],
    },
    {
        "icon": "⚗️",
        "name": "危害性化學品管理",
        "desc": "化學品盤點、SDS 查核、GHS 標示製作、作業環境監測規劃",
        "laws": [
            "危害性化學品標示及通識規則",
            "危害性化學品評估及分級管理辦法",
            "勞工作業場所容許暴露標準",
            "特定化學物質危害預防標準",
            "勞工作業環境監測實施辦法",
        ],
    },
    {
        "icon": "⚙️",
        "name": "危險性機械設備",
        "desc": "起重機、鍋爐、壓力容器、高壓氣體等需定期檢查或領用許可之設備",
        "laws": [
            "危險性機械及設備安全檢查規則",
            "起重升降機具安全規則",
            "鍋爐及壓力容器安全規則",
            "高壓氣體勞工安全規則",
        ],
    },
    {
        "icon": "🏢",
        "name": "職安衛管理系統建置",
        "desc": "首次導入管理制度、釐清人員配置義務、規劃教育訓練計畫",
        "laws": [
            "職業安全衛生管理辦法",
            "職業安全衛生教育訓練規則",
            "勞工作業環境監測實施辦法",
            "勞工健康保護規則",
        ],
    },
    {
        "icon": "🚑",
        "name": "職業災害發生後處理",
        "desc": "重大職災 24 小時通報、調查報告、勞工職能復健補助申請",
        "laws": [
            "職業安全衛生法",
            "職業災害預防及職業災害勞工重建補助辦法",
            "職業傷病診治醫療機構認可管理補助及職業傷病通報辦法",
        ],
    },
    {
        "icon": "👩‍⚕️",
        "name": "女性及特殊族群保護",
        "desc": "妊娠、分娩後勞工及未成年勞工之工作限制與健康保護措施",
        "laws": [
            "女性勞工母性健康保護實施辦法",
            "妊娠與分娩後女性及未滿十八歲勞工禁止從事危險性或有害性工作認定標準",
            "勞工健康保護規則",
        ],
    },
    {
        "icon": "🌡️",
        "name": "高溫熱危害作業",
        "desc": "戶外施工、鑄造、廚房等高溫環境，含熱中暑預防、作息時間規定",
        "laws": [
            "高溫作業勞工作息時間標準",
            "職業安全衛生設施規則",
            "勞工作業場所容許暴露標準",
        ],
    },
    {
        "icon": "🏭",
        "name": "營造工程安全管理",
        "desc": "建築工地、土木工程之施工安全計畫、危害評估及整體統合管理",
        "laws": [
            "營造安全衛生設施標準",
            "工程安全設計及整體工程統合管理辦法",
            "職業安全衛生管理辦法",
            "高架作業勞工保護措施標準",
        ],
    },
    {
        "icon": "🔬",
        "name": "作業環境監測",
        "desc": "定期監測義務、委託認可測定機構、結果記錄與改善",
        "laws": [
            "勞工作業環境監測實施辦法",
            "職業安全衛生管理辦法",
            "勞工作業場所容許暴露標準",
            "特定化學物質危害預防標準",
        ],
    },
]


# ============================================================
# 職安人員實用區（靜態 HTML）
# ============================================================

PRACTITIONER_HTML = """
<section id="practitioner">
  <h2>職安人員實用區</h2>
  <p class="section-note">法規本文之外，職安人員日常工作更常需要的是「門檻判斷」跟「實務案例」。以下整理自職業安全衛生管理辦法附表、教育訓練規則附表，以及官方裁罰查詢系統，內容變動不頻繁，維持靜態內容。</p>

  <h3 class="reg-subhead">應設置人員門檻（職業安全衛生管理辦法附表二）</h3>
  <div class="table-scroll">
    <table class="registry-table">
      <thead><tr><th>事業類別</th><th>勞工人數</th><th>應設置人員</th></tr></thead>
      <tbody>
        <tr><td class="rname">第一類事業（顯著風險）</td><td>未滿30人</td><td>丙種職業安全衛生業務主管</td></tr>
        <tr><td class="rname">第一類事業</td><td>30人以上未滿100人</td><td>乙種職業安全衛生業務主管</td></tr>
        <tr><td class="rname">第一類事業</td><td>100人以上未滿300人</td><td>甲種職業安全衛生業務主管</td></tr>
        <tr><td class="rname">第一類事業</td><td>300人以上未滿500人</td><td>甲種業務主管＋職業安全衛生管理員各1人</td></tr>
        <tr><td class="rname">第一類事業</td><td>500人以上</td><td>甲種業務主管＋職業安全（衛生）管理師＋管理員各1人以上</td></tr>
        <tr><td class="rname">第三類事業（低度風險）</td><td>未滿30人</td><td>丙種職業安全衛生業務主管</td></tr>
        <tr><td class="rname">第三類事業</td><td>30人以上未滿100人</td><td>乙種職業安全衛生業務主管</td></tr>
        <tr><td class="rname">第三類事業</td><td>100人以上未滿500人</td><td>甲種職業安全衛生業務主管</td></tr>
        <tr><td class="rname">第三類事業</td><td>500人以上</td><td>甲種業務主管＋職業安全衛生管理員各1人以上</td></tr>
      </tbody>
    </table>
  </div>
  <p class="table-footnote">表列為法定最低配置，事業單位仍應依實際危害風險增置人員；第二類事業（中度風險）門檻介於第一、三類之間，未列出，請查原辦法附表二。50人以上另需依職業安全衛生法第22條置勞工健康服務人員或委託專業機構。</p>

  <h3 class="reg-subhead">教育訓練時數對照（職業安全衛生教育訓練規則附表，115年6月25日修正版）</h3>
  <div class="table-scroll">
    <table class="registry-table">
      <thead><tr><th>類別</th><th>時數</th><th>適用對象／備註</th></tr></thead>
      <tbody>
        <tr><td class="rname">甲種職業安全衛生業務主管</td><td>42小時</td><td>法規與通識10小時＋一般行業管理制度12小時＋管理實務20小時</td></tr>
        <tr><td class="rname">乙種職業安全衛生業務主管</td><td>35小時</td><td>法規與通識10小時＋管理制度8小時＋管理實務17小時</td></tr>
        <tr><td class="rname">丙種職業安全衛生業務主管</td><td>21小時</td><td>未滿30人之事業單位適用（不分第一、二、三類）</td></tr>
        <tr><td class="rname">丁種職業安全衛生業務主管</td><td>最短時數</td><td>115年新增類別，限第二、三類事業且勞工人數5人以下之雇主本人或代理人；具體時數以官方公告為準</td></tr>
        <tr><td class="rname">營造業甲種職業安全衛生業務主管</td><td>42小時</td><td>法規與通識14小時（含營造安全衛生設施標準4小時）＋管理制度10小時＋管理實務18小時</td></tr>
        <tr><td class="rname">營造業丙種職業安全衛生業務主管</td><td>26小時</td><td>法規與通識4小時＋管理制度4小時＋管理實務18小時</td></tr>
        <tr><td class="rname">職業安全管理師</td><td>130小時</td><td>內含實作6小時；職業安全衛生相關法規占58小時</td></tr>
      </tbody>
    </table>
  </div>
  <p class="table-footnote">職業衛生管理師、職業安全衛生管理員之時數未列出，請查原規則附表三。本表為課程總時數，實際排課仍須依核備之教育訓練機構課程規劃辦理。</p>

  <h3 class="reg-subhead">裁罰基準參考（以臺北市政府裁罰基準為例，115年7月1日修正版）</h3>
  <div class="table-scroll">
    <table class="registry-table">
      <thead><tr><th>違反情形</th><th>法條依據</th><th>裁罰基準</th></tr></thead>
      <tbody>
        <tr><td class="rname">危害性化學品洩漏或引起火災、爆炸致發生重大職業災害（甲類事業）</td><td>第42條第1項</td><td>第一次100萬元，按次累加100萬元，最高累加至300萬元</td></tr>
        <tr><td class="rname">同上情形（乙類事業，115年7月1日後標準，較原30萬元提高）</td><td>第42條第1項</td><td>第一次50萬元，按次累加50萬元，最高累加至300萬元</td></tr>
        <tr><td class="rname">一般違反職業安全衛生法規定（未致重大職災之常見違規）</td><td>依違反條款而定</td><td>每項最高30萬元，得按次處罰；有立即發生危險之虞者，得令停工</td></tr>
      </tbody>
    </table>
  </div>
  <p class="table-footnote">裁罰基準由各地方政府勞工主管機關個別訂定並執行，各縣市金額可能略有差異，上表以臺北市政府公告版本為例，實際處分請以受處分之縣市公告基準及個案裁量為準。</p>

  <h3 class="reg-subhead">常用名詞與作業安全標準：以局限空間為例</h3>
  <div class="law-card">
    <dl>
      <dt>法定定義</dt><dd>依職業安全衛生設施規則第19條之1，指非供勞工在其內部從事經常性作業，勞工進出方法受限制，且無法以自然通風來維持充分、清淨空氣之空間。</dd>
      <dt>氣體濃度標準</dt><dd>氧氣濃度須保持在18%以上；一氧化碳濃度須藉換氣維持在35ppm以下；硫化氫濃度須藉換氣維持在10ppm以下（勞動部職業安全衛生署宣導標準）</dd>
      <dt>主要相關法規</dt><dd>職業安全衛生設施規則、缺氧症預防規則、營造安全衛生設施標準（隧道、沉箱等作業）</dd>
    </dl>
    <span class="stamp-badge">來源：<a href="https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=N0060008" target="_blank" rel="noopener">職業安全衛生設施規則</a>、<a href="https://www.mol.gov.tw/" target="_blank" rel="noopener">勞動部職業安全衛生署宣導資料</a></span>
  </div>
  <p class="table-footnote">「解釋令函」為個案函釋，內容因申請情境而異，尚未整理出可穩定引用的清單，故本節先以法規明文定義與官方宣導的作業標準呈現。</p>

  <h3 class="reg-subhead">實用查詢連結</h3>
  <div class="source-grid">
    <div class="source-card">
      <div class="org">違反勞動法令事業單位（雇主）查詢系統</div>
      <div class="role">勞動部　·　可查全國職安法及58項附屬法規之實際裁罰案例、違反條款、開罰金額</div>
      <div class="link"><a href="https://www.mol.gov.tw/1607/28162/28166/28246" target="_blank" rel="noopener">https://www.mol.gov.tw/1607/28162/28166/28246</a></div>
    </div>
    <div class="source-card">
      <div class="org">勞動部主管法規查詢系統－解釋令函</div>
      <div class="role">勞動部　·　法條抽象用語的實務認定依據，例如局限空間、共同作業之界定</div>
      <div class="link"><a href="https://law.moj.gov.tw/" target="_blank" rel="noopener">https://law.moj.gov.tw/</a></div>
    </div>
    <div class="source-card">
      <div class="org">重大職業災害公開網</div>
      <div class="role">勞動部職業安全衛生署　·　每日更新的個案層級職災揭露，適合作教育訓練案例</div>
      <div class="link"><a href="https://pacs.osha.gov.tw/17238" target="_blank" rel="noopener">https://pacs.osha.gov.tw/17238</a></div>
    </div>
    <div class="source-card">
      <div class="org">全國法規資料庫－勞動部主管法規</div>
      <div class="role">法務部　·　職安相關法規現行條文全文，可複製引用</div>
      <div class="link"><a href="https://law.moj.gov.tw/LawClass/LawSearchContent.aspx?pc=N" target="_blank" rel="noopener">https://law.moj.gov.tw/LawClass/LawSearchContent.aspx?pc=N</a></div>
    </div>
  </div>
</section>
"""


# ============================================================
# 職業安全衛生專業數據表
# ============================================================

# ── 罰則金額解析工具 ──────────────────────────────────────────
_ZH_DIGITS = {'零':0,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
_ZH_MULTS  = {'十':10,'百':100,'千':1000}

def _zh_to_num(s: str) -> int:
    s = s.strip()
    if re.match(r'^\d+$', s):
        return int(s)
    result, curr = 0, 0
    for ch in s:
        if ch in _ZH_DIGITS:
            curr = _ZH_DIGITS[ch]
        elif ch in _ZH_MULTS:
            if curr == 0 and ch == '十':
                curr = 1
            result += curr * _ZH_MULTS[ch]
            curr = 0
    return result + curr

def _parse_penalty_detail(art_no: str, content: str) -> dict:
    """從條文內容萃取結構化罰則資訊（罰鍰金額、刑責、義務主體）"""
    entry: dict = {
        "no": art_no, "text": content[:400],
        "fine_min": None, "fine_max": None,
        "criminal": None, "liable": [], "repeated": False,
    }
    # X萬元以上Y萬元以下
    m = re.search(
        r'(?:新臺幣|處)\s*([零一二三四五六七八九十百千\d]+)萬元以上\s*([零一二三四五六七八九十百千\d]+)萬元以下罰鍰',
        content)
    if m:
        entry["fine_min"] = _zh_to_num(m.group(1))
        entry["fine_max"] = _zh_to_num(m.group(2))
    else:
        m2 = re.search(
            r'(?:新臺幣|處|科)\s*([零一二三四五六七八九十百千\d]+)萬元以下罰鍰',
            content)
        if m2:
            entry["fine_max"] = _zh_to_num(m2.group(1))
        else:
            m3 = re.search(
                r'(?:新臺幣|處|科)\s*([零一二三四五六七八九十百千\d]+)千元以下罰鍰',
                content)
            if m3:
                entry["fine_max"] = round(_zh_to_num(m3.group(1)) / 10, 1)
    cm = re.search(r'([一二三四五六七八九十\d]+年?)以下有期徒刑', content)
    if cm:
        entry["criminal"] = f"有期徒刑 {cm.group(1)} 以下"
    elif '拘役' in content:
        entry["criminal"] = "拘役"
    for party in ["雇主", "事業單位", "工作者", "勞工", "製造者", "供應者", "設計者", "輸入者", "負責人"]:
        if party in content:
            entry["liable"].append(party)
    entry["repeated"] = bool(re.search(r'按次(?:連續)?處罰|得按次', content))
    return entry


# ── 適用對象資料表 ─────────────────────────────────────────────
APPLICABILITY_DATA = {
    "職業安全衛生法": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "適用所有事業單位（含承攬、派遣）；自營作業者另有規定",
    },
    "職業安全衛生法施行細則": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "隨母法（職安法）一併適用",
    },
    "職業安全衛生設施規則": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "一般作業場所安全設施通用標準",
    },
    "職業安全衛生管理辦法": {
        "industries": ["全行業"], "min_workers": 1,
        "risk_class": ["第一類事業", "第二類事業", "第三類事業"],
        "notes": "人員配置依附表一、二分類；第一類（製造/營造）門檻最嚴",
    },
    "職業安全衛生教育訓練規則": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "特殊作業、管理人員時數依附表；115年6月25日新增丁種主管",
    },
    "勞工健康保護規則": {
        "industries": ["全行業"], "min_workers": 50, "risk_class": [],
        "notes": "50人以上設健康服務人員或委託；特殊作業不受人數限制",
    },
    "女性勞工母性健康保護實施辦法": {
        "industries": ["全行業"], "min_workers": 100, "risk_class": [],
        "notes": "100人以上強制；其他得自願辦理",
    },
    "勞工作業環境監測實施辦法": {
        "industries": ["製造業", "化學工業", "礦業", "營造業", "電子業"],
        "min_workers": None, "risk_class": ["甲種作業", "乙種作業"],
        "notes": "有害物質暴露場所均適用，不設人數下限",
    },
    "職業安全衛生標示設置準則": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "危害場所皆須設置；配合危害通識規則使用",
    },
    "妊娠與分娩後女性及未滿十八歲勞工禁止從事危險性或有害性工作認定標準": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "孕婦、產後一年、未滿18歲勞工，應進行工作適性評估",
    },
    "異常氣壓危害預防標準": {
        "industries": ["營造業", "潛水業", "隧道工程"],
        "min_workers": None, "risk_class": [],
        "notes": "壓縮空氣作業（沉箱、潛水）或低壓環境",
    },
    "高架作業勞工保護措施標準": {
        "industries": ["營造業", "電信業", "維修業", "製造業"],
        "min_workers": None, "risk_class": [],
        "notes": "離地面或樓板2公尺以上作業即適用",
    },
    "高溫作業勞工作息時間標準": {
        "industries": ["製造業", "營造業", "餐飲業", "農業"],
        "min_workers": None, "risk_class": [],
        "notes": "WBGT≧25℃之作業環境；附表規定各溫度帶作息比例",
    },
    "精密作業勞工視機能保護設施標準": {
        "industries": ["電子業", "製造業", "印刷業"],
        "min_workers": None, "risk_class": [],
        "notes": "精密儀器操作、VDT作業超過一定時數",
    },
    "重體力勞動作業勞工保護措施標準": {
        "industries": ["製造業", "倉儲業", "營造業"],
        "min_workers": None, "risk_class": [],
        "notes": "搬運物重超過規定標準之作業",
    },
    "高壓氣體勞工安全規則": {
        "industries": ["化學工業", "食品加工", "鋼鐵業", "醫療業"],
        "min_workers": None, "risk_class": [],
        "notes": "製造、儲存、使用高壓氣體（依容量計）",
    },
    "缺氧症預防規則": {
        "industries": ["營造業", "製造業", "污水處理", "食品業"],
        "min_workers": None, "risk_class": [],
        "notes": "局限空間作業；密閉隧道、坑道、桶槽等",
    },
    "營造安全衛生設施標準": {
        "industries": ["營造業"],
        "min_workers": 1, "risk_class": ["甲種危險工作場所", "丙種危險工作場所"],
        "notes": "所有建築/土木/設備工程適用",
    },
    "工程安全設計及整體工程統合管理辦法": {
        "industries": ["營造業"],
        "min_workers": None, "risk_class": ["甲種危險工作場所"],
        "notes": "115年新訂；適用一定規模以上工程專案",
    },
    "林場安全衛生設施規則": {
        "industries": ["林業"],
        "min_workers": None, "risk_class": [], "notes": "林業作業專用",
    },
    "船舶清艙解體勞工安全規則": {
        "industries": ["航運業", "拆船業"],
        "min_workers": None, "risk_class": [], "notes": "船舶清艙、解體作業",
    },
    "碼頭裝卸安全衛生設施標準": {
        "industries": ["倉儲業", "運輸業"],
        "min_workers": None, "risk_class": [], "notes": "港口碼頭裝卸作業",
    },
    "礦場職業衛生設施標準": {
        "industries": ["礦業"],
        "min_workers": None, "risk_class": [], "notes": "礦場採掘作業專用",
    },
    "鍋爐及壓力容器安全規則": {
        "industries": ["製造業", "能源業", "化學工業", "食品業"],
        "min_workers": None, "risk_class": [],
        "notes": "設置鍋爐（第一種/第二種）或壓力容器即適用",
    },
    "起重升降機具安全規則": {
        "industries": ["製造業", "營造業", "倉儲業", "港口業"],
        "min_workers": None, "risk_class": [],
        "notes": "起重機、升降機、人字臂起重桿等",
    },
    "危險性機械及設備安全檢查規則": {
        "industries": ["製造業", "營造業", "能源業"],
        "min_workers": None, "risk_class": [],
        "notes": "鍋爐、壓力容器、起重機等危險性機械設備定期檢查",
    },
    "危害性化學品標示及通識規則": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "使用危害性化學品即適用（GHS標示+SDS）",
    },
    "危害性化學品評估及分級管理辦法": {
        "industries": ["全行業"], "min_workers": 5, "risk_class": [],
        "notes": "5人以上且使用危害化學品；依暴露及危害分級管理",
    },
    "特定化學物質危害預防標準": {
        "industries": ["化學工業", "製造業", "電子業", "皮革業"],
        "min_workers": None, "risk_class": [],
        "notes": "甲類（致癌）/乙類/丙類/丁類特定化學物質",
    },
    "有機溶劑中毒預防規則": {
        "industries": ["製造業", "印刷業", "乾洗業", "電子業", "皮革業"],
        "min_workers": None, "risk_class": [],
        "notes": "第一種/第二種/第三種有機溶劑作業",
    },
    "鉛中毒預防規則": {
        "industries": ["電池製造", "印刷業", "金屬冶煉", "電子業"],
        "min_workers": None, "risk_class": [],
        "notes": "鉛或其化合物之製造/處置/使用/廢棄作業",
    },
    "粉塵危害預防標準": {
        "industries": ["礦業", "營造業", "石材加工", "陶瓷業", "製藥業"],
        "min_workers": None, "risk_class": [],
        "notes": "產生粉塵之特定作業，含矽肺病預防",
    },
    "勞工作業場所容許暴露標準": {
        "industries": ["全行業"], "min_workers": None, "risk_class": [],
        "notes": "配合作業環境監測使用；規定各化學物質PEL/STEL",
    },
    "新化學物質登記管理辦法": {
        "industries": ["化學工業", "製造業"],
        "min_workers": None, "risk_class": [],
        "notes": "製造/輸入量≧100kg之新化學物質須登記",
    },
    "優先管理化學品之指定及運作管理辦法": {
        "industries": ["化學工業", "製造業", "電子業"],
        "min_workers": None, "risk_class": [],
        "notes": "指定之優先管理化學品須申報運作情形",
    },
    "職業災害預防及職業災害勞工重建補助辦法": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "職災勞工補助申請；含預防補助、職能復健",
    },
    "職業傷病診治醫療機構認可管理補助及職業傷病通報辦法": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "職業傷病認定、醫療機構認可、職災通報義務",
    },
    "職業災害勞工職能復健專業機構認可管理及補助辦法": {
        "industries": ["全行業"], "min_workers": 1, "risk_class": [],
        "notes": "職能復健機構認可標準與補助申請",
    },
}

# ── 稽查重點法規（依違規頻率排序）──────────────────────────────
INSPECTION_FOCUS_LAWS = {
    "職業安全衛生法": {
        "rank": 1, "focus_articles": ["6", "21", "23", "32", "37"],
        "common_violations": ["一般安全衛生教育訓練未落實", "未訂定安全衛生工作守則", "特殊作業未辦健康檢查", "重大職災未24小時通報"],
        "note": "歷年勞動檢查違規件數最高，適用所有事業單位",
    },
    "職業安全衛生設施規則": {
        "rank": 2, "focus_articles": ["19之1", "224", "228", "248", "277", "309"],
        "common_violations": ["局限空間進入前未辦許可及氣體測定", "高架作業護欄不符規定", "電氣設備未接地或漏電斷路器", "個人防護具未提供或未確認使用"],
        "note": "條文最多（超過300條），各類設施均有詳細規範",
    },
    "職業安全衛生管理辦法": {
        "rank": 3, "focus_articles": ["11", "12之1", "14", "23", "27"],
        "common_violations": ["職業安全衛生委員會未每季召開", "安全衛生管理計畫流於形式", "自動檢查記錄不完整", "承攬管理計畫未訂定"],
        "note": "管理制度面違規，常與設施規則同時被裁罰",
    },
    "危害性化學品標示及通識規則": {
        "rank": 4, "focus_articles": ["7", "10", "13", "16", "17"],
        "common_violations": ["容器GHS標示不完整或未更新", "SDS未放置於作業現場", "危害性化學品清單未建立或未更新", "勞工未接受SDS教育訓練"],
        "note": "製造業/化工業高頻違規，GHS標示常見格式錯誤",
    },
    "缺氧症預防規則": {
        "rank": 5, "focus_articles": ["4", "5", "14", "22", "26之1"],
        "common_violations": ["進入局限空間前未測定氧氣及有害氣體", "未選任缺氧作業主管", "未訂定緊急避難及搶救方法", "勞工未受缺氧症預防教育訓練"],
        "note": "致死率高，稽查人員重點查核項目之一",
    },
    "勞工作業環境監測實施辦法": {
        "rank": 6, "focus_articles": ["7", "12", "13", "14"],
        "common_violations": ["定期監測未辦理或逾期", "監測結果未告知勞工", "超過容許暴露標準未採取改善措施", "監測紀錄未保存3年"],
        "note": "化學品作業場所必查項目",
    },
    "營造安全衛生設施標準": {
        "rank": 7, "focus_articles": ["5之1", "17之1", "19", "62", "155"],
        "common_violations": ["未訂定施工安全計畫", "墜落防護設施不足（護欄/安全網）", "開挖作業無擋土支撐", "起重機械無合格證書"],
        "note": "營造業職災比例最高，稽查頻率最高行業",
    },
    "高架作業勞工保護措施標準": {
        "rank": 8, "focus_articles": ["3", "5", "6", "8"],
        "common_violations": ["未提供安全帶及安全母索", "作業前未評估環境風險", "安全設施設置不符標準"],
        "note": "墜落是營造業最主要死亡原因",
    },
    "特定化學物質危害預防標準": {
        "rank": 9, "focus_articles": ["6", "7", "20", "38"],
        "common_violations": ["密閉設備未達規定", "局部排氣裝置風速不足", "作業主管未選任", "定期實施自動檢查"],
        "note": "致癌物（甲類）違規有最嚴裁罰",
    },
    "高溫作業勞工作息時間標準": {
        "rank": 10, "focus_articles": ["3", "4", "7", "8"],
        "common_violations": ["未量測WBGT值", "未依WBGT調整作息比例", "未提供防暑飲水", "勞工未接受熱危害預防訓練"],
        "note": "夏季重點查核，職業性熱中暑為職業病",
    },
}

# ── CNS / ISO / 國際標準對應 ──────────────────────────────────
CNS_ISO_MAP = {
    "職業安全衛生法": [
        {"code": "ILO-OSH 2001", "title": "職業安全衛生管理系統指引"},
    ],
    "職業安全衛生管理辦法": [
        {"code": "ISO 45001:2018", "title": "職業安全衛生管理系統"},
        {"code": "CNS 45001:2019", "title": "職業安全衛生管理系統（臺灣國家標準）"},
    ],
    "危害性化學品標示及通識規則": [
        {"code": "GHS Rev.9 (2021)", "title": "化學品全球調和制度"},
        {"code": "CNS 15030", "title": "化學品分類及標示"},
    ],
    "危害性化學品評估及分級管理辦法": [
        {"code": "GHS Rev.9 (2021)", "title": "化學品全球調和制度"},
        {"code": "ISO 31000:2018", "title": "風險管理指引"},
    ],
    "勞工作業場所容許暴露標準": [
        {"code": "ACGIH TLV-TWA/STEL", "title": "美國工業衛生師協會容許暴露值"},
        {"code": "NIOSH REL", "title": "美國職業安全衛生研究所建議暴露值"},
    ],
    "勞工作業環境監測實施辦法": [
        {"code": "ISO 11171", "title": "顆粒污染量測方法"},
        {"code": "NIOSH Method Manual", "title": "美國NIOSH採樣分析方法手冊"},
    ],
    "鍋爐及壓力容器安全規則": [
        {"code": "ASME BPVC", "title": "美國機械工程師學會鍋爐及壓力容器規範"},
        {"code": "CNS 2160 系列", "title": "鍋爐及壓力容器相關國家標準"},
    ],
    "起重升降機具安全規則": [
        {"code": "ISO 4301 系列", "title": "起重機分類與設計規範"},
        {"code": "CNS 14253", "title": "吊掛用鋼索安全規範"},
    ],
    "缺氧症預防規則": [
        {"code": "ANSI/ASSP Z117.1", "title": "局限空間安全要求"},
        {"code": "OSHA 1910.146", "title": "受許可局限空間（美國參考）"},
    ],
    "異常氣壓危害預防標準": [
        {"code": "EN 14153 系列", "title": "潛水作業安全歐洲標準"},
        {"code": "ISO 11107", "title": "潛水呼吸氣體標準"},
    ],
    "高壓氣體勞工安全規則": [
        {"code": "ISO 11114 系列", "title": "氣瓶閥門相容性標準"},
        {"code": "CNS 4620", "title": "高壓氣體安全規則（臺灣）"},
    ],
    "特定化學物質危害預防標準": [
        {"code": "IARC 致癌物分類", "title": "國際癌症研究機構致癌物分級"},
        {"code": "ACGIH A1/A2", "title": "確定/疑似人類致癌物分類"},
    ],
    "有機溶劑中毒預防規則": [
        {"code": "ACGIH TLV-TWA", "title": "各有機溶劑容許暴露值"},
        {"code": "GHS Skin/Resp. Sensitizer", "title": "皮膚/呼吸道致敏標示"},
    ],
    "粉塵危害預防標準": [
        {"code": "ISO 7708", "title": "空氣品質—粒徑分組採樣定義"},
        {"code": "ACGIH TLV-TWA（可呼吸性粉塵）", "title": "可呼吸性粉塵濃度限值"},
    ],
    "缺氧症預防規則": [
        {"code": "ANSI/ASSP Z117.1", "title": "局限空間安全要求"},
    ],
    "職業安全衛生設施規則": [
        {"code": "ISO 45001:2018 §8.1", "title": "作業規劃與控制"},
        {"code": "IEC 60364", "title": "建築物電氣裝置安全標準"},
    ],
    "高溫作業勞工作息時間標準": [
        {"code": "ISO 7933:2004", "title": "熱環境—預測熱緊迫分析（PHS模型）"},
        {"code": "ACGIH WBGT TLV", "title": "濕球黑球溫度容許暴露值"},
    ],
    "新化學物質登記管理辦法": [
        {"code": "REACH (EU)", "title": "歐盟化學品登記、評估、授權及限制"},
        {"code": "K-REACH (KR)", "title": "韓國化學物質登記及評估法"},
    ],
}


# ============================================================
# 新聞：RSS 抓取、HTML 備用、去重
# ============================================================

def normalize_title(title: str) -> str:
    return re.sub(r"[「」『』()（）\[\]【】\s　！!，,、。.？?：:；;—－\-~～]", "", title).strip()


def extract_date(text: str) -> str | None:
    m = DATE_PATTERN.search(text)
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def parse_rss_date(pub_date: str) -> str | None:
    """將 RSS pubDate（RFC 822 格式）轉為 ISO 日期。"""
    if not pub_date:
        return None
    try:
        from email.utils import parsedate
        t = parsedate(pub_date.strip())
        if t:
            return date(t[0], t[1], t[2]).isoformat()
    except Exception:
        pass
    return extract_date(pub_date)


def fetch_rss_source(source: dict, debug: bool = False) -> list[dict]:
    """解析 RSS 2.0 / Atom feed，不需要 robots.txt 檢查（RSS 設計上供訂閱使用）。"""
    name, org, url = source["name"], source["org"], source["url"]
    print(f"處理 RSS 來源：{name}（{url}）")

    try:
        import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15, verify=False)
        resp.raise_for_status()
    except Exception as e:
        print(f"  請求失敗：{e}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"  RSS 解析失敗：{e}", file=sys.stderr)
        return []

    items = []
    # 偵測 Atom vs RSS：Atom 用 <entry>，RSS 用 <item>
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    atom_entries = root.findall(".//atom:entry", ns) or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    rss_items   = list(root.iter("item"))
    entries     = atom_entries if atom_entries else rss_items

    for entry in entries:
        is_atom = (entry.tag == "{http://www.w3.org/2005/Atom}entry" or
                   entry.tag == "entry")

        if is_atom:
            _ns = "http://www.w3.org/2005/Atom"
            def _af(tag):
                el = entry.find(f"{{{_ns}}}{tag}")
                return el if el is not None else entry.find(tag)

            title_el = _af("title")
            title = _text(title_el) if title_el is not None else ""
            link_el = _af("link")
            link = (link_el.get("href", "") if link_el is not None else "")
            date_raw = _text(_af("updated")) or _text(_af("published")) or ""
            description = _text(_af("summary")) or _text(_af("content")) or ""
            date_str = (date_raw[:10] if re.match(r"\d{4}-\d{2}-\d{2}", date_raw) else None) or extract_date(description) or extract_date(title)
        else:
            title = _text(entry.find("title"))
            link_el = entry.find("link")
            link = _text(link_el) if link_el is not None else ""
            if not link and link_el is not None:
                link = link_el.get("href", "")
            pub_date = _text(entry.find("pubDate"))
            description = _text(entry.find("description"))
            date_str = parse_rss_date(pub_date) or extract_date(description) or extract_date(title)

        if not title or not link:
            continue
        if not date_str:
            continue

        if debug:
            print(f"    {'Atom' if is_atom else 'RSS'} entry: {date_str}  {title[:50]}")

        items.append({"title": title.strip(), "link": link.strip(), "date": date_str,
                      "source": name, "org": org, "source_type": source.get("source_type", "news")})

    print(f"  RSS 解析出 {len(items)} 筆項目。")
    return items


def fetch_html_source(source: dict, debug: bool = False) -> list[dict]:
    """HTML 爬蟲備用方案（需先確認 robots.txt 允許）。"""
    name, org, url = source["name"], source["org"], source["url"]
    print(f"處理 HTML 來源：{name}（{url}）")

    rp = urllib.robotparser.RobotFileParser()
    parsed = urlparse(url)
    rp.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    try:
        rp.read()
    except Exception as e:
        print(f"  無法讀取 robots.txt（{e}），保守起見視為不允許", file=sys.stderr)
        return []
    if not rp.can_fetch(USER_AGENT, url):
        print("  robots.txt 不允許存取，跳過此來源。")
        return []

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  請求失敗：{e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    if debug:
        print(f"  [debug] 回應長度：{len(resp.text)} 字元")

    items = []
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True)
        if len(link_text) < 8 or link_text in ("回首頁", "網站導覽", "常見問答", "English"):
            continue
        date_str = None
        node = a
        for _ in range(6):
            node = node.find_next(string=True)
            if node is None:
                break
            found = extract_date(str(node))
            if found:
                date_str = found
                break
        if not date_str:
            continue
        items.append({"title": link_text, "link": urljoin(url, a["href"]), "date": date_str, "source": name, "org": org})

    if debug:
        print(f"  [debug] 抓到 {len(items)} 筆候選項目，前 5 筆：")
        for it in items[:5]:
            print(f"    - {it['date']}  {it['title'][:40]}")
    return items


def fetch_source(source: dict, debug: bool = False) -> list[dict]:
    if source.get("type") == "rss":
        return fetch_rss_source(source, debug)
    return fetch_html_source(source, debug)


def dedupe_and_filter_news(all_items: list[dict]) -> list[dict]:
    seen = {}
    for item in all_items:
        if not any(k in item["title"] for k in OSH_KEYWORDS):
            continue
        key = (normalize_title(item["title"]), item["date"])
        if key not in seen:
            seen[key] = {**item, "also_in": []}
        elif item["source"] not in seen[key]["also_in"] and item["source"] != seen[key]["source"]:
            seen[key]["also_in"].append(item["source"])
    results = list(seen.values())
    results.sort(key=lambda x: x["date"], reverse=True)
    return results


# ============================================================
# 法規：XML 解析、ROC 日期轉換、與靜態清單合併
# ============================================================

def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_latest_revision(history_text: str) -> dict:
    """從沿革內容解析最後一筆修正的變動項目（修正/增訂/刪除條文）。
    注意：沿革日期為中文數字，不解析，日期由 最新異動日期_roc 欄位提供。
    """
    if not history_text:
        return {}
    # 依序號分割各次修正（序號為阿拉伯數字）
    entries = re.split(r'(?=\d+\.中華民國)', history_text.strip())
    entries = [e.strip() for e in entries if e.strip()]
    if not entries:
        return {}
    last = re.sub(r'\s+', ' ', entries[-1])  # 壓縮空白

    def _arts(pattern: str) -> list[str]:
        m = re.search(pattern, last)
        if not m:
            return []
        raw = m.group(1)
        parts = re.split(r'[、，,\s]+', raw.strip())
        result = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # 處理範圍如 12～15
            if '～' in p or '~' in p:
                sides = re.split(r'[～~]', p)
                result.extend(s.strip() for s in sides if s.strip())
            else:
                result.append(p)
        return [x for x in result if re.match(r'^\d[\d\-]*$', x)]

    is_full = bool(re.search(r'(?:修正|訂定)發布(?:名稱及)?全文\s*\d+\s*條', last))
    modified = [] if is_full else _arts(r'修正(?:發布|公告)?(?:名稱及)?第\s*([\d\-~～、，,\s]+?)\s*條')
    added    = _arts(r'增訂(?:發布|公告)?(?:第\s*)?([\d\-~～、，,\s]+?)\s*條')
    deleted  = _arts(r'刪除第\s*([\d\-~～、，,\s]+?)\s*條')

    if not (is_full or modified or added or deleted):
        return {}

    return {
        "is_full": is_full,
        "modified": modified,
        "added": added,
        "deleted": deleted,
    }


def roc_to_iso(roc_str: str) -> str | None:
    digits = re.sub(r"\D", "", roc_str or "")
    if len(digits) == 8:
        # 西元年 YYYYMMDD（XML 公開資料格式）
        y, m, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
    elif len(digits) == 7:
        y, m, d = int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7])
    elif len(digits) == 6:
        y, m, d = int(digits[:2]) + 1911, int(digits[2:4]), int(digits[4:6])
    else:
        return None
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _download_law_xml(url: str, dest: Path) -> bool:
    """從全國法規資料庫下載 XML（ZIP 格式），解壓後存至 dest。"""
    import zipfile, io
    import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    print(f"下載法規 XML：{url}")
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, verify=False)
        resp.raise_for_status()
    except Exception as e:
        print(f"  下載失敗：{e}", file=sys.stderr)
        return False

    content_type = resp.headers.get("Content-Type", "")
    raw = resp.content

    if b"PK\x03\x04" in raw[:4] or "zip" in content_type.lower():
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
                if not xml_name:
                    print("  ZIP 內無 XML 檔案。", file=sys.stderr)
                    return False
                dest.write_bytes(zf.read(xml_name))
                print(f"  解壓縮 {xml_name} → {dest}")
        except Exception as e:
            print(f"  ZIP 解壓縮失敗：{e}", file=sys.stderr)
            return False
    else:
        dest.write_bytes(raw)
        print(f"  已儲存 XML → {dest}")

    return True


def parse_law_xml(xml_path: Path) -> list[dict]:
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        print(f"XML 解析失敗：{e}", file=sys.stderr)
        return []

    root = tree.getroot()
    records = []
    fetched_at = date.today().isoformat()
    today_str = date.today().strftime("%Y%m%d")

    for law in root.iter("法規"):
        name = _text(law.find("法規名稱"))
        law_type = _text(law.find("法規類別"))
        law_url = _text(law.find("法規網址"))
        if not name or any(k in law_type for k in EXCLUDE_STATUS_KEYWORDS):
            continue
        if not any(k in name for k in OSH_KEYWORDS):
            continue

        # 主管機關（此版本 XML 無此欄位，留空由靜態清單補充）
        authority = ""

        # 效力狀態：廢止註記 + 生效日期
        abolish_note = _text(law.find("廢止註記"))
        eff_date_roc = _text(law.find("生效日期"))
        status = "現行"
        if abolish_note:
            status = "廢止"
        elif eff_date_roc:
            # 使用 roc_to_iso() 統一處理 7 碼 ROC 與 8 碼西元格式
            eff_iso = roc_to_iso(eff_date_roc)
            if eff_iso and eff_iso > date.today().isoformat():
                status = "未生效"

        # 條文全部（按章節順序迭代，保留 編章節 結構）
        law_content = law.find("法規內容")
        articles = []
        deleted_arts = []
        scope_parts = []
        penalty_articles = []
        article_previews = {}
        _all_content: dict[str, str] = {}  # art_no → 完整條文（用於 search_text + cross_refs）
        chapters = []
        _cur_chap = None
        _cur_chap_arts: list[str] = []

        for node in (list(law_content) if law_content is not None else []):
            if node.tag == "編章節":
                if _cur_chap is not None and _cur_chap_arts:
                    chapters.append({"title": _cur_chap, "articles": _cur_chap_arts})
                _cur_chap = _text(node).strip()
                _cur_chap_arts = []
            elif node.tag == "條文":
                raw_no = _text(node.find("條號"))
                art_no = raw_no.replace("第", "").replace("條", "").strip()
                if not art_no:
                    continue
                content = _text(node.find("條文內容"))

                # 已刪除條文單獨記錄
                if content and content.strip() in ("（刪除）", "(刪除)"):
                    deleted_arts.append(art_no)
                    continue

                articles.append(art_no)
                if content:
                    article_previews[art_no] = content[:200]  # 擴充至 200 字供 Drawer 顯示
                    _all_content[art_no] = content
                if _cur_chap is not None:
                    _cur_chap_arts.append(art_no)

                # 適用範圍：前 3 條
                try:
                    if int(art_no) <= 3 and content:
                        scope_parts.append(f"第{art_no}條　{content[:200]}")
                except ValueError:
                    pass

                # 罰則條文（結構化解析）
                if content and ("罰鍰" in content or ("罰" in content and ("萬元" in content or "千元" in content))):
                    penalty_articles.append(_parse_penalty_detail(art_no, content))

        # 收尾最後一章
        if _cur_chap is not None and _cur_chap_arts:
            chapters.append({"title": _cur_chap, "articles": _cur_chap_arts})

        # 全文搜尋索引（③ 前端 client-side 搜尋用，max 3000 字）
        _st_parts, _st_total = [], 0
        for _no, _c in _all_content.items():
            chunk = f"第{_no}條 {_c} "
            if _st_total + len(chunk) > 3000:
                break
            _st_parts.append(chunk)
            _st_total += len(chunk)
        search_text = "".join(_st_parts)

        # 法規交叉引用（⑤ 解析條文中「依/準用/適用 + 法規名 + 第X條」的引用）
        # 法規交叉引用（⑤）- 支援阿拉伯數字與中文數字條號
        _cross_pat = re.compile(
            r'(?:依據?|準用|適用|按照?|依照?)\s*'
            r'([^\s，。、「」()（）\d第條據]{3,25}(?:法|條例|辦法|準則|規則|規程|標準))'
            r'(?:[^\n第]{0,6}?)'
            r'第\s*([\d一二三四五六七八九十百千]+(?:之\d+|-\d+)?)\s*條'
        )
        cross_refs: dict[str, list[str]] = {}
        for _art_no, _content in _all_content.items():
            for m in _cross_pat.finditer(_content):
                ref_law = m.group(1).strip()
                ref_art  = m.group(2).strip()
                if ref_law == name or name in ref_law or len(ref_law) < 4:
                    continue
                if ref_law[:2] in ('本法', '本條', '本辦', '本規', '本準', '依本', '於依', '其他'):
                    continue
                if ref_law not in cross_refs:
                    cross_refs[ref_law] = []
                if ref_art not in cross_refs[ref_law]:
                    cross_refs[ref_law].append(ref_art)

        # 是否含附表
        full_text = " ".join(_all_content.values())
        has_table = "附表" in full_text or "附件" in full_text

        # pcode
        pcode_m = re.search(r"pcode=(\w+)", law_url)
        pcode = pcode_m.group(1) if pcode_m else ""

        records.append({
            "name": name,
            "法規性質": _text(law.find("法規性質")),
            "法規類別": law_type,
            "最新異動日期_roc": _text(law.find("最新異動日期")),
            "生效日期_roc": eff_date_roc,
            "沿革摘要": _text(law.find("沿革內容"))[:400],
            "英文法規名稱": _text(law.find("英文法規名稱")),
            "法規網址": law_url,
            "pcode": pcode,
            "authority": authority,
            "status": status,
            "scope": "\n".join(scope_parts),
            "penalty_articles": penalty_articles,
            "articles": articles,
            "deleted_articles": deleted_arts,
            "chapters": chapters,
            "article_previews": article_previews,
            "search_text": search_text,
            "cross_refs": cross_refs,
            "latest_revision": _parse_latest_revision(_text(law.find("沿革內容"))),
            "has_table": has_table,
            "資料擷取日期": fetched_at,
        })
    return records


def merge_registry(static_list: list[dict], xml_records: list[dict]) -> list[dict]:
    merged = [dict(item) for item in static_list]
    by_name = {item["name"]: item for item in merged}

    for rec in xml_records:
        iso_date = roc_to_iso(rec["最新異動日期_roc"])
        if rec["name"] in by_name:
            entry = by_name[rec["name"]]
            if iso_date:
                entry["date"] = iso_date
            if rec["法規網址"]:
                entry["source"] = rec["法規網址"]
            if rec["沿革摘要"]:
                entry["summary"] = rec["沿革摘要"]
            if rec.get("pcode"):
                entry["pcode"] = rec["pcode"]
            if rec.get("articles"):
                entry["articles"] = rec["articles"]
            if rec.get("has_table"):
                entry["has_table"] = rec["has_table"]
            # 新增欄位
            if rec.get("authority"):
                entry["authority"] = rec["authority"]
            if rec.get("status"):
                entry["status"] = rec["status"]
            if rec.get("scope"):
                entry["scope"] = rec["scope"]
            if rec.get("penalty_articles"):
                entry["penalty_articles"] = rec["penalty_articles"]
            if rec.get("deleted_articles") is not None:
                entry["deleted_articles"] = rec["deleted_articles"]
            if rec.get("chapters") is not None:
                entry["chapters"] = rec["chapters"]
            if rec.get("article_previews"):
                entry["article_previews"] = rec["article_previews"]
            if rec.get("search_text"):
                entry["search_text"] = rec["search_text"]
            if rec.get("cross_refs"):
                entry["cross_refs"] = rec["cross_refs"]
            if rec.get("latest_revision"):
                entry["latest_revision"] = rec["latest_revision"]
        else:
            merged.append({
                "name": rec["name"],
                "tier": "reg",
                "cat": "其他",
                "date": iso_date,
                "source": rec["法規網址"] or REG_SOURCE,
                "note": "XML 新增，未在原始清單中",
                "pcode": rec.get("pcode", ""),
                "articles": rec.get("articles", []),
                "deleted_articles": rec.get("deleted_articles", []),
                "chapters": rec.get("chapters", []),
                "article_previews": rec.get("article_previews", {}),
                "search_text": rec.get("search_text", ""),
                "cross_refs": rec.get("cross_refs", {}),
                "latest_revision": rec.get("latest_revision", {}),
                "has_table": rec.get("has_table", False),
                "authority": rec.get("authority", ""),
                "status": rec.get("status", "現行"),
                "scope": rec.get("scope", ""),
                "penalty_articles": rec.get("penalty_articles", []),
            })
    return merged


# ============================================================
# HTML 產生
# ============================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>職安法規觀測站</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Noto+Serif+TC:wght@700;900&family=Noto+Sans+TC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --paper:#F4EFE2; --card:#FBF8EF; --ink:#1E2B3A; --ink-soft:#4C5A6B;
    --stamp:#9C2B22; --brass:#A8842E; --border:rgba(30,43,58,0.14);
    --green:#1a7340; --amber:#8c5e00; --hover:rgba(30,43,58,0.06);
    --blue:#2563eb; --font-sz:11.5px;
    --radius:4px; --shadow:0 1px 3px rgba(30,43,58,.08),0 1px 2px rgba(30,43,58,.04);
    --sidebar-bg:#EDE8D6;
  }}
  [data-theme="dark"] {{
    --paper:#181818; --card:#242424; --ink:#e4dfd5; --ink-soft:#888;
    --stamp:#d95c52; --brass:#c9a050; --border:rgba(255,255,255,0.13);
    --green:#34d399; --amber:#fbbf24; --hover:rgba(255,255,255,0.06);
    --blue:#60a5fa; --shadow:0 1px 3px rgba(0,0,0,.25),0 1px 2px rgba(0,0,0,.18);
    --sidebar-bg:#1a1a1a;
  }}
  @media(prefers-color-scheme:dark){{
    :root:not([data-theme="light"]){{
      --paper:#181818; --card:#242424; --ink:#e4dfd5; --ink-soft:#888;
      --stamp:#d95c52; --brass:#c9a050; --border:rgba(255,255,255,0.13);
      --green:#34d399; --amber:#fbbf24; --hover:rgba(255,255,255,0.06);
      --blue:#60a5fa; --shadow:0 1px 3px rgba(0,0,0,.25),0 1px 2px rgba(0,0,0,.18);
      --sidebar-bg:#1a1a1a;
    }}
  }}
  *{{ box-sizing:border-box; margin:0; padding:0; }}
  body{{ font-family:"Inter","Noto Sans TC",-apple-system,sans-serif; background:var(--paper); color:var(--ink); line-height:1.55; font-size:var(--font-sz); transition:background .25s,color .25s;
    -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale;
    text-rendering:optimizeLegibility; font-feature-settings:"kern" 1,"liga" 1; }}
  /* ── 頂部固定列 ── */
  .topbar{{ position:fixed; top:0; left:0; right:0; height:52px; background:var(--card);
    border-bottom:1px solid var(--border); border-top:3px solid var(--stamp);
    display:flex; align-items:center; padding:0 20px; gap:14px;
    z-index:300; box-shadow:0 2px 8px rgba(30,43,58,.08); }}
  .topbar-brand{{ display:flex; align-items:center; gap:12px; flex-shrink:0; }}
  .topbar-brand h1{{ font-family:"Noto Serif TC",serif; font-weight:900; font-size:15.5px;
    letter-spacing:-.01em; margin:0; color:var(--ink); }}
  .topbar-divider{{ width:1px; height:16px; background:var(--border); flex-shrink:0; }}
  .topbar-subtitle{{ font-size:10.5px; color:var(--ink-soft); letter-spacing:.02em; }}
  .topbar-right{{ margin-left:auto; display:flex; align-items:center; gap:10px; flex-shrink:0; }}
  .topbar-meta{{ font-size:10.5px; color:var(--ink-soft); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:480px; letter-spacing:.01em; font-variant-numeric:tabular-nums; }}
  /* ── 左側選單 ── */
  .sidebar{{ position:fixed; top:52px; left:0; bottom:0; width:176px; background:var(--sidebar-bg);
    border-right:1px solid var(--border); z-index:200; display:flex; flex-direction:column;
    padding:6px 0; overflow-y:auto; }}
  .sidebar-section-label{{ font-size:8.5px; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
    color:var(--ink-soft); padding:10px 12px 2px; opacity:.6; }}
  .sidebar-btn{{ display:flex; align-items:center; gap:7px; width:100%; padding:6px 12px;
    border:none; background:transparent; cursor:pointer; text-align:left;
    font-family:inherit; color:var(--ink-soft); transition:all .15s;
    border-left:3px solid transparent; border-radius:0 4px 4px 0; margin-right:6px; }}
  .sidebar-btn:hover{{ background:rgba(30,43,58,.06); color:var(--ink); }}
  .sidebar-btn.active{{ color:var(--stamp); font-weight:600; background:rgba(156,43,34,.08); border-left-color:var(--stamp); }}
  .snav-icon{{ font-size:13px; flex-shrink:0; opacity:.8; }}
  .snav-label{{ font-size:11.5px; }}
  .sidebar-footer{{ margin-top:auto; padding:10px 12px; font-size:10px; color:var(--ink-soft);
    border-top:1px solid var(--border); line-height:1.8; opacity:.8; }}
  /* ── 主內容區 ── */
  .main-area{{ margin-left:176px; margin-top:52px; padding:20px 24px 80px; }}
  nav.tabs{{ display:none; }}
  .tabpanel{{ display:none; }}
  .tabpanel.active{{ display:block; }}
  h2{{ font-family:"Noto Serif TC",serif; font-size:16px; margin-bottom:4px; }}
  h3{{ font-family:"Noto Serif TC",serif; font-size:14px; font-weight:700; margin:20px 0 8px; }}
  .section-note{{ color:var(--ink-soft); font-size:12px; line-height:1.75; margin-bottom:14px; }}
  .search{{ width:100%; padding:8px 12px; border:1px solid var(--border); font-size:13px;
    background:var(--card); color:var(--ink); margin-bottom:10px;
    border-radius:var(--radius); transition:border-color .15s; outline:none; }}
  .search:focus{{ border-color:var(--stamp); }}
  .controls{{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }}
  .controls button{{ font-size:13px; padding:5px 12px; border:1px solid var(--border);
    background:transparent; cursor:pointer; color:var(--ink);
    border-radius:var(--radius); transition:all .15s; }}
  .controls button:hover:not(.active){{ border-color:var(--ink-soft); }}
  .controls button.active{{ border-color:var(--stamp); color:var(--stamp); background:rgba(156,43,34,.06); font-weight:600; }}
  #read-progress{{position:fixed;top:0;left:0;height:3px;width:0%;background:var(--stamp);z-index:400;transition:width .1s linear;pointer-events:none;}}
  .view-toggle{{display:flex;gap:4px;margin-bottom:12px;}}
  .view-toggle button{{padding:4px 12px;border:1px solid var(--border);background:transparent;cursor:pointer;font-size:12px;color:var(--ink-soft);border-radius:3px;}}
  .view-toggle button.active{{border-color:var(--stamp);color:var(--stamp);background:rgba(156,43,34,.06);font-weight:600;}}
  .filter-badge{{display:inline-block;background:var(--stamp);color:#fff;border-radius:9px;font-size:10px;padding:0 5px;margin-left:4px;min-width:16px;text-align:center;line-height:16px;vertical-align:middle;}}
  .skel-item{{background:var(--card);border:1px solid var(--border);border-left:3px solid rgba(168,132,46,.2);padding:14px 16px;margin-bottom:10px;border-radius:0 4px 4px 0;}}
  .skel-line{{height:13px;border-radius:4px;margin-bottom:8px;background:linear-gradient(90deg,var(--border) 25%,rgba(168,132,46,.2) 50%,var(--border) 75%);background-size:400% 100%;animation:skel-shine 1.4s ease-in-out infinite;}}
  .skel-line.w40{{width:40%;}}.skel-line.w70{{width:70%;}}
  @keyframes skel-shine{{0%{{background-position:100% 0}}100%{{background-position:-100% 0}}}}
  @keyframes item-in{{from{{opacity:0;transform:translateY(5px)}}to{{opacity:1;transform:none}}}}
  .swipe-save-hint{{position:absolute;left:0;top:0;bottom:0;width:64px;background:var(--green);color:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;pointer-events:none;transform:translateX(-64px);transition:transform .15s;}}
  .item.will-save .swipe-save-hint{{transform:translateX(0);}}
  .item{{ background:var(--card); border:1px solid var(--border); border-left:3px solid var(--brass);
    padding:11px 14px; margin-bottom:8px; position:relative; overflow:hidden;
    animation:item-in .22s ease; border-radius:0 4px 4px 0;
    box-shadow:var(--shadow); transition:box-shadow .15s; }}
  .item:hover{{ box-shadow:0 4px 12px rgba(30,43,58,.1); }}
  .item.read{{ opacity:.55; }}
  .item .row{{ display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; }}
  .item .date{{ font-size:11px; color:var(--ink-soft); white-space:nowrap; font-variant-numeric:tabular-nums; }}
  .item h3{{ margin:3px 0 5px; font-size:13.5px; font-family:"Noto Sans TC",sans-serif; font-weight:600; }}
  .item h3 a{{ color:var(--ink); text-decoration:none; }}
  .item h3 a:hover{{ color:var(--stamp); text-decoration:underline; }}
  .item .src{{ font-size:11px; color:var(--ink-soft); }}
  .item .actions{{ display:flex; gap:6px; margin-top:7px; }}
  .item .actions button{{ font-size:11px; padding:3px 8px; border:1px solid var(--border);
    background:transparent; cursor:pointer; border-radius:var(--radius); transition:all .15s; }}
  .item .actions button.on{{ border-color:var(--stamp); color:var(--stamp); }}
  .also{{ font-size:11px; color:var(--ink-soft); margin-top:4px; }}
  .empty-state{{ text-align:center; padding:56px 20px; color:var(--ink-soft); }}
  .empty-state .icon{{ font-size:36px; display:block; margin-bottom:14px; }}
  .empty-state p{{ font-size:13.5px; line-height:1.8; max-width:400px; margin:0 auto; }}
  #newsList.list-view .item{{display:flex;align-items:center;padding:9px 14px;gap:10px;}}
  #newsList.list-view .item .row{{flex-shrink:0;width:78px;flex-direction:column;gap:1px;flex-wrap:nowrap;}}
  #newsList.list-view .item .src{{display:none;}}
  #newsList.list-view .item .date{{font-size:11px;}}
  #newsList.list-view .item h3{{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:0;font-size:13px;min-width:0;}}
  #newsList.list-view .item .also{{display:none;}}
  #newsList.list-view .item .actions button[data-act="read"]{{display:none;}}
  #newsList.list-view .item .actions{{margin-top:0;}}
  .reg-toolbar{{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; align-items:center; }}
  .reg-toolbar input{{ flex:1; min-width:180px; padding:7px 10px; border:1px solid var(--border);
    font-size:13px; background:var(--card); color:var(--ink);
    border-radius:var(--radius); transition:border-color .15s; outline:none; }}
  .reg-toolbar input:focus{{ border-color:var(--stamp); }}
  .cat-filters{{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; }}
  .cat-btn{{ font-size:11px; padding:3px 11px; border:1px solid var(--border); background:var(--card);
    cursor:pointer; color:var(--ink-soft); border-radius:99px; white-space:nowrap; }}
  .cat-btn.active{{ background:var(--stamp); color:#fff; border-color:var(--stamp); font-weight:600; }}
  .reg-controls{{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; align-items:center; }}
  .reg-controls .label{{ font-size:12px; color:var(--ink-soft); margin-right:2px; }}
  .reg-controls button{{ font-size:12px; padding:3px 10px; border:1px solid var(--border);
    background:transparent; cursor:pointer; color:var(--ink);
    border-radius:var(--radius); transition:all .15s; }}
  .reg-controls button:hover:not(.active){{ border-color:var(--ink-soft); }}
  .reg-controls button.active{{ border-color:var(--stamp); color:var(--stamp); background:rgba(156,43,34,.06); font-weight:600; }}
  .reg-controls .sep{{ width:1px; height:20px; background:var(--border); margin:0 4px; align-self:center; }}
  .registry-meta{{ font-size:11.5px; color:var(--ink-soft); margin-bottom:8px; }}
  .table-scroll{{ overflow-x:auto; border:1px solid var(--border); margin-bottom:8px; border-radius:6px; box-shadow:var(--shadow); }}
  table.registry-table{{ width:100%; border-collapse:collapse; font-size:12.5px; min-width:560px; }}
  .registry-table th{{ text-align:left; font-weight:700; font-size:10px; color:var(--ink-soft);
    padding:8px 12px; background:var(--card); border-bottom:2px solid var(--border);
    white-space:nowrap; text-transform:uppercase; letter-spacing:.07em; }}
  .registry-table td{{ padding:8px 12px; border-bottom:1px solid var(--border); vertical-align:top; transition:background .12s; font-variant-numeric:tabular-nums; }}
  .registry-table td:first-child,.registry-table td:nth-child(2){{ vertical-align:middle; }}
  .registry-table tr:last-child td{{ border-bottom:none; }}
  .registry-table tr:nth-child(even) td{{ background:rgba(168,132,46,.025); }}
  .registry-table tr:hover td{{ background:rgba(168,132,46,.07); }}
  .rname{{ font-weight:500; }}
  .rdate{{ white-space:nowrap; font-size:13px; }}
  .rdate.unconfirmed{{ color:var(--ink-soft); font-style:italic; }}
  .tier-tag{{ display:inline-block; font-size:10.5px; padding:2px 7px; white-space:nowrap;
    border:1px solid currentColor; border-radius:3px; font-weight:600; }}
  .tier-tag.act{{ color:var(--stamp); }}
  .tier-tag.reg{{ color:var(--brass); }}
  .tier-tag.dir,.tier-tag.notice{{ color:var(--ink-soft); }}
  .cat-tag{{ display:inline-block; font-size:10.5px; padding:2px 8px; border:1px solid var(--border);
    color:var(--ink-soft); background:rgba(0,0,0,.03); border-radius:99px; }}
  .badge{{ display:inline-block; font-size:10px; padding:2px 6px; border-radius:99px; margin-left:5px;
    vertical-align:middle; white-space:nowrap; line-height:1.4; font-weight:600; }}
  .badge.recent{{ background:#fff3cd; color:var(--amber); border:1px solid #ffc107; }}
  .badge.new-law{{ background:#d1fae5; color:var(--green); border:1px solid #6ee7b7; }}
  .star-btn{{ background:none; border:none; cursor:pointer; font-size:15px; padding:0 3px;
    color:#ccc; line-height:1; vertical-align:middle; transition:color .15s, transform .12s; }}
  .star-btn:hover{{ transform:scale(1.2); }}
  .star-btn.on{{ color:#e6a817; }}
  .rname-btn{{ background:none; border:none; cursor:pointer; text-align:left; padding:0;
    font-size:inherit; font-family:inherit; color:var(--ink); font-weight:500;
    text-decoration:underline dotted; text-underline-offset:3px; transition:color .15s; }}
  .rname-btn:hover{{ color:var(--blue); text-decoration-style:solid; }}
  .src-link{{ font-size:12px; color:var(--ink-soft); text-decoration:none; }}
  .src-link:hover{{ color:var(--stamp); }}
  /* ── Side Drawer ── */
  #drawer-overlay{{ position:fixed; inset:0; background:rgba(0,0,0,.35); z-index:200;
    opacity:0; pointer-events:none; transition:opacity .25s; }}
  #drawer-overlay.open{{ opacity:1; pointer-events:auto; }}
  #law-drawer{{ position:fixed; top:0; right:0; width:min(480px,95vw); height:100vh;
    background:var(--card); z-index:201; box-shadow:-6px 0 32px rgba(0,0,0,.18);
    border-left:1px solid var(--border);
    transform:translateX(100%); transition:transform .28s cubic-bezier(.4,0,.2,1);
    display:flex; flex-direction:column; overflow:hidden; }}
  #law-drawer.open{{ transform:translateX(0); }}
  #drawer-header{{ padding:16px 18px 12px; border-bottom:1px solid var(--border);
    display:flex; align-items:flex-start; gap:10px; flex-shrink:0; }}
  #drawer-header h2{{ margin:0; font-size:15px; flex:1; line-height:1.5; }}
  #drawer-header h2 a{{ color:var(--ink); text-decoration:none; }}
  #drawer-header h2 a:hover{{ color:var(--blue); }}
  #drawer-close{{ background:none; border:none; cursor:pointer; font-size:20px;
    color:var(--ink-soft); padding:3px 5px; line-height:1; flex-shrink:0; margin-top:1px;
    border-radius:var(--radius); transition:color .15s, background .15s; }}
  #drawer-close:hover{{ color:var(--ink); background:var(--hover); }}
  #drawer-body{{ flex:1; overflow-y:auto; padding:16px 18px 24px; }}
  .drawer-meta{{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; font-size:12.5px; }}
  .drawer-meta span{{ background:var(--hover); border-radius:99px; padding:3px 9px;
    border:1px solid var(--border); }}
  .drawer-section{{ margin-bottom:20px; padding-bottom:20px; border-bottom:1px solid var(--border); }}
  .drawer-section:last-child{{ border-bottom:none; padding-bottom:0; }}
  .drawer-section h3{{ font-size:11px; font-weight:700; color:var(--ink-soft);
    text-transform:uppercase; letter-spacing:.08em; margin:0 0 10px;
    display:flex; align-items:center; gap:6px; }}
  .drawer-section h3::after{{ content:""; flex:1; height:1px; background:var(--border); }}
  .drawer-summary{{ font-size:13px; line-height:1.9; color:var(--ink);
    border-left:3px solid var(--amber); padding:8px 12px;
    background:rgba(168,132,46,.04); border-radius:0 4px 4px 0;
    white-space:pre-wrap; }}
  .art-chip{{ display:inline-block; padding:3px 9px; border-radius:4px;
    background:var(--hover); font-size:12px; color:var(--ink); text-decoration:none;
    border:1px solid var(--border); white-space:nowrap; transition:all .15s; }}
  a.art-chip:hover{{ background:var(--blue); color:#fff; border-color:var(--blue); transform:translateY(-1px); box-shadow:0 2px 6px rgba(37,99,235,.3); }}
  .art-chip-deleted{{ text-decoration:line-through; color:var(--ink-soft); opacity:.6; }}
  .deleted-arts-toggle{{ cursor:pointer; font-size:12px; color:var(--ink-soft); user-select:none; list-style:none; }}
  .deleted-arts-toggle::-webkit-details-marker{{ display:none; }}
  .deleted-arts-toggle::before{{ content:"▶ "; font-size:9px; }}
  details.deleted-arts-section[open] .deleted-arts-toggle::before{{ content:"▼ "; }}
  .art-chip-penalty{{ background:#fee2e2; color:#b91c1c; border-color:#fca5a5; font-weight:600; }}
  a.art-chip-penalty:hover{{ background:#b91c1c; color:#fff; border-color:#b91c1c; }}
  .art-chip-added{{ background:#dcfce7; color:#166534; border-color:#86efac; font-weight:600; }}
  a.art-chip-added:hover{{ background:#16a34a; color:#fff; border-color:#16a34a; }}
  .chg-summary{{ margin-top:5px; display:flex; flex-wrap:wrap; gap:4px; }}
  .chg-chip{{ display:inline-flex; align-items:center; font-size:10.5px; padding:1px 7px;
    border-radius:99px; font-weight:600; white-space:nowrap; border:1px solid currentColor; line-height:1.6; }}
  .chg-chip.chg-modified{{ background:rgba(180,83,9,.07); color:#b45309; }}
  .chg-chip.chg-added{{ background:rgba(22,101,52,.07); color:#15803d; }}
  .chg-chip.chg-deleted{{ background:rgba(185,28,28,.07); color:#b91c1c; }}
  [data-theme="dark"] .chg-chip.chg-modified{{ background:rgba(251,191,36,.08); color:#fbbf24; }}
  [data-theme="dark"] .chg-chip.chg-added{{ background:rgba(52,211,153,.08); color:#34d399; }}
  [data-theme="dark"] .chg-chip.chg-deleted{{ background:rgba(252,165,165,.08); color:#fca5a5; }}
  .chg-modified{{ color:#b45309; }} .chg-added{{ color:#166534; }} .chg-deleted{{ color:#b91c1c; }}
  .latest-rev-block{{ background:var(--hover); border-radius:6px; padding:12px 14px;
    margin-bottom:8px; font-size:12.5px; border:1px solid var(--border); }}
  .latest-rev-block h4{{ font-size:11px; font-weight:700; color:var(--ink-soft);
    text-transform:uppercase; letter-spacing:.06em; margin:0 0 8px; }}
  .latest-rev-row{{ display:flex; gap:8px; align-items:baseline; margin:3px 0; font-size:12px; }}
  .rev-label{{ min-width:3em; font-weight:600; }}
  .rev-arts{{ color:var(--ink-soft); word-break:break-all; }}
  .rev-chips{{ display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 8px; }}
  .cross-ref-list{{ display:flex; flex-direction:column; gap:6px; }}
  .cross-ref-row{{ display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; font-size:12.5px; }}
  .cross-ref-law{{ color:var(--blue); text-decoration:none; font-weight:600; white-space:nowrap; }}
  .cross-ref-law:hover{{ text-decoration:underline; }}
  .cross-ref-arts{{ color:var(--ink-soft); font-size:12px; }}
  .art-chip.art-selected{{ background:var(--blue); color:#fff; border-color:var(--blue); box-shadow:0 0 0 2px rgba(59,130,246,.3); }}
  .art-index-toolbar{{ display:flex; gap:6px; align-items:center; margin-bottom:8px; flex-wrap:wrap; }}
  .art-search{{ flex:1; min-width:80px; font-size:12px; padding:3px 8px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--ink); outline:none; }}
  .art-search:focus{{ border-color:var(--blue); }}
  .art-copy-btn{{ font-size:11px; padding:3px 10px; border:1px solid var(--border); border-radius:4px; background:var(--card); cursor:pointer; color:var(--ink-soft); white-space:nowrap; }}
  .art-copy-btn.active{{ background:var(--blue); color:#fff; border-color:var(--blue); }}
  .art-chapter-hd{{ font-size:10.5px; font-weight:700; color:var(--ink-soft); margin:8px 0 3px; letter-spacing:.04em; border-bottom:1px solid var(--border); padding-bottom:2px; }}
  .art-chapter-group:first-child .art-chapter-hd{{ margin-top:2px; }}
  .toast-clip{{ position:fixed; bottom:24px; right:24px; background:#1e293b; color:#fff; font-size:13px; padding:8px 18px; border-radius:6px; z-index:9999; opacity:0; transition:opacity .25s; pointer-events:none; max-width:80vw; word-break:break-all; }}
  .drawer-fulllink{{ display:inline-block; margin-top:10px; padding:7px 16px;
    background:var(--blue); color:#fff; border-radius:5px; text-decoration:none;
    font-size:13px; font-weight:600; }}
  .drawer-fulllink:hover{{ opacity:.88; }}
  .drawer-table-note{{ font-size:12.5px; color:var(--amber); background:#fffbeb;
    border:1px solid #fde68a; border-radius:4px; padding:7px 10px; }}
  .table-footnote{{ font-size:12.5px; color:var(--ink-soft); line-height:1.75; margin:4px 0 22px; }}
  .reg-subhead{{ font-family:"Noto Serif TC",serif; font-size:16px; font-weight:700; margin:26px 0 10px; }}
  .law-card{{ background:var(--card); border:1px solid var(--border); padding:18px; margin-bottom:8px; border-radius:6px; box-shadow:var(--shadow); }}
  .law-card dl{{ display:grid; grid-template-columns:110px 1fr; gap:8px 12px; margin:0; font-size:14px; }}
  .law-card dt{{ color:var(--ink-soft); font-size:13px; }}
  .law-card dd{{ margin:0; line-height:1.65; }}
  .stamp-badge{{ display:inline-block; margin-top:12px; font-size:12px; color:var(--stamp);
    border:1px solid var(--stamp); padding:3px 10px; border-radius:3px;
    transition:background .15s; }}
  .stamp-badge:hover{{ background:rgba(156,43,34,.08); }}
  .stamp-badge a{{ color:inherit; text-decoration:none; }}
  .source-grid{{ display:grid; gap:12px; }}
  .source-card{{ background:var(--card); border:1px solid var(--border); border-left:3px solid var(--stamp); padding:14px 16px; border-radius:0 6px 6px 0; box-shadow:var(--shadow); }}
  .source-card .org{{ font-weight:600; font-size:14px; }}
  .source-card .role{{ font-size:12.5px; color:var(--ink-soft); margin:2px 0; }}
  .source-card .link{{ font-size:12px; margin-top:4px; }}
  .source-card .link a{{ color:var(--stamp); word-break:break-all; }}
  .scenario-search{{ width:100%; padding:9px 12px; border:1px solid var(--border);
    border-radius:var(--radius); font-size:14px; background:var(--card); color:var(--ink);
    margin-bottom:16px; transition:border-color .15s; outline:none; }}
  .scenario-search:focus{{ border-color:var(--stamp); }}
  .scenario-intro{{ color:var(--ink-soft); font-size:13.5px; line-height:1.75; margin-bottom:14px; }}
  .scenario-grid{{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:14px; }}
  .scenario-card{{ background:var(--card); border:1px solid var(--border); border-top:3px solid var(--brass);
    padding:16px; border-radius:0 0 6px 6px; box-shadow:var(--shadow);
    cursor:pointer; transition:box-shadow .18s, transform .18s; }}
  .scenario-card:hover{{ transform:translateY(-2px); box-shadow:0 6px 18px rgba(30,43,58,.12); }}
  .scenario-card.open{{ border-top-color:var(--stamp); }}
  .sc-head{{ display:flex; align-items:flex-start; gap:10px; }}
  .sc-icon{{ font-size:22px; line-height:1.2; flex-shrink:0; padding-top:1px; }}
  .sc-body{{ flex:1; min-width:0; }}
  .sc-title-row{{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:5px; }}
  .sc-title{{ font-family:"Noto Serif TC",serif; font-size:15px; font-weight:700; }}
  .sc-law-count{{ font-size:10.5px; color:var(--ink-soft); background:var(--hover);
    border:1px solid var(--border); border-radius:99px; padding:1px 8px; white-space:nowrap; }}
  .sc-chevron{{ margin-left:auto; font-size:11px; color:var(--ink-soft);
    transition:transform .22s; flex-shrink:0; line-height:1.8; }}
  .scenario-card.open .sc-chevron{{ transform:rotate(180deg); }}
  .sc-desc{{ font-size:12.5px; color:var(--ink-soft); line-height:1.65; }}
  .sc-laws-panel{{ max-height:0; overflow:hidden; transition:max-height .32s ease; }}
  .scenario-card.open .sc-laws-panel{{ max-height:500px; }}
  .sc-laws-inner{{ margin-top:12px; padding-top:12px; border-top:1px solid var(--border); }}
  .scenario-laws{{ list-style:none; }}
  .scenario-laws li{{ margin-bottom:7px; }}
  .law-link{{ color:var(--stamp); text-decoration:none; font-size:13px;
    cursor:pointer; background:none; border:none; padding:0; text-align:left; font-family:inherit; }}
  .law-link:hover{{ text-decoration:underline; }}
  /* ── 底部導覽（手機） ── */
  .bottom-nav{{ display:none; position:fixed; bottom:0; left:0; right:0;
    background:var(--card); border-top:1px solid var(--border);
    box-shadow:0 -2px 12px rgba(30,43,58,.08); z-index:150;
    padding-bottom:env(safe-area-inset-bottom,0px); }}
  .bottom-nav button{{ flex:1; display:flex; flex-direction:column; align-items:center;
    gap:3px; padding:8px 4px 6px; border:none; background:transparent; cursor:pointer;
    font-size:10px; color:var(--ink-soft); transition:color .15s, background .15s; }}
  .bottom-nav button.active{{ color:var(--stamp); font-weight:700; }}
  .bottom-nav button:hover{{ background:var(--hover); }}
  .bn-icon{{ font-size:20px; line-height:1; }}
  footer{{ margin-top:34px; padding-top:16px; border-top:1px solid var(--border);
    font-size:12px; color:var(--ink-soft); line-height:1.8; }}
  @media(max-width:768px){{
    .sidebar{{ display:none; }}
    .main-area{{ margin-left:0; margin-top:52px; padding:20px 18px 80px; }}
    .bottom-nav{{ display:flex; }}
    .topbar-meta{{ display:none; }}
  }}
  @media(max-width:640px){{
    .main-area{{ padding:16px 14px 80px; }}
    .topbar-subtitle{{ display:none; }}
    .topbar-brand h1{{ font-size:16px; }}
    .scenario-grid{{ grid-template-columns:1fr; }}
    .law-card dl{{ grid-template-columns:1fr; }}
    .art-chip{{ padding:6px 12px; font-size:13px; }}
    .art-search{{ padding:7px 10px; font-size:13px; }}
    .art-copy-btn{{ padding:7px 14px; font-size:12px; }}
    .chg-chip{{ font-size:10px; padding:1px 5px; }}
    .latest-rev-block .art-chip{{ padding:5px 11px; font-size:12px; }}
    #law-drawer{{ width:100vw; }}
  }}
  /* ── AI 摘要顯示（卡片內） ── */
  .ai-summary{{ margin:6px 0 2px; font-size:12.5px; line-height:1.65; color:var(--ink-soft); }}
  .ai-tag{{ display:inline-block; font-size:10px; font-weight:600; letter-spacing:.04em;
    background:linear-gradient(135deg,#7c3aed,#2563eb); color:#fff;
    padding:1px 5px; border-radius:3px; margin-right:5px; vertical-align:middle; }}
  .ai-prebuilt{{ background:rgba(124,58,237,.06); border-left:3px solid #7c3aed;
    padding:10px 14px; border-radius:0 4px 4px 0; font-size:13px; line-height:1.7;
    color:var(--ink); margin-bottom:4px; }}
  /* ── 深色/淺色主題切換 ── */
  .theme-btn{{ background:none; border:1px solid var(--border); border-radius:3px; cursor:pointer; font-size:15px; padding:2px 7px; color:var(--ink-soft); margin-left:8px; vertical-align:middle; }}
  .theme-btn:hover{{ color:var(--ink); }}
  /* ── 僅標題視圖 ── */
  #newsList.title-view .item{{ padding:7px 14px; }}
  #newsList.title-view .item .row{{ display:none; }}
  #newsList.title-view .item h3{{ margin:0; font-size:13.5px; font-weight:500; display:flex; align-items:center; gap:8px; }}
  #newsList.title-view .item h3::before{{ content:attr(data-date); font-size:11px; color:var(--ink-soft); white-space:nowrap; flex-shrink:0; font-family:"Noto Sans TC",sans-serif; font-weight:400; }}
  #newsList.title-view .item .ai-summary{{ display:none; }}
  #newsList.title-view .item .also{{ display:none; }}
  #newsList.title-view .item .actions{{ display:none; }}
  #newsList.title-view .item:hover .actions{{ display:flex; margin-top:4px; }}
  /* ── 設定面板 ── */
  .settings-modal{{ position:fixed; inset:0; background:rgba(0,0,0,.45); z-index:500; display:none; align-items:center; justify-content:center; }}
  .settings-modal.open{{ display:flex; }}
  .settings-box{{ background:var(--card); border-radius:8px; padding:22px 26px; width:min(420px,92vw); max-height:90vh; overflow-y:auto; }}
  .settings-box h3{{ margin-bottom:16px; font-size:16px; }}
  .settings-row{{ margin-bottom:18px; }}
  .settings-row label{{ display:block; font-size:13px; font-weight:600; margin-bottom:6px; }}
  .settings-row .hint{{ font-size:11.5px; color:var(--ink-soft); margin-top:3px; }}
  input[type=range]{{ width:100%; accent-color:var(--stamp); }}
  .toggle-row{{ display:flex; align-items:center; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border); }}
  .toggle-row:last-child{{ border:none; }}
  .toggle-label{{ font-size:13px; }}
  .toggle-switch{{ position:relative; width:38px; height:22px; cursor:pointer; }}
  .toggle-switch input{{ opacity:0; width:0; height:0; }}
  .toggle-track{{ position:absolute; inset:0; background:var(--border); border-radius:11px; transition:.2s; }}
  .toggle-switch input:checked+.toggle-track{{ background:var(--stamp); }}
  .toggle-track::after{{ content:""; position:absolute; width:16px; height:16px; left:3px; top:3px; background:#fff; border-radius:50%; transition:.2s; }}
  .toggle-switch input:checked+.toggle-track::after{{ left:19px; }}
  .mute-list{{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; min-height:24px; }}
  .mute-chip{{ display:inline-flex; align-items:center; gap:4px; padding:2px 8px; background:rgba(156,43,34,.1); border:1px solid var(--stamp); border-radius:99px; font-size:12px; color:var(--stamp); }}
  .mute-chip button{{ background:none; border:none; cursor:pointer; color:var(--stamp); font-size:13px; padding:0; line-height:1; }}
  .mute-input-row{{ display:flex; gap:6px; margin-top:8px; }}
  .mute-input-row input{{ flex:1; padding:5px 8px; border:1px solid var(--border); background:var(--paper); color:var(--ink); font-size:13px; border-radius:3px; }}
  .mute-input-row button{{ padding:5px 12px; background:var(--stamp); color:#fff; border:none; cursor:pointer; font-size:12px; border-radius:3px; }}
  /* ── 右鍵/長按選單 ── */
  .ctx-menu{{ position:fixed; background:var(--card); border:1px solid var(--border); border-radius:6px; box-shadow:0 6px 20px rgba(0,0,0,.18); z-index:600; min-width:160px; padding:4px 0; }}
  .ctx-menu button{{ display:block; width:100%; text-align:left; padding:8px 16px; border:none; background:transparent; cursor:pointer; font-size:13px; color:var(--ink); white-space:nowrap; }}
  .ctx-menu button:hover{{ background:var(--hover); }}
  .ctx-menu .ctx-sep{{ height:1px; background:var(--border); margin:3px 0; }}
  /* ── 稍後閱讀 ── */
  .rl-badge{{ display:inline-block;background:#2563eb;color:#fff;border-radius:9px;font-size:10px;padding:0 5px;margin-left:4px;min-width:16px;text-align:center;line-height:16px;vertical-align:middle; }}
  /* ── 新聞來源分類過濾 ── */
  .src-filters{{ display:flex; gap:5px; flex-wrap:wrap; margin-bottom:10px; align-items:center; }}
  .src-filter-label{{ font-size:12px; color:var(--ink-soft); margin-right:2px; }}
  .src-filter-btn{{ font-size:12px; padding:3px 10px; border:1px solid var(--border); background:var(--card); cursor:pointer; color:var(--ink-soft); border-radius:99px; white-space:nowrap; }}
  .src-filter-btn.active{{ background:var(--stamp); color:#fff; border-color:var(--stamp); font-weight:600; }}
  .src-type-badge{{ display:inline-block; font-size:10px; padding:1px 5px; border-radius:2px; margin-left:4px; vertical-align:middle; }}
  .src-type-news{{ background:#dbeafe; color:#1e40af; }}
  .src-type-notice{{ background:#fef9c3; color:#713f12; }}
  .src-type-event{{ background:#f0fdf4; color:#166534; }}
  /* ── 搜尋高亮 ── */
  mark{{ background:rgba(168,132,46,.25); color:var(--ink); padding:0 1px; border-radius:2px; }}
  /* ── 設定與主題按鈕區 ── */
  .tool-btns{{ display:flex; gap:4px; margin-left:auto; align-items:center; }}
  .tool-btn{{ background:none; border:1px solid var(--border); border-radius:3px; cursor:pointer; font-size:13px; padding:3px 8px; color:var(--ink-soft); }}
  .tool-btn:hover{{ color:var(--ink); border-color:var(--ink-soft); }}
  /* ── 鍵盤快捷鍵 modal ── */
  .kbd-modal{{ position:fixed; inset:0; background:rgba(0,0,0,.45); z-index:500; display:none; align-items:center; justify-content:center; }}
  .kbd-modal.open{{ display:flex; }}
  .kbd-box{{ background:var(--card); border-radius:8px; padding:22px 28px; width:min(380px,90vw); }}
  .kbd-box h3{{ margin-bottom:14px; font-size:16px; }}
  .kbd-row{{ display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid var(--border); font-size:13px; }}
  .kbd-row:last-of-type{{ border:none; padding-bottom:0; }}
  kbd{{ background:var(--paper); border:1px solid var(--border); border-radius:3px; padding:1px 6px; font-size:11px; font-family:monospace; }}
  .kbd-hint-btn{{ font-size:11.5px; color:var(--ink-soft); cursor:pointer; border:1px solid var(--border); background:transparent; padding:3px 8px; border-radius:3px; margin-left:auto; }}
  .kbd-hint-btn:hover{{ color:var(--ink); }}
  .item.focused{{ box-shadow:0 0 0 2px var(--stamp); outline:none; }}
  /* ── Boards 版板 ── */
  .board-area{{ margin:-4px 0 10px; }}
  .board-pills{{ display:flex; gap:6px; flex-wrap:wrap; margin-top:5px; align-items:center; }}
  .board-pill{{ padding:3px 10px; border-radius:99px; border:1px solid var(--border); background:transparent; cursor:pointer; font-size:12px; color:var(--ink-soft); }}
  .board-pill.active{{ border-color:var(--stamp); color:var(--stamp); background:rgba(156,43,34,.06); font-weight:600; }}
  .board-pill-wrap{{ display:inline-flex; align-items:center; gap:1px; }}
  .board-del{{ background:none; border:none; cursor:pointer; color:var(--ink-soft); font-size:13px; padding:2px 3px; line-height:1; }}
  .board-del:hover{{ color:var(--stamp); }}
  .board-add-btn{{ padding:3px 10px; border-radius:99px; border:1px dashed var(--border); background:transparent; cursor:pointer; font-size:12px; color:var(--ink-soft); }}
  .board-dropdown{{ position:absolute; right:0; bottom:calc(100% + 4px); background:var(--card); border:1px solid var(--border); border-radius:6px; box-shadow:0 4px 16px rgba(0,0,0,.12); z-index:100; min-width:155px; padding:4px 0; }}
  .board-dropdown button{{ display:block; width:100%; text-align:left; padding:7px 14px; border:none; background:transparent; cursor:pointer; font-size:13px; color:var(--ink); white-space:nowrap; }}
  .board-dropdown button:hover{{ background:var(--hover); }}
  /* ── AI 摘要 modal ── */
  .ai-modal{{ position:fixed; inset:0; background:rgba(0,0,0,.45); z-index:500; display:none; align-items:center; justify-content:center; }}
  .ai-modal.open{{ display:flex; }}
  .ai-box{{ background:var(--card); border-radius:8px; padding:22px 26px; width:min(460px,92vw); }}
  .ai-box h3{{ font-size:15px; margin-bottom:6px; line-height:1.5; }}
  /* ── 通知 toast ── */
  .notif-toast{{ position:fixed; top:10px; right:14px; background:var(--stamp); color:#fff; padding:10px 16px; border-radius:6px; font-size:13px; z-index:600; display:none; cursor:pointer; box-shadow:0 4px 14px rgba(0,0,0,.22); }}
  .notif-toast.show{{ display:block; animation:fadeInDown .3s; }}
  @keyframes fadeInDown{{ from{{transform:translateY(-10px);opacity:0}} to{{transform:translateY(0);opacity:1}} }}
  /* ── 法規效力狀態 badge ── */
  .status-now{{ background:#d1fae5; color:#065f46; border:1px solid #6ee7b7; border-radius:3px; font-size:10.5px; padding:1px 5px; vertical-align:middle; margin-left:4px; }}
  .status-off{{ background:#fee2e2; color:#991b1b; border:1px solid #fca5a5; border-radius:3px; font-size:10.5px; padding:1px 5px; vertical-align:middle; margin-left:4px; }}
  .status-pending{{ background:#fef9c3; color:#713f12; border:1px solid #fde047; border-radius:3px; font-size:10.5px; padding:1px 5px; vertical-align:middle; margin-left:4px; }}
  /* ── 主管機關 ── */
  .authority-row{{ font-size:12px; color:var(--ink-soft); margin-bottom:10px; display:flex; align-items:center; gap:6px; }}
  .authority-row span{{ background:var(--hover); border-radius:3px; padding:2px 7px; color:var(--ink); }}
  /* ── 適用範圍 ── */
  .scope-section{{ margin-bottom:16px; }}
  .scope-section h3{{ font-size:13px; font-weight:600; margin-bottom:6px; color:var(--ink-soft); text-transform:uppercase; letter-spacing:.05em; }}
  .scope-text{{ font-size:12.5px; line-height:1.8; color:var(--ink); background:rgba(168,132,46,.06); border-left:3px solid var(--brass); padding:8px 12px; border-radius:0 4px 4px 0; white-space:pre-line; }}
  /* ── 罰則條文 ── */
  .penalty-section h3{{ font-size:13px; font-weight:600; margin-bottom:6px; color:#b91c1c; text-transform:uppercase; letter-spacing:.05em; }}
  .penalty-art{{ background:#fff5f5; border:1px solid #fca5a5; border-radius:4px; padding:8px 12px; margin-bottom:7px; font-size:12.5px; line-height:1.75; }}
  [data-theme="dark"] .penalty-art{{ background:rgba(185,28,28,.12); border-color:rgba(252,165,165,.25); }}
  .penalty-art .art-no{{ font-weight:700; color:#b91c1c; margin-right:6px; }}
  /* ── 罰則結構化 ── */
  .penalty-struct{{ display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 8px; align-items:center; }}
  .penalty-fine-label{{ font-size:11px; font-weight:700; color:#b91c1c; white-space:nowrap; }}
  .penalty-fine-bar{{ flex:1; min-width:80px; height:7px; background:var(--border); border-radius:4px; overflow:hidden; }}
  .penalty-fine-fill{{ height:100%; background:linear-gradient(90deg,#f97316,#b91c1c); border-radius:4px; transition:width .4s; }}
  .penalty-struct-tag{{ display:inline-flex; align-items:center; gap:3px; font-size:10.5px; padding:2px 7px; border-radius:99px; border:1px solid currentColor; font-weight:600; white-space:nowrap; }}
  .pst-criminal{{ color:#7c3aed; background:rgba(124,58,237,.07); }}
  .pst-repeat{{ color:#b45309; background:rgba(180,83,9,.07); }}
  .pst-liable{{ color:#0369a1; background:rgba(3,105,161,.07); }}
  /* ── 適用對象 ── */
  .applicability-block{{ display:flex; flex-wrap:wrap; gap:6px; margin:6px 0; }}
  .app-tag{{ display:inline-flex; align-items:center; font-size:11px; padding:2px 8px; border-radius:3px; font-weight:600; }}
  .app-industry{{ background:rgba(37,99,235,.08); color:#1d4ed8; border:1px solid rgba(37,99,235,.2); }}
  .app-size{{ background:rgba(5,150,105,.08); color:#065f46; border:1px solid rgba(5,150,105,.2); }}
  .app-risk{{ background:rgba(180,83,9,.08); color:#92400e; border:1px solid rgba(180,83,9,.2); }}
  .app-note{{ font-size:11.5px; color:var(--ink-soft); line-height:1.7; margin-top:4px; }}
  /* ── 稽查重點 ── */
  .inspect-rank{{ display:inline-flex; align-items:center; gap:4px; font-size:11.5px; font-weight:700; color:#b91c1c; margin-bottom:6px; }}
  .inspect-rank-dot{{ width:8px; height:8px; border-radius:50%; background:#b91c1c; }}
  .inspect-violations{{ margin:6px 0; list-style:none; padding:0; }}
  .inspect-violations li{{ font-size:11.5px; color:var(--ink); padding:3px 0 3px 14px; position:relative; border-bottom:1px solid var(--border); }}
  .inspect-violations li::before{{ content:"!"; position:absolute; left:0; color:#b91c1c; font-weight:700; font-size:10px; top:5px; }}
  /* ── 標準對應 ── */
  .std-chip{{ display:inline-flex; align-items:center; gap:4px; font-size:11px; padding:3px 9px; border-radius:3px; border:1px solid rgba(37,99,235,.25); background:rgba(37,99,235,.05); color:#1e40af; margin:2px; font-weight:600; }}
  .std-chip-title{{ font-weight:400; color:var(--ink-soft); }}
  /* ── 稽查重點badge（表格）── */
  .inspect-badge{{ display:inline-flex; align-items:center; gap:2px; font-size:9.5px; font-weight:700; color:#b91c1c; background:#fee2e2; border:1px solid #fca5a5; border-radius:3px; padding:1px 5px; white-space:nowrap; }}
  /* ── 訂閱法規按鈕 ── */
  .sub-btn{{ border:none; background:transparent; cursor:pointer; font-size:15px; padding:0 2px; opacity:.5; transition:opacity .2s; }}
  .sub-btn:hover{{ opacity:1; }}
  .sub-btn.on{{ opacity:1; color:#f59e0b; }}
  .sub-notice{{ background:#fffbeb; border:1px solid #fde68a; border-radius:4px; padding:7px 12px; font-size:12.5px; color:#78350f; margin-bottom:12px; }}
  [data-theme="dark"] .sub-notice{{ background:rgba(120,53,15,.2); color:#fcd34d; }}
  /* ── 並排對比 modal ── */
  .compare-modal{{ position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:600; display:none; align-items:flex-start; justify-content:center; padding:20px 10px; overflow-y:auto; }}
  .compare-modal.open{{ display:flex; }}
  .compare-box{{ background:var(--card); border-radius:8px; padding:22px 24px; width:min(960px,97vw); }}
  .compare-header{{ display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }}
  .compare-header h3{{ font-size:16px; }}
  .compare-header button{{ background:none; border:none; cursor:pointer; font-size:20px; color:var(--ink-soft); }}
  .compare-selects{{ display:flex; gap:10px; margin-bottom:16px; flex-wrap:wrap; }}
  .compare-selects select{{ flex:1; min-width:220px; padding:6px 8px; border:1px solid var(--border); background:var(--paper); color:var(--ink); border-radius:4px; font-size:13px; }}
  .compare-filter-wrap{{ flex:1; min-width:220px; display:flex; flex-direction:column; gap:4px; }}
  .compare-filter-input{{ padding:6px 8px; border:1px solid var(--border); background:var(--paper); color:var(--ink); border-radius:4px; font-size:13px; width:100%; }}
  .compare-filter-input::placeholder{{ color:var(--ink-soft); }}
  /* ── 分頁控制 ── */
  .reg-pager{{ display:flex; gap:4px; flex-wrap:wrap; align-items:center; margin:8px 0; font-size:13px; }}
  .reg-pager button{{ padding:4px 10px; border:1px solid var(--border); background:transparent; cursor:pointer; border-radius:3px; color:var(--ink); font-size:12px; }}
  .reg-pager button.active{{ border-color:var(--stamp); color:var(--stamp); background:rgba(156,43,34,.06); font-weight:600; }}
  .reg-pager button:disabled{{ opacity:.35; cursor:default; }}
  .reg-pager .pager-info{{ font-size:12px; color:var(--ink-soft); margin:0 4px; }}
  .compare-cols{{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media(max-width:640px){{ .compare-cols{{ grid-template-columns:1fr; }} }}
  .compare-col{{ border:1px solid var(--border); border-radius:6px; padding:14px; max-height:60vh; overflow-y:auto; }}
  .compare-col h4{{ font-size:13.5px; font-weight:700; margin-bottom:10px; padding-bottom:8px; border-bottom:1px solid var(--border); }}
  .compare-art{{ font-size:12.5px; line-height:1.75; padding:6px 0; border-bottom:1px solid var(--border); }}
  .compare-art:last-child{{ border:none; }}
  .compare-art .art-no{{ font-weight:700; color:var(--stamp); margin-right:6px; }}
  /* ── 適用性判斷 modal ── */
  .guide-modal{{ position:fixed; inset:0; background:rgba(0,0,0,.45); z-index:600; display:none; align-items:center; justify-content:center; }}
  .guide-modal.open{{ display:flex; }}
  .guide-box{{ background:var(--card); border-radius:8px; padding:24px 28px; width:min(500px,95vw); }}
  .guide-box h3{{ font-size:16px; margin-bottom:18px; }}
  .guide-q{{ margin-bottom:14px; }}
  .guide-q label{{ display:block; font-size:13px; font-weight:600; margin-bottom:5px; }}
  .guide-q select{{ width:100%; padding:7px 9px; border:1px solid var(--border); background:var(--paper); color:var(--ink); border-radius:4px; font-size:13px; }}
  .guide-checks{{ display:flex; flex-wrap:wrap; gap:8px; margin-top:4px; }}
  .guide-checks label{{ display:flex; align-items:center; gap:5px; font-size:13px; font-weight:400; cursor:pointer; }}
  .guide-result-item{{ display:flex; align-items:center; gap:7px; padding:5px 0; border-bottom:1px solid var(--border); font-size:13px; }}
  .guide-result-item:last-child{{ border:none; }}
  .guide-result-item button{{ background:none; border:none; cursor:pointer; color:var(--stamp); font-size:12px; text-decoration:underline; }}
  /* ── 合規稽核 Checklist ── */
  .checklist-section{{ margin-top:16px; }}
  .checklist-section h3{{ font-size:13px; font-weight:600; margin-bottom:8px; color:var(--ink-soft); text-transform:uppercase; letter-spacing:.05em; display:flex; align-items:center; gap:8px; }}
  .checklist-section h3 span{{ font-size:11px; font-weight:400; color:var(--ink-soft); }}
  .ck-item{{ display:flex; align-items:flex-start; gap:8px; padding:5px 0; border-bottom:1px solid var(--border); font-size:12.5px; cursor:pointer; }}
  .ck-item:last-child{{ border:none; }}
  .ck-item input{{ margin-top:2px; flex-shrink:0; accent-color:var(--green); cursor:pointer; }}
  .ck-item label{{ cursor:pointer; line-height:1.6; }}
  .ck-item.done label{{ text-decoration:line-through; color:var(--ink-soft); }}
  .ck-progress{{ height:4px; background:var(--border); border-radius:2px; margin:6px 0 10px; overflow:hidden; }}
  .ck-progress-bar{{ height:100%; background:var(--green); border-radius:2px; transition:width .3s; }}
  /* ── 法規抽屜工具列 ── */
  .drawer-toolbar{{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; }}
  .drawer-tool-btn{{ padding:4px 11px; border:1px solid var(--border); background:transparent; border-radius:4px; cursor:pointer; font-size:12.5px; color:var(--ink-soft); }}
  .drawer-tool-btn:hover{{ color:var(--ink); border-color:var(--ink-soft); }}
  .drawer-tool-btn.active{{ background:var(--stamp); color:#fff; border-color:var(--stamp); }}
  /* ① 法規關係圖譜 */
  #lawGraph {{ display:none; width:100%; height:580px; background:var(--card); border:1px solid var(--border); border-radius:6px; overflow:hidden; position:relative; margin-bottom:20px; }}
  #lawGraph.active {{ display:block; }}
  /* ② 修訂時間軸 */
  #lawTimeline {{ display:none; margin-bottom:20px; }}
  .tl-year-group {{ margin-bottom:20px; }}
  .tl-year-label {{ font-size:15px; font-weight:700; color:var(--stamp); padding:5px 10px 5px 12px; border-left:3px solid var(--stamp); margin-bottom:10px; display:block; }}
  .tl-items {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .tl-item {{ background:var(--card); border:1px solid var(--border); border-radius:5px; padding:8px 12px; cursor:pointer; transition:border-color .15s,box-shadow .15s; min-width:150px; max-width:210px; }}
  .tl-item:hover {{ border-color:var(--stamp); box-shadow:0 2px 8px rgba(30,43,58,.1); }}
  .tl-item-date {{ font-size:10px; color:var(--ink-soft); margin-bottom:3px; }}
  .tl-item-name {{ font-size:11.5px; font-weight:600; color:var(--ink); line-height:1.4; margin-bottom:4px; }}
  .tl-item-meta {{ display:flex; gap:4px; flex-wrap:wrap; align-items:center; }}
  /* ③ 罰則篩選 */
  .penalty-filter-btn {{ background:transparent; border:1px solid var(--border); border-radius:4px; padding:3px 9px; font-size:11.5px; cursor:pointer; color:var(--ink-soft); font-family:inherit; transition:all .15s; }}
  .penalty-filter-btn.active {{ background:#fee2e2; color:#b91c1c; border-color:#fca5a5; font-weight:600; }}
  [data-theme="dark"] .penalty-filter-btn.active {{ background:rgba(185,28,28,.25); color:#fca5a5; border-color:#b91c1c; }}
  /* 視圖切換 */
  .reg-view-toggle {{ display:flex; gap:6px; margin:10px 0 8px; flex-wrap:wrap; }}
  .reg-view-toggle button {{ background:transparent; border:1px solid var(--border); border-radius:4px; padding:4px 12px; font-size:11.5px; cursor:pointer; color:var(--ink-soft); font-family:inherit; transition:all .15s; }}
  .reg-view-toggle button.active {{ background:var(--stamp); color:#fff; border-color:var(--stamp); font-weight:600; }}
  @media(max-width:640px) {{
    .tl-item {{ min-width:130px; max-width:calc(50% - 4px); }}
    #lawGraph {{ height:420px; }}
    .reg-view-toggle button {{ padding:3px 9px; font-size:11px; }}
  }}
</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-brand">
    <h1>職安法規觀測站</h1>
    <div class="topbar-divider"></div>
    <div class="topbar-subtitle">職業安全衛生法規查詢</div>
  </div>
  <div class="topbar-right">
    <div class="topbar-meta">📅 {generated_at} ｜ {news_count} 則動態 ｜ {registry_count} 筆法規</div>
    <button class="theme-btn" id="theme-toggle" title="切換深色/淺色主題" onclick="toggleTheme()">🌙</button>
  </div>
</header>
<nav class="sidebar" id="sidebar">
  <div class="sidebar-section-label">資訊</div>
  <button class="sidebar-btn active" data-tab="news"><span class="snav-icon">📰</span><span class="snav-label">最新動態</span></button>
  <div class="sidebar-section-label">法規</div>
  <button class="sidebar-btn" data-tab="registry"><span class="snav-icon">📋</span><span class="snav-label">現行法規總覽</span></button>
  <button class="sidebar-btn" data-tab="lookup"><span class="snav-icon">🔍</span><span class="snav-label">快速情境查詢</span></button>
  <div class="sidebar-section-label">工具</div>
  <button class="sidebar-btn" data-tab="practitioner"><span class="snav-icon">👷</span><span class="snav-label">職安人員實用區</span></button>
  <div class="sidebar-footer">收藏與讀取狀態<br>僅存於本機瀏覽器</div>
</nav>
<main class="main-area">
<nav class="bottom-nav" id="bottomNav">
  <button data-tab="news" class="active"><span class="bn-icon">📰</span><span>動態</span></button>
  <button data-tab="registry"><span class="bn-icon">📋</span><span>法規</span></button>
  <button data-tab="lookup"><span class="bn-icon">🔍</span><span>情境</span></button>
  <button data-tab="practitioner"><span class="bn-icon">👷</span><span>實用</span></button>
</nav>

  <section class="tabpanel active" id="tab-news">
    <h2>最新動態</h2>
    <p class="section-note">從勞動部及職業安全衛生署官方新聞稿擷取職安相關公告，自動去重後依日期排序。</p>
    <input class="search" id="newsSearch" placeholder="搜尋標題關鍵字…">
    <div class="controls">
      <button data-filter="all" class="active">全部<span class="filter-badge" id="badge-all"></span></button>
      <button data-filter="unread">未讀<span class="filter-badge" id="badge-unread"></span></button>
      <button data-filter="starred">已收藏<span class="filter-badge" id="badge-starred"></span></button>
      <button data-filter="readlater">稍後閱讀<span class="rl-badge" id="badge-rl"></span></button>
    </div>
    <div class="src-filters">
      <span class="src-filter-label">來源類型：</span>
      <button class="src-filter-btn active" data-srctype="news">📰 新聞稿</button>
      <button class="src-filter-btn active" data-srctype="notice">📋 公告</button>
      <button class="src-filter-btn" data-srctype="event">📅 活動訊息</button>
    </div>
    <div class="board-area" id="board-area" style="display:none">
      <span style="font-size:11.5px;color:var(--ink-soft)">版板：</span>
      <div class="board-pills" id="board-pills"></div>
    </div>
    <div class="view-toggle">
      <button id="vbtn-magazine" class="active">☰ 摘要</button>
      <button id="vbtn-list">≡ 列表</button>
      <button id="vbtn-title">— 標題</button>
      <div class="tool-btns">
        <button class="tool-btn" onclick="document.getElementById('settings-modal').classList.add('open')" title="設定">⚙</button>
        <button class="kbd-hint-btn" onclick="document.getElementById('kbd-modal').classList.add('open')">? 快捷鍵</button>
      </div>
    </div>
    <div id="newsList"></div>
  </section>

  <section class="tabpanel" id="tab-registry">
    <h2>現行法規總覽</h2>
    <p class="section-note">職業安全衛生母法及附屬法規命令。<span style="color:var(--amber);font-weight:600">近期修正</span>標示為近12個月內有修正紀錄；<span style="color:var(--green);font-weight:600">新訂</span>為新制定法規；「待確認」表示尚未取得官方驗證日期。</p>
    <div class="reg-toolbar">
      <input id="lawSearch" placeholder="搜尋法規名稱或條文內容…" autocomplete="off">
    </div>
    <div class="cat-filters" id="catFilters"></div>
    <div class="reg-controls">
      <span class="label">篩選：</span>
      <button data-lawfilter="all" class="active">全部</button>
      <button data-lawfilter="starred">已收藏</button>
      <button data-lawfilter="subscribed">🔔 已訂閱</button>
      <button data-lawfilter="recent3m">近3月修正</button>
      <button data-lawfilter="recent">近1年修正</button>
      <div class="sep"></div>
      <span class="label">排序：</span>
      <button data-sort="cat" class="active">依分類</button>
      <button data-sort="date">依修正日期</button>
      <button data-sort="name">依名稱</button>
      <div class="sep"></div>
      <button onclick="openCompare()" style="font-size:12px">⚖ 並排對比</button>
      <button onclick="document.getElementById('guide-modal').classList.add('open')" style="font-size:12px">🎯 適用判斷</button>
      <div class="sep"></div>
      <button class="penalty-filter-btn" id="penaltyFilterBtn" onclick="togglePenaltyFilter()">⚠ 含罰則</button>
      <button class="penalty-filter-btn" id="inspectFocusBtn" onclick="toggleInspectFilter()">🔍 稽查重點</button>
    </div>
    <div class="reg-view-toggle">
      <button class="active" data-regview="table">📋 表格</button>
      <button data-regview="timeline">📅 時間軸</button>
      <button data-regview="graph">🕸 關係圖譜</button>
    </div>
    <div class="registry-meta" id="registryMeta"></div>
    <div id="lawTimeline"></div>
    <div id="lawGraph"></div>
    <div class="table-scroll" id="mainTableScroll">
      <table class="registry-table">
        <thead><tr><th></th><th></th><th>法規名稱</th><th>分類</th><th>位階</th><th>最新修正日期</th><th>資料來源</th></tr></thead>
        <tbody id="registryBody"></tbody>
      </table>
    </div>
    <div class="reg-pager" id="registryPager"></div>
    <h3>近期行政規則與公告</h3>
    <p class="section-note" style="margin-top:-6px">非法規命令，但對職安執行實務有重要影響。</p>
    <div class="table-scroll">
      <table class="registry-table">
        <thead><tr><th>名稱</th><th>位階</th><th>最新修正日期</th><th>資料來源</th></tr></thead>
        <tbody id="directiveBody"></tbody>
      </table>
    </div>
  </section>

  <section class="tabpanel" id="tab-lookup">
    <h2>快速情境查詢</h2>
    <p class="scenario-intro">依作業類型或管理情境快速定位相關法規。點擊情境卡片展開相關法規，再點選法規名稱即可在右側抽屜查看詳情。</p>
    <input type="text" id="scenarioSearch" class="scenario-search" placeholder="搜尋情境名稱、說明或法規…">
    <div class="scenario-grid" id="scenarioGrid"></div>
  </section>

  <section class="tabpanel" id="tab-practitioner">
    {practitioner_html}
  </section>

  <footer>
    新聞日期為官方發布日，不代表法規正式生效日，請點連結查閱官方原文核實。本頁不會自動更新，重新執行 osh_dashboard.py 可取得最新資料。
  </footer>
</main>
<div id="settings-modal" class="settings-modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="settings-box">
    <h3>⚙ 設定</h3>
    <div class="settings-row">
      <label>字體大小 <span id="font-size-label">14px</span></label>
      <input type="range" id="font-size-range" min="12" max="20" value="14" step="1">
      <div class="hint">調整全站文字大小</div>
    </div>
    <div class="settings-row">
      <div class="toggle-row">
        <span class="toggle-label">捲動自動標記已讀</span>
        <label class="toggle-switch">
          <input type="checkbox" id="auto-mark-toggle">
          <span class="toggle-track"></span>
        </label>
      </div>
    </div>
    <div class="settings-row">
      <label>靜音關鍵字</label>
      <div class="mute-list" id="mute-list"></div>
      <div class="mute-input-row">
        <input type="text" id="mute-input" placeholder="輸入要屏蔽的關鍵字…">
        <button onclick="addMuteKeyword()">新增</button>
      </div>
      <div class="hint">含有靜音關鍵字的新聞將不顯示</div>
    </div>
    <button onclick="document.getElementById('settings-modal').classList.remove('open')" style="padding:6px 16px;border:1px solid var(--border);background:transparent;cursor:pointer;border-radius:4px;font-size:13px">關閉</button>
  </div>
</div>
<div id="clip-toast" class="toast-clip"></div>
<div id="ctx-menu" class="ctx-menu" style="display:none"></div>

<div id="compare-modal" class="compare-modal" onclick="if(event.target===this)closeCompare()">
  <div class="compare-box">
    <div class="compare-header">
      <h3>⚖ 法規並排對比</h3>
      <button onclick="closeCompare()">✕</button>
    </div>
    <div class="compare-selects">
      <div class="compare-filter-wrap">
        <input class="compare-filter-input" id="compare-filter-a" placeholder="搜尋法規 A…" autocomplete="off">
        <select id="compare-sel-a" onchange="renderCompare()"><option value="">— 選擇法規 A —</option></select>
      </div>
      <div class="compare-filter-wrap">
        <input class="compare-filter-input" id="compare-filter-b" placeholder="搜尋法規 B…" autocomplete="off">
        <select id="compare-sel-b" onchange="renderCompare()"><option value="">— 選擇法規 B —</option></select>
      </div>
    </div>
    <div class="compare-cols" id="compare-cols"></div>
  </div>
</div>

<div id="guide-modal" class="guide-modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="guide-box">
    <h3>⚖ 法規適用性判斷</h3>
    <div class="guide-q">
      <label>事業類別</label>
      <select id="guide-industry">
        <option value="">— 請選擇 —</option>
        <option value="管理制度">管理制度（通用）</option>
        <option value="營造工程">營造業</option>
        <option value="作業環境">製造業</option>
        <option value="化學品安全">化學品相關</option>
        <option value="職業衛生">辦公室 / 服務業</option>
        <option value="機械設備">機械設備操作</option>
      </select>
    </div>
    <div class="guide-q">
      <label>主要危害類型（可複選）</label>
      <div class="guide-checks" id="guide-hazards">
        <label><input type="checkbox" value="高溫"> 高溫作業</label>
        <label><input type="checkbox" value="高架"> 高架/墜落</label>
        <label><input type="checkbox" value="局限空間"> 局限空間/缺氧</label>
        <label><input type="checkbox" value="化學品"> 危害性化學品</label>
        <label><input type="checkbox" value="機械"> 危險性機械</label>
        <label><input type="checkbox" value="環境監測"> 作業環境監測</label>
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button onclick="runGuide()" style="background:var(--stamp);color:#fff;border:none;padding:7px 20px;border-radius:4px;cursor:pointer;font-size:13px">判斷適用法規</button>
      <button onclick="document.getElementById('guide-modal').classList.remove('open')" style="border:1px solid var(--border);background:transparent;padding:7px 14px;border-radius:4px;cursor:pointer;font-size:13px">關閉</button>
    </div>
    <div id="guide-result" style="margin-top:16px"></div>
  </div>
</div>

<div id="kbd-modal" class="kbd-modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="kbd-box">
    <h3>鍵盤快捷鍵</h3>
    <div class="kbd-row"><span><kbd>j</kbd> / <kbd>↓</kbd></span><span>下一篇</span></div>
    <div class="kbd-row"><span><kbd>k</kbd> / <kbd>↑</kbd></span><span>上一篇</span></div>
    <div class="kbd-row"><span><kbd>o</kbd> / <kbd>Enter</kbd></span><span>開啟文章</span></div>
    <div class="kbd-row"><span><kbd>s</kbd></span><span>收藏 / 取消收藏</span></div>
    <div class="kbd-row"><span><kbd>r</kbd></span><span>加入 / 移出稍後閱讀</span></div>
    <div class="kbd-row"><span><kbd>S</kbd></span><span>分享文章</span></div>
    <div class="kbd-row"><span><kbd>m</kbd></span><span>標記已讀</span></div>
    <div class="kbd-row"><span><kbd>?</kbd></span><span>顯示此說明</span></div>
    <div class="kbd-row"><span><kbd>Esc</kbd></span><span>關閉</span></div>
    <button onclick="document.getElementById('kbd-modal').classList.remove('open')" style="margin-top:16px;padding:6px 16px;border:1px solid var(--border);background:transparent;cursor:pointer;border-radius:4px;">關閉</button>
  </div>
</div>
<div id="ai-modal" class="ai-modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="ai-box">
    <h3 id="ai-title"></h3>
    <p id="ai-source" style="font-size:12px;color:var(--ink-soft);margin-bottom:14px"></p>
    <div id="ai-content"></div>
    <div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap">
      <a id="ai-claude-link" href="#" target="_blank" rel="noopener" style="padding:7px 14px;background:var(--stamp);color:#fff;text-decoration:none;border-radius:4px;font-size:13px">以 Claude 摘要 ↗</a>
      <button id="ai-copy-btn" style="padding:7px 14px;border:1px solid var(--border);background:transparent;cursor:pointer;border-radius:4px;font-size:13px">複製連結</button>
      <button onclick="document.getElementById('ai-modal').classList.remove('open')" style="padding:7px 14px;border:1px solid var(--border);background:transparent;cursor:pointer;border-radius:4px;font-size:13px">關閉</button>
    </div>
  </div>
</div>
<div id="notif-toast" class="notif-toast"></div>
<div id="read-progress"></div>

<div id="drawer-overlay" onclick="closeDrawer()"></div>
<div id="law-drawer">
  <div id="drawer-header">
    <h2 id="drawer-title"></h2>
    <button id="drawer-close" onclick="closeDrawer()" title="關閉">✕</button>
  </div>
  <div id="drawer-body"></div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/fuse.js/7.0.0/fuse.min.js"></script>
<script>
  window.onerror = function(msg, src, line, col, err) {{
    var d = document.createElement("div");
    d.style.cssText = "background:#c00;color:#fff;padding:12px 16px;position:fixed;top:0;left:0;right:0;z-index:9999;font-size:14px";
    d.textContent = "JS 錯誤（第 " + line + " 行）：" + msg;
    document.body && document.body.prepend(d);
    return false;
  }};
  const NEWS = {news_json};
  const REGISTRY = {registry_json};
  const DIRECTIVES = {directives_json};
  const SCENARIOS = {scenarios_json};
  const CATEGORIES = {categories_json};
  const CHECKLISTS = {checklists_json};
  const APPLICABILITY = {applicability_json};
  const INSPECTION_FOCUS = {inspection_focus_json};
  const CNS_ISO = {cns_iso_json};
  const TIER_LABEL = {{act:"法律",reg:"法規命令",dir:"行政規則",notice:"公告"}};
  const STORE_KEY = "osh_state_v2";
  let state;
  try {{
    state = JSON.parse(localStorage.getItem(STORE_KEY) || '{{"read":{{}},"starred":{{}},"starredLaws":{{}}}}');
  }} catch(e) {{
    state = {{"read":{{}},"starred":{{}},"starredLaws":{{}}}};
  }}
  if (!state || typeof state !== "object") state = {{"read":{{}},"starred":{{}},"starredLaws":{{}}}};
  if (!state.read || typeof state.read !== "object") state.read = {{}};
  if (!state.starred || typeof state.starred !== "object") state.starred = {{}};
  if (!state.starredLaws || typeof state.starredLaws !== "object") state.starredLaws = {{}};
  if (!state.boards || !Array.isArray(state.boards)) state.boards = [];
  if (!state.readLater || typeof state.readLater !== "object") state.readLater = {{}};
  if (!Array.isArray(state.mutedKeywords)) state.mutedKeywords = [];
  if (typeof state.autoMarkRead === "undefined") state.autoMarkRead = false;
  if (!state.subscribedLaws || typeof state.subscribedLaws !== "object") state.subscribedLaws = {{}};
  if (!state.lawLastSeen || typeof state.lawLastSeen !== "object") state.lawLastSeen = {{}};
  if (!state.checklists || typeof state.checklists !== "object") state.checklists = {{}};

  let newsFilter = "all", lawFilter = "all", catFilter = "all", sortMode = "cat";
  let regViewMode = "table", penaltyFilter = false, inspectFilter = false;
  let _fuseIndex = null, _graphSim = null;
  var currentBoard = null;
  var srcTypeFilter = new Set(["news", "notice"]); // 預設隱藏活動訊息
  var registryPage = 1;
  var REGISTRY_PAGE_SIZE = 30;
  var _regRows = [];

  function save() {{ localStorage.setItem(STORE_KEY, JSON.stringify(state)); updateBadges(); }}
  function idOf(item) {{ return item.date + "|" + item.title; }}

  // ① 閱讀進度條
  window.addEventListener("scroll", function() {{
    const el = document.getElementById("read-progress");
    const max = document.documentElement.scrollHeight - window.innerHeight;
    el.style.width = (max > 0 ? Math.round(window.scrollY / max * 100) : 0) + "%";
  }}, {{passive: true}});

  // ② 視圖切換（摘要 / 列表 / 僅標題）
  let viewMode = localStorage.getItem("osh_view") || "magazine";
  function applyView() {{
    const list = document.getElementById("newsList");
    list.classList.toggle("list-view", viewMode === "list");
    list.classList.toggle("title-view", viewMode === "title");
    document.getElementById("vbtn-magazine").classList.toggle("active", viewMode === "magazine");
    document.getElementById("vbtn-list").classList.toggle("active", viewMode === "list");
    var vbtnTitle = document.getElementById("vbtn-title");
    if (vbtnTitle) vbtnTitle.classList.toggle("active", viewMode === "title");
    localStorage.setItem("osh_view", viewMode);
  }}
  document.getElementById("vbtn-magazine").onclick = function() {{ viewMode = "magazine"; applyView(); }};
  document.getElementById("vbtn-list").onclick = function() {{ viewMode = "list"; applyView(); }};
  document.getElementById("vbtn-title").onclick = function() {{ viewMode = "title"; applyView(); }};

  // ③ Badge 計數（未讀 / 已收藏 / 稍後閱讀 / 全部）
  function updateBadges() {{
    var unread = NEWS.filter(function(n) {{ return !state.read[idOf(n)]; }}).length;
    var starred = NEWS.filter(function(n) {{ return !!state.starred[idOf(n)]; }}).length;
    var rl = Object.keys(state.readLater).length;
    document.getElementById("badge-all").textContent = NEWS.length || "";
    document.getElementById("badge-unread").textContent = unread || "";
    document.getElementById("badge-starred").textContent = starred || "";
    var rlBadge = document.getElementById("badge-rl");
    if (rlBadge) rlBadge.textContent = rl || "";
  }}

  // ④ Skeleton 骨架屏
  function showSkeleton() {{
    document.getElementById("newsList").innerHTML =
      '<div class="skel-item"><div class="skel-line w40"></div><div class="skel-line w70"></div><div class="skel-line"></div></div>'.repeat(3);
  }}

  // ⑤ 滑動收藏（mobile touch swipe-right）
  function addSwipe(el, item) {{
    var startX = 0, dx = 0;
    el.addEventListener("touchstart", function(e) {{
      startX = e.touches[0].clientX; dx = 0;
    }}, {{passive: true}});
    el.addEventListener("touchmove", function(e) {{
      dx = e.touches[0].clientX - startX;
      if (dx > 0) el.style.transform = "translateX(" + Math.min(dx * 0.6, 64) + "px)";
      if (dx > 72) el.classList.add("will-save"); else el.classList.remove("will-save");
    }}, {{passive: true}});
    el.addEventListener("touchend", function() {{
      el.style.transform = "";
      el.classList.remove("will-save");
      if (dx > 72) {{
        state.starred[idOf(item)] = true;
        save();
        renderNews();
      }}
    }}, {{passive: true}});
  }}

  const GENERATED = new Date("{generated_date}");
  const cutoff12m = new Date(GENERATED);
  cutoff12m.setMonth(cutoff12m.getMonth() - 12);
  const cutoff3m = new Date(GENERATED);
  cutoff3m.setMonth(cutoff3m.getMonth() - 3);
  function isRecent(d) {{ return !!d && new Date(d) >= cutoff12m; }}
  function isRecent3m(d) {{ return !!d && new Date(d) >= cutoff3m; }}
  function isNewLaw(r) {{ return !!(r.note && r.note.includes("新訂定")); }}
  function _chgSummary(r) {{
    var lr = r.latest_revision;
    if (!lr) return '';
    if (lr.is_full) return '<span class="chg-chip chg-modified">全文修正</span>';
    var chips = [];
    if (lr.modified && lr.modified.length)
      chips.push('<span class="chg-chip chg-modified">✏ 修正 ' + lr.modified.length + ' 條</span>');
    if (lr.added && lr.added.length)
      chips.push('<span class="chg-chip chg-added">＋增 ' + lr.added.length + ' 條</span>');
    if (lr.deleted && lr.deleted.length)
      chips.push('<span class="chg-chip chg-deleted">✕刪 ' + lr.deleted.length + ' 條</span>');
    return chips.join('');
  }}

  function renderNews() {{
    try {{ _renderNews(); }} catch(e) {{
      const list = document.getElementById("newsList");
      if (list) list.innerHTML = '<div class="empty-state"><p style="color:red">JS錯誤：' + e.message + '</p></div>';
    }}
  }}
  function _renderNews() {{
    const q = document.getElementById("newsSearch").value.trim();
    const qRe = q ? new RegExp(q.replace(/[.*+?^${{}}()|[\\]\\\\]/g,'\\\\$&'),'g') : null;
    function hl(text) {{
      return qRe ? text.replace(qRe,'<mark>$&</mark>') : text;
    }}
    const filtered = NEWS.filter(item => {{
      const id = idOf(item);
      if (newsFilter === "unread" && state.read[id]) return false;
      if (newsFilter === "readlater" && !state.readLater[id]) return false;
      if (newsFilter === "starred") {{
        if (currentBoard !== null) {{
          var board = state.boards.find(function(b) {{ return b.id === currentBoard; }});
          if (!board || board.items.indexOf(id) < 0) return false;
        }} else {{
          if (!state.starred[id]) return false;
        }}
      }}
      if (state.mutedKeywords && state.mutedKeywords.length) {{
        if (state.mutedKeywords.some(function(kw) {{ return item.title.includes(kw); }})) return false;
      }}
      if (!srcTypeFilter.has(item.source_type || "news")) return false;
      if (q && !item.title.includes(q) && !(item.summary && item.summary.includes(q))) return false;
      return true;
    }});
    const list = document.getElementById("newsList");
    if (!filtered.length) {{
      const msg = NEWS.length
        ? "沒有符合篩選條件的新聞。"
        : "目前尚無職安相關新聞資料。<br>可能是來源回應異常或 robots.txt 限制，請重新執行 osh_dashboard.py。";
      list.innerHTML = '<div class="empty-state"><span class="icon">' + (NEWS.length ? "🔍" : "📭") + '</span><p>' + msg + '</p></div>';
      return;
    }}
    list.innerHTML = "";
    filtered.forEach(item => {{
      const id = idOf(item);
      const el = document.createElement("div");
      el.className = "item" + (state.read[id] ? " read" : "");
      el.dataset.id = id;
      const also = (item.also_in && item.also_in.length)
        ? '<div class="also">同時見於：' + item.also_in.join("、") + '</div>' : "";
      const aiSummary = item.summary
        ? '<div class="ai-summary"><span class="ai-tag">✦ AI</span>' + item.summary + '</div>'
        : '';
      const rlLabel = state.readLater[id] ? "📌 已加入" : "📌 稍後閱讀";
      const rlCls = state.readLater[id] ? " on" : "";
      const srcTypeLabelMap = {{news:"新聞稿", notice:"公告", event:"活動"}};
      const srcTypeCls = "src-type-" + (item.source_type || "news");
      const srcTypeBadge = '<span class="src-type-badge ' + srcTypeCls + '">' + (srcTypeLabelMap[item.source_type] || "新聞稿") + '</span>';
      el.innerHTML =
        '<div class="swipe-save-hint">★</div>' +
        '<div class="row"><span class="src">' + item.org + ' · ' + item.source + srcTypeBadge + '</span><span class="date">' + item.date + '</span></div>' +
        '<h3 data-date="' + item.date + '"><a href="' + item.link + '" target="_blank" rel="noopener">' + hl(item.title) + '</a></h3>' +
        aiSummary +
        also +
        '<div class="actions" style="position:relative">' +
          '<button data-act="read">' + (state.read[id] ? "已讀" : "標為已讀") + '</button>' +
          '<button data-act="star" class="' + (state.starred[id] ? "on" : "") + '">' + (state.starred[id] ? "★ 已收藏" : "☆ 收藏") + '</button>' +
          '<button data-act="rl" class="' + rlCls + '">' + rlLabel + '</button>' +
          '<button data-act="board">版板 ▾</button>' +
          '<button data-act="ai">✦ AI摘要</button>' +
          '<button data-act="share">分享</button>' +
        '</div>';
      el.querySelector('[data-act="read"]').onclick = () => {{ state.read[id] = !state.read[id]; save(); renderNews(); }};
      el.querySelector('[data-act="star"]').onclick = () => {{ state.starred[id] = !state.starred[id]; save(); renderNews(); }};
      el.querySelector('[data-act="rl"]').onclick = () => {{
        if (state.readLater[id]) delete state.readLater[id]; else state.readLater[id] = true;
        save(); renderNews();
      }};
      el.querySelector('[data-act="board"]').onclick = function(e) {{ showBoardDropdown(e.currentTarget, item); }};
      el.querySelector('[data-act="ai"]').onclick = function() {{ openAISummary(item); }};
      el.querySelector('[data-act="share"]').onclick = function() {{ shareItem(item); }};
      setupLongPress(el, item);
      addSwipe(el, item);
      list.appendChild(el);
    }});
    if (state.autoMarkRead) setupAutoMark();
  }}

  // ⑦ Boards 版板管理
  function renderBoardArea() {{
    var area = document.getElementById('board-area');
    var pills = document.getElementById('board-pills');
    area.style.display = (newsFilter === 'starred') ? '' : 'none';
    pills.innerHTML = '';
    var allPill = document.createElement('button');
    allPill.className = 'board-pill' + (currentBoard === null ? ' active' : '');
    allPill.textContent = '全部收藏';
    allPill.onclick = function() {{ currentBoard = null; renderBoardArea(); renderNews(); }};
    pills.appendChild(allPill);
    state.boards.forEach(function(board) {{
      var wrap = document.createElement('span');
      wrap.className = 'board-pill-wrap';
      var pill = document.createElement('button');
      pill.className = 'board-pill' + (currentBoard === board.id ? ' active' : '');
      pill.textContent = board.name + ' (' + board.items.length + ')';
      pill.onclick = function() {{ currentBoard = board.id; renderBoardArea(); renderNews(); }};
      var del = document.createElement('button');
      del.className = 'board-del';
      del.textContent = '×';
      del.title = '刪除版板';
      del.onclick = function(e) {{
        e.stopPropagation();
        if (!confirm('刪除版板「' + board.name + '」？')) return;
        state.boards = state.boards.filter(function(b) {{ return b.id !== board.id; }});
        if (currentBoard === board.id) currentBoard = null;
        save(); renderBoardArea(); renderNews();
      }};
      wrap.appendChild(pill); wrap.appendChild(del);
      pills.appendChild(wrap);
    }});
    var addBtn = document.createElement('button');
    addBtn.className = 'board-add-btn';
    addBtn.textContent = '＋ 新增版板';
    addBtn.onclick = function() {{
      var name = prompt('版板名稱：');
      if (!name || !name.trim()) return;
      state.boards.push({{id:'b_'+Date.now(), name:name.trim(), items:[]}});
      save(); renderBoardArea();
    }};
    pills.appendChild(addBtn);
  }}

  function showBoardDropdown(btn, item) {{
    document.querySelectorAll('.board-dropdown').forEach(function(d) {{ d.remove(); }});
    var dd = document.createElement('div');
    dd.className = 'board-dropdown';
    var id = idOf(item);
    if (state.boards.length === 0) {{
      var hint = document.createElement('div');
      hint.style.cssText = 'padding:8px 14px;font-size:12px;color:var(--ink-soft)';
      hint.textContent = '尚無版板';
      dd.appendChild(hint);
    }}
    state.boards.forEach(function(board) {{
      var inBoard = board.items.indexOf(id) >= 0;
      var bBtn = document.createElement('button');
      bBtn.textContent = (inBoard ? '✓ ' : '') + board.name;
      bBtn.onclick = function() {{
        var idx = board.items.indexOf(id);
        if (idx >= 0) board.items.splice(idx, 1); else board.items.push(id);
        dd.remove(); save(); renderBoardArea(); renderNews();
      }};
      dd.appendChild(bBtn);
    }});
    var newBtn = document.createElement('button');
    newBtn.style.borderTop = '1px solid var(--border)';
    newBtn.textContent = '＋ 新增版板…';
    newBtn.onclick = function() {{
      dd.remove();
      var name = prompt('版板名稱：');
      if (!name || !name.trim()) return;
      var newId = 'b_' + Date.now();
      state.boards.push({{id:newId, name:name.trim(), items:[id]}});
      save(); renderBoardArea(); renderNews();
    }};
    dd.appendChild(newBtn);
    btn.parentElement.appendChild(dd);
    setTimeout(function() {{
      document.addEventListener('click', function close(e) {{
        if (!dd.contains(e.target) && e.target !== btn) {{ dd.remove(); document.removeEventListener('click', close); }}
      }});
    }}, 0);
  }}

  // ⑧ AI 摘要
  function openAISummary(item) {{
    document.getElementById('ai-title').textContent = item.title;
    document.getElementById('ai-source').textContent = item.org + ' · ' + item.date;
    var claudeUrl = 'https://claude.ai/new?q=' + encodeURIComponent('請摘要以下文章的重點（繁體中文）：' + item.link);
    document.getElementById('ai-claude-link').href = claudeUrl;
    document.getElementById('ai-copy-btn').onclick = function() {{
      navigator.clipboard.writeText(item.link).then(function() {{
        document.getElementById('ai-copy-btn').textContent = '已複製 ✓';
        setTimeout(function() {{ document.getElementById('ai-copy-btn').textContent = '複製連結'; }}, 2000);
      }}).catch(function() {{
        document.getElementById('ai-copy-btn').textContent = item.link;
      }});
    }};
    var contentHtml = '';
    if (item.summary) {{
      contentHtml =
        '<div class="ai-prebuilt">' + item.summary + '</div>' +
        '<p style="font-size:11.5px;color:var(--ink-soft);margin-top:4px">✦ 由 Gemini AI 預先產生</p>';
    }} else {{
      contentHtml =
        '<p style="font-size:13px;color:var(--ink-soft);line-height:1.75">點擊「以 Claude 摘要」可將文章連結傳給 Claude AI，自動取得繁體中文重點摘要。</p>';
    }}
    contentHtml +=
      '<p style="margin-top:10px;font-size:12px;color:var(--ink-soft)">文章連結：' +
      '<a href="' + item.link + '" target="_blank" rel="noopener" style="color:var(--stamp);word-break:break-all">' + item.link + '</a></p>';
    document.getElementById('ai-content').innerHTML = contentHtml;
    document.getElementById('ai-modal').classList.add('open');
  }}

  // ⑨ 通知推送（頁面載入時檢查新文章）
  function checkNewArticles() {{
    if (!NEWS.length) return;
    var lastSeen = localStorage.getItem('osh_last_seen');
    var newest = NEWS[0].date;
    if (lastSeen && newest > lastSeen) {{
      var count = NEWS.filter(function(n) {{ return n.date > lastSeen; }}).length;
      showNotifToast(count);
    }}
    localStorage.setItem('osh_last_seen', newest);
  }}

  function showNotifToast(count) {{
    var toast = document.getElementById('notif-toast');
    toast.textContent = '🔔 有 ' + count + ' 則新動態，點此查看未讀';
    toast.classList.add('show');
    toast.onclick = function() {{
      toast.classList.remove('show');
      document.querySelectorAll('[data-filter]').forEach(function(b) {{
        b.classList.toggle('active', b.dataset.filter === 'unread');
      }});
      newsFilter = 'unread';
      currentBoard = null;
      renderBoardArea(); renderNews(); updateBadges();
      if ('Notification' in window && Notification.permission === 'default') {{
        Notification.requestPermission().then(function(perm) {{
          if (perm === 'granted') {{
            new Notification('職安法規觀測站', {{body: '有 ' + count + ' 則新的職安動態'}});
          }}
        }});
      }}
    }};
    setTimeout(function() {{ toast.classList.remove('show'); }}, 10000);
  }}

  // ─── 深色 / 淺色主題 ───────────────────────────────────────────
  function initTheme() {{
    var saved = localStorage.getItem("osh_theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = (document.documentElement.getAttribute("data-theme") === "dark") ? "☀️" : "🌙";
  }}
  function toggleTheme() {{
    var cur = document.documentElement.getAttribute("data-theme");
    var next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("osh_theme", next);
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = next === "dark" ? "☀️" : "🌙";
  }}

  // ─── 字體大小 ─────────────────────────────────────────────────
  function initFontSize() {{
    var saved = localStorage.getItem("osh_font_sz") || "14";
    function applySize(sz) {{
      document.documentElement.style.setProperty("--font-sz", sz + "px");
      document.body.style.zoom = (parseFloat(sz) / 14).toFixed(3);
    }}
    applySize(saved);
    var range = document.getElementById("font-size-range");
    var label = document.getElementById("font-size-label");
    if (range) {{
      range.value = saved;
      if (label) label.textContent = saved + "px";
      range.addEventListener("input", function() {{
        var sz = range.value;
        applySize(sz);
        if (label) label.textContent = sz + "px";
        localStorage.setItem("osh_font_sz", sz);
      }});
    }}
  }}

  // ─── 靜音關鍵字 ───────────────────────────────────────────────
  function renderMuteList() {{
    var list = document.getElementById("mute-list");
    if (!list) return;
    list.innerHTML = "";
    state.mutedKeywords.forEach(function(kw, idx) {{
      var chip = document.createElement("span");
      chip.className = "mute-chip";
      chip.innerHTML = kw + '<button title="移除" onclick="removeMuteKeyword(' + idx + ')">×</button>';
      list.appendChild(chip);
    }});
    var autoToggle = document.getElementById("auto-mark-toggle");
    if (autoToggle) autoToggle.checked = !!state.autoMarkRead;
  }}
  function addMuteKeyword() {{
    var input = document.getElementById("mute-input");
    if (!input) return;
    var kw = input.value.trim();
    if (!kw || state.mutedKeywords.includes(kw)) {{ input.value = ""; return; }}
    state.mutedKeywords.push(kw);
    input.value = "";
    save(); renderMuteList(); renderNews();
  }}
  function removeMuteKeyword(idx) {{
    state.mutedKeywords.splice(idx, 1);
    save(); renderMuteList(); renderNews();
  }}
  // Enter 鍵新增靜音關鍵字
  var muteInput = document.getElementById("mute-input");
  if (muteInput) muteInput.addEventListener("keydown", function(e) {{ if (e.key === "Enter") addMuteKeyword(); }});

  // ─── 自動標已讀（IntersectionObserver）────────────────────────
  var _autoMarkObserver = null;
  function setupAutoMark() {{
    if (_autoMarkObserver) _autoMarkObserver.disconnect();
    if (!state.autoMarkRead) return;
    _autoMarkObserver = new IntersectionObserver(function(entries) {{
      entries.forEach(function(entry) {{
        if (!entry.isIntersecting) {{
          var id = entry.target.dataset.id;
          if (id && !state.read[id]) {{
            state.read[id] = true;
            save();
          }}
        }}
      }});
    }}, {{threshold: 0.1}});
    document.querySelectorAll('#newsList .item[data-id]').forEach(function(el) {{
      _autoMarkObserver.observe(el);
    }});
  }}
  function toggleAutoMark() {{
    var cb = document.getElementById("auto-mark-toggle");
    state.autoMarkRead = cb ? cb.checked : !state.autoMarkRead;
    save();
    if (state.autoMarkRead) setupAutoMark(); else if (_autoMarkObserver) _autoMarkObserver.disconnect();
  }}
  // 設定面板 toggle 事件
  var amToggle = document.getElementById("auto-mark-toggle");
  if (amToggle) amToggle.addEventListener("change", toggleAutoMark);

  // ─── Web Share API ────────────────────────────────────────────
  function shareItem(item) {{
    if (navigator.share) {{
      navigator.share({{title: item.title, url: item.link}}).catch(function() {{}});
    }} else {{
      navigator.clipboard.writeText(item.link).then(function() {{
        var t = document.createElement("div");
        t.textContent = "連結已複製 ✓";
        t.style.cssText = "position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#333;color:#fff;padding:6px 14px;border-radius:4px;font-size:13px;z-index:9999";
        document.body.appendChild(t);
        setTimeout(function() {{ t.remove(); }}, 2000);
      }}).catch(function() {{ prompt("複製連結：", item.link); }});
    }}
  }}

  // ─── 長按右鍵選單 ─────────────────────────────────────────────
  var _ctxTimer = null;
  function showCtxMenu(x, y, item) {{
    var menu = document.getElementById("ctx-menu");
    if (!menu) return;
    var id = idOf(item);
    menu.innerHTML = "";
    function addBtn(label, fn) {{
      var btn = document.createElement("button");
      btn.textContent = label;
      btn.onclick = function() {{ menu.style.display = "none"; fn(); }};
      menu.appendChild(btn);
    }}
    addBtn("分享", function() {{ shareItem(item); }});
    addBtn(state.readLater[id] ? "📌 移除稍後閱讀" : "📌 加入稍後閱讀", function() {{
      if (state.readLater[id]) delete state.readLater[id]; else state.readLater[id] = true;
      save(); renderNews();
    }});
    addBtn(state.starred[id] ? "★ 取消收藏" : "☆ 收藏", function() {{
      state.starred[id] = !state.starred[id]; save(); renderNews();
    }});
    var sep = document.createElement("div"); sep.className = "ctx-sep"; menu.appendChild(sep);
    addBtn(state.read[id] ? "標為未讀" : "標為已讀", function() {{
      state.read[id] = !state.read[id]; save(); renderNews();
    }});
    // 定位
    var vw = window.innerWidth, vh = window.innerHeight;
    menu.style.display = "block";
    var mw = menu.offsetWidth, mh = menu.offsetHeight;
    menu.style.left = (x + mw > vw ? vw - mw - 8 : x) + "px";
    menu.style.top = (y + mh > vh ? vh - mh - 8 : y) + "px";
    setTimeout(function() {{
      document.addEventListener("click", function _close() {{
        menu.style.display = "none";
        document.removeEventListener("click", _close);
      }});
    }}, 0);
  }}
  function setupLongPress(el, item) {{
    var timer = null;
    el.addEventListener("pointerdown", function(e) {{
      if (e.button !== 0) return;
      timer = setTimeout(function() {{
        showCtxMenu(e.clientX, e.clientY, item);
      }}, 500);
    }});
    el.addEventListener("pointerup", function() {{ clearTimeout(timer); }});
    el.addEventListener("pointercancel", function() {{ clearTimeout(timer); }});
    el.addEventListener("pointermove", function() {{ clearTimeout(timer); }});
    // 右鍵也顯示選單
    el.addEventListener("contextmenu", function(e) {{
      e.preventDefault();
      showCtxMenu(e.clientX, e.clientY, item);
    }});
  }}

  // ─── 法規訂閱 ────────────────────────────────────────────────
  function toggleSubscribeLaw(name) {{
    if (!state.subscribedLaws) state.subscribedLaws = {{}};
    if (state.subscribedLaws[name]) {{
      delete state.subscribedLaws[name];
    }} else {{
      state.subscribedLaws[name] = true;
      if (!state.lawLastSeen) state.lawLastSeen = {{}};
      // 記錄目前版本，之後才能偵測新修訂
      var r = REGISTRY.find(function(x) {{ return x.name === name; }});
      if (r && r.date) state.lawLastSeen[name] = r.date;
    }}
    save(); renderRegistry();
  }}
  function checkSubscriptionUpdates() {{
    if (!state.subscribedLaws) return;
    var updated = REGISTRY.filter(function(r) {{
      return state.subscribedLaws[r.name] &&
             state.lawLastSeen && state.lawLastSeen[r.name] &&
             r.date && r.date > state.lawLastSeen[r.name];
    }});
    if (updated.length) {{
      var msg = '🔔 ' + updated.length + ' 筆訂閱法規已更新：' + updated.map(function(r) {{ return r.name; }}).join('、');
      var toast = document.getElementById('notif-toast');
      toast.textContent = msg;
      toast.classList.add('show');
      toast.onclick = function() {{
        toast.classList.remove('show');
        document.querySelectorAll("[data-lawfilter]").forEach(function(b) {{
          b.classList.toggle("active", b.dataset.lawfilter === "subscribed");
        }});
        lawFilter = "subscribed"; renderRegistry();
        switchTab("registry");
      }};
      setTimeout(function() {{ toast.classList.remove('show'); }}, 12000);
    }}
  }}

  // ─── 合規 Checklist ───────────────────────────────────────────
  function toggleCheck(lawName, idx, checked) {{
    if (!state.checklists) state.checklists = {{}};
    if (!state.checklists[lawName]) state.checklists[lawName] = {{}};
    state.checklists[lawName][idx] = checked;
    save();
    // 即時更新 UI（不重新整個 drawer）
    var row = document.getElementById('ckrow_' + idx);
    if (row) row.classList.toggle('done', checked);
    // 更新進度條
    var ckItems = CHECKLISTS[lawName];
    if (ckItems) {{
      var ckState = state.checklists[lawName] || {{}};
      var doneCount = Object.keys(ckState).filter(function(k) {{ return ckState[k]; }}).length;
      var pct = Math.round(doneCount / ckItems.length * 100);
      var bar = document.querySelector('.ck-progress-bar');
      if (bar) bar.style.width = pct + '%';
      var hTitle = document.querySelector('.checklist-section h3 span');
      if (hTitle) hTitle.textContent = doneCount + '/' + ckItems.length + ' 完成 (' + pct + '%)';
    }}
  }}

  // ─── 法規並排對比 ─────────────────────────────────────────────
  function _fillCompareSelect(sel, filterQ, label) {{
    var cur = sel.value;
    sel.innerHTML = '<option value="">— 選擇' + (label||'法規') + ' —</option>';
    REGISTRY.forEach(function(r, i) {{
      if (r.articles && r.articles.length > 0) {{
        if (filterQ) {{
          var q = filterQ;
          var hit = r.name.includes(q) ||
                    (r.scope && r.scope.includes(q)) ||
                    (r.search_text && r.search_text.includes(q)) ||
                    (r.cat && r.cat.includes(q));
          if (!hit) return;
        }}
        var opt = document.createElement('option');
        opt.value = i; opt.textContent = r.name;
        if (String(i) === cur) opt.selected = true;
        sel.appendChild(opt);
      }}
    }});
  }}
  function openCompare() {{
    var modal = document.getElementById('compare-modal');
    var selA = document.getElementById('compare-sel-a');
    var selB = document.getElementById('compare-sel-b');
    var fA = document.getElementById('compare-filter-a');
    var fB = document.getElementById('compare-filter-b');
    _fillCompareSelect(selA, fA ? fA.value : '', '法規 A');
    _fillCompareSelect(selB, fB ? fB.value : '', '法規 B');
    if (fA) {{
      fA.oninput = function() {{ _fillCompareSelect(selA, fA.value, '法規 A'); renderCompare(); }};
    }}
    if (fB) {{
      fB.oninput = function() {{ _fillCompareSelect(selB, fB.value, '法規 B'); renderCompare(); }};
    }}
    modal.classList.add('open');
    renderCompare();
  }}
  function closeCompare() {{
    document.getElementById('compare-modal').classList.remove('open');
  }}
  function renderCompare() {{
    var selA = document.getElementById('compare-sel-a');
    var selB = document.getElementById('compare-sel-b');
    var cols = document.getElementById('compare-cols');
    if (!cols) return;
    function colHtml(sel) {{
      if (!sel.value) return '<div class="compare-col"><p style="color:var(--ink-soft);font-size:13px;padding:12px">請選擇法規</p></div>';
      var r = REGISTRY[+sel.value];
      if (!r) return '';
      var pcode = r.pcode || '';
      var artBase = pcode ? 'https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=' + pcode + '&flno=' : '';
      var statusCls = r.status === '廢止' ? 'status-off' : r.status === '未生效' ? 'status-pending' : 'status-now';
      var h = '<div class="compare-col"><h4>' + r.name + ' <span class="' + statusCls + '">' + (r.status||'現行') + '</span></h4>';
      if (r.scope) h += '<div class="scope-text" style="font-size:11.5px;margin-bottom:8px">' + r.scope.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</div>';
      if (r.articles && r.articles.length) {{
        var delA = (r.deleted_articles && r.deleted_articles.length) ? r.deleted_articles : [];
        var penA = new Set((r.penalty_articles || []).map(function(pa) {{ return pa.no; }}));
        var prevA = r.article_previews || {{}};
        h += '<div style="font-size:11px;color:var(--ink-soft);margin-bottom:4px">' +
          r.articles.length + ' 條有效' + (delA.length ? '・' + delA.length + ' 條已刪除' : '') +
          (penA.size ? '・<span style="color:#b91c1c">' + penA.size + ' 罰則</span>' : '') + '</div>';
        r.articles.forEach(function(no) {{
          var isPen = penA.has(no);
          var prev = prevA[no] ? prevA[no].replace(/"/g,'&quot;') : '';
          var ta = prev ? ' title="' + prev + '"' : '';
          var style = isPen ? 'color:#b91c1c;font-weight:600' : 'color:var(--stamp)';
          h += artBase
            ? '<div class="compare-art"><a href="' + artBase + encodeURIComponent(no) + '" target="_blank" class="art-no" style="' + style + '"' + ta + '>第' + no + '條</a></div>'
            : '<div class="compare-art"><span class="art-no" style="' + style + '"' + ta + '>第' + no + '條</span></div>';
        }});
        if (delA.length > 0) {{
          h += '<details class="deleted-arts-section" style="margin-top:6px">' +
            '<summary class="deleted-arts-toggle">已刪除條文（' + delA.length + ' 條）</summary>';
          delA.forEach(function(no) {{
            h += artBase
              ? '<div class="compare-art"><a href="' + artBase + encodeURIComponent(no) + '" target="_blank" class="art-no art-chip-deleted" style="color:var(--ink-soft)">第' + no + '條</a></div>'
              : '<div class="compare-art"><span class="art-no art-chip-deleted" style="color:var(--ink-soft)">第' + no + '條</span></div>';
          }});
          h += '</details>';
        }}
      }}
      return h + '</div>';
    }}
    cols.innerHTML = colHtml(selA) + colHtml(selB);
  }}

  // ─── 適用性判斷助手 ───────────────────────────────────────────
  function runGuide() {{
    var industry = document.getElementById('guide-industry').value;
    var hazards = [...document.querySelectorAll('#guide-hazards input:checked')].map(function(i) {{ return i.value; }});
    var result = document.getElementById('guide-result');
    var relevant = REGISTRY.filter(function(r) {{
      if (industry && r.cat !== industry) return false;
      return true;
    }});
    // 危害類型細篩
    if (hazards.length) {{
      var hazardMap = {{
        '高溫': ['高溫', '熱危害'],
        '高架': ['高架', '墜落', '營造'],
        '局限空間': ['缺氧', '局限空間'],
        '化學品': ['化學品', '有機溶劑', '危害性'],
        '機械': ['機械', '鍋爐', '壓力容器', '起重'],
        '環境監測': ['監測', '容許暴露', '作業環境'],
      }};
      relevant = relevant.filter(function(r) {{
        return hazards.some(function(h) {{
          var kws = hazardMap[h] || [h];
          return kws.some(function(kw) {{ return r.name.includes(kw) || (r.cat||'').includes(kw); }});
        }});
      }});
    }}
    if (!relevant.length) {{
      result.innerHTML = '<p style="color:var(--ink-soft);font-size:13px">找不到符合條件的法規，請放寬篩選條件。</p>';
      return;
    }}
    result.innerHTML = '<p style="font-size:12.5px;color:var(--ink-soft);margin-bottom:8px">找到 ' + relevant.length + ' 筆適用法規：</p>' +
      relevant.map(function(r) {{
        return '<div class="guide-result-item">' +
          '<span class="cat-tag">' + (r.cat||'') + '</span>' +
          '<button class="guide-jump-btn" data-law="' + r.name.replace(/&/g,'&amp;').replace(/"/g,'&quot;') + '">' + r.name + '</button>' +
          '</div>';
      }}).join('');
    result.querySelectorAll('.guide-jump-btn').forEach(function(btn) {{
      btn.onclick = function() {{
        document.getElementById('guide-modal').classList.remove('open');
        jumpToLaw(btn.dataset.law);
      }};
    }});
  }}

  function buildCatFilters() {{
    const cf = document.getElementById("catFilters");
    function makeBtn(label, value) {{
      const btn = document.createElement("button");
      btn.className = "cat-btn" + (value === "all" ? " active" : "");
      btn.textContent = label;
      btn.onclick = () => {{
        catFilter = value;
        document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        registryPage = 1; renderRegistry();
      }};
      cf.appendChild(btn);
    }}
    makeBtn("全部分類", "all");
    CATEGORIES.forEach(c => makeBtn(c, c));
  }}

  function renderRegistry() {{
    let rows = REGISTRY.slice();
    if (catFilter !== "all") rows = rows.filter(r => r.cat === catFilter);
    if (lawFilter === "starred") rows = rows.filter(r => state.starredLaws[r.name]);
    if (lawFilter === "subscribed") rows = rows.filter(r => state.subscribedLaws && state.subscribedLaws[r.name]);
    if (lawFilter === "recent")   rows = rows.filter(r => isRecent(r.date));
    if (lawFilter === "recent3m") rows = rows.filter(r => isRecent3m(r.date));
    if (penaltyFilter) rows = rows.filter(r => r.penalty_articles && r.penalty_articles.length > 0);
    if (inspectFilter) rows = rows.filter(r => INSPECTION_FOCUS.hasOwnProperty(r.name));
    const q = document.getElementById("lawSearch").value.trim();
    var fuseActive = false;
    if (q) {{
      if (q.length >= 2 && window.Fuse) {{
        if (!_fuseIndex) _fuseIndex = new Fuse(REGISTRY, {{
          keys:[{{name:'name',weight:3}},{{name:'scope',weight:1.5}},{{name:'authority',weight:1}},{{name:'cat',weight:.8}},{{name:'search_text',weight:.5}}],
          includeScore:true, threshold:0.4, minMatchCharLength:2
        }});
        var hits = _fuseIndex.search(q, {{limit:200}});
        var nameSet = new Set(rows.map(r => r.name));
        rows = hits.filter(h => nameSet.has(h.item.name)).map(h => h.item);
        fuseActive = true;
      }} else {{
        rows = rows.filter(r => r.name.includes(q)||(r.scope&&r.scope.includes(q))||(r.note&&r.note.includes(q))||(r.authority&&r.authority.includes(q))||(r.search_text&&r.search_text.includes(q)));
      }}
    }}

    if (!fuseActive) {{
      if (sortMode === "date") {{
        rows.sort((a, b) => {{
          if (!a.date && !b.date) return a.name.localeCompare(b.name, "zh-Hant");
          if (!a.date) return 1; if (!b.date) return -1;
          return b.date.localeCompare(a.date);
        }});
      }} else if (sortMode === "name") {{
        rows.sort((a, b) => a.name.localeCompare(b.name, "zh-Hant"));
      }} else {{
        rows.sort((a, b) => {{
          const ci = CATEGORIES.indexOf(a.cat), cj = CATEGORIES.indexOf(b.cat);
          if (ci !== cj) return (ci < 0 ? 999 : ci) - (cj < 0 ? 999 : cj);
          const tierOrder = {{act:0,reg:1,dir:2,notice:3}};
          if (a.tier !== b.tier) return (tierOrder[a.tier]||9) - (tierOrder[b.tier]||9);
          return a.name.localeCompare(b.name, "zh-Hant");
        }});
      }}
    }}

    const confirmed = rows.filter(r => r.date).length;

    document.getElementById("mainTableScroll").style.display = regViewMode === "table" ? "" : "none";
    document.getElementById("registryPager").style.display = regViewMode === "table" ? "" : "none";
    document.getElementById("lawTimeline").style.display = regViewMode === "timeline" ? "block" : "none";
    document.getElementById("lawGraph").style.display = regViewMode === "graph" ? "block" : "none";

    if (regViewMode === "timeline") {{
      document.getElementById("registryMeta").textContent = "時間軸：顯示 " + rows.length + " 筆，依最新修正日期排列";
      _renderTimeline(rows); return;
    }}
    if (regViewMode === "graph") {{
      var crossCount = REGISTRY.filter(r => r.cross_refs && Object.keys(r.cross_refs).length > 0).length;
      document.getElementById("registryMeta").textContent = "關係圖譜：" + REGISTRY.filter(r => r.articles && r.articles.length).length + " 部法規，" + crossCount + " 部含交叉引用（拖曳移動節點，滾輪縮放）";
      renderGraph(); return;
    }}

    const totalPages = Math.max(1, Math.ceil(rows.length / REGISTRY_PAGE_SIZE));
    if (registryPage > totalPages) registryPage = totalPages;
    const pageStart = (registryPage - 1) * REGISTRY_PAGE_SIZE;
    const pageRows = rows.slice(pageStart, pageStart + REGISTRY_PAGE_SIZE);
    _regRows = rows;

    document.getElementById("registryMeta").textContent =
      "顯示 " + rows.length + " 筆（共 " + REGISTRY.length + " 筆），已確認修正日期 " + confirmed + " 筆" +
      (totalPages > 1 ? "，第 " + registryPage + " / " + totalPages + " 頁" : "");

    document.getElementById("registryBody").innerHTML = pageRows.map(function(r, i) {{
      var idx = pageStart + i;
      const recent3m = isRecent3m(r.date) && !isNewLaw(r);
      const recent = isRecent(r.date) && !isNewLaw(r) && !recent3m;
      const isnew = isNewLaw(r);
      const badge = isnew ? '<span class="badge new-law">新訂</span>'
                  : recent3m ? '<span class="badge recent" style="background:#dc2626">近3月修正</span>'
                  : recent   ? '<span class="badge recent">近期修正</span>' : "";
      const chgSum = (recent || recent3m) ? _chgSummary(r) : '';
      const dateText = r.date || "待確認";
      const dateCls = r.date ? "rdate" : "rdate unconfirmed";
      const noteTxt = (r.note && !isnew) ? '<br><small style="color:var(--ink-soft);font-size:11px">' + r.note + '</small>' : "";
      const starred = state.starredLaws[r.name];
      const isSub = !!(state.subscribedLaws && state.subscribedLaws[r.name]);
      const hasUpdate = isSub && state.lawLastSeen && state.lawLastSeen[r.name] && r.date && r.date > state.lawLastSeen[r.name];
      const statusCls = r.status === '廢止' ? 'status-off' : r.status === '未生效' ? 'status-pending' : '';
      const statusBadge = statusCls ? '<span class="' + statusCls + '">' + (r.status || '現行') + '</span>' : '';
      // ③ 全文搜尋：標示透過條文內容命中的法規
      var artMatchBadge = '';
      if (q && !r.name.includes(q) && r.search_text && r.search_text.includes(q) && r.article_previews) {{
        var matchedNos = (r.articles || []).filter(function(no) {{
          return (r.article_previews[no] || '').includes(q);
        }});
        if (matchedNos.length)
          artMatchBadge = '<span class="badge" style="background:#6d28d9;font-size:10px">條文符合：第' +
            matchedNos.slice(0,3).join('、') + (matchedNos.length > 3 ? '…' : '') + '條</span>';
      }}
      var penBadge = (penaltyFilter && r.penalty_articles && r.penalty_articles.length > 0)
        ? '<span class="art-chip-penalty" style="font-size:10px;padding:1px 5px;vertical-align:middle">罰則×' + r.penalty_articles.length + '</span>' : '';
      var inspBadge = INSPECTION_FOCUS.hasOwnProperty(r.name)
        ? '<span class="inspect-badge">🔍 稽查重點 #' + INSPECTION_FOCUS[r.name].rank + '</span>' : '';
      return '<tr' + (hasUpdate ? ' style="background:rgba(245,158,11,.07)"' : '') + '>' +
        '<td><button class="star-btn' + (starred ? ' on' : '') + '" data-law="' + r.name.replace(/"/g, '&quot;') + '" title="收藏">' + (starred ? '★' : '☆') + '</button></td>' +
        '<td><button class="sub-btn' + (isSub ? ' on' : '') + '" data-sublaw="' + r.name.replace(/"/g, '&quot;') + '" title="' + (isSub ? '取消訂閱' : '訂閱此法規') + '">' + (isSub ? '🔔' : '🔕') + '</button></td>' +
        '<td class="rname"><button class="rname-btn" data-idx="' + idx + '">' + r.name + '</button>' + badge + statusBadge + artMatchBadge + penBadge + inspBadge + (hasUpdate ? '<span class="badge recent" style="background:#f59e0b">已更新</span>' : '') +
          (chgSum ? '<div class="chg-summary">' + chgSum + '</div>' : '') + '</td>' +
        '<td><span class="cat-tag">' + (r.cat || '') + '</span></td>' +
        '<td><span class="tier-tag ' + r.tier + '">' + TIER_LABEL[r.tier] + '</span></td>' +
        '<td class="' + dateCls + '">' + dateText + noteTxt + '</td>' +
        '<td><a href="' + r.source + '" target="_blank" rel="noopener" class="src-link">全文 ↗</a></td>' +
        '</tr>';
    }}).join("");

    // 分頁控制
    var pager = document.getElementById("registryPager");
    if (totalPages <= 1) {{
      pager.innerHTML = "";
    }} else {{
      var pbtns = '<button data-pgact="prev"' + (registryPage === 1 ? ' disabled' : '') + '>‹ 上頁</button>';
      for (var p = 1; p <= totalPages; p++) {{
        pbtns += '<button data-pgact="goto" data-pgno="' + p + '" class="' + (p === registryPage ? 'active' : '') + '">' + p + '</button>';
      }}
      pbtns += '<button data-pgact="next"' + (registryPage === totalPages ? ' disabled' : '') + '>下頁 ›</button>';
      pager.innerHTML = pbtns;
      pager.querySelectorAll('[data-pgact="prev"]').forEach(function(b) {{
        b.onclick = function() {{ registryPage--; renderRegistry(); }};
      }});
      pager.querySelectorAll('[data-pgact="goto"]').forEach(function(b) {{
        b.onclick = function() {{ registryPage = +b.dataset.pgno; renderRegistry(); }};
      }});
      pager.querySelectorAll('[data-pgact="next"]').forEach(function(b) {{
        b.onclick = function() {{ registryPage++; renderRegistry(); }};
      }});
    }}

    document.querySelectorAll(".star-btn").forEach(btn => {{
      btn.onclick = () => {{
        const name = btn.dataset.law;
        if (state.starredLaws[name]) delete state.starredLaws[name];
        else state.starredLaws[name] = true;
        save(); renderRegistry();
      }};
    }});
    document.querySelectorAll(".sub-btn[data-sublaw]").forEach(btn => {{
      btn.onclick = () => toggleSubscribeLaw(btn.dataset.sublaw);
    }});
    document.querySelectorAll(".rname-btn").forEach(btn => {{
      btn.onclick = () => openDrawer(_regRows[+btn.dataset.idx]);
    }});
  }}

  function openDrawer(r) {{
    const pcode = r.pcode || "";
    const artBase = pcode ? "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=" + pcode + "&flno=" : "";
    const isSub = !!(state.subscribedLaws && state.subscribedLaws[r.name]);

    // 標題 + 訂閱按鈕
    document.getElementById("drawer-title").innerHTML =
      '<a href="' + r.source + '" target="_blank" rel="noopener">' + r.name + ' ↗</a>' +
      '<button class="sub-btn' + (isSub ? ' on' : '') + '" id="sub-btn-drawer" title="' + (isSub ? '取消訂閱' : '訂閱此法規，修訂時提醒') + '" data-sublaw="' + r.name.replace(/&/g,'&amp;').replace(/"/g,'&quot;') + '">' + (isSub ? '🔔' : '🔕') + '</button>';
    document.getElementById("sub-btn-drawer").onclick = function() {{ toggleSubscribeLaw(r.name); }};

    // 法規訂閱異動通知
    var noticeHtml = '';
    if (isSub && state.lawLastSeen && state.lawLastSeen[r.name] && r.date && r.date > state.lawLastSeen[r.name]) {{
      noticeHtml = '<div class="sub-notice">⚠ 此法規自您上次查看後已於 ' + r.date + ' 修訂，請注意更新內容。</div>';
    }}

    // 主體 meta
    var statusCls = r.status === '廢止' ? 'status-off' : r.status === '未生效' ? 'status-pending' : 'status-now';
    var statusLabel = r.status || '現行';
    let html = noticeHtml + '<div class="drawer-meta">' +
      '<span>' + (TIER_LABEL[r.tier] || r.tier) + '</span>' +
      '<span class="' + statusCls + '">' + statusLabel + '</span>' +
      (r.cat ? '<span>' + r.cat + '</span>' : '') +
      (r.date ? '<span>最新修正 ' + r.date + '</span>' : '<span style="color:#aaa">日期待確認</span>') +
      (r.has_table ? '<span style="background:#fffbeb;color:#b45309">含附表</span>' : '') +
      '</div>';

    // 主管機關
    if (r.authority) {{
      html += '<div class="authority-row">主管機關：<span>' + r.authority + '</span></div>';
    }}

    // 附表提示
    if (r.has_table) {{
      html += '<div class="drawer-section"><div class="drawer-table-note">' +
        '⚠ 本法規含附表／附件，請至全文頁面查看或下載。' +
        '</div></div>';
    }}

    // 適用範圍（前3條）
    if (r.scope) {{
      html += '<div class="drawer-section scope-section"><h3>適用範圍</h3>' +
        '<div class="scope-text">' + r.scope.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</div>' +
        '</div>';
    }}

    // 最新修正詳情
    var lr = r.latest_revision;
    if (lr && (lr.is_full || (lr.modified && lr.modified.length) || (lr.added && lr.added.length) || (lr.deleted && lr.deleted.length))) {{
      var lrDate = r.date || '';
      html += '<div class="drawer-section"><div class="latest-rev-block">';
      html += '<h4>最新修正' + (lrDate ? '　' + lrDate : '') + '</h4>';
      if (lr.is_full) {{
        html += '<div class="latest-rev-row"><span class="rev-label chg-modified">✏ 全文</span><span class="rev-arts">全文修正</span></div>';
      }} else {{
        if (lr.modified && lr.modified.length) {{
          html += '<div class="latest-rev-row"><span class="rev-label chg-modified">✏ 修正（' + lr.modified.length + '條）</span></div>';
          html += '<div class="rev-chips">';
          lr.modified.forEach(function(no) {{
            html += artBase
              ? '<a href="'+artBase+encodeURIComponent(no)+'" target="_blank" class="art-chip">第'+no+'條</a>'
              : '<span class="art-chip">第'+no+'條</span>';
          }});
          html += '</div>';
        }}
        if (lr.added && lr.added.length) {{
          html += '<div class="latest-rev-row"><span class="rev-label chg-added">➕ 增訂（' + lr.added.length + '條）</span></div>';
          html += '<div class="rev-chips">';
          lr.added.forEach(function(no) {{
            html += artBase
              ? '<a href="'+artBase+encodeURIComponent(no)+'" target="_blank" class="art-chip art-chip-added">第'+no+'條</a>'
              : '<span class="art-chip art-chip-added">第'+no+'條</span>';
          }});
          html += '</div>';
        }}
        if (lr.deleted && lr.deleted.length) {{
          html += '<div class="latest-rev-row"><span class="rev-label chg-deleted">✖ 刪除（' + lr.deleted.length + '條）</span></div>';
          html += '<div class="rev-chips">';
          lr.deleted.forEach(function(no) {{
            html += '<span class="art-chip art-chip-deleted">第'+no+'條</span>';
          }});
          html += '</div>';
        }}
      }}
      html += '</div></div>';
    }}

    // ⑤ 法規交叉引用
    var cr = r.cross_refs;
    if (cr && Object.keys(cr).length) {{
      html += '<div class="drawer-section"><h3>條文引用其他法規</h3><div class="cross-ref-list">';
      Object.keys(cr).sort().forEach(function(lawName) {{
        var arts = cr[lawName];
        var searchUrl = 'https://law.moj.gov.tw/LawClass/LawSearchContent.aspx?pc=&searchType=Keyword&searchField=law&searchContent=' + encodeURIComponent(lawName);
        html += '<div class="cross-ref-row">';
        html += '<a href="' + searchUrl + '" target="_blank" class="cross-ref-law">' + lawName + '</a>';
        html += '<span class="cross-ref-arts">第' + arts.join('、') + '條</span>';
        html += '</div>';
      }});
      html += '</div></div>';
    }}

    // 沿革摘要
    if (r.summary) {{
      html += '<div class="drawer-section"><h3>沿革摘要</h3>' +
        '<div class="drawer-summary">' + r.summary.replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'<br>') + '</div>' +
        '</div>';
    }}

    // ── 罰則條文（結構化）
    if (r.penalty_articles && r.penalty_articles.length > 0) {{
      var maxFine = Math.max.apply(null, r.penalty_articles.map(function(pa) {{ return pa.fine_max || 0; }}));
      html += '<div class="drawer-section penalty-section"><h3>罰則條文（' + r.penalty_articles.length + ' 條）</h3>';
      r.penalty_articles.forEach(function(pa) {{
        var t = pa.text.replace(/</g,'&lt;').replace(/>/g,'&gt;');
        t = t.replace(/(罰(?:鍰|款)[^。；\n]*(?:萬|千)元[^。；\n]*)/g, '<strong style="color:#b91c1c">$1</strong>');
        html += '<div class="penalty-art"><span class="art-no">第' + pa.no + '條</span>';
        // 結構化資訊列
        var hasMeta = pa.fine_max || pa.criminal || (pa.liable && pa.liable.length);
        if (hasMeta) {{
          html += '<div class="penalty-struct">';
          if (pa.fine_max) {{
            var fineLabel = (pa.fine_min ? pa.fine_min + '～' + pa.fine_max : '≤' + pa.fine_max) + ' 萬元';
            var pct = maxFine > 0 ? Math.round((pa.fine_max / maxFine) * 100) : 50;
            html += '<span class="penalty-fine-label">罰鍰 ' + fineLabel + '</span>'
              + '<span class="penalty-fine-bar"><span class="penalty-fine-fill" style="width:' + pct + '%"></span></span>';
          }}
          if (pa.criminal) html += '<span class="penalty-struct-tag pst-criminal">⚖ ' + pa.criminal + '</span>';
          if (pa.repeated) html += '<span class="penalty-struct-tag pst-repeat">↻ 按次處罰</span>';
          if (pa.liable && pa.liable.length)
            pa.liable.forEach(function(l) {{ html += '<span class="penalty-struct-tag pst-liable">' + l + '</span>'; }});
          html += '</div>';
        }}
        html += t + '</div>';
      }});
      html += '</div>';
    }}

    // ── 適用對象
    var appData = APPLICABILITY[r.name];
    if (appData) {{
      html += '<div class="drawer-section"><h3>適用對象</h3>';
      html += '<div class="applicability-block">';
      if (appData.industries && appData.industries.length)
        appData.industries.forEach(function(ind) {{ html += '<span class="app-tag app-industry">🏭 ' + ind + '</span>'; }});
      if (appData.min_workers != null)
        html += '<span class="app-tag app-size">👥 ' + appData.min_workers + ' 人以上</span>';
      if (appData.risk_class && appData.risk_class.length)
        appData.risk_class.forEach(function(rc) {{ html += '<span class="app-tag app-risk">⚠ ' + rc + '</span>'; }});
      html += '</div>';
      if (appData.notes) html += '<div class="app-note">' + appData.notes + '</div>';
      html += '</div>';
    }}

    // ── 稽查重點
    var ifData = INSPECTION_FOCUS[r.name];
    if (ifData) {{
      html += '<div class="drawer-section"><h3>稽查重點</h3>';
      html += '<div class="inspect-rank"><span class="inspect-rank-dot"></span>全行業稽查優先度 #' + ifData.rank + '　' + (ifData.note || '') + '</div>';
      if (ifData.focus_articles && ifData.focus_articles.length) {{
        var artBase2 = r.pcode ? 'https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=' + r.pcode + '&flno=' : '';
        html += '<div style="margin:4px 0 8px;display:flex;flex-wrap:wrap;gap:4px">';
        ifData.focus_articles.forEach(function(no) {{
          html += artBase2
            ? '<a href="' + artBase2 + encodeURIComponent(no) + '" target="_blank" class="art-chip art-chip-penalty" style="font-size:10.5px">第' + no + '條</a>'
            : '<span class="art-chip art-chip-penalty" style="font-size:10.5px">第' + no + '條</span>';
        }});
        html += '</div>';
      }}
      if (ifData.common_violations && ifData.common_violations.length) {{
        html += '<ul class="inspect-violations">';
        ifData.common_violations.forEach(function(v) {{ html += '<li>' + v + '</li>'; }});
        html += '</ul>';
      }}
      html += '</div>';
    }}

    // ── 對應標準（CNS/ISO）
    var stdList = CNS_ISO[r.name];
    if (stdList && stdList.length) {{
      html += '<div class="drawer-section"><h3>對應國際標準</h3><div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:2px">';
      stdList.forEach(function(s) {{
        html += '<span class="std-chip"><strong>' + s.code + '</strong>'
          + (s.title ? ' <span class="std-chip-title">· ' + s.title + '</span>' : '')
          + '</span>';
      }});
      html += '</div></div>';
    }}

    // 條文索引（①章節分組 ②罰則標色 ③tooltip預覽 ④搜尋 ⑤多選複製）
    if (r.articles && r.articles.length > 0) {{
      var delArts = (r.deleted_articles && r.deleted_articles.length) ? r.deleted_articles : [];
      var penaltySet = new Set((r.penalty_articles || []).map(function(pa) {{ return pa.no; }}));
      var addedSet  = new Set((r.latest_revision && r.latest_revision.added) ? r.latest_revision.added : []);
      var previews = r.article_previews || {{}};
      var cntLabel = r.articles.length + ' 條有效' + (delArts.length ? '（' + delArts.length + ' 條已刪除）' : '');
      html += '<div class="drawer-section">';
      html += '<h3>條文索引（共 ' + cntLabel + '）</h3>';
      // ④ 搜尋工具列
      html += '<div class="art-index-toolbar">';
      html += '<input class="art-search" id="art-srch" type="text" placeholder="搜尋條號…" autocomplete="off">';
      html += '<button class="art-copy-btn" id="art-ms-btn" title="切換多選模式後點擊條文可批次複製引用">☑ 多選</button>';
      html += '<button class="art-copy-btn" id="art-cp-btn" style="display:none">複製引用（<span id="art-sel-cnt">0</span>）</button>';
      html += '</div>';
      // ②③ chip 產生函式
      function _mkChip(no) {{
        var cls = 'art-chip' + (penaltySet.has(no) ? ' art-chip-penalty' : addedSet.has(no) ? ' art-chip-added' : '');
        var prev = previews[no] ? previews[no].replace(/"/g,'&quot;') : '';
        var ta = prev ? ' title="' + prev + '"' : '';
        return artBase
          ? '<a href="' + artBase + encodeURIComponent(no) + '" target="_blank" rel="noopener" class="' + cls + '" data-artno="' + no + '"' + ta + '>第' + no + '條</a>'
          : '<span class="' + cls + '" data-artno="' + no + '"' + ta + '>第' + no + '條</span>';
      }}
      html += '<div id="art-chips-wrap">';
      // ① 章節分組 or 平鋪
      if (r.chapters && r.chapters.length > 0) {{
        r.chapters.forEach(function(ch) {{
          html += '<div class="art-chapter-group">';
          html += '<div class="art-chapter-hd">' + ch.title + '</div>';
          html += '<div style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:6px">';
          ch.articles.forEach(function(no) {{ html += _mkChip(no); }});
          html += '</div></div>';
        }});
      }} else {{
        html += '<div style="display:flex;flex-wrap:wrap;gap:6px;max-height:200px;overflow-y:auto;padding:2px 0">';
        r.articles.forEach(function(no) {{ html += _mkChip(no); }});
        html += '</div>';
      }}
      html += '</div>';
      if (delArts.length > 0) {{
        html += '<details class="deleted-arts-section" style="margin-top:6px">';
        html += '<summary class="deleted-arts-toggle">已刪除條文（' + delArts.length + ' 條）</summary>';
        html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;padding:2px 0">';
        delArts.forEach(function(no) {{
          html += artBase
            ? '<a href="' + artBase + encodeURIComponent(no) + '" target="_blank" rel="noopener" class="art-chip art-chip-deleted">第' + no + '條</a>'
            : '<span class="art-chip art-chip-deleted">第' + no + '條</span>';
        }});
        html += '</div></details>';
      }}
      html += '</div>';
    }}

    // 全文按鈕
    html += '<a href="' + r.source + '" target="_blank" rel="noopener" class="drawer-fulllink">開啟全文頁面 ↗</a>';

    // 合規稽核 Checklist
    var ckItems = CHECKLISTS[r.name];
    if (ckItems && ckItems.length > 0) {{
      var ckState = (state.checklists && state.checklists[r.name]) || {{}};
      var doneCount = Object.keys(ckState).filter(function(k) {{ return ckState[k]; }}).length;
      var pct = Math.round(doneCount / ckItems.length * 100);
      html += '<div class="drawer-section checklist-section">' +
        '<h3>合規稽核 Checklist <span>' + doneCount + '/' + ckItems.length + ' 完成 (' + pct + '%)</span></h3>' +
        '<div class="ck-progress"><div class="ck-progress-bar" style="width:' + pct + '%"></div></div>';
      ckItems.forEach(function(item, idx) {{
        var cid = 'ck_' + idx;
        var done = !!ckState[idx];
        html += '<div class="ck-item' + (done ? ' done' : '') + '" id="ckrow_' + idx + '">' +
          '<input type="checkbox" id="' + cid + '" class="ck-input" data-idx="' + idx + '"' + (done ? ' checked' : '') + '>' +
          '<label for="' + cid + '">' + item + '</label></div>';
      }});
      html += '</div>';
    }}

    document.getElementById("drawer-body").innerHTML = html;

    // 綁定 checklist 事件（避免 onclick 字串的引號問題）
    var ckInputs = document.querySelectorAll('#drawer-body .ck-input');
    ckInputs.forEach(function(inp) {{
      inp.onchange = function() {{ toggleCheck(r.name, +inp.dataset.idx, inp.checked); }};
    }});

    // ─── 條文索引互動（①-⑤）────────────────────────────────────
    (function() {{
      var msBtn  = document.getElementById('art-ms-btn');
      var cpBtn  = document.getElementById('art-cp-btn');
      var srch   = document.getElementById('art-srch');
      var wrap   = document.getElementById('art-chips-wrap');
      var cntEl  = document.getElementById('art-sel-cnt');
      var multiMode = false;
      var sel = new Set();
      function showToast(msg) {{
        var t = document.getElementById('clip-toast');
        if (!t) return;
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(function() {{ t.classList.remove('show'); }}, 2200);
      }}
      // ⑤ 多選切換
      if (msBtn) msBtn.onclick = function() {{
        multiMode = !multiMode;
        msBtn.classList.toggle('active', multiMode);
        cpBtn.style.display = multiMode ? '' : 'none';
        if (!multiMode) {{
          sel.clear();
          wrap && wrap.querySelectorAll('.art-chip.art-selected').forEach(function(c) {{ c.classList.remove('art-selected'); }});
          if (cntEl) cntEl.textContent = '0';
        }}
      }};
      // ⑤ 複製所選引用
      if (cpBtn) cpBtn.onclick = function() {{
        if (!sel.size) return;
        var sorted = [...sel].sort(function(a,b) {{
          var na = parseFloat(a), nb = parseFloat(b);
          return isNaN(na) || isNaN(nb) ? a.localeCompare(b) : na - nb;
        }});
        var txt = r.name + '第' + sorted.join('條、第') + '條';
        navigator.clipboard.writeText(txt)
          .then(function() {{ showToast('已複製 ' + sel.size + ' 條引用'); }})
          .catch(function() {{ showToast('複製失敗，請手動選取'); }});
      }};
      // ⑤ 委派點擊（多選模式攔截，普通模式放行）
      if (wrap) wrap.addEventListener('click', function(e) {{
        if (!multiMode) return;
        var chip = e.target.closest('.art-chip');
        if (!chip || !chip.dataset.artno) return;
        e.preventDefault();
        var no = chip.dataset.artno;
        if (sel.has(no)) {{ sel.delete(no); chip.classList.remove('art-selected'); }}
        else {{ sel.add(no); chip.classList.add('art-selected'); }}
        if (cntEl) cntEl.textContent = sel.size;
      }});
      // ④ 條號搜尋
      if (srch) srch.addEventListener('input', function() {{
        var q = srch.value.trim();
        wrap && wrap.querySelectorAll('.art-chip').forEach(function(c) {{
          c.style.display = (!q || (c.dataset.artno || '').includes(q)) ? '' : 'none';
        }});
        wrap && wrap.querySelectorAll('.art-chapter-group').forEach(function(g) {{
          var vis = [...g.querySelectorAll('.art-chip')].filter(function(c) {{ return c.style.display !== 'none'; }}).length;
          g.style.display = vis ? '' : 'none';
        }});
      }});
    }})();

    document.getElementById("law-drawer").classList.add("open");
    document.getElementById("drawer-overlay").classList.add("open");
    document.body.style.overflow = "hidden";

    // 記錄查看時間（供訂閱通知判斷）
    if (!state.lawLastSeen) state.lawLastSeen = {{}};
    if (r.date) {{ state.lawLastSeen[r.name] = r.date; save(); }}
  }}

  function closeDrawer() {{
    document.getElementById("law-drawer").classList.remove("open");
    document.getElementById("drawer-overlay").classList.remove("open");
    document.body.style.overflow = "";
  }}

  // ⑥ 鍵盤快捷鍵（j/k 上下、s 收藏、o 開啟、m 已讀、? 說明、Esc 關閉）
  var focusedItemIdx = -1;
  function getFocusItems() {{ return [...document.querySelectorAll('#newsList .item')]; }}
  function setItemFocus(idx) {{
    var items = getFocusItems();
    if (!items.length) return;
    focusedItemIdx = Math.max(0, Math.min(idx, items.length - 1));
    items.forEach(function(el, i) {{ el.classList.toggle('focused', i === focusedItemIdx); }});
    items[focusedItemIdx].scrollIntoView({{behavior:'smooth', block:'nearest'}});
  }}
  document.addEventListener("keydown", function(e) {{
    var tag = document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    var kbdModal = document.getElementById('kbd-modal');
    var aiModal = document.getElementById('ai-modal');
    if (e.key === 'Escape') {{
      kbdModal.classList.remove('open');
      aiModal.classList.remove('open');
      document.getElementById('compare-modal').classList.remove('open');
      document.getElementById('guide-modal').classList.remove('open');
      closeDrawer();
      document.querySelectorAll('.board-dropdown').forEach(function(d) {{ d.remove(); }});
      return;
    }}
    if (e.key === '?') {{ kbdModal.classList.add('open'); return; }}
    var items = getFocusItems();
    if (!items.length) return;
    if (e.key === 'j' || e.key === 'ArrowDown') {{ e.preventDefault(); setItemFocus(focusedItemIdx + 1); return; }}
    if (e.key === 'k' || e.key === 'ArrowUp') {{ e.preventDefault(); setItemFocus(Math.max(0, focusedItemIdx - 1)); return; }}
    if (focusedItemIdx < 0) return;
    var cur = items[focusedItemIdx];
    if (e.key === 's') {{ var sb = cur.querySelector('[data-act="star"]'); if (sb) sb.click(); }}
    else if (e.key === 'o' || e.key === 'Enter') {{ var a = cur.querySelector('h3 a'); if (a) window.open(a.href, '_blank'); }}
    else if (e.key === 'm') {{ var rb = cur.querySelector('[data-act="read"]'); if (rb) rb.click(); }}
    else if (e.key === 'r') {{ var rlb = cur.querySelector('[data-act="rl"]'); if (rlb) rlb.click(); }}
    else if (e.key === 'S') {{ var curItem = NEWS.find(function(n) {{ return idOf(n) === cur.dataset.id; }}); if (curItem) shareItem(curItem); }}
  }});

  function renderDirectives() {{
    document.getElementById("directiveBody").innerHTML = DIRECTIVES.map(r => {{
      const badge = isRecent(r.date) ? '<span class="badge recent">近期</span>' : "";
      return '<tr>' +
        '<td class="rname">' + r.name + badge + '</td>' +
        '<td><span class="tier-tag ' + r.tier + '">' + TIER_LABEL[r.tier] + '</span></td>' +
        '<td class="rdate">' + (r.date || "待確認") + (r.note ? " (" + r.note + ")" : "") + '</td>' +
        '<td><a href="' + r.source + '" target="_blank" rel="noopener">查看 ↗</a></td>' +
        '</tr>';
    }}).join("");
  }}

  function jumpToLaw(name) {{
    switchTab("registry");
    catFilter = "all"; lawFilter = "all"; sortMode = "cat";
    document.querySelectorAll(".cat-btn").forEach(function(b, i) {{ b.classList.toggle("active", i === 0); }});
    document.querySelectorAll("[data-lawfilter]").forEach(function(b) {{ b.classList.toggle("active", b.dataset.lawfilter === "all"); }});
    document.querySelectorAll("[data-sort]").forEach(function(b) {{ b.classList.toggle("active", b.dataset.sort === "cat"); }});
    document.getElementById("lawSearch").value = name;
    renderRegistry();
    window.scrollTo({{top: 0, behavior: "smooth"}});
  }}

  function renderScenarios() {{
    const grid = document.getElementById("scenarioGrid");
    SCENARIOS.forEach(function(s) {{
      const card = document.createElement("div");
      card.className = "scenario-card";
      const lawItems = s.laws.map(function(l) {{
        return '<li><button class="law-link" data-law="' + l.replace(/"/g,'&quot;') +
          '" onclick="event.stopPropagation();jumpToLaw(this.dataset.law)">→ ' + l + '</button></li>';
      }}).join("");
      card.innerHTML =
        '<div class="sc-head">' +
          '<span class="sc-icon">' + s.icon + '</span>' +
          '<div class="sc-body">' +
            '<div class="sc-title-row">' +
              '<span class="sc-title">' + s.name + '</span>' +
              '<span class="sc-law-count">' + s.laws.length + ' 部法規</span>' +
              '<span class="sc-chevron">▼</span>' +
            '</div>' +
            '<div class="sc-desc">' + s.desc + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="sc-laws-panel">' +
          '<div class="sc-laws-inner">' +
            '<ul class="scenario-laws">' + lawItems + '</ul>' +
          '</div>' +
        '</div>';
      card.addEventListener("click", function(e) {{
        if (!e.target.closest(".sc-laws-panel")) {{
          this.classList.toggle("open");
        }}
      }});
      grid.appendChild(card);
    }});
  }}

  document.querySelectorAll("[data-filter]").forEach(btn => {{
    btn.onclick = () => {{
      document.querySelectorAll("[data-filter]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active"); newsFilter = btn.dataset.filter;
      currentBoard = null;
      renderBoardArea();
      renderNews();
    }};
  }});
  document.querySelectorAll("[data-srctype]").forEach(btn => {{
    btn.onclick = () => {{
      const t = btn.dataset.srctype;
      if (srcTypeFilter.has(t)) {{
        srcTypeFilter.delete(t);
        btn.classList.remove("active");
      }} else {{
        srcTypeFilter.add(t);
        btn.classList.add("active");
      }}
      renderNews();
    }};
  }});
  document.querySelectorAll("[data-lawfilter]").forEach(btn => {{
    btn.onclick = () => {{
      document.querySelectorAll("[data-lawfilter]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active"); lawFilter = btn.dataset.lawfilter; registryPage = 1; renderRegistry();
    }};
  }});
  document.querySelectorAll("[data-sort]").forEach(btn => {{
    btn.onclick = () => {{
      sortMode = btn.dataset.sort;
      document.querySelectorAll("[data-sort]").forEach(b => b.classList.toggle("active", b.dataset.sort === sortMode));
      registryPage = 1; renderRegistry();
    }};
  }});
  document.querySelectorAll("[data-regview]").forEach(btn => {{
    btn.onclick = () => {{
      regViewMode = btn.dataset.regview;
      document.querySelectorAll("[data-regview]").forEach(b => b.classList.toggle("active", b.dataset.regview === regViewMode));
      registryPage = 1; renderRegistry();
    }};
  }});
  var _newsDebounce, _lawDebounce;
  document.getElementById("newsSearch").addEventListener("input", function() {{
    clearTimeout(_newsDebounce);
    _newsDebounce = setTimeout(renderNews, 200);
  }});
  document.getElementById("lawSearch").addEventListener("input", function() {{
    clearTimeout(_lawDebounce);
    registryPage = 1;
    _lawDebounce = setTimeout(renderRegistry, 200);
  }});
  function switchTab(tabName) {{
    document.querySelectorAll(".sidebar-btn, .bottom-nav button").forEach(function(b) {{
      b.classList.toggle("active", b.dataset.tab === tabName);
    }});
    document.querySelectorAll(".tabpanel").forEach(function(p) {{
      p.classList.remove("active");
    }});
    document.getElementById("tab-" + tabName).classList.add("active");
  }}
  document.querySelectorAll(".sidebar-btn, .bottom-nav button").forEach(function(btn) {{
    btn.addEventListener("click", function() {{ switchTab(this.dataset.tab); }});
  }});
  document.getElementById("scenarioSearch").addEventListener("input", function() {{
    var q = this.value.trim();
    document.querySelectorAll(".scenario-card").forEach(function(card, i) {{
      var s = SCENARIOS[i];
      var hit = !q || s.name.includes(q) || s.desc.includes(q) ||
                s.laws.some(function(l) {{ return l.includes(q); }});
      card.style.display = hit ? "" : "none";
    }});
  }});

  // ① 法規關係圖譜（D3 force-directed）
  function renderGraph() {{
    var el = document.getElementById("lawGraph");
    if (!el) return;
    if (!window.d3) {{
      el.innerHTML = '<div style="padding:60px 40px;text-align:center;color:var(--ink-soft);line-height:2">'
        + '<div style="font-size:36px;margin-bottom:12px">🕸</div>'
        + '<div style="font-weight:600;color:var(--ink);margin-bottom:6px">D3.js 圖形庫尚未載入</div>'
        + '<div style="font-size:12px">瀏覽器可能封鎖了外部 CDN 腳本。<br>'
        + '請嘗試：關閉 Tracking Prevention、或改用 Chrome / Firefox。</div>'
        + '</div>';
      return;
    }}
    var W = el.offsetWidth || el.parentElement.offsetWidth || 760, H = 580;
    if (_graphSim) {{ _graphSim.stop(); _graphSim = null; }}
    var allLaws = REGISTRY.filter(function(r) {{ return r.articles && r.articles.length > 0; }});
    var nodes = allLaws.map(function(r) {{
      return {{ id: r.name, arts: r.articles.length, cat: r.cat || '', tier: r.tier || 'dir',
               cross: r.cross_refs ? Object.keys(r.cross_refs).length : 0, _r: r }};
    }});
    var nodeMap = {{}};
    nodes.forEach(function(n) {{ nodeMap[n.id] = n; }});
    var links = [];
    allLaws.forEach(function(r) {{
      if (!r.cross_refs) return;
      Object.keys(r.cross_refs).forEach(function(target) {{
        if (nodeMap[target] && target !== r.name) {{
          links.push({{ source: r.name, target: target, val: r.cross_refs[target].length }});
        }}
      }});
    }});
    var catPal = {{'職安衛主要法規':'#dc4b4b','化學品管理':'#e07a2a','營造業':'#c99a1a','機械設備':'#4aaa52','電氣安全':'#2a72e0','防護具':'#6a52c0','職業衛生':'#b052c0','勞工行政':'#52a8b0','職業傷病':'#3aa0d0'}};
    d3.select(el).selectAll("*").remove();
    var svg = d3.select(el).append("svg").attr("width", W).attr("height", H);
    var g = svg.append("g");
    svg.call(d3.zoom().scaleExtent([0.1, 6]).on("zoom", function(e) {{ g.attr("transform", e.transform); }}));
    var sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).id(function(d) {{ return d.id; }}).distance(85).strength(0.35))
      .force("charge", d3.forceManyBody().strength(-200))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("collide", d3.forceCollide(function(d) {{ return Math.sqrt(d.arts) * 2.2 + 9; }}));
    _graphSim = sim;
    var link = g.append("g").attr("stroke-opacity", 0.55).selectAll("line").data(links).join("line")
      .attr("stroke", "var(--border)").attr("stroke-width", function(d) {{ return Math.min(3, Math.sqrt(d.val) + 0.4); }});
    var node = g.append("g").selectAll("g").data(nodes).join("g").attr("cursor", "pointer");
    node.call(d3.drag()
      .on("start", function(e, d) {{ if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
      .on("drag",  function(e, d) {{ d.fx = e.x; d.fy = e.y; }})
      .on("end",   function(e, d) {{ if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }})
    );
    node.on("click", function(e, d) {{ openDrawer(d._r); }});
    node.append("circle")
      .attr("r", function(d) {{ return Math.sqrt(d.arts) * 2 + 7; }})
      .attr("fill", function(d) {{ return catPal[d.cat] || '#888'; }})
      .attr("opacity", 0.8).attr("stroke", "var(--card)").attr("stroke-width", 2);
    node.append("text")
      .text(function(d) {{ return d.id.length > 9 ? d.id.slice(0, 9) + '…' : d.id; }})
      .attr("dy", function(d) {{ return Math.sqrt(d.arts) * 2 + 18; }})
      .attr("text-anchor", "middle").attr("font-size", 9.5).attr("font-family", "inherit")
      .attr("fill", "var(--ink)").attr("pointer-events", "none");
    node.append("title").text(function(d) {{
      return d.id + ' ｜ 條文數：' + d.arts + ' ｜ 交叉引用：' + d.cross + ' 部（點擊節點開啟抽屜）';
    }});
    sim.on("tick", function() {{
      link.attr("x1", function(d) {{ return d.source.x; }}).attr("y1", function(d) {{ return d.source.y; }})
          .attr("x2", function(d) {{ return d.target.x; }}).attr("y2", function(d) {{ return d.target.y; }});
      node.attr("transform", function(d) {{ return "translate(" + d.x + "," + d.y + ")"; }});
    }});
  }}

  // ② 修訂時間軸
  function _renderTimeline(rows) {{
    var byYear = {{}};
    rows.forEach(function(r) {{
      var yr = r.date ? r.date.slice(0, 4) : '待確認';
      if (!byYear[yr]) byYear[yr] = [];
      byYear[yr].push(r);
    }});
    var years = Object.keys(byYear).sort(function(a, b) {{ return b.localeCompare(a); }});
    var html = years.map(function(yr) {{
      byYear[yr].sort(function(a, b) {{
        if (!a.date && !b.date) return 0;
        if (!a.date) return 1;
        if (!b.date) return -1;
        return b.date.localeCompare(a.date);
      }});
      var items = byYear[yr].map(function(r) {{
        var tierCls = r.tier || 'dir';
        var ridx = REGISTRY.indexOf(r);
        var pen = r.penalty_articles && r.penalty_articles.length
          ? '<span class="art-chip-penalty" style="font-size:9px;padding:1px 4px">罰則×' + r.penalty_articles.length + '</span>' : '';
        return '<div class="tl-item" data-ridx="' + ridx + '" onclick="var _r=REGISTRY[+this.dataset.ridx];if(_r)openDrawer(_r);">'
          + '<div class="tl-item-date">' + (r.date || '待確認') + '</div>'
          + '<div class="tl-item-name">' + r.name + '</div>'
          + '<div class="tl-item-meta"><span class="tier-tag ' + tierCls + '" style="font-size:9.5px">' + TIER_LABEL[tierCls] + '</span>' + pen + '</div>'
          + '</div>';
      }}).join('');
      return '<div class="tl-year-group">'
        + '<div class="tl-year-label">' + yr + '</div>'
        + '<div class="tl-items">' + items + '</div></div>';
    }}).join('');
    document.getElementById("lawTimeline").innerHTML = html || '<div style="padding:20px;color:var(--ink-soft)">無符合條件的法規。</div>';
  }}

  // ③ 罰則篩選切換
  function togglePenaltyFilter() {{
    penaltyFilter = !penaltyFilter;
    document.getElementById("penaltyFilterBtn").classList.toggle("active", penaltyFilter);
    registryPage = 1; renderRegistry();
  }}
  function toggleInspectFilter() {{
    inspectFilter = !inspectFilter;
    document.getElementById("inspectFocusBtn").classList.toggle("active", inspectFilter);
    registryPage = 1; renderRegistry();
  }}

  buildCatFilters();
  initTheme();
  initFontSize();
  applyView();
  renderBoardArea();
  renderMuteList();
  showSkeleton();
  setTimeout(function() {{
    renderNews();
    updateBadges();
    checkNewArticles();
    checkSubscriptionUpdates();
  }}, 300);
  renderRegistry();
  renderDirectives();
  renderScenarios();
</script>
</body>
</html>
"""


SUMMARY_CACHE = Path("news_summaries.json")


def fetch_article_text(url: str, session: requests.Session) -> str:
    """抓取新聞原文頁面並提取正文文字（最多 3000 字）"""
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = session.get(url, timeout=15, verify=False,
                           headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form",
                         "noscript", "iframe"]):
            tag.decompose()
        # 優先找主文區塊
        main = (soup.find("article")
                or soup.find("div", class_=re.compile(r"content|article|main|body|news", re.I))
                or soup.find("main")
                or soup)
        text = main.get_text(separator="\n", strip=True)
        # 過濾短行（連結文字、選單等）
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 15]
        return "\n".join(lines[:80])[:3000]
    except Exception as e:
        print(f"  無法抓取文章（{url[:60]}）：{e}", file=sys.stderr)
        return ""


def summarize_with_gemini(title: str, text: str, api_key: str) -> str:
    """呼叫 Gemini 產生 2-3 句繁體中文摘要，失敗時最多重試 3 次"""
    import time
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        f"/gemini-flash-lite-latest:generateContent?key={api_key}"
    )
    prompt = (
        "你是一位台灣職業安全衛生領域的專業摘要助理。\n"
        "以下是一篇台灣政府機關的新聞稿，請閱讀後用繁體中文完整寫出 2-3 句摘要。\n"
        "摘要需包含：主要政策措施、適用對象或範圍、重要數字或日期（如有）。\n"
        "重要：必須使用繁體中文，不得使用英文或簡體中文。只輸出摘要本身。\n\n"
        f"標題：{title}\n\n"
        f"內文：\n{text}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 500, "temperature": 0.1},
    }
    for attempt in range(3):
        try:
            resp = requests.post(endpoint, json=payload, timeout=40)
            if resp.status_code == 503:
                wait = 10 * (attempt + 1)
                print(f"  Gemini 服務忙碌，{wait}s 後重試…", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code == 429:
                wait = 60
                print(f"  Gemini 達到速率限制，等待 {wait}s…", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                print(f"  Gemini 無回應（可能被安全篩選器攔截）", file=sys.stderr)
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                print(f"  Gemini 回應無 parts（finishReason: {candidates[0].get('finishReason')}）", file=sys.stderr)
                return ""
            # 過濾掉 thinking model 的 thought/thoughtSignature 部分
            for part in parts:
                if (not part.get("thought") and not part.get("thoughtSignature")
                        and part.get("text")):
                    return part["text"].strip()
            return parts[-1].get("text", "").strip()
        except Exception as e:
            print(f"  Gemini 摘要失敗（第{attempt+1}次）：{e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(5)
    return ""


def load_cached_summaries(news: list[dict]) -> None:
    """從快取讀取已有的摘要並附加到 news（不呼叫任何 API）"""
    if not SUMMARY_CACHE.exists():
        return
    try:
        cache = json.loads(SUMMARY_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return
    for item in news:
        url = item.get("link", "")
        if url and url in cache:
            item["summary"] = cache[url]


def enrich_news_summaries(news: list[dict], api_key: str) -> None:
    """只對尚無摘要記錄的新文章呼叫 Gemini，更新快取檔"""
    cache: dict = {}
    if SUMMARY_CACHE.exists():
        try:
            cache = json.loads(SUMMARY_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    session = requests.Session()
    updated = False

    for item in news:
        url = item.get("link", "")
        if not url:
            continue
        if cache.get(url):  # 已有非空摘要 → 直接用
            item["summary"] = cache[url]
            continue
        import time as _time; _time.sleep(3)  # 避免連續呼叫觸發速率限制
        print(f"  AI摘要中：{item['title'][:45]}…")
        text = fetch_article_text(url, session)
        summary = summarize_with_gemini(item["title"], text, api_key) if text else ""
        if summary:  # 只存非空摘要，空的下次重試
            cache[url] = summary
            item["summary"] = summary
            updated = True
        else:
            print(f"  → 無法產生摘要（原文抓取失敗），下次重試。", file=sys.stderr)

    if updated:
        SUMMARY_CACHE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"摘要快取已更新：{SUMMARY_CACHE}（{len(cache)} 筆）。")
    else:
        print("  所有新聞均有快取，未呼叫 Gemini API。")


def build_html(news: list[dict], registry: list[dict], directives: list[dict], output_path: Path):
    from urllib.parse import quote as _urlq
    _base_urls = {"https://law.moj.gov.tw/", "https://law.moj.gov.tw"}

    def _fix_source(item: dict) -> dict:
        """將通用首頁 URL 轉成以法規名稱為關鍵字的搜尋連結。"""
        if item.get("source") in _base_urls:
            q = _urlq(item["name"], safe="")
            return {**item, "source": f"https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?ty=keyword&p=&q={q}"}
        return item

    now = datetime.now()
    html = HTML_TEMPLATE.format(
        generated_at=now.strftime("%Y-%m-%d %H:%M"),
        generated_date=now.strftime("%Y-%m-%d"),
        news_count=len(news),
        registry_count=len(registry),
        practitioner_html=PRACTITIONER_HTML,
        news_json=json.dumps(news, ensure_ascii=False),
        registry_json=json.dumps([_fix_source(r) for r in registry], ensure_ascii=False),
        directives_json=json.dumps([_fix_source(d) for d in directives], ensure_ascii=False),
        scenarios_json=json.dumps(QUICK_SCENARIOS, ensure_ascii=False),
        categories_json=json.dumps(CATEGORIES, ensure_ascii=False),
        checklists_json=json.dumps(CHECKLISTS, ensure_ascii=False),
        applicability_json=json.dumps(APPLICABILITY_DATA, ensure_ascii=False),
        inspection_focus_json=json.dumps(INSPECTION_FOCUS_LAWS, ensure_ascii=False),
        cns_iso_json=json.dumps(CNS_ISO_MAP, ensure_ascii=False),
    )
    output_path.write_text(html, encoding="utf-8")


# ============================================================
# main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="產生職安法規觀測站儀表板")
    parser.add_argument("--law-xml", type=Path, help="全國法規資料庫公開資料的 XML 檔路徑（選填）")
    parser.add_argument("--fetch-xml", action="store_true", help="自動從全國法規資料庫下載最新 XML（優先於 --law-xml）")
    parser.add_argument("-o", "--output", type=Path, default=Path("osh_dashboard.html"))
    parser.add_argument("--debug", action="store_true", help="印出每個新聞來源實際抓到的內容")
    parser.add_argument("--news-only", action="store_true", help="跳過法規 XML 解析，只更新新聞")
    parser.add_argument("--gemini-key", default="", help="Google Gemini API key（也可設定環境變數 GEMINI_API_KEY）")
    args = parser.parse_args()

    all_news = []
    for source in SOURCES:
        all_news.extend(fetch_source(source, debug=args.debug))
    news = dedupe_and_filter_news(all_news)
    print(f"新聞：去重、篩選後共 {len(news)} 則。")

    load_cached_summaries(news)  # 永遠讀快取，不呼叫 API

    gemini_key = args.gemini_key or os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        print("AI摘要：為尚無記錄的新文章呼叫 Gemini…")
        enrich_news_summaries(news, gemini_key)

    XML_CACHE = Path("osh_xml_records.json")

    registry = STATIC_REGISTRY
    if not args.news_only:
        if args.fetch_xml:
            all_xml_records: list[dict] = []
            any_ok = False
            for url, fname in LAW_XML_URLS:
                dest = Path(fname)
                if _download_law_xml(url, dest):
                    any_ok = True
                    all_xml_records.extend(parse_law_xml(dest))
                else:
                    print(f"  略過 {fname}，繼續下一個。", file=sys.stderr)
            if any_ok:
                # ① 自動更新偵測：比對舊快取的異動日期，列出有變動的法規
                if XML_CACHE.exists():
                    try:
                        _old = {r["name"]: r.get("最新異動日期_roc", "") for r in json.loads(XML_CACHE.read_text(encoding="utf-8"))}
                        _changed = [r["name"] for r in all_xml_records if _old.get(r["name"]) != r.get("最新異動日期_roc", "")]
                        _new_laws = [r["name"] for r in all_xml_records if r["name"] not in _old]
                        if _changed:
                            print(f"  ↑ 法規異動 {len(_changed)} 筆：{'、'.join(_changed[:6])}{'…' if len(_changed) > 6 else ''}")
                        if _new_laws:
                            print(f"  ★ 新增法規 {len(_new_laws)} 筆：{'、'.join(_new_laws[:4])}")
                        if not _changed and not _new_laws:
                            print("  法規：無異動，快取為最新狀態。")
                    except Exception:
                        pass

                registry = merge_registry(STATIC_REGISTRY, all_xml_records)
                confirmed = sum(1 for r in registry if r["date"])
                print(f"法規：XML 共解析 {len(all_xml_records)} 筆，合併後共 {len(registry)} 筆，已確認日期 {confirmed} 筆。")
                XML_CACHE.write_text(
                    json.dumps(all_xml_records, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                print(f"已更新法規快取 {XML_CACHE}（{len(all_xml_records)} 筆，{XML_CACHE.stat().st_size // 1024} KB）。")
            else:
                print("所有 XML 下載失敗，嘗試讀取本機法規快取…", file=sys.stderr)
                if XML_CACHE.exists():
                    all_xml_records = json.loads(XML_CACHE.read_text(encoding="utf-8"))
                    registry = merge_registry(STATIC_REGISTRY, all_xml_records)
                    confirmed = sum(1 for r in registry if r["date"])
                    print(f"法規：使用快取 {XML_CACHE}（{len(all_xml_records)} 筆），合併後共 {len(registry)} 筆，已確認日期 {confirmed} 筆。")
                elif args.law_xml and args.law_xml.exists():
                    xml_records = parse_law_xml(args.law_xml)
                    registry = merge_registry(STATIC_REGISTRY, xml_records)
                else:
                    print("無可用法規來源，法規區塊維持靜態清單。", file=sys.stderr)
        elif args.law_xml:
            if args.law_xml.exists():
                xml_records = parse_law_xml(args.law_xml)
                registry = merge_registry(STATIC_REGISTRY, xml_records)
                confirmed = sum(1 for r in registry if r["date"])
                print(f"法規：XML 解析出 {len(xml_records)} 筆，合併後共 {len(registry)} 筆，已確認日期 {confirmed} 筆。")
                XML_CACHE.write_text(
                    json.dumps(xml_records, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                print(f"已更新法規快取 {XML_CACHE}（{len(xml_records)} 筆）。")
            else:
                print(f"找不到 {args.law_xml}，法規區塊維持靜態清單。", file=sys.stderr)
        elif XML_CACHE.exists():
            # 自動讀取本機快取（不需任何旗標）
            all_xml_records = json.loads(XML_CACHE.read_text(encoding="utf-8"))
            registry = merge_registry(STATIC_REGISTRY, all_xml_records)
            confirmed = sum(1 for r in registry if r["date"])
            print(f"法規：自動使用快取 {XML_CACHE}（{len(all_xml_records)} 筆），合併後共 {len(registry)} 筆。")

    build_html(news, registry, STATIC_DIRECTIVES, args.output)
    print(f"已產生 {args.output}，用瀏覽器打開即可查看。")


if __name__ == "__main__":
    main()
