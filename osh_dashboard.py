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
import re
import sys
import urllib.robotparser
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
]
EXCLUDE_STATUS_KEYWORDS = ["廢止", "停止適用"]
USER_AGENT = "OSHDashboardBot/1.0 (+local personal use script)"
DATE_PATTERN = re.compile(r"(20\d{2})[.\-/年](\d{1,2})[.\-/月](\d{1,2})")

SOURCES = [
    # type="rss"：解析 RSS feed（結構穩定，不需要 BeautifulSoup）
    # type="html"：HTML 爬蟲（備用，需確認 robots.txt 允許）
    {"name": "勞動部新聞稿", "org": "勞動部", "url": "https://www.mol.gov.tw/1607/1632/1633/RssList", "type": "rss"},
    # {"name": "職安署新聞稿", "org": "勞動部職業安全衛生署", "url": "https://www.osha.gov.tw/...", "type": "rss"},
]

LAW_XML_URL = "https://sendlaw.moj.gov.tw/PublicData/GetFile.ashx?DType=XML&AuData=CF"

REG_SOURCE = "https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6"
REG_SOURCE_EN = "https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6"

STATIC_REGISTRY = [
    {"name": "職業安全衛生法", "tier": "act", "cat": "管理制度", "date": "2025-12-19", "source": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=N0060001", "note": "全國法規資料庫已驗證"},
    {"name": "職業安全衛生法施行細則", "tier": "reg", "cat": "管理制度", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "職業安全衛生設施規則", "tier": "reg", "cat": "作業環境", "date": "2026-06-30", "source": REG_SOURCE, "note": "全國法規資料庫鏡像尚顯示舊版，資料同步中"},
    {"name": "職業安全衛生管理辦法", "tier": "reg", "cat": "管理制度", "date": "2026-06-30", "source": REG_SOURCE, "note": "修正條文已見流通，正式生效日待官方公告確認"},
    {"name": "職業安全衛生教育訓練規則", "tier": "reg", "cat": "管理制度", "date": "2026-06-25", "source": REG_SOURCE_EN, "note": "附表一、附表二時數修正"},
    {"name": "勞工健康保護規則", "tier": "reg", "cat": "職業衛生", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "女性勞工母性健康保護實施辦法", "tier": "reg", "cat": "職業衛生", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "勞工作業環境監測實施辦法", "tier": "reg", "cat": "作業環境", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "職業安全衛生標示設置準則", "tier": "reg", "cat": "作業環境", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "妊娠與分娩後女性及未滿十八歲勞工禁止從事危險性或有害性工作認定標準", "tier": "reg", "cat": "職業衛生", "date": "2025-11-20", "source": REG_SOURCE, "note": None},
    {"name": "異常氣壓危害預防標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "高架作業勞工保護措施標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "高溫作業勞工作息時間標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "精密作業勞工視機能保護設施標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "重體力勞動作業勞工保護措施標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "高壓氣體勞工安全規則", "tier": "reg", "cat": "機械設備", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "缺氧症預防規則", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "營造安全衛生設施標準", "tier": "reg", "cat": "營造工程", "date": "2026-06-30", "source": REG_SOURCE, "note": None},
    {"name": "工程安全設計及整體工程統合管理辦法", "tier": "reg", "cat": "營造工程", "date": "2026-06-30", "source": REG_SOURCE, "note": "新訂定"},
    {"name": "林場安全衛生設施規則", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "船舶清艙解體勞工安全規則", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "碼頭裝卸安全衛生設施標準", "tier": "reg", "cat": "特殊作業", "date": "2025-09-10", "source": REG_SOURCE, "note": None},
    {"name": "礦場職業衛生設施標準", "tier": "reg", "cat": "特殊作業", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "鍋爐及壓力容器安全規則", "tier": "reg", "cat": "機械設備", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "起重升降機具安全規則", "tier": "reg", "cat": "機械設備", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "危險性機械及設備安全檢查規則", "tier": "reg", "cat": "機械設備", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "危害性化學品標示及通識規則", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "危害性化學品評估及分級管理辦法", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "特定化學物質危害預防標準", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "有機溶劑中毒預防規則", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "鉛中毒預防規則", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "粉塵危害預防標準", "tier": "reg", "cat": "化學品安全", "date": None, "source": REG_SOURCE, "note": None},
    {"name": "勞工作業場所容許暴露標準", "tier": "reg", "cat": "化學品安全", "date": "2025-04-11", "source": REG_SOURCE_EN, "note": None},
    {"name": "新化學物質登記管理辦法", "tier": "reg", "cat": "化學品安全", "date": "2025-08-08", "source": REG_SOURCE_EN, "note": "新訂定"},
    {"name": "優先管理化學品之指定及運作管理辦法", "tier": "reg", "cat": "化學品安全", "date": "2024-06-06", "source": REG_SOURCE_EN, "note": None},
    {"name": "職業災害預防及職業災害勞工重建補助辦法", "tier": "reg", "cat": "職業災害", "date": "2024-12-12", "source": REG_SOURCE_EN, "note": None},
    {"name": "職業傷病診治醫療機構認可管理補助及職業傷病通報辦法", "tier": "reg", "cat": "職業災害", "date": "2024-11-18", "source": REG_SOURCE_EN, "note": None},
    {"name": "職業災害勞工職能復健專業機構認可管理及補助辦法", "tier": "reg", "cat": "職業災害", "date": "2024-01-30", "source": REG_SOURCE_EN, "note": None},
]

STATIC_DIRECTIVES = [
    {"name": "勞動部補助授權勞動檢查機構督促事業單位遵守職業安全衛生法令計畫", "tier": "dir", "date": "2026-07-07", "source": "https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6", "note": None},
    {"name": "適用職業安全衛生法部分規定之事業範圍", "tier": "notice", "date": "2026-07-01", "source": "https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6", "note": None},
    {"name": "職業安全衛生法第24條第1項規定之其他特定機械之種類及應具之容量", "tier": "notice", "date": "2026-07-01", "source": "https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6", "note": None},
    {"name": "勞動檢查機構執行職業安全衛生法第46條第2項講習實施要點", "tier": "dir", "date": "2026-07-01", "source": "https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6", "note": "訂定"},
    {"name": "職業安全衛生管理系統績效審查及績效良好選拔作業要點", "tier": "dir", "date": "2026-07-01", "source": "https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6", "note": "修正並改名"},
    {"name": "違反職業安全衛生法及勞動檢查法案件處理要點", "tier": "dir", "date": "2026-06-30", "source": "https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6", "note": None},
]

TIER_LABEL = {"act": "法律", "reg": "法規命令", "dir": "行政規則", "notice": "公告"}

# 法規分類（決定顯示順序）
CATEGORIES = ["管理制度", "作業環境", "職業衛生", "化學品安全", "機械設備", "特殊作業", "營造工程", "職業災害"]

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
      <div class="link"><a href="https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6" target="_blank" rel="noopener">https://law.moj.gov.tw/LawClass/LawSearchResult.aspx?p=N&t=A1A2E3F6</a></div>
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
    """解析 RSS 2.0 feed，不需要 robots.txt 檢查（RSS 設計上供訂閱使用）。"""
    name, org, url = source["name"], source["org"], source["url"]
    print(f"處理 RSS 來源：{name}（{url}）")

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
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
    for item in root.iter("item"):
        title = _text(item.find("title"))
        link_el = item.find("link")
        link = _text(link_el) if link_el is not None else ""
        if not link and link_el is not None:
            link = link_el.get("href", "")
        pub_date = _text(item.find("pubDate"))
        description = _text(item.find("description"))

        if not title or not link:
            continue

        date_str = parse_rss_date(pub_date) or extract_date(description) or extract_date(title)
        if not date_str:
            continue

        if debug:
            print(f"    RSS item: {date_str}  {title[:50]}")

        items.append({"title": title.strip(), "link": link.strip(), "date": date_str, "source": name, "org": org})

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


def roc_to_iso(roc_str: str) -> str | None:
    digits = re.sub(r"\D", "", roc_str or "")
    if len(digits) not in (6, 7):
        return None
    if len(digits) == 6:
        y, m, d = int(digits[:2]) + 1911, int(digits[2:4]), int(digits[4:6])
    else:
        y, m, d = int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7])
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _download_law_xml(dest: Path) -> bool:
    """從全國法規資料庫下載職安相關 XML（ZIP 格式），解壓後存至 dest。"""
    import zipfile, io
    print(f"下載法規 XML：{LAW_XML_URL}")
    try:
        resp = requests.get(LAW_XML_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  下載失敗：{e}", file=sys.stderr)
        return False

    content_type = resp.headers.get("Content-Type", "")
    raw = resp.content

    # 伺服器回應可能是 ZIP 或純 XML
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

    for law in root.iter("法規"):
        name = _text(law.find("法規名稱"))
        law_type = _text(law.find("法規類別"))
        law_url = _text(law.find("法規網址"))
        if not name or any(k in law_type for k in EXCLUDE_STATUS_KEYWORDS):
            continue
        if not any(k in name for k in OSH_KEYWORDS):
            continue

        records.append({
            "name": name,
            "法規性質": _text(law.find("法規性質")),
            "法規類別": law_type,
            "最新異動日期_roc": _text(law.find("最新異動日期")),
            "生效日期_roc": _text(law.find("生效日期")),
            "沿革摘要": _text(law.find("沿革內容"))[:200],
            "英文法規名稱": _text(law.find("英文法規名稱")),
            "法規網址": law_url,
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
                entry["note"] = rec["沿革摘要"][:60]
        else:
            merged.append({
                "name": rec["name"],
                "tier": "reg",
                "cat": "其他",
                "date": iso_date,
                "source": rec["法規網址"] or REG_SOURCE,
                "note": "XML 新增，未在原始清單中",
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
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@700;900&family=Noto+Sans+TC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --paper:#F4EFE2; --card:#FBF8EF; --ink:#1E2B3A; --ink-soft:#4C5A6B;
    --stamp:#9C2B22; --brass:#A8842E; --border:rgba(30,43,58,0.14);
    --green:#1a7340; --amber:#8c5e00;
  }}
  *{{ box-sizing:border-box; margin:0; padding:0; }}
  body{{ font-family:"Noto Sans TC",-apple-system,sans-serif; background:var(--paper); color:var(--ink); line-height:1.6; }}
  .wrap{{ max-width:960px; margin:0 auto; padding:32px 20px 90px; }}
  header{{ border-bottom:2px solid var(--border); padding-bottom:18px; margin-bottom:20px; }}
  header h1{{ font-family:"Noto Serif TC",serif; font-weight:900; font-size:26px; margin-bottom:4px; }}
  .subtitle{{ font-size:13px; color:var(--ink-soft); margin-bottom:6px; }}
  .meta{{ font-size:12px; color:var(--ink-soft); }}
  nav.tabs{{ display:flex; gap:2px; margin-bottom:24px; border-bottom:2px solid var(--border); flex-wrap:wrap; }}
  nav.tabs button{{ font-size:14px; padding:10px 18px; border:none; background:transparent; cursor:pointer;
    color:var(--ink-soft); border-bottom:3px solid transparent; margin-bottom:-2px; transition:color .15s; }}
  nav.tabs button.active{{ color:var(--stamp); border-bottom-color:var(--stamp); font-weight:700; }}
  nav.tabs button:hover:not(.active){{ color:var(--ink); }}
  .tabpanel{{ display:none; }}
  .tabpanel.active{{ display:block; }}
  h2{{ font-family:"Noto Serif TC",serif; font-size:20px; margin-bottom:6px; }}
  h3{{ font-family:"Noto Serif TC",serif; font-size:16px; font-weight:700; margin:26px 0 10px; }}
  .section-note{{ color:var(--ink-soft); font-size:13.5px; line-height:1.75; margin-bottom:18px; }}
  .search{{ width:100%; padding:8px 10px; border:1px solid var(--border); font-size:14px;
    background:var(--card); color:var(--ink); margin-bottom:10px; }}
  .controls{{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }}
  .controls button{{ font-size:13px; padding:5px 12px; border:1px solid var(--border);
    background:transparent; cursor:pointer; color:var(--ink); }}
  .controls button.active{{ border-color:var(--stamp); color:var(--stamp); background:rgba(156,43,34,.06); font-weight:600; }}
  .item{{ background:var(--card); border:1px solid var(--border); border-left:3px solid var(--brass);
    padding:14px 16px; margin-bottom:10px; }}
  .item.read{{ opacity:.55; }}
  .item .row{{ display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; }}
  .item .date{{ font-size:12px; color:var(--ink-soft); white-space:nowrap; }}
  .item h3{{ margin:4px 0 6px; font-size:15px; font-family:"Noto Sans TC",sans-serif; font-weight:600; }}
  .item h3 a{{ color:var(--ink); text-decoration:none; }}
  .item h3 a:hover{{ color:var(--stamp); text-decoration:underline; }}
  .item .src{{ font-size:12px; color:var(--ink-soft); }}
  .item .actions{{ display:flex; gap:8px; margin-top:8px; }}
  .item .actions button{{ font-size:12px; padding:3px 9px; border:1px solid var(--border); background:transparent; cursor:pointer; }}
  .item .actions button.on{{ border-color:var(--stamp); color:var(--stamp); }}
  .also{{ font-size:11px; color:var(--ink-soft); margin-top:4px; }}
  .empty-state{{ text-align:center; padding:56px 20px; color:var(--ink-soft); }}
  .empty-state .icon{{ font-size:36px; display:block; margin-bottom:14px; }}
  .empty-state p{{ font-size:13.5px; line-height:1.8; max-width:400px; margin:0 auto; }}
  .reg-toolbar{{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; align-items:center; }}
  .reg-toolbar input{{ flex:1; min-width:180px; padding:7px 10px; border:1px solid var(--border);
    font-size:14px; background:var(--card); color:var(--ink); }}
  .cat-filters{{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; }}
  .cat-btn{{ font-size:12px; padding:4px 12px; border:1px solid var(--border); background:var(--card);
    cursor:pointer; color:var(--ink-soft); border-radius:99px; white-space:nowrap; }}
  .cat-btn.active{{ background:var(--stamp); color:#fff; border-color:var(--stamp); font-weight:600; }}
  .reg-controls{{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; align-items:center; }}
  .reg-controls .label{{ font-size:12px; color:var(--ink-soft); margin-right:2px; }}
  .reg-controls button{{ font-size:12.5px; padding:4px 11px; border:1px solid var(--border);
    background:transparent; cursor:pointer; color:var(--ink); }}
  .reg-controls button.active{{ border-color:var(--stamp); color:var(--stamp); background:rgba(156,43,34,.06); font-weight:600; }}
  .reg-controls .sep{{ width:1px; height:20px; background:var(--border); margin:0 4px; align-self:center; }}
  .registry-meta{{ font-size:12.5px; color:var(--ink-soft); margin-bottom:10px; }}
  .table-scroll{{ overflow-x:auto; border:1px solid var(--border); margin-bottom:8px; }}
  table.registry-table{{ width:100%; border-collapse:collapse; font-size:13.5px; min-width:560px; }}
  .registry-table th{{ text-align:left; font-weight:600; font-size:11.5px; color:var(--ink-soft);
    padding:9px 12px; background:var(--card); border-bottom:1px solid var(--border); white-space:nowrap; }}
  .registry-table td{{ padding:8px 12px; border-bottom:1px solid var(--border); vertical-align:middle; }}
  .registry-table tr:last-child td{{ border-bottom:none; }}
  .registry-table tr:hover td{{ background:rgba(168,132,46,.05); }}
  .rname{{ font-weight:500; }}
  .rdate{{ white-space:nowrap; font-size:13px; }}
  .rdate.unconfirmed{{ color:var(--ink-soft); font-style:italic; }}
  .tier-tag{{ display:inline-block; font-size:11px; padding:1px 7px; white-space:nowrap; border:1px solid currentColor; }}
  .tier-tag.act{{ color:var(--stamp); }}
  .tier-tag.reg{{ color:var(--brass); }}
  .tier-tag.dir,.tier-tag.notice{{ color:var(--ink-soft); }}
  .cat-tag{{ display:inline-block; font-size:11px; padding:1px 7px; border:1px solid var(--border);
    color:var(--ink-soft); background:rgba(0,0,0,.03); }}
  .badge{{ display:inline-block; font-size:10px; padding:1px 5px; border-radius:2px; margin-left:5px;
    vertical-align:middle; white-space:nowrap; line-height:1.5; }}
  .badge.recent{{ background:#fff3cd; color:var(--amber); border:1px solid #ffc107; }}
  .badge.new-law{{ background:#d1fae5; color:var(--green); border:1px solid #6ee7b7; }}
  .star-btn{{ background:none; border:none; cursor:pointer; font-size:15px; padding:0 3px;
    color:#ccc; line-height:1; vertical-align:middle; }}
  .star-btn.on{{ color:#e6a817; }}
  .table-footnote{{ font-size:12.5px; color:var(--ink-soft); line-height:1.75; margin:4px 0 22px; }}
  .reg-subhead{{ font-family:"Noto Serif TC",serif; font-size:16px; font-weight:700; margin:26px 0 10px; }}
  .law-card{{ background:var(--card); border:1px solid var(--border); padding:18px; margin-bottom:8px; }}
  .law-card dl{{ display:grid; grid-template-columns:110px 1fr; gap:8px 12px; margin:0; font-size:14px; }}
  .law-card dt{{ color:var(--ink-soft); font-size:13px; }}
  .law-card dd{{ margin:0; line-height:1.65; }}
  .stamp-badge{{ display:inline-block; margin-top:12px; font-size:12px; color:var(--stamp);
    border:1px solid var(--stamp); padding:3px 10px; }}
  .stamp-badge a{{ color:inherit; text-decoration:none; }}
  .source-grid{{ display:grid; gap:12px; }}
  .source-card{{ background:var(--card); border:1px solid var(--border); border-left:3px solid var(--stamp); padding:14px 16px; }}
  .source-card .org{{ font-weight:600; font-size:14px; }}
  .source-card .role{{ font-size:12.5px; color:var(--ink-soft); margin:2px 0; }}
  .source-card .link{{ font-size:12px; margin-top:4px; }}
  .source-card .link a{{ color:var(--stamp); word-break:break-all; }}
  .scenario-intro{{ color:var(--ink-soft); font-size:13.5px; line-height:1.75; margin-bottom:18px; }}
  .scenario-grid{{ display:grid; grid-template-columns:repeat(auto-fill,minmax(270px,1fr)); gap:14px; }}
  .scenario-card{{ background:var(--card); border:1px solid var(--border); border-top:3px solid var(--brass); padding:16px; }}
  .sc-head{{ display:flex; align-items:center; gap:8px; margin-bottom:6px; }}
  .sc-icon{{ font-size:20px; line-height:1; }}
  .sc-title{{ font-family:"Noto Serif TC",serif; font-size:15px; font-weight:700; }}
  .sc-desc{{ font-size:12.5px; color:var(--ink-soft); line-height:1.65; margin-bottom:10px; }}
  .scenario-laws{{ list-style:none; }}
  .scenario-laws li{{ margin-bottom:5px; }}
  .law-link{{ color:var(--stamp); text-decoration:none; font-size:13px;
    cursor:pointer; background:none; border:none; padding:0; text-align:left; font-family:inherit; }}
  .law-link:hover{{ text-decoration:underline; }}
  footer{{ margin-top:34px; padding-top:16px; border-top:1px solid var(--border);
    font-size:12px; color:var(--ink-soft); line-height:1.8; }}
  @media(max-width:640px){{
    header h1{{ font-size:21px; }}
    nav.tabs button{{ font-size:13px; padding:8px 12px; }}
    .scenario-grid{{ grid-template-columns:1fr; }}
    .law-card dl{{ grid-template-columns:1fr; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>職安法規觀測站</h1>
    <div class="subtitle">職業安全衛生法規查詢 · 最新動態 · 情境索引</div>
    <div class="meta">資料產生時間：{generated_at} ｜ {news_count} 則職安動態 ｜ {registry_count} 筆子法規 ｜ 收藏與讀取狀態僅存於本機瀏覽器</div>
  </header>

  <nav class="tabs">
    <button data-tab="news" class="active">最新動態</button>
    <button data-tab="registry">現行法規總覽</button>
    <button data-tab="lookup">快速情境查詢</button>
    <button data-tab="practitioner">職安人員實用區</button>
  </nav>

  <section class="tabpanel active" id="tab-news">
    <h2>最新動態</h2>
    <p class="section-note">從勞動部官方新聞稿擷取職安相關公告，自動去重後依日期排序。</p>
    <input class="search" id="newsSearch" placeholder="搜尋標題關鍵字…">
    <div class="controls">
      <button data-filter="all" class="active">全部</button>
      <button data-filter="unread">未讀</button>
      <button data-filter="starred">已收藏</button>
    </div>
    <div id="newsList"></div>
  </section>

  <section class="tabpanel" id="tab-registry">
    <h2>現行法規總覽</h2>
    <p class="section-note">職業安全衛生母法及附屬法規命令。<span style="color:var(--amber);font-weight:600">近期修正</span>標示為近12個月內有修正紀錄；<span style="color:var(--green);font-weight:600">新訂</span>為新制定法規；「待確認」表示尚未取得官方驗證日期。</p>
    <div class="reg-toolbar">
      <input id="lawSearch" placeholder="搜尋法規名稱…" autocomplete="off">
    </div>
    <div class="cat-filters" id="catFilters"></div>
    <div class="reg-controls">
      <span class="label">篩選：</span>
      <button data-lawfilter="all" class="active">全部</button>
      <button data-lawfilter="starred">已收藏</button>
      <button data-lawfilter="recent">近期修正</button>
      <div class="sep"></div>
      <span class="label">排序：</span>
      <button data-sort="cat" class="active">依分類</button>
      <button data-sort="date">依修正日期</button>
      <button data-sort="name">依名稱</button>
    </div>
    <div class="registry-meta" id="registryMeta"></div>
    <div class="table-scroll">
      <table class="registry-table">
        <thead><tr><th></th><th>法規名稱</th><th>分類</th><th>位階</th><th>最新修正日期</th><th>資料來源</th></tr></thead>
        <tbody id="registryBody"></tbody>
      </table>
    </div>
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
    <p class="scenario-intro">依作業類型或管理情境快速定位相關法規。點選法規名稱即可跳至「現行法規總覽」查看詳情與官方連結。</p>
    <div class="scenario-grid" id="scenarioGrid"></div>
  </section>

  <section class="tabpanel" id="tab-practitioner">
    {practitioner_html}
  </section>

  <footer>
    新聞日期為官方發布日，不代表法規正式生效日，請點連結查閱官方原文核實。本頁不會自動更新，重新執行 osh_dashboard.py 可取得最新資料。
  </footer>
</div>

<script>
  const NEWS = {news_json};
  const REGISTRY = {registry_json};
  const DIRECTIVES = {directives_json};
  const SCENARIOS = {scenarios_json};
  const CATEGORIES = {categories_json};
  const TIER_LABEL = {{act:"法律",reg:"法規命令",dir:"行政規則",notice:"公告"}};
  const STORE_KEY = "osh_state_v2";
  let state = JSON.parse(localStorage.getItem(STORE_KEY) || '{{"read":{{}},"starred":{{}},"starredLaws":{{}}}}');
  if (!state.starredLaws) state.starredLaws = {{}};

  let newsFilter = "all", lawFilter = "all", catFilter = "all", sortMode = "cat";

  function save() {{ localStorage.setItem(STORE_KEY, JSON.stringify(state)); }}
  function idOf(item) {{ return item.date + "|" + item.title; }}

  const GENERATED = new Date("{generated_date}");
  const cutoff12m = new Date(GENERATED);
  cutoff12m.setMonth(cutoff12m.getMonth() - 12);
  function isRecent(d) {{ return !!d && new Date(d) >= cutoff12m; }}
  function isNewLaw(r) {{ return !!(r.note && r.note.includes("新訂定")); }}

  function renderNews() {{
    const q = document.getElementById("newsSearch").value.trim();
    const filtered = NEWS.filter(item => {{
      const id = idOf(item);
      if (newsFilter === "unread" && state.read[id]) return false;
      if (newsFilter === "starred" && !state.starred[id]) return false;
      if (q && !item.title.includes(q)) return false;
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
      const also = (item.also_in && item.also_in.length)
        ? '<div class="also">同時見於：' + item.also_in.join("、") + '</div>' : "";
      el.innerHTML =
        '<div class="row"><span class="src">' + item.org + ' · ' + item.source + '</span><span class="date">' + item.date + '</span></div>' +
        '<h3><a href="' + item.link + '" target="_blank" rel="noopener">' + item.title + '</a></h3>' +
        also +
        '<div class="actions">' +
          '<button data-act="read">' + (state.read[id] ? "已讀" : "標為已讀") + '</button>' +
          '<button data-act="star" class="' + (state.starred[id] ? "on" : "") + '">' + (state.starred[id] ? "★ 已收藏" : "☆ 收藏") + '</button>' +
        '</div>';
      el.querySelector('[data-act="read"]').onclick = () => {{ state.read[id] = !state.read[id]; save(); renderNews(); }};
      el.querySelector('[data-act="star"]').onclick = () => {{ state.starred[id] = !state.starred[id]; save(); renderNews(); }};
      list.appendChild(el);
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
        renderRegistry();
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
    if (lawFilter === "recent") rows = rows.filter(r => isRecent(r.date));
    const q = document.getElementById("lawSearch").value.trim();
    if (q) rows = rows.filter(r => r.name.includes(q));

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

    const confirmed = rows.filter(r => r.date).length;
    document.getElementById("registryMeta").textContent =
      "顯示 " + rows.length + " 筆（共 " + REGISTRY.length + " 筆），已確認修正日期 " + confirmed + " 筆";

    document.getElementById("registryBody").innerHTML = rows.map(r => {{
      const recent = isRecent(r.date) && !isNewLaw(r);
      const isnew = isNewLaw(r);
      const badge = isnew ? '<span class="badge new-law">新訂</span>'
                  : recent ? '<span class="badge recent">近期修正</span>' : "";
      const dateText = r.date || "待確認";
      const dateCls = r.date ? "rdate" : "rdate unconfirmed";
      const noteTxt = (r.note && !isnew) ? '<br><small style="color:var(--ink-soft);font-size:11px">' + r.note + '</small>' : "";
      const starred = state.starredLaws[r.name];
      return '<tr>' +
        '<td><button class="star-btn' + (starred ? ' on' : '') + '" data-law="' + r.name.replace(/"/g, '&quot;') + '" title="收藏">' + (starred ? '★' : '☆') + '</button></td>' +
        '<td class="rname">' + r.name + badge + '</td>' +
        '<td><span class="cat-tag">' + (r.cat || '') + '</span></td>' +
        '<td><span class="tier-tag ' + r.tier + '">' + TIER_LABEL[r.tier] + '</span></td>' +
        '<td class="' + dateCls + '">' + dateText + noteTxt + '</td>' +
        '<td><a href="' + r.source + '" target="_blank" rel="noopener">查看 ↗</a></td>' +
        '</tr>';
    }}).join("");

    document.querySelectorAll(".star-btn").forEach(btn => {{
      btn.onclick = () => {{
        const name = btn.dataset.law;
        if (state.starredLaws[name]) delete state.starredLaws[name];
        else state.starredLaws[name] = true;
        save(); renderRegistry();
      }};
    }});
  }}

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
    document.querySelectorAll("nav.tabs button").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tabpanel").forEach(p => p.classList.remove("active"));
    document.querySelector('[data-tab="registry"]').classList.add("active");
    document.getElementById("tab-registry").classList.add("active");
    catFilter = "all"; lawFilter = "all"; sortMode = "cat";
    document.querySelectorAll(".cat-btn").forEach((b, i) => b.classList.toggle("active", i === 0));
    document.querySelectorAll("[data-lawfilter]").forEach(b => b.classList.toggle("active", b.dataset.lawfilter === "all"));
    document.querySelectorAll("[data-sort]").forEach(b => b.classList.toggle("active", b.dataset.sort === "cat"));
    document.getElementById("lawSearch").value = name;
    renderRegistry();
    window.scrollTo({{top: 0, behavior: "smooth"}});
  }}

  function renderScenarios() {{
    const grid = document.getElementById("scenarioGrid");
    SCENARIOS.forEach(s => {{
      const card = document.createElement("div");
      card.className = "scenario-card";
      const lawItems = s.laws.map(l =>
        '<li><button class="law-link" data-law="' + l.replace(/"/g, '&quot;') + '" onclick="jumpToLaw(this.dataset.law)">→ ' + l + '</button></li>'
      ).join("");
      card.innerHTML =
        '<div class="sc-head"><span class="sc-icon">' + s.icon + '</span><span class="sc-title">' + s.name + '</span></div>' +
        '<div class="sc-desc">' + s.desc + '</div>' +
        '<ul class="scenario-laws">' + lawItems + '</ul>';
      grid.appendChild(card);
    }});
  }}

  document.querySelectorAll("[data-filter]").forEach(btn => {{
    btn.onclick = () => {{
      document.querySelectorAll("[data-filter]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active"); newsFilter = btn.dataset.filter; renderNews();
    }};
  }});
  document.querySelectorAll("[data-lawfilter]").forEach(btn => {{
    btn.onclick = () => {{
      document.querySelectorAll("[data-lawfilter]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active"); lawFilter = btn.dataset.lawfilter; renderRegistry();
    }};
  }});
  document.querySelectorAll("[data-sort]").forEach(btn => {{
    btn.onclick = () => {{
      sortMode = btn.dataset.sort;
      document.querySelectorAll("[data-sort]").forEach(b => b.classList.toggle("active", b.dataset.sort === sortMode));
      renderRegistry();
    }};
  }});
  document.getElementById("newsSearch").addEventListener("input", renderNews);
  document.getElementById("lawSearch").addEventListener("input", renderRegistry);
  document.querySelectorAll("nav.tabs button").forEach(btn => {{
    btn.onclick = () => {{
      document.querySelectorAll("nav.tabs button").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tabpanel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    }};
  }});

  buildCatFilters();
  renderNews();
  renderRegistry();
  renderDirectives();
  renderScenarios();
</script>
</body>
</html>
"""


def build_html(news: list[dict], registry: list[dict], directives: list[dict], output_path: Path):
    now = datetime.now()
    html = HTML_TEMPLATE.format(
        generated_at=now.strftime("%Y-%m-%d %H:%M"),
        generated_date=now.strftime("%Y-%m-%d"),
        news_count=len(news),
        registry_count=len(registry),
        practitioner_html=PRACTITIONER_HTML,
        news_json=json.dumps(news, ensure_ascii=False),
        registry_json=json.dumps(registry, ensure_ascii=False),
        directives_json=json.dumps(directives, ensure_ascii=False),
        scenarios_json=json.dumps(QUICK_SCENARIOS, ensure_ascii=False),
        categories_json=json.dumps(CATEGORIES, ensure_ascii=False),
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
    args = parser.parse_args()

    all_news = []
    for source in SOURCES:
        all_news.extend(fetch_source(source, debug=args.debug))
    news = dedupe_and_filter_news(all_news)
    print(f"新聞：去重、篩選後共 {len(news)} 則。")

    registry = STATIC_REGISTRY
    if not args.news_only:
        xml_path: Path | None = None

        if args.fetch_xml:
            auto_xml = Path("law_data_auto.xml")
            if _download_law_xml(auto_xml):
                xml_path = auto_xml
            else:
                print("自動下載失敗，改用 --law-xml 或靜態清單。", file=sys.stderr)
                xml_path = args.law_xml if args.law_xml and args.law_xml.exists() else None
        elif args.law_xml:
            xml_path = args.law_xml if args.law_xml.exists() else None
            if xml_path is None:
                print(f"找不到 {args.law_xml}，法規區塊維持靜態清單。", file=sys.stderr)

        if xml_path:
            xml_records = parse_law_xml(xml_path)
            registry = merge_registry(STATIC_REGISTRY, xml_records)
            confirmed = sum(1 for r in registry if r["date"])
            print(f"法規：XML 解析出 {len(xml_records)} 筆，合併後共 {len(registry)} 筆，已確認日期 {confirmed} 筆。")

    build_html(news, registry, STATIC_DIRECTIVES, args.output)
    print(f"已產生 {args.output}，用瀏覽器打開即可查看。")


if __name__ == "__main__":
    main()
