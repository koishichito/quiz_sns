# Keycloak 最小構成セットアップ（HTTP・摩擦ゼロ版）

「とにかく動く」を最優先にした動作確認用の手順。**HTTP only（TLSなし）/ PKCEなし /
メール確認なし / firewalld停止 / SELinux permissive / Keycloak は 8080 直公開 /
フロントは素のJS** という構成にしている。

> [!WARNING]
> **この構成は動作確認専用で、本番のセキュリティ要件は満たさない。**
> TLS・PKCE・firewall・SELinux enforcing・メール確認を戻す手順は、別ファイル
> `keycloak-centos10-3tier-setup.md` の §8 チェックリストにある。
> 進め方は「まず動かす → あとで固める」の二段構え。

## 前提・記法

- web層IP = `WEB_IP`、DB層IP = `DB_IP`、API層IP = `API_IP`
- **唯一の注意点：`http://WEB_IP:8080` という値を全箇所で同一表記にする。**
  IPとホスト名を混ぜない（issuer / JWKS / フロントの url 指定がズレると即失敗する）。
- OS は CentOS / RHEL 系を想定。

---

## 1. 全ホスト共通：セキュリティの摩擦を消す

```bash
# firewalld を止める（全部開ける、の最短手）
sudo systemctl disable --now firewalld

# SELinux を permissive に
# （firewall だけと言われても、SELinux は CentOS で一番"動かない"原因なので
#   同じ理屈で外す。本番では戻す）
sudo setenforce 0
sudo sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
```

これで `:Z`（ボリュームラベル）も `httpd_can_network_connect` も気にしなくてよくなる。

---

## 2. DB層：Keycloak 用 PostgreSQL

```bash
sudo dnf -y install postgresql-server postgresql
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql

sudo -u postgres psql <<'SQL'
CREATE DATABASE keycloak;
CREATE USER keycloak WITH ENCRYPTED PASSWORD 'kcpass';
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;
\connect keycloak
GRANT ALL ON SCHEMA public TO keycloak;
SQL

# 全許可（firewall停止済み・閉じた環境前提）
sudo sed -i "s/^#\?listen_addresses.*/listen_addresses = '*'/" /var/lib/pgsql/data/postgresql.conf
echo "host  keycloak  keycloak  WEB_IP/32  scram-sha-256" | sudo tee -a /var/lib/pgsql/data/pg_hba.conf
sudo systemctl restart postgresql
```

---

## 3. web層：Docker CE + Keycloak

```bash
sudo dnf -y remove podman buildah 2>/dev/null || true
sudo curl -fsSL https://download.docker.com/linux/centos/docker-ce.repo -o /etc/yum.repos.d/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
```

`/opt/kc/docker-compose.yml`：

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.6
    command: start-dev --import-realm        # 動作優先なので dev モード（外部DBは使う）
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://DB_IP:5432/keycloak
      KC_DB_USERNAME: keycloak
      KC_DB_PASSWORD: kcpass
      KC_HOSTNAME: http://WEB_IP:8080        # issuer を固定（HTTP）
      KC_HTTP_ENABLED: "true"
    ports:
      - "8080:8080"                          # 直公開（nginx 経由しない）
    volumes:
      - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro
    restart: unless-stopped
```

---

## 4. realm 定義（最小・登録ON・メールなし・PKCEなし）

`/opt/kc/keycloak/realm-export.json`：

```json
{
  "realm": "myapp",
  "enabled": true,
  "sslRequired": "none",
  "registrationAllowed": true,
  "verifyEmail": false,
  "loginWithEmailAllowed": true,
  "resetPasswordAllowed": true,
  "clients": [
    {
      "clientId": "myapp-web",
      "enabled": true,
      "protocol": "openid-connect",
      "publicClient": true,
      "standardFlowEnabled": true,
      "directAccessGrantsEnabled": true,
      "redirectUris": ["http://WEB_IP/*"],
      "webOrigins": ["http://WEB_IP"]
    }
  ]
}
```

ポイント：

- `sslRequired: none` … HTTP を許可
- `registrationAllowed: true` … 登録ON
- `verifyEmail: false` … メール確認なし（＝登録した瞬間に使える）
- **PKCE属性なし** … HTTP / 非secure context だと `crypto.subtle` が使えず
  PKCE S256 が失敗するため、あえて要求しない
- `redirectUris` / `webOrigins` … **フロントのオリジン**（ポート80 ＝ `http://WEB_IP`）

起動：

```bash
cd /opt/kc
docker compose up -d
docker compose logs -f keycloak     # DB接続 & realm import を確認
```

---

## 5. web層：フロント配信（素のJS）

```bash
sudo dnf -y install nginx
sudo systemctl enable --now nginx
# 既定のドキュメントルートに置くだけ（permissive なのでラベル問題なし）
```

`/usr/share/nginx/html/index.html`：

```html
<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>MyApp</title></head>
<body>
  <button id="signup">新規登録</button>
  <button id="login">ログイン</button>
  <button id="logout">ログアウト</button>
  <button id="me">/auth/me を叩く</button>
  <pre id="out"></pre>

  <script type="module">
    import Keycloak from "https://cdn.jsdelivr.net/npm/keycloak-js@26/+esm";

    const kc = new Keycloak({
      url: "http://WEB_IP:8080",   // Keycloak。KC_HOSTNAME と完全一致
      realm: "myapp",
      clientId: "myapp-web",
    });

    await kc.init({});             // PKCE指定なし。ログイン後の戻りはここで処理される
    const out = document.getElementById("out");
    out.textContent = kc.authenticated
      ? "logged in: " + kc.tokenParsed?.preferred_username
      : "not logged in";

    document.getElementById("signup").onclick = () => kc.register({ redirectUri: location.href });
    document.getElementById("login").onclick  = () => kc.login({ redirectUri: location.href });
    document.getElementById("logout").onclick = () => kc.logout({ redirectUri: location.href });
    document.getElementById("me").onclick = async () => {
      const r = await fetch("http://API_IP:8000/auth/me", {
        headers: { Authorization: "Bearer " + kc.token },
      });
      out.textContent = r.status + " " + await r.text();
    };
  </script>
</body>
</html>
```

> 閉域でブラウザが CDN に出られない場合は、keycloak-js の `keycloak.min.js` を
> 落として同じディレクトリに置き、`import Keycloak from "./keycloak.min.js"` に変える。

---

## 6. 動作確認

ブラウザで `http://WEB_IP/` を開く → **「新規登録」** → Keycloak の登録フォーム
（メール確認なし）→ アカウント作成 → `http://WEB_IP/` に**ログイン済みで戻る**。
`logged in: <ユーザ名>` が出れば「フロントからアクセスしてアカウント作成」は達成。

curl で素早く確認したい場合（`directAccessGrantsEnabled: true` にしてあるので）：

```bash
curl -sS -X POST http://WEB_IP:8080/realms/myapp/protocol/openid-connect/token \
  -d grant_type=password \
  -d client_id=myapp-web \
  -d username=<作ったユーザ> \
  -d password=<パス>
```

---

## 7. API層（繋ぐ時だけ・登録には不要）

API層の FastAPI は次のように設定する：

- `KEYCLOAK_SERVER_URL=http://WEB_IP:8080/`
- `KEYCLOAK_REALM=myapp`

issuer も JWKS も同じ `http://WEB_IP:8080`。API層から `WEB_IP:8080` に届けばOK。
`main.py` の CORS `allow_origins` に `http://WEB_IP`（フロントのオリジン）を入れる。
検証コードはこれまでのものをそのまま流用可。

---

## まとめ

これで全層 HTTP・摩擦ゼロで「登録できる」ところまで動く。
実運用に上げる時は、`keycloak-centos10-3tier-setup.md` の §8 チェックリストで
**TLS・PKCE・firewalld・SELinux enforcing・メール確認** を戻す、という二段構えで進める。
**まず動かす → 固める、の順。**
