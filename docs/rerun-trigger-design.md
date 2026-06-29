# 錯誤通知信「重新執行」功能設計

## 需求背景

當 Import 腳本執行失敗時，系統會發送錯誤通知信給相關人員。  
目前收到信後，需回到辦公室或 VPN 才能手動重跑，無法即時處理。

**核心需求**：在錯誤通知信中加入按鈕，讓 User 在放假時能直接從**手機**觸發重新執行。

---

## 使用情境

- User 人在外（假日、出差、下班後）
- 以手機收到錯誤通知信
- 希望點一下就能重跑，不需 VPN、不需開電腦
- 有時放假不只一天，需要補跑 **d+N** 天的匯率資料（N 最多預估 5 天）

---

## 技術限制

Email 中的「按鈕」本質只能是超連結（`<a href>`），無法直接執行程式。  
點擊後必須透過外部機制來觸發腳本。

---

## 建議方案：`mailto:` 按鈕 + 信箱監控腳本

### 為何選此方案

| 考量 | 說明 |
|------|------|
| 手機友善 | 點按鈕 → 開郵件 App（已預填）→ 送出，共 2 步 |
| 不需 VPN | 走公司 email，從外部亦可操作 |
| 天然身份驗證 | 只有有公司信箱帳號的人才能觸發 |
| 不需開 port | 無需將 Server 暴露至外網 |
| 低技術風險 | 使用既有 email 基礎設施 |

### 流程說明

```
[手機收到錯誤信]
      ↓
[點「重新執行 d+N」按鈕]
      ↓
[手機郵件 App 開啟，subject/body 已預填]
      ↓
[User 確認送出]
      ↓
[Server 端：check_rerun_inbox.py 每 N 分鐘掃描信箱]
      ↓
[偵測到符合格式的信件 → 解析腳本名稱與天數 offset]
      ↓
[subprocess 執行對應腳本，帶入 --offset 參數]
      ↓
[執行完成後，回信通知觸發者結果]
```

### Email 按鈕設計（多顆預設按鈕）

錯誤信中提供多顆預設按鈕，每顆對應不同天數，點一下即送出，不需手動輸入：

```
[ 重跑 d+1 ]  [ 重跑 d+2 ]  [ 重跑 d+3 ]  [ 重跑 d+4 ]  [ 重跑 d+5 ]
```

Subject 格式：`RERUN:<腳本名>:<offset>`  
範例：`RERUN:ImportDailyRate:3`

### 信件格式規格

| 欄位 | 格式 | 範例 |
|------|------|------|
| To | 專用信箱（待定） | `exchange-rate-bot@sercomm.com` |
| Subject | `RERUN:<script>:<days>` | `RERUN:ImportDailyRate:2` |
| Body | 任意（不解析） | — |
| 驗證方式 | 寄件人必須在白名單內 | 從 `email_config.yaml` 取得 |

---

## 需修改的程式

### 1. 各 Import 腳本：支援 `--offset` 參數

目前 `conversion_date` 在 `ImportDailyRate.py` 寫死為 `today + 1 day`，  
需改為可接受命令列參數：

```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--offset', type=int, default=1,
                    help='conversion_date = today + offset days')
args = parser.parse_args()
```

影響範圍：
- `ImportDailyRate.py` — `conversion_date` 計算
- `ImportWebRate.py` — `conversion_date` 計算
- `ImportPortalRate.py` — 視實際邏輯決定是否需要

### 2. 錯誤通知信：加入重跑按鈕區塊

修改 `email_body.html` template 或在 `send_email_notification()` 中，  
當 `mailtype == "error"` 時，在信件 body 末端注入按鈕 HTML。

傳入的 `script_name` 參數用於組成正確的 mailto subject。

### 3. 新增：`check_rerun_inbox.py`

監控信箱的獨立腳本，功能：
- 讀取信箱（IMAP 或 Exchange API）
- 過濾符合 `RERUN:*` subject 格式的信件
- 驗證寄件人白名單
- 呼叫對應腳本（帶 `--offset`）
- 將已處理信件標記已讀或移至子資料夾
- 執行完成後回信通知

透過 **Windows Task Scheduler** 每 5 分鐘執行一次。

---

## 待確認事項

- [ ] **專用信箱**：使用現有信箱（如 IT 信箱）還是新開一個？
- [ ] **Mail Server 類型**：Microsoft 365 (Exchange Online) 還是傳統 IMAP？這決定讀信的 API 方式
- [ ] **適用腳本範圍**：`ImportDailyRate`、`ImportWebRate`、`ImportPortalRate` 全部要支援，還是只有部分？
- [ ] **觸發者白名單**：誰可以觸發重跑？從現有 `receiver_emails_failure` 清單取得？
- [ ] **最大 offset 天數**：按鈕預設做到 d+5，是否足夠？
- [ ] **重複觸發保護**：同一天若多次觸發同一腳本，是否需要防呆？
- [ ] **執行結果通知**：回信給觸發者即可，還是也要通知原錯誤信的所有收件人？

---

## 未採用方案說明

| 方案 | 放棄原因 |
|------|---------|
| 內網 Flask server | 外部（手機）無法存取，需 VPN |
| Flask + Cloudflare Tunnel | 將 Server 暴露至外網，安全疑慮較高 |
| Power Automate / Logic Apps | 需 M365 授權與 IT 配合，建置成本高 |
| Telegram Bot | 需另裝 App，導入成本高；公司政策未必允許 |
