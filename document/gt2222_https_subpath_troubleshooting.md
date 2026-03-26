# GT-2222 HTTPSサブパス公開 トラブルシュートナレッジ

## 目的

`https://gt-2222/meetingminutesnotebook/` で本アプリを安定公開するまでの試行錯誤を記録し、同じハマりを再発させないための運用ナレッジを残す。

---

## 最終的に成立した構成

- 公開URL: `https://gt-2222/meetingminutesnotebook/`
- ホストNginx (Ubuntu): 443終端、`/meetingminutesnotebook/` を `http://127.0.0.1:8085/meetingminutesnotebook/` へリバースプロキシ
- フロントコンテナNginx: サブパスプレフィックスを剥がして `/index.html` と `/assets` に解決
- フロントビルド変数:
  - `VITE_BASE_PATH=/meetingminutesnotebook/`
  - `VITE_API_BASE=/meetingminutesnotebook`
- CORS:
  - `MM_CORS_ORIGINS` に `https://gt-2222` を含める

---

## 主要なハマりどころと原因

## 1) DNS/FQDN問題 (`genetec.local`)

### 症状

- `gt-2222.genetec.local` が名前解決できない
- `gt-2222` 単体は名前解決できる

### 原因

- 社内DNSに `genetec.local` 側のレコードが存在しない

### 対応

- URL/設定を `gt-2222` ベースに統一
- `MM_CORS_ORIGINS` も `https://gt-2222` を使う

---

## 2) 443の `server_name` 重複

### 症状

- `nginx -T` で `conflicting server name "gt-2222" on 0.0.0.0:443, ignored`
- 設定を入れたはずなのに 404 が続く

### 原因

- `server_name gt-2222` を持つ `server` ブロックが複数あり、片方が無視される
- `location /meetingminutesnotebook/` を「無視される側」に書いていた

### 対応

- 443の `gt-2222` は実質1系統に整理
- 実際に使われる `server` ブロックに `location /meetingminutesnotebook/` を追加

---

## 3) サブパス配信時の静的ファイル404

### 症状

- ブラウザが真っ白
- DevToolsで `index-*.js`, `index-*.css`, `favicon.svg` が 404
- `curl http://127.0.0.1:8085/meetingminutesnotebook/` が 404

### 原因 (本件の本丸)

- `frontend/nginx.conf` のサブパス実装が不整合
- 以前の設定は `/meetingminutesnotebook/index.html` を探す前提だったが、Viteの出力は通常 `dist/index.html` と `dist/assets/`（サブディレクトリを作らない）

### 対応

- `frontend/nginx.conf` で `/meetingminutesnotebook/` を受けたら rewrite でプレフィックスを剥がす方式に変更

例:

```nginx
location ^~ /meetingminutesnotebook/ {
    rewrite ^/meetingminutesnotebook/(.*)$ /$1 break;
    try_files $uri $uri/ /index.html;
}
```

---

## 4) Dockerビルド時に `VITE_BASE_PATH` が効かない

### 症状

- `docker compose config` で `VITE_BASE_PATH: /`、`VITE_API_BASE: ""`
- コンテナ内 `/usr/share/nginx/html/meetingminutesnotebook` が存在しない

### 原因

- `docker-compose.yml` 参照の `.env` が不足/空
- `frontend/vite.config.ts` が `loadEnv` のみ参照していたため、Docker build args/env が反映されにくいケースがあった

### 対応

1. `vite.config.ts` で `process.env.VITE_BASE_PATH` を優先
2. `.env` に `VITE_BASE_PATH`, `VITE_API_BASE` を明示
3. `frontend` を `--no-cache` で再ビルド

---

## 5) 自己署名証明書で「セキュリティ保護なし」

### 症状

- URLは開けるがブラウザ表示が `セキュリティ保護なし`（アドレスバーで `https` に取り消し線）

### 切り分け（サーバが正しいか）

```bash
echo | openssl s_client -connect 127.0.0.1:443 -servername gt-2222 2>/dev/null | openssl x509 -noout -issuer -subject -ext subjectAltName
```

**悪い例（ルートCAを信頼しても直らない）**

- `issuer=CN = gt-2222` かつ `subject=CN = gt-2222`
- `No extensions in certificate`（SAN なし）

→ ホストNginxが **`rootCA` で署名したサーバ証明書ではなく**、別の **自己署名サーバ証明書**（例: `/etc/ssl/certs/selfsigned.crt`）を出している。  
→ Windows に `rootCA.crt` を入れても **鎖がつながらない**。

**良い例（サーバ設定OK・あとはクライアント信頼）**

- `issuer` に **`CN = Local Root CA`**（作成した `rootCA.crt` の subject と一致）
- `subject` に **`CN = gt-2222`**
- `X509v3 Subject Alternative Name` に **`DNS:gt-2222`**

### 原因A: ホストNginxの証明書パスが誤り

- `ssl_certificate` に **`gt-2222.crt`（CA署名）** と **`gt-2222.key`** を指定する
- **`rootCA.crt` を `ssl_certificate` に指定しない**（配布用。サーバにはサーバ証明書）

### 原因B: サーバは正しいが Windows がルートを信頼していない

- インポートウィザードで **「証明書の種類に基づいて自動的にストアを選択」** だけにすると、**ルートCAが「信頼されたルート証明機関」に入らない**ことが多い
- **「証明書をすべて次のストアに配置する」→ 参照 → 「信頼されたルート証明機関」** を明示する
- 保存場所は **ローカル コンピューター**（管理者）推奨
- インポートするのは **`rootCA.crt` のみ**（`gt-2222.crt` をルートストアに入れても鎖の信頼にはならない）
- インポート後は **Edge を完全終了**してから再アクセス

### 同一 `server` 内の他 `location` への影響

- `ssl_certificate` は **`server { }` 単位**で効く。`server_name GT-2222` のブロックで `/jupyter/` と `/meetingminutesnotebook/` を両方扱う場合、**証明書はどちらのパスでも同じ**になる（通常は問題にならない）

### 対応まとめ

1. 証明書ファイルを `/etc/nginx/ssl/gt-2222/` 等に配置し、443 の `ssl_certificate` / `ssl_certificate_key` を差し替え
2. `sudo nginx -t && sudo systemctl reload nginx`
3. 上記 `openssl` で issuer/SAN を確認
4. 各クライアントに `rootCA.crt` を **信頼されたルート証明機関**へ手動指定でインポート
5. `rootCA.key` は絶対に配布しない

---

## 今回の修正ファイル（要点）

- `frontend/nginx.conf`
  - サブパス配信時の rewrite + fallback を正規化
- `frontend/vite.config.ts`
  - `process.env.VITE_BASE_PATH` 優先
- `frontend/index.html`
  - favicon を `%BASE_URL%favicon.svg` に変更
- `scripts/tar-scp.sh`
  - `.env` 非存在時の警告追加
  - `TAR_SCP_SET_ENV=gt2222` で `config/gt-2222.env` を `.env` に反映可能
- `scripts/server-rebuild.sh` / `scripts/server-rebuild.bat`
  - 既定で `frontend` を `--no-cache` ビルド

---

## 再発防止チェックリスト（デプロイ時）

1. `docker compose config | grep -A12 'frontend:'` で以下を確認
   - `VITE_BASE_PATH: /meetingminutesnotebook/`
   - `VITE_API_BASE: /meetingminutesnotebook`
2. サーバへ反映後、`docker compose build frontend --no-cache`
3. コンテナ内確認
   - `curl -sI http://127.0.0.1:8085/meetingminutesnotebook/` が 200
4. ホストNginx経由確認
   - `curl -skI --resolve gt-2222:443:127.0.0.1 https://gt-2222/meetingminutesnotebook/` が 200
5. ブラウザ確認
   - `https://gt-2222/meetingminutesnotebook/`
   - 必要なら Ctrl+F5（ハードリロード）
   - **ブラウザ通知**を使う場合: 画面上部やアドレスバー付近で **通知の許可** を求めるポップアップ／バナーが出たら **許可**する（拒否のままだと完了通知が届かない）。サイト設定で通知がブロックされていないかも確認する
6. TLS（ルートCA運用時）
   - サーバ: 上記 `openssl` で `issuer=Local Root CA` かつ `DNS:gt-2222` を確認
   - クライアント: `rootCA.crt` を **信頼されたルート証明機関**（手動ストア指定）に入れたうえで Edge 再起動

---

## 代表コマンド集

### 設定値確認

```bash
docker compose config | grep -A12 'frontend:'
```

### フロント再ビルド

```bash
docker compose build frontend --no-cache
docker compose up -d
```

### 経路疎通確認

```bash
curl -sI http://127.0.0.1:8085/meetingminutesnotebook/
curl -skI --resolve gt-2222:443:127.0.0.1 https://gt-2222/meetingminutesnotebook/
```

### 443で提示している証明書の確認

```bash
echo | openssl s_client -connect 127.0.0.1:443 -servername gt-2222 2>/dev/null | openssl x509 -noout -issuer -subject -ext subjectAltName
```

### tar-scp運用（GT-2222向け）

```bash
TAR_SCP_SET_ENV=gt2222 ./scripts/tar-scp.sh
```

---

## 運用メモ

- `docker compose` が読む `.env` は **composeファイルと同じディレクトリの `.env` のみ**
- `.env` は通常 `.gitignore` されるため、転送スクリプトで明示対応しないと欠落しやすい
- `nginx -T` は「いま実際に読み込まれている設定」を確認できるため、重複競合の切り分けに有効
- HTTPSの信頼問題（証明書）は、アプリ404とは分けて切り分ける
- ブラウザの「保護なし」は **サーバ証明書の中身**と **クライアントのルート信頼**の二段で切り分ける（`openssl` でサーバ側を先に確定させる）
- **デスクトップ通知**はブラウザの仕様上、**安全なコンテキスト（信頼できる HTTPS 等）**で使いやすい。通知を利用するユーザーには **許可ダイアログで「許可」**してもらう運用とする
