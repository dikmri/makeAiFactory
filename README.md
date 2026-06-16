# makeAiFactory

<p align="center">
  <img src="assets/icon.png" width="128" height="128" alt="makeAiFactory icon">
</p>

> 画像をドラッグ＆ドロップするだけで、ローカルPCがAI動画工場に変わる。

**makeAiFactory** は、AI画像を動画に変換するアプリケーションです。  
ComfyUI と Wan 2.2 モデルを自動でセットアップし、難しい設定なしに高品質なAI動画を生成できます。  
すべての処理はあなたのPC上で完結し、画像・動画が外部に送信されることはありません。

---

## 特徴

- **ドラッグ＆ドロップ／クリップボード貼り付けで即生成** — 画像をウィンドウにドロップ、または `Ctrl+V` で貼り付けるだけで動画生成が始まります
- **フォルダ一括生成** — フォルダを指定して複数枚の画像をまとめて動画化（途中キャンセルも可能）
- **完全ローカル処理** — インターネット接続は初回セットアップのみ。生成データは外部送信されません
- **自動セットアップ** — Python 環境・ComfyUI・モデルの構築をアプリが自動で行います
- **インストール先を自由に選択** — Cドライブ以外のドライブにも対応
- **モデルプリセット切り替え** — 通常 / 軽量 / 超軽量の3段階からお使いのPCスペックに合わせて選択
- **VRAMモード切り替え** — VRAMが少ない環境向けの省VRAMモードを搭載
- **高速化 (SageAttention)** — 対応環境では生成を高速化するオプションを ON/OFF 可能
- **CUDA 自動選択** — GPU ドライバーを検出し cu128 / cu124 / cu121 / cu118 を自動で切り替えます
- **完成時の自動保存・通知音** — 完成した動画を指定フォルダへ自動保存、完成時に通知音を再生（音量調整可）
- **常に最前面表示モード** — ウィンドウが他のウィンドウの裏に隠れないように固定可能
- **自動更新** — 新バージョンを自動検出してダウンロード・適用します

## 動作環境

| 項目 | 要件 |
|------|------|
| OS | Windows 10 / 11 (64bit) |
| GPU | NVIDIA GPU 必須（他社GPU・内蔵GPUのみの環境は非対応） |
| VRAM | 8 GB 以上（16 GB 以上推奨。8〜16GB の場合は省VRAMモードの利用を推奨） |
| RAM | 24 GB 以上（プリセットにより異なる。下表参照） |
| ストレージ | 約 55 GB 以上の空き容量（モデルプリセットにより変動） |
| GPUドライバー | 最新版を推奨（古いドライバーでも CUDA バージョンを自動判別し対応します） |
| インターネット | 初回セットアップ時のみ必要 |

モデルプリセット別の推奨スペック:

| プリセット | 品質 | VRAM目安 | RAM目安 |
|-----------|------|----------|---------|
| 通常モード | 最高品質 | ~14 GB | ~48 GB+ |
| 軽量モード | 高品質 | ~9 GB | ~32 GB+ |
| 超軽量モード | 標準品質 | ~8 GB | ~24 GB+ |

> RTX 3060 / 4060 / 5060 Ti など幅広い NVIDIA GPU に対応しています。VRAMが少ない環境では「軽量モード」「超軽量モード」や省VRAMモードの利用をおすすめします。

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
2. 画像をアプリウィンドウにドラッグ＆ドロップ（または `Ctrl+V` で貼り付け）
3. 動画生成が自動で始まる（数分〜20分程度、PCスペックや設定により変動）
4. 生成完了後、プレビューがループ再生される
5. 「名前を付けて保存」で好きな場所に MP4 を保存

複数枚をまとめて処理したい場合は、フォルダ指定での一括生成も利用できます。

## 設定メニュー

メニューバーの **設定** から、以下の項目を変更できます。

- インストール場所の変更（変更後はアプリの再起動が必要）
- 自動保存先フォルダの設定・有効化
- 常に最前面に表示
- モデルプリセット（通常 / 軽量 / 超軽量）
- VRAMモード（通常 / 超省VRAM）
- 高速化 (SageAttention) の有効化
- 完成通知音（鳴らす/鳴らさない、フォルダ生成時、音量）

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
.venv\Scripts\pip install pyinstaller PySide6 httpx websockets pydantic aiofiles pillow hatchling
.venv\Scripts\pip install . --no-deps
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

`assets\icon.ico` と `assets\icon.png`（README用）が生成されます。

### リリース

Git タグを作成してプッシュすると GitHub Actions が自動でビルド・リリースします。

```bash
git tag v0.6.0
git push origin v0.6.0
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
