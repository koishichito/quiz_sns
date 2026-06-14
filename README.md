# Quiz SNS API — FastAPI + SQLAlchemy 2.0 + MySQL + Keycloak

クイズを投稿し、投稿されたクイズにコメントできるSNSのバックエンド。
認証は自前実装ではなく **Keycloak(OpenID Connect)に委譲**している。
学習用に全ファイルへ設計意図のコメントを入れてある。

## 認証アーキテクチャ

このアプリはパスワードを一切預からない。トークンを検証するだけの
「リソースサーバ」であり、登録・ログイン・トークン発行はKeycloakの仕事。

```
[クライアント] ──(1) username/password──▶ [Keycloak]   ← 登録・ログインはこちら
[クライアント] ◀──(2) アクセストークン(RS256署名のJWT)── [Keycloak]
[クライアント] ──(3) Authorization: Bearer <token>──▶ [このAPI]
[このAPI]      ──(4) 公開鍵(JWKS)を取得(初回のみ・以後キャッシュ)──▶ [Keycloak]
[このAPI]         (5) 署名・期限・発行者を検証 → users行をJIT作成 → 応答
```

設計意図の詳しい解説は `app/auth.py` 冒頭のコメントにまとめてある
(HS256→RS256の理由 / JWKSとkid / JITプロビジョニング)。

## 構成

```
quiz_sns/
├── app/
│   ├── main.py          # 入口: アプリの組み立て(lifespan, CORS, ルーター登録)
│   ├── config.py        # .env を読む設定クラス(Keycloakの接続先もここ)
│   ├── database.py      # エンジン / SessionLocal / Base / get_db(DIの核)
│   ├── models.py        # ORMモデル = DBのテーブル定義(User, Quiz, Comment)
│   ├── schemas.py       # Pydanticスキーマ = APIの入出力の契約
│   ├── auth.py          # Keycloakトークンの検証(JWKS/RS256)/ get_current_user
│   ├── crud.py          # DB操作の関数群(JITプロビジョニング含む)
│   └── routers/
│       ├── auth.py      # GET /auth/me(トークン疎通確認。register/loginはKeycloakへ移管)
│       ├── quizzes.py   # /quizzes の一覧・投稿・詳細・削除
│       └── comments.py  # /quizzes/{quiz_id}/comments の一覧・投稿
├── frontend/            # 最小フロントエンド(素のJS。Keycloakでログインする)
│   ├── index.html       # 画面
│   ├── api.js           # 通信層: login()→Keycloak / apiFetch()→このAPI
│   └── app.js           # 画面ロジック(入力取得→呼び出し→描画)
├── tests/
│   └── test_api.py      # 結合テスト(DB→SQLite、Keycloak→自作RSA鍵に差し替え)
├── requirements.txt
└── .env.example
```

読む順番の推奨: `main.py` → `routers/` → `auth.py` → `crud.py` → `models.py`。
入出力の形で迷ったら `schemas.py`、土台は `database.py` / `config.py`。

## セットアップ

### 1. MySQL 8 側の準備

```sql
-- mysql -u root -p で入って実行
CREATE DATABASE quiz_db CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'quiz_user'@'%' IDENTIFIED BY 'quiz_pass';
GRANT ALL PRIVILEGES ON quiz_db.* TO 'quiz_user'@'%';
FLUSH PRIVILEGES;
```

utf8mb4 を明示すること。MySQL の `utf8` は3バイトまでの偽UTF-8で、絵文字等で壊れる。

### 2. Keycloak 側の準備

**配られたKeycloakを使う場合**は、接続先URL・realm名・クライアントIDを
教えてもらい、次節の `.env` に書くだけでよい。このAPIがKeycloak側に
期待する設定は以下の3点:

- realm(例: `quiz-sns`)
- パブリッククライアント(例: `quiz-sns-api`)。
  curl でトークンを取るなら **Direct access grants(パスワードグラント)** を有効にする
- ユーザー(username / password)

**手元で立てる場合**(開発用):

```bash
docker run --name keycloak -p 8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.2 start-dev
```

http://localhost:8080 の管理コンソール(admin/admin)で
realm `quiz-sns` → クライアント `quiz-sns-api`(Direct access grants有効)→
ユーザーを作成する。

### 3. Python 側

```bash
cd quiz_sns
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# .env を編集: DATABASE_URL の接続情報と KEYCLOAK_*(配られた値)
```

### 4. 起動

```bash
uvicorn app.main:app --reload
```

起動時に lifespan 内の `create_all` がテーブルを自動作成する(開発用。実務は Alembic)。

> **旧バージョン(自前認証)からの移行時の注意**: users テーブルの構造が
> 変わっている(`hashed_password` 廃止 / `keycloak_sub` 追加)。`create_all` は
> 既存テーブルを変更しないので、古い `quiz_db` が残っている場合は
> `DROP DATABASE quiz_db;` してから作り直すこと。

ブラウザで http://127.0.0.1:8000/docs を開くと Swagger UI が出る。
右上の Authorize ボタンは **Keycloakのトークンエンドポイントへ直接**
username/password を送る(client_id 欄には `quiz-sns-api` を入れる)。
これはブラウザ→Keycloakの通信なので、Keycloak側クライアント設定の
Web origins に `http://127.0.0.1:8000` を加えておかないとCORSで失敗する。
失敗する環境では、下記の curl でトークンを取るのが確実。

## curl での動作確認フロー

```bash
# 1. トークン取得(宛先はこのAPIではなく【Keycloak】。フォーム形式で送る)
TOKEN=$(curl -s -X POST "http://localhost:8080/realms/quiz-sns/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=quiz-sns-api" \
  -d "username=keisuke" \
  -d "password=password123" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. トークンの疎通確認(このAPIへの初回アクセス時、users行が自動作成される =
#    JITプロビジョニング。登録APIを叩いていないのにユーザーが存在する点に注目)
curl http://127.0.0.1:8000/auth/me -H "Authorization: Bearer $TOKEN"

# 3. クイズ投稿(認証必須)
curl -X POST http://127.0.0.1:8000/quizzes \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"膝抜き","question":"主目的は?","choices":["蹴る","支持を抜いて落ちる"],"answer_index":1}'

# 4. 一覧(認証不要)
curl http://127.0.0.1:8000/quizzes

# 5. コメント投稿
curl -X POST http://127.0.0.1:8000/quizzes/1/comments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"body":"良問"}'

# 6. 詳細(コメント込み)
curl http://127.0.0.1:8000/quizzes/1
```

## フロントエンド(frontend/)

素のHTML+JSだけの最小フロントエンド。ビルド不要。ログインは
**ブラウザから Keycloak のトークンエンドポイントへ直接**(Direct Access Grant)行い、
得たアクセストークンを `localStorage` に保存して、以降このAPIへ `Bearer` で添える。
このAPIには登録・ログイン機能が無い(Keycloakへ委譲済み)ので、フロントの
ログインも宛先が Keycloak になっている点が要。

```bash
cd frontend
python -m http.server 5173   # http://localhost:5173 で配信
```

ブラウザで http://localhost:5173 を開く。`frontend/api.js` 冒頭の設定を、
配られた環境に合わせる:

- `API_BASE` … このAPI(FastAPI)のURL。既定 `http://localhost:8000`
- `KEYCLOAK_BASE` / `KEYCLOAK_REALM` / `KEYCLOAK_CLIENT_ID`
  … **このAPIの `.env`(`KEYCLOAK_*`)と必ず同じ値にする**。
  食い違うと、得たトークンを API 側が「別realmの物」として弾く。

CORS の注意(2か所):

1. **ブラウザ → このAPI**: API 側 `app/main.py` の `allow_origins` に
   フロントの配信元が必要。既定で `http://localhost:5173` と `:3000` を許可済み
   なので、上記の `5173` で配信すればそのまま通る。
2. **ブラウザ → Keycloak**(ログイン時): Keycloak 側クライアントの **Web origins** に
   `http://localhost:5173` を加えておく。無いと応答を読めず CORS で失敗する。

画面上部の「状態: ログイン中: ○○」表示は `/auth/me` の結果で、トークンが
このAPIで通用している(=Keycloakと噛み合っている)ことの確認になる。

> 本番では、ブラウザがパスワードを直接握る Direct Access Grant ではなく、
> Keycloak のログイン画面へリダイレクトする **Authorization Code + PKCE** が推奨。
> 切り替え方は `frontend/api.js` 末尾の注記参照(`apiFetch` を境界にしてあるので
> 画面ロジック `app.js` は無修正で済む)。

## テスト

```bash
pytest tests/ -q
```

MySQL も **Keycloak も不要**。外部依存は2つとも差し替える:

- DB: `dependency_overrides` で `get_db` をインメモリSQLiteへ(DIの実利が一番分かる場所)
- Keycloak: テストがRSA鍵ペアを自作し、秘密鍵で署名したトークンを発行、
  公開鍵を返す偽JWKSクライアントを `app.auth.jwks_client` に差し込む
  (= テストがKeycloakのフリをする)。署名・期限・発行者の検証経路は
  本物のまま通るので、「偽造トークンが弾かれること」までテストできる
  (tests/test_api.py 冒頭参照)

## SQLを観察したいとき

`app/database.py` の `create_engine(..., echo=False)` を `echo=True` に変えると、
発行される SQL が全部ログに出る。`GET /quizzes/{id}` を叩いて、
selectinload が N+1 をどう潰しているか(クエリが3本にまとまる)を見ると面白い。
