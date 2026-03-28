# GreenBoost 運用・メンテナンス手順書（別冊）

**対象:** Ubuntu 20.04 系（カーネル 5.15 HWE 等）＋ NVIDIA GPU ＋ **Docker で Ollama** 運用を想定したメモ。  
**目的:** 設定場所と確認コマンドを一覧化し、担当者が交代しても追えるようにする。

---

## 1. 用語と全体像（概要）

| 用語 | 意味 |
|------|------|
| **T1** | GPU 物理 VRAM（ホット層） |
| **T2** | システム RAM を GPU から触れる経路に載せるプール（コールド層） |
| **T3** | NVMe スワップ側のオーバーフロー（設定次第で 0 も可） |
| **カーネルモジュール** | `greenboost.ko` — `/dev/greenboost`、sysfs の `pool_info` |
| **CUDA シム** | `libgreenboost_cuda.so` — `LD_PRELOAD` で Ollama のメモリ確保をフック |

**Docker 利用時:** シムはコンテナ内で有効。カーネル **`pool_info` の `T2 allocated` が常に 0** でも、**コンテナ経路（Path B）ではカーネル集計に乗らない**ことがある。異常と断定しない。

---

## 2. 主要パス一覧

| 種別 | パス |
|------|------|
| ソース（例） | `/home/<USER>/greenboost/nvidia_greenboost/` |
| カーネルモジュール（インストール後） | `/lib/modules/$(uname -r)/extra/greenboost.ko` |
| CUDA シム | `/usr/local/lib/libgreenboost_cuda.so` |
| modprobe 設定 | `/etc/modprobe.d/greenboost.conf` |
| プール情報（sysfs） | `/sys/class/greenboost/greenboost/pool_info` |
| 診断ログ | `/var/log/greenboost/diagnose-latest.log` |
| Ollama Docker 構成（例） | `/home/<USER>/ollama-server/docker-compose.yaml` |

---

## 3. 環境差分と対応（ビルド・ソース）

GreenBoost の upstream ソースは、**想定する Linux カーネル版**と**想定するユーザー空間（ヘッダ・glibc）**に依存する。実機の **カーネルが古い**、または **Ubuntu 20.04 などヘッダが異なる**と、そのままではビルドや実行時に不整合が出る。差分の種類ごとに対応を分ける。

### 3.1 Linux カーネル API の差への対応

**対象:** 例として **5.15 系**（HWE 含む）と、より新しいカーネル（5.18 以降、6.x など）で変わった API。

| 差分の例 | 対応の考え方 |
|----------|----------------|
| `dma_buf` のマッピング API（`dma_buf_map` と `iosys_map` など） | カーネル版で分岐（`LINUX_VERSION_CODE` 等） |
| `pin_user_pages` の引数数 | 6.5 未満では第 5 引数を `NULL` にする等 |
| `class_create` / `eventfd_signal` のシグネチャ | 版ごとの推奨形に合わせる |

**このリポジトリ:** `meeting-minutes-generator/meeting-minutes-generator/greenboost.c` に上記を反映した版を置いてある。NVIDIA 配布ツリーへ上書きしてビルドする。

### 3.2 ユーザー空間（システムヘッダ・ディストリ）の差への対応

**対象:** 例として **Ubuntu 20.04**。`sys/mman.h` に **`MAP_HUGE_2MB` が定義されない**ため、シム単体のコンパイルが失敗することがある。

| 差分の例 | 対応の考え方 |
|----------|----------------|
| 巨大ページ関連マクロの欠如 | `#include <sys/mman.h>` 直後に、`MAP_HUGE_SHIFT` / `MAP_HUGE_2MB` の **`#ifndef` 互換定義**を置く |

**このリポジトリ:** `meeting-minutes-generator/meeting-minutes-generator/greenboost_cuda_shim.c` に上記を反映した版を置いてある。

### 3.3 運用上の位置づけとサーバーへの反映

- **カーネル版を上げる**だけでは解消しない差分（ヘッダ由来のビルドエラー）と、**カーネル API 差分**は別軸。両方あり得るため、エラーメッセージとファイル名で **3.1 と 3.2 のどちらに当たるか**を切り分ける。
- **このリポジトリ内の参照パス**（ワークスペース直下から）:
  - `meeting-minutes-generator/meeting-minutes-generator/greenboost.c`
  - `meeting-minutes-generator/meeting-minutes-generator/greenboost_cuda_shim.c`
- NVIDIA 配布ツリーの同名ファイルへ上書きしたうえで、**4.2** の手順どおり `make && sudo make install` する。

---

## 4. インストール手順

### 4.1 `full-install` を使う場合

- **先頭で purge が走る**ため、カスタムした `greenboost.conf` や手元の `.so` が**消える**前提で使う。
- **`cpu-perf.service` の `systemctl enable --now`** で**止まって見える**ことがある → 下記「9.1」参照。
- ビルド前に **3 章**のとおり、必要なら **パッチ済み `greenboost.c` / `greenboost_cuda_shim.c`** を NVIDIA ツリーへ取り込む。

### 4.2 モジュール・シムのみ再ビルド（`full-install` を使わない）

purge を避けたい場合や、ソースだけ差し替えて入れ直す場合の例。

```bash
docker stop ollama-server 2>/dev/null || true
cd ~/greenboost/nvidia_greenboost
make clean && make
sudo make install
sudo ldconfig
sudo modprobe -r greenboost 2>/dev/null || true
sudo modprobe greenboost   # または 5 章の環境変数付き load
```

`modprobe.d` の `options` 行は **5 章** または **`sudo ./greenboost_setup.sh load` 成功後に生成された `greenboost.conf` をコピー**して固定する。

---

## 5. カーネルパラメータ（Tier サイズ）

### 5.1 意味

| パラメータ（例） | 意味 |
|------------------|------|
| `physical_vram_gb` | T1（論理上の GPU VRAM 上限）。**整数のみ**（5.5 は不可）。 |
| `virtual_vram_gb` | T2 プール上限（GB） |
| `nvme_swap_gb` / `nvme_pool_gb` | T3。無効にするなら **両方 0** |
| `safety_reserve_gb` | **空き RAM（freeram ベース）**と比較する安全枠。厳しすぎると T2 が使えない |

### 5.2 再ロード例（値は運用に合わせて変更）

```bash
docker stop ollama-server
cd ~/greenboost/nvidia_greenboost
sudo GPU_PHYS_GB=6 \
  VIRT_VRAM_GB=20 \
  NVME_SWAP_GB=0 NVME_POOL_GB=0 \
  RESERVE_GB=6 \
  ./greenboost_setup.sh load
```

**永続化:** 上記と同じ内容を `/etc/modprobe.d/greenboost.conf` の `options greenboost ...` 1 行に反映する。

### 5.3 `pool_info` の確認

```bash
watch -n1 'cat /sys/class/greenboost/greenboost/pool_info'
```

**注意:** `Free RAM` は **`free` コマンドの `available` と一致しない**ことが多い（カーネルは **MemFree 系**に近い値を見る）。`available` は大きいのに OOM guard が出る場合は **キャッシュ**や **reserve** の影響。

---

## 6. Docker（Ollama）側の設定

### 6.1 `docker-compose.yaml` で最低限必要なもの

- **`LD_PRELOAD=/usr/local/lib/libgreenboost_cuda.so`**
- **`GREENBOOST_ACTIVE=1`**（Ollama は `dlopen` するため必須級）
- **ホストの `libgreenboost_cuda.so` をコンテナに read-only マウント**
- **GPU 渡し:** `deploy.resources.reservations.devices`（nvidia）または `gpus: all`
- **`NVIDIA_VISIBLE_DEVICES`:** `all` または `0`。**`void` は使わない**

### 6.2 よく触る環境変数

| 変数 | 例 | 意味 |
|------|-----|------|
| `GREENBOOST_VRAM_HEADROOM_MB` | `3072` | VRAM 空きがこの MB 未満に近づくと、**大きい確保を RAM 側に回しやすくする**目安（実装依存のしきい値）。 |
| `GREENBOOST_DEBUG` | `0` / `1` | `1` でシムの stderr ログが増える。 |

### 6.3 `/dev/greenboost`

- **ホストにデバイスが無い**とき `devices:` を書くと **コンテナ起動失敗**する。
- **無ければ `devices` ブロックを書かない。** Path B のみで動くことが多い。

### 6.4 反映手順

```bash
cd ~/ollama-server
docker compose up -d --force-recreate
```

---

## 7. 動作確認コマンド（実行例）

### 7.1 モジュール

```bash
lsmod | grep greenboost
ls -l /dev/greenboost
cat /sys/class/greenboost/greenboost/pool_info
```

### 7.2 コンテナ内シム（拡張 VRAM 表示）

```bash
docker exec ollama-server nvidia-smi --query-gpu=memory.total,memory.used --format=csv
```

**`memory.total` が 6144 MiB ではなく大きい** → コンテナ内ではシムの NVML フックが効いているサイン。

### 7.3 環境変数（PID1 と runner）

```bash
docker exec ollama-server sh -c 'tr "\0" "\n" < /proc/1/environ | grep -E "LD_PRELOAD|GREENBOOST"'
docker exec ollama-server sh -c 'R=$(pgrep -f "ollama runner" | head -1); tr "\0" "\n" < /proc/$R/environ | grep -E "LD_PRELOAD|GREENBOOST"'
```

### 7.4 負荷時

```bash
docker stats ollama-server --no-stream
docker exec ollama-server ollama ps
```

### 7.5 診断スクリプト

```bash
cd ~/greenboost/nvidia_greenboost
sudo ./greenboost_setup.sh diagnose
```

**Docker 運用では「Ollama systemd が無い」「LD_PRELOAD がホストに無い」が [FAIL] になりやすい。** コンテナの 7.2〜7.3 が通っていれば **その FAIL は無視してよい**ことが多い。

---

## 8. `GREENBOOST_VRAM_HEADROOM_MB` の変更

1. `~/ollama-server/docker-compose.yaml` の `environment` で値を変更（例: `"2048"`）。
2. `docker compose up -d --force-recreate`
3. 7.3 で runner に新値が付いたか確認。

---

## 9. トラブルシュート

### 9.1 `full-install` が `cpu-perf` の直後で止まる

- **原因:** `systemctl enable --now` が **認証待ち**でブロックすることがある。
- **対処:** `greenboost_setup.sh` 内の **`enable --now` を `enable` のみ**に変更し、**実コンソール（Ctrl+Alt+F3）または `ssh -t`** で `sudo systemctl start cpu-perf.service`。**不要ならサービス無効化**も可。

### 9.2 `apt update` / `command-not-found` で `apt_pkg` エラー

- **原因:** ディストリ既定の **`python3` の版**と、**`python3-apt` がリンクしている CPython の版**がずれていると `import apt_pkg` が失敗する（例: `python3` が 3.10 なのに `python3-apt` が 3.8 向け `.so` のみ）。
- **対処:** `sudo update-alternatives --config python3` で **apt と整合する版を優先**する、またはシステム用と開発用の Python を分離する。

### 9.3 コンテナ起動: `/dev/greenboost` が無い

- **対処:** compose から **`devices:` を削除**。

### 9.4 `MAP_HUGE_2MB` ビルドエラー（シム）

- **原因:** **3.2**（ユーザー空間・ヘッダの差）に該当。`sys/mman.h` にマクロが無い環境で発生しうる。
- **対処:** **3.2** のとおりパッチ済み `greenboost_cuda_shim.c` をリポジトリから取り込み、**3.3** の手順で再ビルドする。

### 9.5 メモリがきつい

- `free -h` の **`Swap` 使用**を確認。
- **`virtual_vram_gb` / `RESERVE_GB`** の見直し、または **モデル・コンテキストの縮小**。

---

## 10. 変更履歴（運用で記入）

| 日付 | 変更内容 | 担当 |
|------|----------|------|
|      |          |      |

---

*この手順書は運用実態に合わせて随時更新すること。*
