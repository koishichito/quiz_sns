# Quiz SNS API — FastAPI + SQLAlchemy 2.0 + MySQL

クイズを投稿し、投稿されたクイズにコメントできるSNSのバックエンド。
JWT認証つき。学習用に全ファイルへ設計意図のコメントを入れてある。

## 構成

```
quiz_sns/
├── app/
│   ├── main.py          # 入口: アプリの組み立て(lifespan, CORS, ルーター登録)
│   ├── config.py        # .env を読む設定クラス
│   ├── database.py      # エンジン / SessionLocal / Base / get_db(DIの核)
│   ├── models.py        # ORMモデル = DBのテーブル定義(User, Quiz, Comment)
│   ├── schemas.py       # Pydanticスキーマ = APIの入出力の契約
│   ├── auth.py          # パスワードハッシュ / JWT発行 / get_current_user
│   ├── crud.py          # DB操作の関数群(エンドポイントから呼ばれる)
│   └── routers/
│       ├── auth.py      # POST /auth/register, /auth/login
│       ├── quizzes.py   # /quizzes の一覧・投稿・詳細・削除
│       └── comments.py  # /quizzes/{quiz_id}/comments の一覧・投稿
├── tests/
│   └── test_api.py      # 結合テスト(DIでDBをSQLiteに差し替え)
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

### 2. Python 側

```bash
cd quiz_sns
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# .env を編集: DATABASE_URL の接続情報と SECRET_KEY(openssl rand -hex 32)
```

### 3. 起動

```bash
uvicorn app.main:app --reload
```

起動時に lifespan 内の `create_all` がテーブルを自動作成する(開発用。実務は Alembic)。

ブラウザで http://127.0.0.1:8000/docs を開くと Swagger UI が出る。
**最初はコードよりこれを触るのが早い。** 右上の Authorize ボタンに
username/password を入れると、以降のリクエストに自動で
`Authorization: Bearer <token>` が付く。

## curl での動作確認フロー

```bash
# 1. ユーザー登録
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "keisuke", "password": "password123"}'

# 2. ログイン(注意: JSONではなくフォーム形式。-d の素の key=value)
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -d "username=keisuke&password=password123" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

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

## テスト

```bash
pytest tests/ -q
```

MySQL は不要。`dependency_overrides` で `get_db` をインメモリSQLiteに
差し替えて全エンドポイントを通しで検証する(tests/test_api.py 冒頭参照)。
ここが依存性注入の実利が一番分かる場所。

## SQLを観察したいとき

`app/database.py` の `create_engine(..., echo=False)` を `echo=True` に変えると、
発行される SQL が全部ログに出る。`GET /quizzes/{id}` を叩いて、
selectinload が N+1 をどう潰しているか(クエリが3本にまとまる)を見ると面白い。
