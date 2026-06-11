"""アプリケーションの入口。起動コマンド: uvicorn app.main:app --reload
                                            ~~~~~~~~ ~~~
                                            app/main.py の中の `app` 変数、の意味

このファイルの仕事は「組み立て」だけ:
  - 起動/終了時の処理(lifespan)
  - ミドルウェア(CORS)
  - ルーターの登録
ビジネスロジックは一切持たない。持たせない。

━━ Uvicorn と FastAPI の関係(通信の入口)━━━━━━━━━━━━━━━━
Uvicorn = ASGIサーバ。TCPソケットを listen し、届いた生のHTTPバイト列を
          パースして「ASGI」という標準形式に変換するのが仕事。
FastAPI = ASGIアプリケーション。ASGI形式のリクエストを受け取り、
          ルーティング・検証・依存解決・直列化を行うのが仕事。
この分業(ASGIという共通規格)のおかげで、FastAPI製アプリは
Uvicorn 以外のASGIサーバ(Hypercorn等)でもそのまま動く。

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━
- async def / await
    非同期関数。lifespan はASGI仕様の都合で async で書く必要がある。
    一方、各エンドポイントを(async ではない)普通の def で書いているのは
    手抜きではなく整合性のため: 同期DBドライバ(PyMySQL)を使う以上、
    def で書けば FastAPI が別スレッドプールで実行してくれて
    イベントループを塞がない。逆に async def の中で同期DB呼び出しをすると
    ループ全体が詰まり、全リクエストが道連れになる。
    「全部asyncにすれば速い」は誤解で、ドライバとの組み合わせで決まる。

- @asynccontextmanager + yield
    「yield の前 = 入場処理 / 後 = 退場処理」を1つの関数にまとめる装飾。
    database.py の get_db と同じ分割原理の async 版・アプリ寿命スケール版。

- # noqa: F401
    リンター(flake8等)への「この『未使用import』警告は意図的だから
    黙っていてくれ」という指示コメント。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# この import は一見「使っていない」ように見えるが必須。
# models.py がここで読み込まれることで User/Quiz/Comment のクラス定義が実行され、
# Base.metadata(テーブル台帳)に登録される。これが無いと下の create_all が
# 「テーブル0件」と認識して何も作らない。忘れやすい罠の筆頭
from . import models  # noqa: F401
from .database import Base, engine
from .routers import auth, comments, quizzes


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- yield より前: サーバ起動時に1回だけ実行 ----
    # metadata 台帳にあるテーブルのうち【まだ存在しないものだけ】CREATE TABLE する。
    # 既存テーブルの変更(カラム追加等)は一切反映しない点に注意。
    # 開発初期の道具としては便利だが、実務では Alembic で
    # マイグレーション(スキーマ変更の履歴管理)をするのが筋
    Base.metadata.create_all(bind=engine)
    yield
    # ---- yield より後: サーバ終了時に実行(今回は何もしない)----
    # ※ 古い記事の @app.on_event("startup") / ("shutdown") は非推奨化済み。
    #   現在はこの lifespan が公式の書き方


app = FastAPI(title="Quiz SNS API", lifespan=lifespan)
# title は /docs(Swagger UI)の見出しに出る。version, description 等も指定できる

# ━ CORS(Cross-Origin Resource Sharing)━
# ブラウザは「ページの出所(オリジン)と異なるサーバへのJS通信」を
# デフォルトで遮断する(同一オリジンポリシー)。
# localhost:5173 の React から localhost:8000 のこのAPIを呼ぶのは
# ポートが違う=別オリジン扱いなので、サーバ側が「この出所からは許可する」と
# 応答ヘッダで明示的に宣言する必要がある。それを担うのがこのミドルウェア。
# curl や Swagger UI からの確認だけなら不要(ブラウザ内のJSだけが対象の仕組み)
# だが、フロントエンドを繋ぐ段になると必須
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],  # 許可する出所の一覧
    allow_credentials=True,  # Cookie等の資格情報付きリクエストを許可
    allow_methods=["*"],     # 全HTTPメソッドを許可
    allow_headers=["*"],     # 全ヘッダー(Authorization含む)を許可
)

# 各ルーターの対応表を本体へ合流させる。これで
# /auth/* /quizzes/* /quizzes/{id}/comments/* がすべて有効になる
app.include_router(auth.router)
app.include_router(quizzes.router)
app.include_router(comments.router)


@app.get("/")
def health_check():
    # 死活監視用。dict を return すると FastAPI が自動で JSON にして返す
    return {"status": "ok"}
