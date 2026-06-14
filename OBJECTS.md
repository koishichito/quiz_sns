# オブジェクトリファレンス（網羅版）

このプロジェクト（Quiz SNS API: FastAPI + SQLAlchemy 2.0 + MySQL + Keycloak）で
定義されているオブジェクトを、層ごとに**1つ残らず**解説する。
各オブジェクトには `ファイル:行` を併記してあるのでソースと突き合わせて読める。

> 全体像は [README.md](README.md) を、認証の設計意図は `app/auth.py` 冒頭コメントを参照。
> 読む順番の推奨: 本書も `main.py` → `routers/` → `auth.py` → `crud.py` → `models.py` の順。

---

## 0. 全体像 — オブジェクトの関係

```
                    ┌─────────────── 設定・基盤層 ───────────────┐
                    │  Settings / settings (config.py)           │
                    │  engine / SessionLocal / Base / get_db     │
                    │              (database.py)                 │
                    └────────────────────┬───────────────────────┘
                                         │ 参照・DI
   フロントエンド層              アプリ/ルーター層           データモデル層(ORM)
   ┌──────────────┐  HTTP   ┌────────────────────┐  使用  ┌──────────────────┐
   │ api.js       │ ──────▶ │ app (FastAPI)      │ ─────▶ │ User             │
   │  apiFetch    │         │ auth.router        │        │ Quiz   ──< Comment│
   │  login →KC   │         │ quizzes.router     │        │ User  ──< Quiz    │
   │ app.js       │         │ comments.router    │        │ User  ──< Comment │
   │  handle系    │         └─────────┬──────────┘        └──────────────────┘
   └──────────────┘                   │ 呼び出し                  ▲
                                       ▼                          │ ORM⇔dict
                          ビジネスロジック層 / 認証層        スキーマ層(Pydantic)
                          ┌────────────────────────┐      ┌──────────────────┐
                          │ crud.py(DB操作の関数群) │ ───▶ │ QuizCreate 等(入力)│
                          │ auth.py(トークン検証)   │      │ QuizRead 等(出力)  │
                          │  jwks_client/get_current│      └──────────────────┘
                          └────────────────────────┘
```

オブジェクトの種別ごとの早見表:

| 種別 | 主なオブジェクト | 定義ファイル |
|---|---|---|
| 設定 | `Settings`, `settings` | `app/config.py` |
| DB基盤 | `engine`, `SessionLocal`, `Base`, `get_db` | `app/database.py` |
| ORMモデル | `User`, `Quiz`, `Comment` | `app/models.py` |
| Pydanticスキーマ | `UserRead`, `QuizCreate`, `QuizRead`, `QuizReadWithComments`, `CommentCreate`, `CommentRead` | `app/schemas.py` |
| CRUD関数 | `get_user_by_sub`, `get_or_create_user`, `create_quiz`, `list_quizzes`, `get_quiz`, `get_quiz_with_details`, `delete_quiz`, `create_comment`, `list_comments` | `app/crud.py` |
| 認証部品 | `ALGORITHM`, `oauth2_scheme`, `jwks_client`, `get_current_user` | `app/auth.py` |
| アプリ/ルーター | `app`, `lifespan`, `health_check`, `auth.router`, `quizzes.router`, `comments.router` + 各エンドポイント | `app/main.py`, `app/routers/*` |
| フロント | `apiFetch`, `login`, `logout`, `fetchMe`, `ApiError`, `handle*`/`load*`/`refresh*` + 各定数 | `frontend/api.js`, `frontend/app.js` |

---

## 1. 設定・基盤層

### 1.1 `Settings`（`app/config.py:33`） — Pydantic Settings

アプリ全体の設定を一元管理するクラス。インスタンス化時に環境変数 / `.env` を読み込む
（12 Factor App の Config 原則）。値の優先順位は **OS環境変数 > .env ファイル > デフォルト値**。

`model_config`（`config.py:34`, `SettingsConfigDict`）: `env_file=".env"` /
`env_file_encoding="utf-8"` / `extra="ignore"`（.env に未知キーがあっても無視）。

| フィールド | 型 | デフォルト | 環境変数 | 意味 |
|---|---|---|---|---|
| `database_url` | `str` | `mysql+pymysql://quiz_user:quiz_pass@127.0.0.1:3306/quiz_db?charset=utf8mb4` | `DATABASE_URL` | DB接続文字列（`方言+ドライバ://…`） |
| `keycloak_server_url` | `str` | `http://localhost:8080` | `KEYCLOAK_SERVER_URL` | Keycloak サーバURL |
| `keycloak_realm` | `str` | `quiz-sns` | `KEYCLOAK_REALM` | 信用する realm 名 |
| `keycloak_client_id` | `str` | `quiz-sns-api` | `KEYCLOAK_CLIENT_ID` | トークン取得時にクライアントが名乗るID。検証自体には未使用（Swagger/curl例で参照） |
| `keycloak_audience` | `str \| None` | `None` | `KEYCLOAK_AUDIENCE` | 設定すると `aud` クレーム検証を有効化（任意） |

**計算プロパティ**（`@property`。`server_url`+`realm` から導出し、変え忘れ事故を防ぐ）:

| プロパティ | 戻り値 | 用途 |
|---|---|---|
| `keycloak_issuer`（`config.py:75`） | `{server_url}/realms/{realm}` | トークンの `iss` と厳密一致すべき発行者名 |
| `keycloak_jwks_url`（`config.py:81`） | `{issuer}/protocol/openid-connect/certs` | 署名検証用 公開鍵一覧(JWKS) URL |
| `keycloak_token_url`（`config.py:86`） | `{issuer}/protocol/openid-connect/token` | トークン発行URL（ログイン宛先） |

> 旧 `SECRET_KEY`（自前署名鍵）は認証の Keycloak 委譲に伴い廃止済み。

### 1.2 `settings`（`app/config.py:95`） — シングルトンインスタンス

`Settings()` のモジュールレベル単一インスタンス。全モジュールが `from .config import settings`
で共有する。**import の瞬間に検証が走る**ため、設定ミスは初回リクエスト時ではなく
**起動時に発覚**する（fail-fast）。

### 1.3 `engine`（`app/database.py:46` / `:52`） — Engine（コネクションプール）

`create_engine` の戻り値。コネクションプール本体で、アプリ全体で1個を共有。
`settings.database_url` が `sqlite` で始まるかで生成パラメータを2分岐する。

- **SQLite分岐**（テスト用）: `connect_args={"check_same_thread": False}`（FastAPIが同期エンドポイントを別スレッドで実行するため）, `echo=False`。
- **MySQL分岐**（本番用）: `pool_pre_ping=True`（使用直前に疎通確認、"server has gone away" 対策）, `pool_recycle=3600`（古い接続を破棄）, `echo=False`（`True` で発行SQLを全ログ出力＝学習時に有用）。

> `create_engine` は遅延接続。最初のクエリ実行時に初めて物理接続を張る。

### 1.4 `SessionLocal`（`app/database.py:68`） — sessionmaker（セッション工場）

`SessionLocal()` 呼び出しごとに新しい `Session` を1個生成する工場。
`bind=engine` / `autoflush=False`（クエリ毎の自動flushを無効化しSQL発行タイミングを予測しやすくする）。
※ SQLAlchemy 2.0 では `autocommit=False` は引数ごと削除済み（書くと TypeError）。

### 1.5 `Base`（`app/database.py:75`） — 全ORMモデルの基底クラス

`DeclarativeBase` を継承。本体は docstring のみ。これを継承したクラス（User/Quiz/Comment）が
定義されるたびテーブル情報が `Base.metadata` 台帳へ自動登録され、`main.py` の
`Base.metadata.create_all()` がそこから `CREATE TABLE` を発行する。

### 1.6 `get_db`（`app/database.py:85`） — DIの核（ジェネレータ依存性）

```python
db = SessionLocal()
try:
    yield db
finally:
    db.close()
```

FastAPI の依存性注入の核。エンドポイントは `db: Session = Depends(get_db)` と書くだけ。
`yield` 前で資源調達、後で後片付け。`finally` に `close()` を置くことで途中で例外が出ても
**必ず接続をプールへ返却**する（リーク防止）。`close()` は commit ではない——
**commit 判断は `crud.py` 側の責務に統一**する方針。

---

## 2. データモデル層（ORM = DBのテーブル定義）

SQLAlchemy 2.0 の型付き宣言スタイル（`Mapped[...] = mapped_column(...)`）。
`Mapped[str]`＝NOT NULL、`Mapped[str | None]`＝nullable を型注釈から自動推論する。
テーブル関係: **`users ──< quizzes ──< comments`** かつ **`users ──< comments`**（すべて1対多）。

### 2.1 `User`（`app/models.py:59`, テーブル `users`）

Keycloak 上のアイデンティティ（`keycloak_sub`）とアプリDBの行を結びつけ、表示名をキャッシュする。
認証自体は Keycloak へ委譲済みで `hashed_password` は**廃止**。

| カラム | 型 | 制約 | 意味 |
|---|---|---|---|
| `id` | `int` | PK, autoincrement | 主キー（DB採番） |
| `keycloak_sub` | `String(36)` | unique, index, NOT NULL | Keycloak の不変ユーザーID（`sub`、UUID）。毎リクエスト検索のため index 必須。unique はJIT並行実行時の重複防止の最後の砦 |
| `username` | `String(255)` | index, NOT NULL | 表示用ユーザー名（`preferred_username` の写し）。一意性は Keycloak 側管理のため unique なし |
| `created_at` | `DateTime` | NOT NULL, `server_default=func.now()` | 作成日時（DB側採番） |

**relationship**: `quizzes`→`Quiz`（1対多, back_populates=`owner`） / `comments`→`Comment`（1対多, back_populates=`author`）。

### 2.2 `Quiz`（`app/models.py:98`, テーブル `quizzes`）

クイズ1件。選択肢は正規化せず JSON カラムへ格納する設計判断（単体検索せず常にクイズと一緒に読むため）。

| カラム | 型 | 制約 | 意味 |
|---|---|---|---|
| `id` | `int` | PK, autoincrement | 主キー |
| `title` | `String(200)` | NOT NULL | タイトル |
| `question` | `Text` | NOT NULL | 問題文（長さ無制限） |
| `choices` | `JSON` | NOT NULL | 選択肢リストを丸ごと格納（list↔JSON は自動変換） |
| `answer_index` | `int` | NOT NULL | 正解インデックス（0始まり） |
| `explanation` | `Text` | nullable | 解説（任意） |
| `owner_id` | `int` | FK→`users.id`(ondelete=CASCADE), index, NOT NULL | 投稿者の外部キー |
| `created_at` | `DateTime` | NOT NULL, `server_default=func.now()` | 作成日時 |

**relationship**: `owner`→`User`（多対1, back_populates=`quizzes`） /
`comments`→`Comment`（1対多, back_populates=`quiz`, **`cascade="all, delete-orphan"`**, `order_by="Comment.created_at"`）。
コメントの cascade は DB側 `ondelete="CASCADE"` と二重化し、ORM経由でもSQL直叩きでも整合を保つ。

### 2.3 `Comment`（`app/models.py:141`, テーブル `comments`）

あるクイズへの1件のコメント（どのクイズに・誰が・何を書いたか）。

| カラム | 型 | 制約 | 意味 |
|---|---|---|---|
| `id` | `int` | PK, autoincrement | 主キー |
| `quiz_id` | `int` | FK→`quizzes.id`(ondelete=CASCADE), index, NOT NULL | 対象クイズ |
| `author_id` | `int` | FK→`users.id`(ondelete=CASCADE), index, NOT NULL | 投稿者 |
| `body` | `Text` | NOT NULL | 本文 |
| `created_at` | `DateTime` | NOT NULL, `server_default=func.now()` | 作成日時 |

**relationship**: `quiz`→`Quiz`（多対1, back_populates=`comments`） / `author`→`User`（多対1, back_populates=`comments`）。

> 全 `created_at` は Python側 `default` ではなく `server_default=func.now()` を採用し、
> アプリ非経由のINSERTでも値が入るよう時刻基準をDBサーバに統一している。
> `cascade` を明示するのは `Quiz.comments` のみ、`lazy=` はいずれも既定（遅延ロード）。

---

## 3. スキーマ層（Pydantic = APIの入出力の契約）

「分離型」設計: `◯◯Create`＝入力（検証装置）、`◯◯Read`＝出力（フィルタ装置・`response_model`）。
ORM連携設定 `ConfigDict(from_attributes=True)` を持つのは **Read系のみ**。
Pydantic v2 記法（`ConfigDict` / `model_validator` / PEP 604 `str | None`）で統一。

| スキーマ | 行 | 区分 | 継承 | from_attributes | 主なフィールド |
|---|---|---|---|---|---|
| `UserRead` | `schemas.py:54` | 出力 | BaseModel | ✅ | `id` / `username` / `created_at` |
| `QuizCreate` | `schemas.py:71` | 入力 | BaseModel | – | `title` / `question` / `choices` / `answer_index` / `explanation` |
| `QuizRead` | `schemas.py:97` | 出力（一覧） | BaseModel | ✅ | 上記 + `id` / `created_at` / `owner: UserRead` |
| `QuizReadWithComments` | `schemas.py:132` | 出力（詳細） | **QuizRead 継承** | （継承） | QuizRead 全部 + `comments: list[CommentRead]` |
| `CommentCreate` | `schemas.py:116` | 入力 | BaseModel | – | `body` |
| `CommentRead` | `schemas.py:123` | 出力 | BaseModel | ✅ | `id` / `body` / `created_at` / `author: UserRead` |

### 入力スキーマの検証ルール

- **`QuizCreate`**: `title`=`Field(min_length=1, max_length=200)` / `question`=`Field(min_length=1)` /
  `choices`=`Field(min_length=2, max_length=10)`（**要素数**2〜10個） / `answer_index`=`Field(ge=0)` /
  `explanation`=`str | None = None`。
  さらに **`@model_validator(mode="after")` `validate_answer_index`**（`schemas.py:80`）で
  `answer_index >= len(choices)` なら `ValueError`（→ FastAPIが422に変換）。範囲外の正解を構造的に弾く。
- **`CommentCreate`**: `body`=`Field(min_length=1, max_length=1000)`。

### ネスト/継承の全体図

```
QuizReadWithComments (extends QuizRead)
├─ owner: UserRead              （QuizRead から継承）
├─ id / title / question / ...  （QuizRead から継承）
└─ comments: list[CommentRead]  （追加）
        └─ author: UserRead
```

> **漏れる経路を構造的に塞ぐ**方針が明文化されている:
> 入力(Create)には `owner_id`/`author_id`/`quiz_id` を含めない（なりすまし防止。投稿者はトークン由来）、
> 出力(Read)には `keycloak_sub` を含めない（内部IDを露出させない）。
> `owner`/`author` の直列化で遅延SELECTが走らないよう、`crud.py` 側で `selectinload` 先読みする。

---

## 4. ビジネスロジック層（`app/crud.py`）

DB操作を関数化した層。エンドポイントは「HTTPの都合」に集中し、SQLの都合はここへ隔離。
`Session` は引数で受け取り自前生成しない。**commit の責務はこの層に統一**
（＝書き込み系 crud 関数は戻った時点でDBに確定済み、という契約）。

| 関数 | 行 | シグネチャ概略 | 種別 | 副作用 |
|---|---|---|---|---|
| `get_user_by_sub` | `crud.py:46` | `(db, keycloak_sub) -> User \| None` | 読取 | なし |
| `get_or_create_user` | `crud.py:53` | `(db, *, keycloak_sub, username) -> User` | 読取/書込 | 初回は add+commit+refresh、改名時は commit、競合時 rollback |
| `create_quiz` | `crud.py:97` | `(db, data: QuizCreate, owner_id) -> Quiz` | 書込 | add→commit→refresh |
| `list_quizzes` | `crud.py:108` | `(db, skip=0, limit=20) -> list[Quiz]` | 読取 | なし。`selectinload(owner)` でN+1対策 |
| `get_quiz` | `crud.py:127` | `(db, quiz_id) -> Quiz \| None` | 読取 | `db.get`（主キー最短検索） |
| `get_quiz_with_details` | `crud.py:131` | `(db, quiz_id) -> Quiz \| None` | 読取 | `selectinload(owner)` + `selectinload(comments).selectinload(author)`（2段先読み） |
| `delete_quiz` | `crud.py:147` | `(db, quiz) -> None` | 書込 | `db.delete`→commit。cascadeでコメントも削除 |
| `create_comment` | `crud.py:155` | `(db, quiz_id, author_id, data: CommentCreate) -> Comment` | 書込 | add→commit→refresh |
| `list_comments` | `crud.py:165` | `(db, quiz_id) -> list[Comment]` | 読取 | `selectinload(author)`。投稿順 |

### JITプロビジョニング — `get_or_create_user`（中核）

引数の `*` 以降はキーワード専用で、同型2引数（`sub` と `name`）の取り違えを呼び出し時点で防ぐ。

1. `get_user_by_sub(db, keycloak_sub)` で既存検索。
2. **存在しない**: `User(keycloak_sub, username)` を `add`→`commit`。
   並行初回リクエストの競合で `IntegrityError`（unique 違反）が出たら `rollback` し、
   **先に勝った行を読み直して**正常続行。成功時は `refresh` でDB採番の `id`/`created_at` を反映。
3. **存在する**: Keycloak側で改名されていれば `username` を追従更新（`commit`+`refresh`）。
   紐付けキーが `sub` だからこそ、改名しても同一ユーザーとして扱える。

> 事前同期バッチが不要になり、Keycloak側でユーザーが増えてもアプリ側は何もしなくてよい。
> 毎リクエスト呼ばれるが基本はSELECT 1回、INSERTは初回だけ。

---

## 5. 認証層（`app/auth.py`）

アプリは認証を自前実装せず Keycloak(IdP)へ委譲。アプリは**リソースサーバ**として
トークンを検証するだけ。

| オブジェクト | 行 | 種別 | 役割 |
|---|---|---|---|
| `ALGORITHM` | `auth.py:55` | 定数 `"RS256"` | JWT署名検証アルゴリズム（公開鍵方式 RSA+SHA-256） |
| `oauth2_scheme` | `auth.py:64` | `OAuth2PasswordBearer` | `Authorization: Bearer <token>` から token を抽出する依存性。ヘッダ不正なら401。`tokenUrl` はSwagger表示用 |
| `jwks_client` | `auth.py:71` | `jwt.PyJWKClient` | 公開鍵(JWKS)の取得・kid照合・キャッシュ係。モジュール読込時に1個生成し使い回す。生成時はHTTPアクセスせず初回検証時に取得（以後キャッシュ） |
| `get_current_user` | `auth.py:74` | FastAPI依存性 | トークン検証→`models.User` 復元。`Depends(get_current_user)` で全認証必須エンドポイントへ注入 |

**なぜ RS256 か**（HS256 からの変更）: 発行者(Keycloak)と検証者(このAPI)が別プロセスのため、
共通鍵(HS256)では「検証できる者は誰でも偽造できる」。RS256 なら
**署名＝Keycloakだけが持つ秘密鍵 / 検証＝公開してよい公開鍵** に分離でき、SSO構成の土台になる。

### `get_current_user` の検証フロー

1. **kid→公開鍵**（`auth.py:101`）: `jwks_client.get_signing_key_from_jwt(token)`。トークンヘッダの `kid` で対応鍵を選ぶ（キャッシュになければここでKeycloakへHTTP）。
2. **`jwt.decode`（`auth.py:103`）で一括検証**:
   - `algorithms=["RS256"]` — **こちらで明示**（トークン自称を信用すると「アルゴリズム混乱攻撃」の余地）。
   - `issuer=settings.keycloak_issuer` — `iss` を信用 realm と厳密一致。
   - `audience` + `options["verify_aud"]=settings.keycloak_audience is not None` — 設定時のみ `aud` 検証。
   - `options["require"]=["exp","sub"]` — exp/sub の存在を要求（無期限トークン事故を防ぐ）。
3. **失敗集約**（`auth.py:125`）: `except jwt.PyJWTError` → `HTTPException(401, "認証情報が無効です", headers={"WWW-Authenticate":"Bearer"})`。署名不正・期限切れ・iss不一致・JWKS取得失敗を全部ここへ（理由を細かく返さない＝攻撃者にヒントを与えない）。
4. **ユーザー決定**（`auth.py:132`）: `username = preferred_username or sub` を求め、
   `crud.get_or_create_user(db, keycloak_sub=sub, username=username)` を返す（=ここでJIT発火）。

> `db` はエンドポイント側の `Depends(get_db)` と**同一 Session**（FastAPIが同一リクエスト内で依存結果をキャッシュするため）。

---

## 6. アプリ/ルーター層

### 6.1 `app/main.py` — 組み立て専用

| オブジェクト | 行 | 内容 |
|---|---|---|
| `lifespan` | `main.py:51` | `@asynccontextmanager`。`yield` 前に起動時1回 `Base.metadata.create_all(bind=engine)` で未存在テーブルのみCREATE（スキーマ変更は反映されない＝実務はAlembic） |
| `app` | `main.py:65` | `FastAPI(title="Quiz SNS API", lifespan=lifespan)`。本体。`title` はSwagger見出し |
| CORSミドルウェア | `main.py:76` | `allow_origins=["http://localhost:3000","http://localhost:5173"]`, `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`。フロント接続時に必須 |
| ルーター登録 | `main.py:86` | `include_router(auth.router / quizzes.router / comments.router)` |
| `health_check` | `main.py:91` | `@app.get("/")`、`{"status":"ok"}` を返す死活監視 |

> `from . import models  # noqa: F401`（`main.py:46`）は必須。models 読込でクラス定義が実行され
> `Base.metadata` にテーブルが登録されて初めて `create_all` が機能する。

### 6.2 ルーターとエンドポイント

**`auth.router`**（`routers/auth.py:28`, prefix=`/auth`, tags=`["auth"]`）— `/register`・`/login` はKeycloakへ移管済み。

| メソッド | パス | 関数 | 行 | response_model | 認証 | ステータス |
|---|---|---|---|---|---|---|
| GET | `/auth/me` | `read_me` | `auth.py:31` | `UserRead` | 要（`get_current_user`） | 200 |

**`quizzes.router`**（`routers/quizzes.py:23`, prefix=`/quizzes`, tags=`["quizzes"]`）

| メソッド | パス | 関数 | 行 | 入力 | response_model | 認証 | ステータス |
|---|---|---|---|---|---|---|---|
| GET | `/quizzes` | `list_quizzes` | `quizzes.py:27` | query `skip(ge=0)`, `limit(ge=1,le=100)` | `list[QuizRead]` | 不要 | 200 |
| POST | `/quizzes` | `create_quiz` | `quizzes.py:41` | body `QuizCreate` | `QuizRead` | 要 | 201 |
| GET | `/quizzes/{quiz_id}` | `get_quiz` | `quizzes.py:58` | path `quiz_id` | `QuizReadWithComments` | 不要 | 200 / 404 |
| DELETE | `/quizzes/{quiz_id}` | `delete_quiz` | `quizzes.py:73` | path `quiz_id` | （本文なし） | 要 | 204 / 404 / 403 |

> 投稿者は `current_user.id`（トークン由来）を使用しボディの自己申告を信用しない。
> DELETE は「認証(401)」と「認可(403=他人のクイズ)」を区別する好例。

**`comments.router`**（`routers/comments.py:21`, prefix=`/quizzes/{quiz_id}/comments`, tags=`["comments"]`）— 従属資源の階層URL設計。

| メソッド | パス | 関数 | 行 | 入力 | response_model | 認証 | ステータス |
|---|---|---|---|---|---|---|---|
| GET | `/quizzes/{quiz_id}/comments` | `list_comments` | `comments.py:24` | path `quiz_id` | `list[CommentRead]` | 不要 | 200 / 404 |
| POST | `/quizzes/{quiz_id}/comments` | `create_comment` | `comments.py:34` | path `quiz_id` + body `CommentCreate` | `CommentRead` | 要 | 201 / 404 |

> 存在しないクイズへの問い合わせは空リストでなく404（「コメント0件」と「クイズが無い」を区別）。
> `create_comment` は `quiz_id`=URL / `author_id`=トークン / `body`=ボディ の3調達元が合流する。

---

## 7. フロントエンド層

### 7.1 `frontend/api.js` — API/Keycloak 通信モジュール

| 種別 | 名前 | 行 | 役割 |
|---|---|---|---|
| 定数 | `API_BASE` | `api.js:73` | 自API URL `http://localhost:8000`（末尾スラッシュなし） |
| 定数 | `KEYCLOAK_BASE` | `api.js:76` | `http://localhost:8080` |
| 定数 | `KEYCLOAK_REALM` | `api.js:77` | `quiz-sns` |
| 定数 | `KEYCLOAK_CLIENT_ID` | `api.js:78` | `quiz-sns-api`（パブリッククライアント、secret不要） |
| 定数 | `KEYCLOAK_TOKEN_URL` | `api.js:82` | トークン発行URL（config.pyの `keycloak_token_url` と同式） |
| 定数 | `TOKEN_KEY` | `api.js:90` | localStorage キー `quiz_token` |
| 関数 | `getToken`/`setToken`/`clearToken` | `api.js:92-94` | トークンの読み/保存/削除 |
| クラス | `ApiError extends Error` | `api.js:101` | HTTP失敗の独自例外。`status` と `detail` を保持 |
| async関数 | `apiFetch` | `api.js:115` | 自APIへの共通通信。body時に`Content-Type`付与、トークンあれば`Bearer`付与、204はnull、!okで`ApiError`、401で`clearToken()` |
| async関数 | `login` | `api.js:171` | **Keycloakトークンエンドポイント**へ `x-www-form-urlencoded`(`grant_type=password`…)送信。成功で`setToken` |
| 関数 | `logout` | `api.js:202` | ローカルトークン破棄のみ（自APIはステートレス） |
| async関数 | `fetchMe` | `api.js:209` | `apiFetch("/auth/me")`。トークン疎通＋JIT結果確認 |

> トークンは `localStorage` 永続保存（XSSリスクを学習用途で許容）。`KEYCLOAK_*` は API側 `.env` と必ず同値に。
> 末尾コメントに本番向け Authorization Code + PKCE への切替手順あり。

### 7.2 `frontend/app.js` — 画面操作（入力取得→呼び出し→描画）

`index.html` で api.js→app.js の順に読み込むクラシックスクリプト（import なしで api.js の関数を共有）。

| 関数 | 行 | 役割 / 叩くAPI |
|---|---|---|
| `refreshAuthStatus` | `app.js:37` | ログイン状態表示更新。`fetchMe()`（→`GET /auth/me`）。`textContent`使用でXSS安全 |
| `loadQuizzes` | `app.js:56` | 一覧取得・描画（`GET /quizzes`）。`q.title — by q.owner.username` を表示 |
| `handleLogin` | `app.js:78` | `login()`→成功で状態更新（→Keycloakトークンエンドポイント）。失敗は`error_description`表示 |
| `handleLogout` | `app.js:94` | `logout()`→状態更新（API呼び出しなし） |
| `handleCreateQuiz` | `app.js:101` | 入力を組立て`POST /quizzes`。選択肢はtextareaを改行split。401/422/その他でエラー分岐 |

イベント結線（`app.js:145`）: `btn-login/btn-logout/btn-create` に各ハンドラ、読み込み直後に `loadQuizzes()`+`refreshAuthStatus()`。

**フロントが叩くエンドポイント**:

| 操作 | 宛先 | 形式 |
|---|---|---|
| ログイン | **Keycloak** `…/openid-connect/token` | POST form（`grant_type=password`…） |
| 自分情報 | 自API `/auth/me` | GET（Bearer） |
| クイズ一覧 | 自API `/quizzes` | GET（認証不要） |
| クイズ投稿 | 自API `/quizzes` | POST JSON（Bearer必須） |

> 注: コメント関連 / クイズ詳細 / 削除 はバックエンドには存在するが、画面UIは未実装。

---

## 8. テスト用に差し替えられるオブジェクト（参考）

[tests/test_api.py](tests/test_api.py) は外部依存2つを差し替えて MySQL も Keycloak も不要にする:

- **DB**: `dependency_overrides` で `get_db`（§1.6）をインメモリSQLiteへ → DIの実利が最も分かる箇所。
- **Keycloak**: テストがRSA鍵ペアを自作し秘密鍵で署名したトークンを発行、公開鍵を返す偽JWKSクライアントを
  `app.auth.jwks_client`（§5）に差し込む。署名・期限・発行者の検証経路は本物のまま通るので
  「偽造トークンが弾かれること」までテストできる。
