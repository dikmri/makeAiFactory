# makeAiFactory

> 画像を1枚ドラッグするだけで、ローカルPCがAI動画工場に変わる。

**makeAiFactory** は、AI画像を動画に変換するアプリケーションです。  
ComfyUI と Wan 2.2 モデルを自動でセットアップし、難しい設定なしに高品質なAI動画を生成できます。  
すべての処理はあなたのPC上で完結し、画像・動画が外部に送信されることはありません。

---

## 特徴

- **ドラッグ＆ドロップで即生成** — 画像を窓に落とすだけで動画生成が始まります
- **完全ローカル処理** — インターネット接続は初回セットアップのみ。生成データは外部送信されません
- **自動セットアップ** — Python 環境・ComfyUI・モデルの構築をアプリが自動で行います
- **インストール先を自由に選択** — Cドライブ以外のドライブにも対応（約 45 GB の空き容量が必要）
- **CUDA 自動選択** — GPU ドライバーを検出し cu128 / cu124 / cu121 を自動で切り替えます

## 動作環境

| 項目 | 要件 |
|------|------|
| OS | Windows 10 / 11 (64bit) |
| GPU | NVIDIA GPU（VRAM 12 GB 以上推奨） |
| ドライバー | NVIDIA ドライバー 520 以降 |
| ストレージ | 約 45 GB の空き容量 |
| インターネット | 初回セットアップ時のみ必要 |

> RTX 3060 / 4060 / 5060 Ti など幅広い NVIDIA GPU に対応しています。

## インストール

1. [Releases](../../releases/latest) ページから最新の `makeAiFactory-vX.X.X-windows.zip` をダウンロード
2. 任意のフォルダに解凍
3. `makeAiFactory.exe` を実行
4. インストール先フォルダを選択（例: `D:\makeAiFactory\runtime`）
5. 利用規約に同意してセットアップを開始

**初回セットアップは数時間かかります**（モデルのダウンロードが中心です）。  
セットアップが完了すると、次回からは数秒で起動します。

## 使い方

1. アプリを起動してセットアップが完了するまで待つ
2. AI 画像をアプリウィンドウにドラッグ＆ドロップ
3. 動画生成が自動で始まる（10〜20 分程度）
4. 生成完了後、プレビューがループ再生される
5. 「名前を付けて保存」で好きな場所に MP4 を保存

## インストール場所の変更

メニューバーの **設定 → インストール場所を変更...** から変更できます。  
変更後はアプリを再起動してください。

---

## 開発者向け

### 必要環境

- Python 3.13
- Git

### セットアップ

```bash
git clone https://github.com/dikmri/makeAiFactory.git
cd makeAiFactory
python -m venv .venv
.venv\Scripts\pip install pyinstaller PySide6 httpx websockets pydantic pillow
```

### EXE ビルド

```bash
python -m PyInstaller makeAiFactory.spec --noconfirm
```

ビルド成果物は `dist\makeAiFactory\` に出力されます。

### アイコン再生成

```bash
python tools\create_icon.py
```

### リリース

Git タグを作成してプッシュすると GitHub Actions が自動でビルド・リリースします。

```bash
git tag v0.2.0
git push origin v0.2.0
```

---

## 使用しているOSSライブラリ

| ライブラリ | ライセンス |
|-----------|-----------|
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | GPL-3.0 |
| [Wan 2.2 モデル](https://huggingface.co/Wan-AI) | Apache-2.0 |
| [PyTorch](https://pytorch.org/) | BSD-3-Clause |
| [PySide6](https://wiki.qt.io/Qt_for_Python) | LGPL-3.0 |
| [uv](https://github.com/astral-sh/uv) | MIT / Apache-2.0 |
| [VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite) | GPL-3.0 |

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) をご覧ください。

---

## 免責事項

- 生成コンテンツの利用・公開に関する責任はすべてユーザーに帰属します
- 実在する人物の同意なき性的コンテンツや、未成年を対象としたコンテンツの生成を禁じます
- 本アプリは「現状のまま」提供され、開発者は生成結果による損害に責任を負いません
