# infra-kit

各項目共用的工程元件：部署、追蹤、反向代理這類每個項目都會重做的東西。沒有業務程式。

本 repo 公開。客戶的主機位址、主機名、網域、帳號與任何密鑰都不得放進來；範例一律用 `192.0.2.0/24`、`example.org` 這類保留值。pre-commit 擋常見的幾種。

## 收什麼

一個元件一個目錄。項目會 `include`、執行、或在 CI 跑的才放這裡；只給人讀的約定與理由由協調維護，不放本 repo。

| 目錄 | 元件 | 狀態 |
|---|---|---|
| `otel/` | OTel 追蹤：Collector 設定、nginx 模組片段、Python 零程式注入包法、串接檢查 | v0.1 |
| `ansible-facts/` | 用 ansible 部署的項目輸出 `部署事實.json`（給驗收文件）：驗證紀錄 callback、連線展開 filter、轉接器 | 只有規格 |

預計收進來的：OTel 追蹤（各語言的零程式注入包法、Collector 設定與 ansible role、串接測試）、nginx 片段、離線安裝 Docker 的 ansible playbook。

## 版本

整個 repo 一個版本號，照 semver 打 tag（`v0.1.0`）。項目引用時指定 tag，不跟 main。

- 任何一個元件有不相容的改動，升主版號
- main 上的改動要等打 tag 才生效。打 tag 由協調決定

## 引用

各元件的引用方式寫在該目錄的 README。
