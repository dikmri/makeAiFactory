<div align="center">

# ⚡🔥 makeImg 🌈🎮

### ～ ドパガキ大喜び超絶ゲーミングAI画像生成アプリ ～～

**ComfyUI搭載 · ローカルPCで爆速txt2img · ワンクリック起動**

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white&style=for-the-badge)
![PySide6](https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white&style=for-the-badge)
![License](https://img.shields.io/badge/License-ICKK-black?style=for-the-badge)

</div>

---

## 🌟 これ何？

**makeImg** は、ComfyUIをバックエンドに隠蔽した**ワンクリック起動・ワンクリック生成**のAI画像生成アプリです。

- 🔥 **起動するだけ** → モデルDLからComfyUI起動まで全自動
- ⚡ **ボタン1つで生成** → 1回 / X回 / ∞回モード搭載
- 🎮 **特殊スキン搭載** → ネオンカラーHSLグラデーションが踊り狂うゲーミングUI
- 🔊 **タイプライターSE** → キー入力毎にカチッ、{はチャリン、|はブーン
- 🎯 **プロンプトハイライト** → `()` `[]` `{}` `:0.5` `|` 色分け表示
- 🎲 **ランダム選択構文** → `{red,|blue,|green,}` で毎回ランダムに選ばれる
- 💾 **Ctrl+Sで即保存** → チャララーンSE付きプリセット上書き保存
- 📊 **リアルタイムシステム監視** → タイトルバーにCPU/RAM/GPU/VRAM表示

---

## 🚀 使い方

```bash
python makeimg_launcher.py
```

これだけ。初回起動時に必要なものを全部自動で入れます。

### 2回目以降

起動するだけ。ComfyUIも自動起動。

### テキスト生成

1. プロンプトを入力（ハイライト付きエディタ）
2. **1回生成** / **5回生成** / **∞回生成** をクリック
3. 画像が出る。おしまい。

---

## 🎮 特殊スキン

ヘルプ → ⚡特殊スキン⚡ をONにすると……

| 通常モード | 特殊スキン |
|-----------|-----------|
| まともなUI | グラデーション爆発 |
| 静かな画面 | HSLアニメーション常時稼働 |
| 普通のタイトルバー | CPU/GPU/VRAMリアルタイム監視 |
| 何もなし | キー入力毎にカチッチャリンブーン |
| 退屈な保存 | Ctrl+Sでチャララーン ♨️ |

---

## ✍️ プロンプト構文

| 構文 | 効果 | ハイライト色 |
|------|------|-------------|
| `(word)` | 強調 | 🔵 水色 |
| `(word:1.5)` | 強調 + 重み | 🔵 水色 + 🟡 黄色 |
| `[word]` | 弱化 | 🟠 オレンジ |
| `{word}` | 強調(variant) | 🟣 紫 |
| `{red\|blue\|green}` | ランダム選択 | 🟣 紫 + 🔴 \| |
| `# コメント` | コメント行 | ⬛ グレー |
| `// コメント` | コメント行 | ⬛ グレー |
| `,` | トークン区切り | ⬛ グレー |

### ランダム選択例

```
{1girl,|2girls,|3girls,}, {blonde,|black,|red,} hair
```

生成のたびに `{...|...}` の中からランダムで1つ選ばれます。`|` なしの `{word}` は通常の強調として動作します。

---

## ⌨️ ショートカットキー

| キー | 動作 |
|------|------|
| `Ctrl+S` | プリセット上書き保存（チャララーンSE付き） |

---

## 📁 出力ファイル名パターン

ファイル → ファイル名パターン... で変更可能：

| 変数 | 内容 | 例 |
|------|------|-----|
| `{timestamp}` | 日時 | `20260618_143052` |
| `{seed}` | SEED値 | `3688324904` |
| `{job_id}` | ジョブID | `abc123` |

デフォルト: `{timestamp}_{seed}`

---

## 🔧 設定項目

- **VRAMモード** → 通常 / 超省VRAM
- **完成通知音** → ON/OFF + 音量 + 連続生成時（毎回/完了時のみ）
- **自動保存** → 完了時に指定フォルダへコピー
- **常に最前面** → 画像生成中に裏に行かない
- **SEED値** → ランダム / 固定切替
- **ワークフロー** → 画像用 / 動画用（予定）

---

## 🛠️ 技術スタック

- **バックエンド**: ComfyUI (WebSocket通信でリアルタイム進捗取得)
- **フロントエンド**: PySide6 (Qt6)
- **画像生成**: Stable Diffusion ( anyModel / miaomiaoRealskin )
- **言語**: Python 3.12+
- **パッケージ管理**: uv

---

## 📂 プロジェクト構成

```
makeImg/
├── makeimg_launcher.py          # 起動スクリプト
├── pyproject.toml               # 依存関係
├── app/
│   └── manifest/                # モデル・ノード・ランタイム定義
├── devs/
│   └── 画像用_master_api.json   # ComfyUIワークフロー
└── src/makeimg/
    ├── app.py                   # アプリ本体・シグナル接続
    ├── comfy/
    │   ├── api_client.py        # ComfyUI API + WebSocket通信
    │   ├── workflow_patcher.py  # ワークフロー動的パッチ
    │   ├── progress_tracker.py  # 進捗トラッキング
    │   └── output_resolver.py   # 出力画像解決
    ├── core/
    │   ├── app_controller.py    # セットアップ制御
    │   ├── job_controller.py    # 生成ジョブ制御
    │   ├── settings_store.py    # 設定永続化
    │   └── paths.py             # パス管理
    ├── gui/
    │   ├── main_window.py       # メインウィンドウ(ゲーミングUI含む)
    │   ├── loading_clock.py     # ローディング時計演出
    │   ├── prompt_highlighter.py # プロンプトシンタックスハイライト
    │   └── se_generator.py      # SE音声プログラム生成
    └── runtime/
        ├── model_installer.py   # モデル自動DL
        ├── comfy_installer.py   # ComfyUI自動セットアップ
        └── system_probe.py      # システム情報取得
```

---

## ⚠️ 要件

- **OS**: Windows 10/11
- **GPU**: NVIDIA GPU (VRAM 8GB+推奨、6GBでもnovramモードで動作)
- **RAM**: 16GB+推奨
- **Python**: 3.12+
- **ディスク**: 10GB+の空き容量（モデルDL用）

---

<div align="center">

**⚡ ドパガキ大喜びで画像生成しよう 🔥**

</div>