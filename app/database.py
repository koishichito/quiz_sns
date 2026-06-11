"""DB接続の土台。ここで定義する4点セットが他の全ファイルから使われる。

  engine        … DBへの接続(プール)そのもの。アプリ全体で1個
  SessionLocal  … セッション(=1リクエスト分の作業単位)を作る工場
  Base          … 全ORMモデルの親クラス(テーブル定義の台帳を持つ)
  get_db        … リクエストごとにセッションを貸し出す依存関係(DIの核)

━━ engine と Session の関係(混同しやすい)━━━━━━━━━━━━━━━━
engine  = 電話回線の束(コネクションプール)。作るのは高コストなので
          アプリ全体で1個を共有する。
Session = 1本の通話。リクエストが来るたびに開始し、終わったら必ず切る。
          トランザクション(BEGIN〜COMMIT/ROLLBACK)の文脈もセッションが持つ。
          「engine をリクエストごとに作る」「Session を使い回す」はどちらも誤り。

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━━━
- def get_db(): の中の yield
    yield を含む関数は「ジェネレータ関数」になる。呼び出しても中身は
    すぐ実行されず、next() されるたびに次の yield まで進んで一時停止する。
    FastAPI はこの「途中で止まれる」性質を、リクエスト前処理(yieldの前)と
    後処理(yieldの後)の分割に流用している。詳細は get_db 本体のコメント。

- try / finally
    finally 節は「try の中で何が起きても(正常終了でも例外でも)必ず実行」。
    リソースの後片付けの定番構文。

- class Base(DeclarativeBase): の本体が docstring だけ
    継承するだけで役目を果たすクラス。Python はクラス本体に最低1文を
    要求するので、空にしたいときは pass か docstring を置く。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings
# ↑ 先頭の . は「同じパッケージ(= __init__.py のある同じディレクトリ)」の意
#   (相対インポート)。app がパッケージとして読み込まれる前提の書き方なので、
#   python app/database.py のような直接実行では ImportError で壊れる。
#   起動は必ず uvicorn app.main:app のようにパッケージ経由で行う。

engine = create_engine(
    settings.database_url,
    # ━ MySQL 運用で事実上必須の2点 ━
    # MySQL は wait_timeout(既定8時間)を過ぎたアイドル接続をサーバ側から切る。
    # 切れた接続をプールから掴むと "MySQL server has gone away" になるため:
    pool_pre_ping=True,  # 接続を使う直前に軽い疎通確認(SELECT 1 相当)を打つ
    pool_recycle=3600,   # 3600秒より古い接続は捨てて作り直す
    echo=False,  # True にすると発行SQLが全部ログに出る。学習中は True を強く推奨。
                 # ORMが裏で何をしているか(N+1問題の有無など)が目視できる
)
# create_engine はこの時点では DB に接続しない(遅延接続)。
# 最初に実際のクエリが流れた瞬間、初めて物理接続が張られる。

# セッション工場。SessionLocal() と呼ぶたびに新しい Session が1個できる。
# 注意: 古い記事にある autocommit=False は SQLAlchemy 2.0 で引数ごと削除済み。
#       書くと TypeError(2.0 では非autocommitが唯一の動作なので指定自体が不要)
SessionLocal = sessionmaker(
    bind=engine,      # この工場が作るセッションはどの engine を使うか
    autoflush=False,  # クエリのたびに自動で flush(未確定変更のSQL送信)をしない。
                      # 「SQLが飛ぶタイミング」を予測しやすくするための定番設定
)


class Base(DeclarativeBase):
    """全ORMモデルの基底クラス。

    これを継承したクラス(User, Quiz, Comment)が定義されるたびに、
    そのテーブル情報が Base.metadata という台帳へ自動登録される。
    main.py の Base.metadata.create_all() は、この台帳を見て
    CREATE TABLE 文を発行している。
    """


def get_db():
    """リクエスト1件ごとにDBセッションを貸し出す依存関係(依存性注入の核)。

    エンドポイント側は db: Session = Depends(get_db) と書くだけ。
    FastAPI はこの関数を内部でこう操作する(擬似コード):

        gen = get_db()       # ジェネレータ生成(この時点で中身は1行も実行されない)
        db  = next(gen)      # yield まで実行 → SessionLocal() の結果を受け取る
        ... エンドポイント関数を db 付きで実行し、レスポンスを直列化 ...
        genを最後まで進める   # yield の続き = finally: db.close() が走る

    つまりこの関数は yield を境に前半と後半へ割られていて、
      yield より前 = リクエスト処理の【前】に実行(資源の調達)
      yield        = ここで一時停止し、値(db)をエンドポイントへ渡す
      yield より後 = レスポンス生成【後】に実行(後片付け)
    という前後処理のサンドイッチ構造になっている。

    finally に置いてあるので、エンドポイントの途中で例外が爆発しても
    セッションは必ず close される(接続リークの防止)。

    なお close() は「接続をプールへ返却」する操作であって commit ではない。
    commit するか否かの判断は crud.py 側の責務に置く方針で統一している。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
