# DB層セットアップ手順（MySQL 8.4 / CentOS Stream 10）

Keycloak とアプリ（FastAPI）を **同じ MySQL 8.4 に集約する** 前提の、DB層サーバ構築手順。

- 対象サーバ: CentOS Stream 10（RHEL 10 系）
- DB層 IP: `DB_IP = 10.0.0.30`
- 前提: **firewalld 停止** ・ **SELinux permissive** は前回適用済み
  （そのため 3306 はそのまま到達でき、SELinux のラベル問題も出ない）

## 構成の方針

このサーバ1台に、**用途の異なる2つの別データベース**を置く。共有せず別DBにするのがポイント
（Keycloak は自分のスキーマを専有する）。

| データベース | 用途        | 接続ユーザ    |
| ------------ | ----------- | ------------- |
| `keycloak`   | Keycloak用  | `keycloak`    |
| `quiz_db`    | アプリ用    | `quiz_user`   |

> RHEL 10 / CentOS Stream 10 は MySQL 8.4 を `mysql8.4-server` パッケージで直接提供する。
> サービス名は `mysqld`。

### 実行順（全体像）

1. `mysql8.4-server` をインストール
2. `mysql_secure_installation` で root パスワード設定
3. `bind-address=0.0.0.0` を入れて再起動（リモート接続許可）
4. 2つのDBと2つのユーザを作成
5. クライアント側の認証対処（JDBCパラメータ／pymysql に cryptography）
6. 疎通確認

---

## 1. MySQL 8.4 インストール

```bash
sudo dnf -y install mysql8.4-server
sudo systemctl enable --now mysqld
mysqld --version
```

> パッケージが見つからなければ `dnf search mysql` で名前を確認する。

## 2. 初期設定（root パスワード）

```bash
sudo mysql_secure_installation
```

- el10 の distro パッケージは **root が空で起動**するので、最初の「現在の root パスワード」は
  空のまま Enter → 新パスワードを設定。
- もし一時パスワードを要求されたら、ログから確認する:

  ```bash
  sudo grep 'temporary password' /var/log/mysql/mysqld.log
  ```

## 3. リモート接続を許可（bind-address）

デフォルトだと外から繋げないことがあるので、全インターフェースで待ち受けにする。

```bash
echo -e "[mysqld]\nbind-address=0.0.0.0" | sudo tee /etc/my.cnf.d/network.cnf
sudo systemctl restart mysqld
```

> firewalld は停止済みなので 3306 の穴あけは不要。

## 4. データベースとユーザを作成

```bash
sudo mysql -u root -p <<'SQL'
CREATE DATABASE keycloak CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE quiz_db  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER 'keycloak'@'%'  IDENTIFIED BY 'kcpass';
CREATE USER 'quiz_user'@'%' IDENTIFIED BY 'quiz_pass';

GRANT ALL PRIVILEGES ON keycloak.* TO 'keycloak'@'%';
GRANT ALL PRIVILEGES ON quiz_db.*  TO 'quiz_user'@'%';
FLUSH PRIVILEGES;
SQL
```

- `@'%'` は全ホスト許可（firewall 停止・閉域前提の最短手）。
  絞るなら `'keycloak'@'WEB_IP'`、`'quiz_user'@'API_IP'` にする。
- 文字コードは Keycloak / MySQL とも **utf8mb4** が推奨。
  Keycloak は DB の既定 charset でテーブルを作るため、DB側を utf8mb4 にしておけば足りる。

## 5. ★MySQL 8.4 の認証の注意（ここを外すと「繋がらない」になる）

MySQL 8.4 の既定認証は `caching_sha2_password`。**非TLS接続だと両クライアントでハマりどころがある**
ので、それぞれ対処を入れる。

- **Keycloak（JDBC・非TLS）**:
  接続URLに `allowPublicKeyRetrieval=true&useSSL=false` を付ける（compose に設定済み）。
  これで公開鍵交換が通る。
- **FastAPI（pymysql・非TLS）**:
  api 層で `pip install cryptography` を入れる。
  これが無いと `caching_sha2_password` の処理に失敗して接続エラーになる（よくある詰まり）。

> **代替案（基本は不要）**: どちらも避けたいなら `mysql_native_password` でユーザを作る手もあるが、
> 8.4 は native_password を既定で無効化しているので、先に `/etc/my.cnf.d/` に
> `mysql-native-password=ON` を足して再起動 → `CREATE USER ... IDENTIFIED WITH mysql_native_password`
> という追加手順が要る。手数を考えると、**caching_sha2 のまま上の2点で対処する方が楽**。

## 6. 疎通確認と各層の接続文字列

web層・api層から到達確認:

```bash
mysql -h 10.0.0.30 -u keycloak  -p keycloak     # Keycloak用
mysql -h 10.0.0.30 -u quiz_user -p quiz_db      # アプリ用
```

### Keycloak（web層 compose）

```yaml
KC_DB: mysql
KC_DB_URL: "jdbc:mysql://10.0.0.30:3306/keycloak?allowPublicKeyRetrieval=true&useSSL=false"
KC_DB_USERNAME: keycloak
KC_DB_PASSWORD: kcpass
```

### FastAPI（api層 `.env`）

```
DATABASE_URL=mysql+pymysql://quiz_user:quiz_pass@10.0.0.30:3306/quiz_db?charset=utf8mb4
```

> これまで `localhost` / `127.0.0.1` だったホスト部を **DB層IP（10.0.0.30）** に変える。
> `?charset=utf8mb4` を付ける。

---

## まとめ

- Keycloak とアプリで **別DB**（`keycloak` / `quiz_db`）を1台の MySQL 8.4 に集約。
- Keycloak は初回起動時に `keycloak` DB へテーブルを自動生成するので、
  **DB側で用意するのは空のDBとユーザだけ**。
- 認証は `caching_sha2_password` のまま、JDBC パラメータと pymysql の cryptography で対処するのが最短。
